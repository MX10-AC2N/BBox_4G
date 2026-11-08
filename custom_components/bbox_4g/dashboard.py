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
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    CONF_DASHBOARDS,
    DASHBOARD_AUTO,
    DASHBOARD_CHOICES,
    DASHBOARD_MUSHROOM,
    DASHBOARD_NATIVE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

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


def _devices(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Known devices with the entity ids of their own entities.

    A device is kept when its presence sensor is registered; the switches that
    do not exist (e.g. no Wi-Fi switch on a wired device) are simply absent.
    """
    reg = er.async_get(hass)
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
        out.append(
            {
                "mac": mac,
                "name": str(dev.get("name") or mac),
                "presence": presence,
                "internet": _eid(reg, "switch", f"internet_{mac}"),
                "wifi": _eid(reg, "switch", f"wifi_device_{mac}"),
                "link": _eid(reg, "switch", f"device_{mac}"),
            }
        )
    out.sort(key=lambda d: d["name"].lower())
    return out


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
    return [{"condition": "state", "entity": dev["presence"], "state": "on"}]


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


def _sections_view(
    box_cards: list[dict[str, Any]],
    txt: dict[str, str],
    connected: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
) -> dict[str, Any]:
    def heading(title: str, icon: str) -> dict[str, Any]:
        return {"type": "heading", "heading": title, "icon": icon, "heading_style": "title"}

    sections = [
        {"type": "grid", "cards": [heading(txt["box"], "mdi:router-wireless"), *box_cards]},
        {
            "type": "grid",
            "cards": [heading(txt["connected"], "mdi:lan-connect"), *connected],
        },
        {
            "type": "grid",
            "cards": [heading(txt["blocked"], "mdi:web-off"), *blocked],
        },
    ]
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
        "title": dev["name"],
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
    return _sections_view(box_cards, txt, connected, blocked)


# ---------------------------------------------------------------------------
# mushroom : header + switch chips per device
# ---------------------------------------------------------------------------


def _mushroom_device_card(txt: dict[str, str], dev: dict[str, Any]) -> dict[str, Any]:
    """Header (name, IP, link type) + one chip per switch of the device."""
    ref = dev["internet"] or dev["presence"]
    pres = dev["presence"]
    link = f"(state_attr('{ref}', 'lien') | string | lower)"
    wifi = f"('wi' in {link})"
    header = {
        "type": "custom:mushroom-template-card",
        "entity": pres,
        "primary": f"{{{{ state_attr('{ref}', 'nom') or '{dev['mac']}' }}}}",
        "secondary": (
            f"{{% if is_state('{pres}', 'on') %}}"
            f"{{{{ state_attr('{ref}', 'ip') or '' }}}} · "
            f"{{{{ state_attr('{ref}', 'ssid') or ('{txt['wifi']}' if {wifi} else '{txt['wired']}') }}}}"
            f"{{% else %}}{txt['offline']}{{% endif %}}"
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
    return {"type": "vertical-stack", "cards": cards}


def _config_mushroom(hass: HomeAssistant) -> dict[str, Any]:
    txt = _t(hass)
    box = _box_entities(hass)
    devices = _devices(hass)

    chips: list[dict[str, Any]] = []
    if box["clients"]:
        chips.append(
            {
                "type": "entity",
                "entity": box["clients"],
                "icon": "mdi:lan-connect",
                "icon_color": "green",
                "content_info": "state",
            }
        )
    for key, icon in (("wifi", "mdi:wifi"), ("wifi24", "mdi:wifi"), ("wifi5", "mdi:wifi-star")):
        if box[key]:
            chips.append({"type": "entity", "entity": box[key], "icon": icon})
    if box["guest"]:
        chips.append(
            {
                "type": "entity",
                "entity": box["guest"],
                "icon": "mdi:account-network",
                "content_info": "state",
            }
        )
    for key in ("refresh", "reconnect"):
        if box[key]:
            chips.append({"type": "entity", "entity": box[key]})

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
    return _sections_view(box_cards, txt, connected, blocked)


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
