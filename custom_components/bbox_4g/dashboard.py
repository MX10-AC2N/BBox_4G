"""Lovelace dashboards for the Bbox integration.

Three optional dashboards are generated (sidebar, editable from the UI):

* ``native``   – no custom card;
* ``mushroom`` – needs the *Mushroom* cards;
* ``auto``     – needs *auto-entities*; light list that follows new devices
  on its own.

``native`` and ``mushroom`` have the same layout, in three sections:

1. **Router** – Wi-Fi, guest network, refresh…;
2. **Connected** – every device currently online (wired or Wi-Fi) with its
   local IP and *its own* switches (Internet, Wi-Fi, connection);
3. **Blocked** – every device with a switch cut, to restore it quickly.

Every device has one card per section; ``conditional`` cards show or hide it
from the live state, so the layout is rewritten only when a device appears,
disappears or is renamed. Entities are resolved through the entity registry
(unique ids), never guessed from their name.

A *new* dashboard needs one Home Assistant restart to show up in the
sidebar; afterwards it is updated live.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .parental import QUOTA_KEYS, TIME_KEYS
from .const import (
    CONF_BLOCK_SCOPE,
    CONF_DASHBOARDS,
    DASHBOARD_AUTO,
    DASHBOARD_CHOICES,
    DASHBOARD_MUSHROOM,
    DASHBOARD_NATIVE,
    DEFAULT_BLOCK_SCOPE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# the router names unnamed devices after their MAC address
_MAC_NAME_RE = re.compile(r"^[0-9a-f]{2}([-:][0-9a-f]{2}){5}$", re.IGNORECASE)
_MAC_NAME_JINJA = "^[0-9A-Fa-f]{2}([-:][0-9A-Fa-f]{2}){5}$"

# url_path MUST contain a hyphen (Home Assistant rule for new dashboards)
STORAGE_PATHS = {
    DASHBOARD_NATIVE: "bbox-native",
    DASHBOARD_AUTO: "bbox-auto",
    DASHBOARD_MUSHROOM: "bbox-mushroom",
}

STORAGE_TITLES = {
    DASHBOARD_NATIVE: "Bbox",
    DASHBOARD_AUTO: "Bbox (auto)",
    DASHBOARD_MUSHROOM: "Bbox (Mushroom)",
}

STORAGE_ICONS = {
    DASHBOARD_NATIVE: "mdi:router-wireless",
    DASHBOARD_AUTO: "mdi:router-wireless",
    DASHBOARD_MUSHROOM: "mdi:router-wireless",
}

# ---------------------------------------------------------------------------
# Texts (French / English, chosen from the Home Assistant language)
# ---------------------------------------------------------------------------

_TEXTS: dict[str, dict[str, str]] = {
    "fr": {
        "box": "Box",
        "connected": "Appareils connectés",
        "blocked": "Appareils bloqués",
        "wired": "Filaire",
        "wifi": "Wi-Fi",
        "offline": "Hors ligne",
        "unknown": "Appareil inconnu",
        "online": "en ligne",
        "guest": "Invité",
        "none_connected": "Aucun appareil connecté.",
        "none_blocked": "Aucun appareil bloqué.",
        "parental": "Contrôle parental",
        "pc_windows": "Plages de coupure",
        "pc_control": "Contrôle",
        "pc_extra": "+1 h",
        "pc_status": "Statut",
        "pc_enabled": "Contrôle activé",
        "pc_override": "Rétablir 1 h",
        "pc_week": "Semaine",
        "pc_weekend": "Week-end",
        "pc_wk_start": "Semaine : coupure à",
        "pc_wk_end": "Semaine : rétablissement à",
        "pc_we_start": "Week-end : coupure à",
        "pc_we_end": "Week-end : rétablissement à",
        "pc_quota_wk": "Quota semaine",
        "pc_quota_we": "Quota week-end",
        "pc_used": "Temps utilisé",
        "pc_remaining": "Temps restant",
        "pc_holiday": "Mode vacances",
        "pc_warn": "Alerte avant fin de quota",
        "pc_bonus": "+15 min de quota",
        "pc_dev_override": "Rétablir 1 h",
        "pc_left_today": "Temps restant aujourd'hui",
        "sw_internet": "Internet",
        "sw_wifi": "Wi-Fi",
        "sw_link": "Connexion",
        "row_ip": "Adresse IP",
        "row_internet": "Accès Internet",
        "row_wifi": "Accès Wi-Fi",
        "row_link": "Connexion",
        "empty": "Aucun appareil connu pour le moment.",
        "notif_title": "Bbox Bouygues",
        "notif": (
            "**Dashboards Bbox prêts** : {paths}\n\n"
            "1. **Redémarrez Home Assistant** une fois (nécessaire pour qu'un "
            "nouveau dashboard apparaisse dans la barre latérale)\n"
            "2. Ouvrez-le dans la barre latérale\n"
            "3. Menu ⋮ → **Modifier le dashboard** pour le personnaliser\n\n"
            "**mushroom** nécessite les cartes HACS *Mushroom*, **auto** "
            "*auto-entities*. Essayez **native** sinon.\n"
            "Ensuite, la liste d'appareils se met à jour toute seule."
        ),
    },
    "en": {
        "box": "Router",
        "connected": "Connected devices",
        "blocked": "Blocked devices",
        "wired": "Wired",
        "wifi": "Wi-Fi",
        "offline": "Offline",
        "unknown": "Unknown device",
        "online": "online",
        "guest": "Guest",
        "none_connected": "No connected device.",
        "none_blocked": "No blocked device.",
        "parental": "Parental control",
        "pc_windows": "Bedtime windows",
        "pc_control": "Control",
        "pc_extra": "+1 h",
        "pc_status": "Status",
        "pc_enabled": "Control enabled",
        "pc_override": "Restore for 1 h",
        "pc_week": "Weekdays",
        "pc_weekend": "Weekend",
        "pc_wk_start": "Weekdays: cut at",
        "pc_wk_end": "Weekdays: restore at",
        "pc_we_start": "Weekend: cut at",
        "pc_we_end": "Weekend: restore at",
        "pc_quota_wk": "Weekday quota",
        "pc_quota_we": "Weekend quota",
        "pc_used": "Time used",
        "pc_remaining": "Time left",
        "pc_holiday": "Holiday mode",
        "pc_warn": "Alert before quota ends",
        "pc_bonus": "+15 min quota",
        "pc_dev_override": "Restore for 1 h",
        "pc_left_today": "Time left today",
        "sw_internet": "Internet",
        "sw_wifi": "Wi-Fi",
        "sw_link": "Link",
        "row_ip": "IP address",
        "row_internet": "Internet access",
        "row_wifi": "Wi-Fi access",
        "row_link": "Connection",
        "empty": "No known device yet.",
        "notif_title": "Bbox Bouygues",
        "notif": (
            "**Bbox dashboards ready**: {paths}\n\n"
            "1. **Restart Home Assistant** once (needed for a new dashboard "
            "to show up in the sidebar)\n"
            "2. Open it from the sidebar\n"
            "3. ⋮ menu → **Edit dashboard** to customise it\n\n"
            "**mushroom** needs the HACS *Mushroom* cards, **auto** needs "
            "*auto-entities*. Try **native** otherwise.\n"
            "Afterwards the device list updates by itself."
        ),
    },
}


def _t(hass: HomeAssistant) -> dict[str, str]:
    lang = str(getattr(hass.config, "language", "en") or "en").lower()
    return _TEXTS["fr" if lang.startswith("fr") else "en"]


# ---------------------------------------------------------------------------
# Entity lookup (entity registry, by unique id)
# ---------------------------------------------------------------------------


def _eid(reg: er.EntityRegistry, domain: str, unique_id: str) -> str | None:
    return reg.async_get_entity_id(domain, DOMAIN, unique_id)


def _box_entities(hass: HomeAssistant) -> dict[str, str | None]:
    """``entity_id`` of the router entities (``None`` when not registered)."""
    reg = er.async_get(hass)
    return {
        "clients": _eid(reg, "sensor", "clients_connected"),
        "known": _eid(reg, "sensor", "devices_known"),
        "guest": _eid(reg, "sensor", "guest_network"),
        "wifi": _eid(reg, "switch", "wifi_all"),
        "wifi24": _eid(reg, "switch", "wifi_24"),
        "wifi5": _eid(reg, "switch", "wifi_5"),
        "refresh": _eid(reg, "button", "refresh"),
        "reconnect": _eid(reg, "button", "reconnect"),
    }


def _scope(hass: HomeAssistant) -> str:
    for entry in hass.config_entries.async_entries(DOMAIN):
        return str((entry.options or {}).get(CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE))
    return DEFAULT_BLOCK_SCOPE


def _devices(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Known devices with the entity ids of their own entities.

    A device is kept when its presence sensor is registered; the switches that
    do not exist (e.g. no Wi-Fi switch on a wired device) are simply absent.
    """
    reg = er.async_get(hass)
    # With the default scope (``lan``) the "Connexion" switch drives the very
    # same MAC filter as "Internet": showing both would be a duplicate.
    show_link = _scope(hass) != DEFAULT_BLOCK_SCOPE
    raw: list[dict[str, Any]] = []
    for value in (hass.data.get(DOMAIN) or {}).values():
        if isinstance(value, dict) and "coordinator" in value:
            raw += list((value["coordinator"].data or {}).get("devices") or [])

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dev in raw:
        mac = str(dev.get("mac") or "").lower()
        if not mac or mac in seen:
            continue
        seen.add(mac)
        presence = _eid(reg, "binary_sensor", f"presence_{mac}")
        if not presence:
            continue
        name = str(dev.get("name") or mac)
        out.append(
            {
                "mac": mac,
                "name": name,
                "unnamed": bool(_MAC_NAME_RE.match(name)),
                "presence": presence,
                "internet": _eid(reg, "switch", f"internet_{mac}"),
                "wifi": _eid(reg, "switch", f"wifi_device_{mac}"),
                "link": _eid(reg, "switch", f"device_{mac}") if show_link else None,
            }
        )
    # named devices first (A-Z), unnamed ones last
    out.sort(key=lambda d: (d["unnamed"], d["name"].lower()))
    return out


def _tail(dev: dict[str, Any]) -> str:
    """Last two bytes of the MAC, e.g. ``5E:B5``."""
    return dev["mac"][-5:].upper()


def _label(txt: dict[str, str], dev: dict[str, Any]) -> str:
    if dev["unnamed"]:
        return f"{txt['unknown']} ({_tail(dev)})"
    return dev["name"]


def _parental_profiles(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Parental-control profiles with the entity ids of their entities."""
    txt = _t(hass)
    reg = er.async_get(hass)
    out: list[dict[str, Any]] = []
    for value in (hass.data.get(DOMAIN) or {}).values():
        manager = value.get("parental") if isinstance(value, dict) else None
        if not manager:
            continue
        for prof in manager.profiles:
            pid = prof["id"]

            def ent(domain: str, key: str, pid: str = pid) -> str | None:
                return _eid(reg, domain, f"parental_{pid}_{key}")

            devices: list[dict[str, Any]] = []
            for mac in prof["macs"]:
                found = manager.coordinator.device_by_mac(mac) or {}
                name = str(found.get("name") or "")
                label = (
                    f"{txt['unknown']} {mac[-5:].upper()}"
                    if not name or _MAC_NAME_RE.match(name)
                    else name
                )
                devices.append(
                    {
                        "label": label,
                        "used": ent("sensor", f"{mac}_used"),
                        "remaining": ent("sensor", f"{mac}_remaining"),
                        "bonus": ent("button", f"{mac}_bonus"),
                        "override": ent("button", f"{mac}_override"),
                        "quotas": {
                            k: ent("number", f"{mac}_{k}") for k in QUOTA_KEYS
                        },
                    }
                )
            out.append(
                {
                    "name": prof["name"],
                    "status": ent("sensor", "status"),
                    "enabled": ent("switch", "enabled"),
                    "override": ent("button", "override"),
                    "holiday": ent("switch", "holiday"),
                    "warn": ent("number", "warn_min"),
                    "times": {k: ent("time", k) for k in TIME_KEYS},
                    "devices": devices,
                }
            )
    out.sort(key=lambda p: p["name"].lower())
    return out


def _parental_settings_rows(txt: dict[str, str], prof: dict[str, Any]) -> list[Any]:
    """Settings rows (explicit names: the default ones are too long and get cut)."""
    rows: list[Any] = []
    times = [
        {"entity": e, "name": txt[f"pc_{k}"]} for k, e in prof["times"].items() if e
    ]
    if times:
        rows += [{"type": "section", "label": txt["pc_windows"]}, *times]
    for dev in prof["devices"]:
        entities: list[Any] = []
        if dev.get("remaining"):
            entities.append({"entity": dev["remaining"], "name": txt["pc_remaining"]})
        if dev["used"]:
            entities.append({"entity": dev["used"], "name": txt["pc_used"]})
        for k, e in dev["quotas"].items():
            if e:
                entities.append(
                    {"entity": e, "name": txt["pc_quota_wk" if k == "wk_quota" else "pc_quota_we"]}
                )
        if dev.get("bonus"):
            entities.append({"entity": dev["bonus"], "name": txt["pc_bonus"]})
        if dev.get("override"):
            entities.append({"entity": dev["override"], "name": txt["pc_dev_override"]})
        if entities:
            rows += [{"type": "section", "label": dev["label"]}, *entities]
    if prof.get("warn"):
        rows.append({"entity": prof["warn"], "name": txt["pc_warn"]})
    return rows


def _parental_left_card(txt: dict[str, str], prof: dict[str, Any]) -> dict[str, Any] | None:
    """Small table: time left today for each device of the profile."""
    rows = [
        f"| {d['label']} | {{{{ states('{d['remaining']}') }}}} |"
        for d in prof["devices"]
        if d.get("remaining")
    ]
    if not rows:
        return None
    table = "\n".join([f"| {txt['pc_left_today']} | |", "|:--|--:|", *rows])
    return {"type": "markdown", "content": table}


def _parental_native_card(txt: dict[str, str], prof: dict[str, Any]) -> dict[str, Any]:
    rows: list[Any] = []
    for key, label in (
        ("status", "pc_status"),
        ("enabled", "pc_enabled"),
        ("holiday", "pc_holiday"),
        ("override", "pc_override"),
    ):
        if prof[key]:
            rows.append({"entity": prof[key], "name": txt[label]})
    rows += _parental_settings_rows(txt, prof)
    card: dict[str, Any] = {
        "type": "entities",
        "title": prof["name"],
        "show_header_toggle": False,
        "state_color": True,
        "entities": rows,
    }
    left = _parental_left_card(txt, prof)
    if left:
        return {"type": "vertical-stack", "cards": [card, left]}
    return card


def _parental_mushroom_card(txt: dict[str, str], prof: dict[str, Any]) -> dict[str, Any]:
    st, en, btn = prof["status"], prof["enabled"], prof["override"]
    cards: list[dict[str, Any]] = []
    if st:
        code = f"state_attr('{st}', 'code')"
        t = prof["times"]
        summary = ""
        if all(t.get(k) for k in TIME_KEYS):
            def span(a: str, b: str) -> str:
                return f"{{{{ states('{t[a]}')[:5] }}}}–{{{{ states('{t[b]}')[:5] }}}}"

            summary = (
                f" · {txt['pc_week']} {span('wk_start', 'wk_end')}"
                f" · {txt['pc_weekend']} {span('we_start', 'we_end')}"
            )
        cards.append(
            {
                "type": "custom:mushroom-template-card",
                "entity": st,
                "primary": prof["name"],
                "secondary": f"{{{{ states('{st}') }}}}{summary}",
                "icon": f"{{{{ state_attr('{st}', 'icon') or 'mdi:shield-account' }}}}",
                "icon_color": (
                    f"{{% set c = {code} %}}"
                    "{% if c == 'free' %}green{% elif c in ['schedule','quota'] %}red"
                    "{% elif c == 'override' %}orange{% else %}grey{% endif %}"
                ),
                "tap_action": {"action": "more-info"},
            }
        )
    chips: list[dict[str, Any]] = []
    if en:
        chips.append(
            {
                "type": "template",
                "entity": en,
                "icon": "mdi:shield-account",
                "icon_color": f"{{{{ 'green' if is_state('{en}', 'on') else 'grey' }}}}",
                "content": txt["pc_control"],
                "tap_action": {"action": "toggle"},
            }
        )
    hol = prof.get("holiday")
    if hol:
        chips.append(
            {
                "type": "template",
                "entity": hol,
                "icon": "mdi:beach",
                "icon_color": f"{{{{ 'amber' if is_state('{hol}', 'on') else 'grey' }}}}",
                "content": txt["pc_holiday"],
                "tap_action": {"action": "toggle"},
            }
        )
    if btn:
        chips.append(
            {
                "type": "template",
                "entity": btn,
                "icon": "mdi:lock-clock",
                "icon_color": (
                    f"{{{{ 'orange' if state_attr('{st}', 'code') in ['schedule', 'quota'] "
                    "else 'grey' }}"
                ) if st else "orange",
                "content": txt["pc_extra"],
                "tap_action": {
                    "action": "perform-action",
                    "perform_action": "button.press",
                    "target": {"entity_id": btn},
                },
            }
        )
    if chips:
        cards.append({"type": "custom:mushroom-chips-card", "chips": chips})
    left = _parental_left_card(txt, prof)
    if left:
        cards.append(left)
    rows = _parental_settings_rows(txt, prof)
    if rows:
        cards.append(
            {
                "type": "entities",
                "show_header_toggle": False,
                "entities": rows,
            }
        )
    return {"type": "vertical-stack", "cards": cards}


def _switches(dev: dict[str, Any]) -> list[str]:
    return [dev[k] for k in ("internet", "wifi", "link") if dev.get(k)]


def _only(items: list[str | None]) -> list[str]:
    return [i for i in items if i]


def _box_rows(box: dict[str, str | None]) -> list[str]:
    return _only(
        [box[k] for k in ("clients", "known", "guest", "wifi", "wifi24", "wifi5", "refresh", "reconnect")]
    )


# ---------------------------------------------------------------------------
# Visibility conditions
# ---------------------------------------------------------------------------


def _cond_connected(dev: dict[str, Any]) -> list[dict[str, Any]]:
    """Online and not blocked (a blocked device is listed under "Blocked" only)."""
    return [
        {"condition": "state", "entity": dev["presence"], "state": "on"},
        *(
            {"condition": "state", "entity": e, "state_not": "off"}
            for e in _switches(dev)
        ),
    ]


def _cond_blocked(dev: dict[str, Any]) -> list[dict[str, Any]]:
    """Blocked = at least one of the device switches is off."""
    states = [
        {"condition": "state", "entity": e, "state": "off"} for e in _switches(dev)
    ]
    if len(states) <= 1:
        return states
    return [{"condition": "or", "conditions": states}]


def _conditional(conditions: list[dict[str, Any]], card: dict[str, Any]) -> dict[str, Any]:
    return {"type": "conditional", "conditions": conditions, "card": card}


def _empty_hints(
    txt: dict[str, str], devices: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Helper texts shown only while a section would otherwise be empty."""
    if not devices:
        return [], []
    # "no connected device" = for every device: offline OR blocked
    none_on = [
        {
            "condition": "or",
            "conditions": [
                {"condition": "state", "entity": d["presence"], "state_not": "on"},
                *(
                    {"condition": "state", "entity": e, "state": "off"}
                    for e in _switches(d)
                ),
            ],
        }
        for d in devices
    ]
    none_off = [
        {"condition": "state", "entity": e, "state_not": "off"}
        for d in devices
        for e in _switches(d)
    ]
    hint_connected = [
        _conditional(none_on, {"type": "markdown", "content": txt["none_connected"]})
    ]
    hint_blocked = (
        [_conditional(none_off, {"type": "markdown", "content": txt["none_blocked"]})]
        if none_off
        else []
    )
    return hint_connected, hint_blocked


def _count_template(devices: list[dict[str, Any]], kind: str) -> str:
    """Jinja expression: number of connected / blocked devices."""
    parts: list[str] = []
    for d in devices:
        sw = _switches(d)
        cut = " or ".join(f"is_state('{e}', 'off')" for e in sw)
        if kind == "connected":
            expr = f"is_state('{d['presence']}', 'on')"
            if cut:
                expr += f" and not ({cut})"
            parts.append(f"(({expr}) | int(0))")
        elif cut:
            parts.append(f"(({cut}) | int(0))")
    return "{{ " + (" + ".join(parts) if parts else "0") + " }}"


def _sections_view(
    box_cards: list[dict[str, Any]],
    txt: dict[str, str],
    connected: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    *,
    devices: list[dict[str, Any]] | None = None,
    counts: bool = False,
    parental: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    devices = devices or []
    hint_connected, hint_blocked = _empty_hints(txt, devices)
    connected = [*connected, *hint_connected]
    blocked = [*blocked, *hint_blocked]

    def heading(title: str, icon: str, kind: str = "") -> dict[str, Any]:
        if counts:
            # Mushroom title cards accept templates: "Connected devices (3)".
            # Used for every section so the headings look the same.
            if kind and devices:
                title = f"{title} ({_count_template(devices, kind)})"
            return {"type": "custom:mushroom-title-card", "title": title}
        return {"type": "heading", "heading": title, "icon": icon, "heading_style": "title"}

    sections = [
        {"type": "grid", "cards": [heading(txt["box"], "mdi:router-wireless"), *box_cards]},
        {
            "type": "grid",
            "cards": [heading(txt["connected"], "mdi:lan-connect", "connected"), *connected],
        },
        {
            "type": "grid",
            "cards": [heading(txt["blocked"], "mdi:web-off", "blocked"), *blocked],
        },
    ]
    if parental:
        sections.append(
            {
                "type": "grid",
                "cards": [heading(txt["parental"], "mdi:shield-account"), *parental],
            }
        )
    return {
        "views": [
            {
                "title": "Bbox",
                "path": "home",
                "type": "sections",
                "max_columns": 2,
                "sections": sections,
            }
        ]
    }


# ---------------------------------------------------------------------------
# native : standard cards only
# ---------------------------------------------------------------------------


def _native_device_card(txt: dict[str, str], dev: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    if dev["internet"]:
        rows.append(
            {
                "type": "attribute",
                "entity": dev["internet"],
                "attribute": "ip",
                "name": txt["row_ip"],
                "icon": "mdi:ip-network",
            }
        )
    for key, label in (("internet", "row_internet"), ("wifi", "row_wifi"), ("link", "row_link")):
        if dev.get(key):
            rows.append({"entity": dev[key], "name": txt[label]})
    return {
        "type": "entities",
        "title": _label(txt, dev),
        "show_header_toggle": False,
        "state_color": True,
        "entities": rows,
    }


def _config_native(hass: HomeAssistant) -> dict[str, Any]:
    txt = _t(hass)
    box = _box_entities(hass)
    devices = _devices(hass)

    box_cards: list[dict[str, Any]] = [
        {"type": "entities", "show_header_toggle": False, "entities": _box_rows(box)}
    ]
    if not devices:
        box_cards.append({"type": "markdown", "content": txt["empty"]})

    connected = [
        _conditional(_cond_connected(d), _native_device_card(txt, d)) for d in devices
    ]
    blocked = [
        _conditional(_cond_blocked(d), _native_device_card(txt, d))
        for d in devices
        if _switches(d)
    ]
    parental = [_parental_native_card(txt, p) for p in _parental_profiles(hass)]
    return _sections_view(
        box_cards, txt, connected, blocked, devices=devices, parental=parental
    )


# ---------------------------------------------------------------------------
# mushroom : header + switch chips per device
# ---------------------------------------------------------------------------


def _mushroom_device_card(txt: dict[str, str], dev: dict[str, Any]) -> dict[str, Any]:
    """Header (name, IP, link type) + one chip per switch of the device."""
    ref = dev["internet"] or dev["presence"]
    pres = dev["presence"]
    link = f"(state_attr('{ref}', 'lien') | string | lower)"
    wifi = f"('wi' in {link})"
    # unnamed device (the router names it after its MAC address)?
    unnamed = (
        f"{{% set n = state_attr('{ref}', 'nom') | string %}}"
        f"{{% set unk = (n in ['', 'None']) or n | regex_match('{_MAC_NAME_JINJA}') %}}"
    )
    ip = f"((state_attr('{ref}', 'ip') or '') | string | replace(',', ';')).split(';')[0]"
    header = {
        "type": "custom:mushroom-template-card",
        "entity": pres,
        "primary": (
            f"{unnamed}{{% if unk %}}{txt['unknown']}{{% else %}}{{{{ n }}}}{{% endif %}}"
        ),
        "secondary": (
            f"{unnamed}"
            f"{{% if is_state('{pres}', 'on') %}}"
            f"{{{{ {ip} }}}} · "
            f"{{{{ state_attr('{ref}', 'ssid') or ('{txt['wifi']}' if {wifi} else '{txt['wired']}') }}}}"
            f"{{% else %}}{txt['offline']}{{% endif %}}"
            f"{{% if unk %}} · {_tail(dev)}{{% endif %}}"
        ),
        "icon": f"{{% if {wifi} %}}mdi:wifi{{% else %}}mdi:ethernet{{% endif %}}",
        "icon_color": f"{{{{ 'green' if is_state('{pres}', 'on') else 'grey' }}}}",
        "tap_action": {"action": "more-info"},
    }

    chips: list[dict[str, Any]] = []
    for key, label, on_icon, off_icon in (
        ("internet", "sw_internet", "mdi:web", "mdi:web-off"),
        ("wifi", "sw_wifi", "mdi:wifi", "mdi:wifi-off"),
        ("link", "sw_link", "mdi:lan-connect", "mdi:lan-disconnect"),
    ):
        eid = dev.get(key)
        if not eid:
            continue
        chips.append(
            {
                "type": "template",
                "entity": eid,
                "icon": f"{{{{ '{on_icon}' if is_state('{eid}', 'on') else '{off_icon}' }}}}",
                "icon_color": f"{{{{ 'green' if is_state('{eid}', 'on') else 'red' }}}}",
                "content": txt[label],
                "tap_action": {"action": "toggle"},
            }
        )

    cards: list[dict[str, Any]] = [header]
    if chips:
        cards.append({"type": "custom:mushroom-chips-card", "chips": chips})
    return {
        "type": "vertical-stack",
        "cards": cards,
        # optional (needs card_mod): glue the header and its chips together
        "card_mod": {
            "style": ":host { --vertical-stack-card-gap: 0px; --stack-card-gap: 0px; }"
        },
    }


def _box_chips(txt: dict[str, str], box: dict[str, str | None]) -> list[dict[str, Any]]:
    """Labelled, coloured chips for the router section."""
    chips: list[dict[str, Any]] = []
    clients = box["clients"]
    if clients:
        chips.append(
            {
                "type": "template",
                "entity": clients,
                "icon": "mdi:lan-connect",
                "icon_color": f"{{{{ 'green' if states('{clients}') | int(0) > 0 else 'grey' }}}}",
                "content": f"{{{{ states('{clients}') }}}} {txt['online']}",
                "tap_action": {"action": "more-info"},
            }
        )
    for key, label in (("wifi", txt["sw_wifi"]), ("wifi24", "2,4 GHz"), ("wifi5", "5 GHz")):
        eid = box[key]
        if eid:
            chips.append(
                {
                    "type": "template",
                    "entity": eid,
                    "icon": f"{{{{ 'mdi:wifi' if is_state('{eid}', 'on') else 'mdi:wifi-off' }}}}",
                    "icon_color": f"{{{{ 'green' if is_state('{eid}', 'on') else 'red' }}}}",
                    "content": label,
                    "tap_action": {"action": "toggle"},
                }
            )
    guest = box["guest"]
    if guest:
        chips.append(
            {
                "type": "template",
                "entity": guest,
                "icon": "mdi:account-network",
                "icon_color": f"{{{{ 'green' if is_state('{guest}', 'activé') else 'grey' }}}}",
                "content": txt["guest"],
                "tap_action": {"action": "more-info"},
            }
        )
    # icon-only action chips (a button's own state is just its last press)
    for key, icon, confirm in (("refresh", "mdi:refresh", False), ("reconnect", "mdi:connection", True)):
        eid = box[key]
        if eid:
            tap: dict[str, Any] = {
                "action": "perform-action",
                "perform_action": "button.press",
                "target": {"entity_id": eid},
            }
            if confirm:
                tap["confirmation"] = True
            chips.append({"type": "action", "icon": icon, "tap_action": tap})
    return chips


def _config_mushroom(hass: HomeAssistant) -> dict[str, Any]:
    txt = _t(hass)
    box = _box_entities(hass)
    devices = _devices(hass)

    chips = _box_chips(txt, box)
    box_cards: list[dict[str, Any]] = []
    if chips:
        box_cards.append({"type": "custom:mushroom-chips-card", "chips": chips})
    if not devices:
        box_cards.append({"type": "markdown", "content": txt["empty"]})

    connected = [
        _conditional(_cond_connected(d), _mushroom_device_card(txt, d)) for d in devices
    ]
    blocked = [
        _conditional(_cond_blocked(d), _mushroom_device_card(txt, d))
        for d in devices
        if _switches(d)
    ]
    parental = [_parental_mushroom_card(txt, p) for p in _parental_profiles(hass)]
    return _sections_view(
        box_cards,
        txt,
        connected,
        blocked,
        devices=devices,
        counts=True,
        parental=parental,
    )


# ---------------------------------------------------------------------------
# auto : auto-entities (follows new devices on its own)
# ---------------------------------------------------------------------------


def _auto_section(title: str, attrs: dict[str, str], domain_switch: bool = True) -> dict[str, Any]:
    include: dict[str, Any] = {
        "domain": "switch",
        "attributes": attrs,
        "options": {"secondary_info": "attribute", "attribute": "ip"},
    }
    return {
        "type": "custom:auto-entities",
        "show_empty": False,
        "card": {"type": "entities", "title": title, "show_header_toggle": False},
        "filter": {"include": [include]},
        "sort": {"method": "attribute", "attribute": "nom", "ignore_case": True},
    }


def _config_auto(hass: HomeAssistant) -> dict[str, Any]:
    txt = _t(hass)
    box = _box_entities(hass)
    parental = []
    for prof in _parental_profiles(hass):
        card = _parental_native_card(txt, prof)
        # no section heading in a masonry view: put it in the card title
        first = card["cards"][0] if card["type"] == "vertical-stack" else card
        first["title"] = f"{txt['parental']} – {prof['name']}"
        parental.append(card)
    return {
        "views": [
            {
                "title": "Bbox",
                "path": "home",
                "type": "masonry",
                "cards": [
                    {
                        "type": "entities",
                        "title": txt["box"],
                        "show_header_toggle": False,
                        "entities": _box_rows(box),
                    },
                    # one row per device: its Internet switch
                    _auto_section(
                        txt["connected"],
                        {"appareil": "client", "portee": "internet", "etat_connexion": "connecte"},
                    ),
                    # every client switch that is cut (Internet or Wi-Fi)
                    _auto_section(
                        txt["blocked"], {"appareil": "client", "acces": "coupe"}
                    ),
                    *parental,
                ],
            }
        ]
    }


def _config_for(hass: HomeAssistant, kind: str) -> dict[str, Any]:
    builders = {
        DASHBOARD_NATIVE: _config_native,
        DASHBOARD_AUTO: _config_auto,
        DASHBOARD_MUSHROOM: _config_mushroom,
    }
    return builders.get(kind, _config_native)(hass)


# ---------------------------------------------------------------------------
# Writing to the Lovelace storage
# ---------------------------------------------------------------------------


def _live_dashboard(hass: HomeAssistant, url_path: str) -> Any | None:
    """The registered ``LovelaceStorage`` of a dashboard, if it is live."""
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
    except ImportError:  # pragma: no cover
        return None
    data = hass.data.get(LOVELACE_DATA)
    dashboards = getattr(data, "dashboards", None) or {}
    obj = dashboards.get(url_path)
    return obj if obj is not None and hasattr(obj, "async_save") else None


async def async_create_storage_dashboard(hass: HomeAssistant, kind: str) -> str:
    """Create or update a storage dashboard.

    Returns ``"new"`` (restart needed), ``"updated"`` (live), ``"unchanged"``
    or ``""`` on failure.
    """
    url_path = STORAGE_PATHS.get(kind)
    if not url_path:
        return ""
    config = _config_for(hass, kind)

    try:
        live = _live_dashboard(hass, url_path)
        if live is not None:
            try:
                if await live.async_load(False) == config:
                    return "unchanged"
            except Exception:  # noqa: BLE001 - no config yet
                pass
            await live.async_save(config)
            _LOGGER.info("Dashboard %s updated", url_path)
            return "updated"

        item = {
            "id": url_path,
            "url_path": url_path,
            "title": STORAGE_TITLES[kind],
            "icon": STORAGE_ICONS[kind],
            "show_in_sidebar": True,
            "require_admin": False,
        }
        dashboards: Store = Store(hass, 1, "lovelace_dashboards")
        data = await dashboards.async_load() or {}
        items = [i for i in (data.get("items") or []) if i.get("id") != url_path]
        items.append(item)
        data["items"] = items
        await dashboards.async_save(data)

        # LovelaceStorage reads ``{"config": <view config>}``
        content: Store = Store(hass, 1, f"lovelace.{url_path}")
        await content.async_save({"config": config})
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unable to write the dashboard %s", url_path)
        return ""

    _LOGGER.info("Dashboard %s written (restart needed to show it)", url_path)
    return "new"


async def async_setup_selected_dashboards(
    hass: HomeAssistant,
    selected: list[str],
    *,
    notify: bool = True,
) -> list[str]:
    """Create the requested dashboards and return their url paths."""
    created: list[str] = []
    needs_restart = False
    for kind in selected:
        if kind not in DASHBOARD_CHOICES:
            continue
        result = await async_create_storage_dashboard(hass, kind)
        if result:
            created.append(STORAGE_PATHS[kind])
        needs_restart = needs_restart or result == "new"

    if notify and created and needs_restart:
        txt = _t(hass)
        persistent_notification.async_create(
            hass,
            txt["notif"].format(paths=", ".join(f"`/{p}`" for p in created)),
            title=txt["notif_title"],
            notification_id="bbox_4g_dashboards",
        )

    return created


async def async_write_dashboard(
    hass: HomeAssistant, devices: list[dict[str, Any]]
) -> bool:
    """Called by the coordinator when the device list changes.

    Rewrites the dashboards chosen in the options so that new, removed or
    renamed devices show up without any manual action.
    """
    kinds: list[str] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        for kind in (entry.options or {}).get(CONF_DASHBOARDS, []) or []:
            if kind in DASHBOARD_CHOICES and kind not in kinds:
                kinds.append(kind)
    if not kinds:
        return False
    await async_setup_selected_dashboards(hass, kinds, notify=False)
    return True
