"""Génération et enregistrement des tableaux de bord Lovelace.

Deux modes :

1. **Storage** (recommandé) — le dashboard apparaît dans la barre latérale
   et reste **éditable** depuis l\'interface Home Assistant.
2. **Fichiers YAML** — écrits dans ``/config/lovelace/`` en secours.

Trois variantes : native, auto-entities, Mushroom.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DASHBOARD_AUTO,
    DASHBOARD_MUSHROOM,
    DASHBOARD_NATIVE,
)

_LOGGER = logging.getLogger(__name__)

YAML_FILES = {
    DASHBOARD_NATIVE: "bbox.yaml",
    DASHBOARD_AUTO: "bbox-auto.yaml",
    DASHBOARD_MUSHROOM: "bbox-mushroom.yaml",
}

STORAGE_PATHS = {
    DASHBOARD_NATIVE: "bbox",
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
    DASHBOARD_MUSHROOM: "mdi:mushroom",
}

ENTITES_BOX = [
    "sensor.bbox_clients_connectes",
    "sensor.bbox_appareils_connus",
    "sensor.bbox_reseau_invite",
    "switch.bbox_wifi",
    "switch.bbox_wifi_24_ghz",
    "switch.bbox_wifi_5_ghz",
    "button.bbox_actualiser",
    "button.bbox_reconnecter",
]


def _box_status_card() -> dict[str, Any]:
    return {
        "type": "entities",
        "title": "État de la box",
        "show_header_toggle": False,
        "entities": list(ENTITES_BOX),
    }


def _auto_entities_card(title: str, include: list[dict[str, Any]]) -> dict[str, Any]:
    """Carte auto-entities compatible HA 2024+ / 2026.

    On filtre uniquement sur les attributs posés par l\'intégration
    (``appareil``, ``etat_connexion``, ``acces``), jamais sur
    ``device_class`` (non exposé de façon fiable dans les state attributes).
    """
    return {
        "type": "custom:auto-entities",
        "show_empty": True,
        "card": {
            "type": "entities",
            "title": title,
            "show_header_toggle": False,
        },
        "filter": {
            "include": include,
            "exclude": [
                {"entity_id": "*appareil_independant*"},
                {"entity_id": "*separate*"},
            ],
        },
        "sort": {"method": "name"},
    }


def _view_native() -> dict[str, Any]:
    return {
        "title": "Appareils",
        "path": "home",
        "icon": "mdi:router-wireless",
        "cards": [
            _box_status_card(),
            {
                "type": "markdown",
                "content": (
                    "### Clients\n"
                    "Les interrupteurs de chaque appareil sont sous le device "
                    "**Bbox** (ou sous l\'appareil indépendant).\n\n"
                    "Pour une liste dynamique, utilisez **Bbox (auto)** ou "
                    "**Bbox (Mushroom)**."
                ),
            },
        ],
    }


def _view_auto() -> dict[str, Any]:
    return {
        "title": "Appareils",
        "path": "home",
        "icon": "mdi:router-wireless",
        "cards": [
            _box_status_card(),
            _auto_entities_card(
                "Appareils connectés",
                [
                    {
                        "attributes": {
                            "appareil": "client",
                            "etat_connexion": "connecte",
                        }
                    }
                ],
            ),
            _auto_entities_card(
                "Appareils déconnectés",
                [
                    {
                        "attributes": {
                            "appareil": "client",
                            "etat_connexion": "deconnecte",
                        }
                    }
                ],
            ),
            _auto_entities_card(
                "Accès Internet coupé",
                [
                    {
                        "attributes": {
                            "appareil": "client",
                            "acces": "coupe",
                        }
                    }
                ],
            ),
        ],
    }


def _mushroom_entity_options(color: str) -> dict[str, Any]:
    return {
        "type": "custom:mushroom-entity-card",
        "fill_container": True,
        "icon_color": color,
    }


def _view_mushroom() -> dict[str, Any]:
    def _grid_auto(title: str, include: list[dict[str, Any]], color: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "heading",
                "heading": title,
                "icon": "mdi:lan-connect" if "Connect" in title or "connect" in title.lower() else "mdi:lan",
            },
            {
                "type": "custom:auto-entities",
                "show_empty": True,
                "card": {"type": "grid", "columns": 1, "square": False},
                "card_param": "cards",
                "filter": {
                    "include": include,
                    "exclude": [
                        {"entity_id": "*appareil_independant*"},
                        {"entity_id": "*separate*"},
                    ],
                },
                "options": _mushroom_entity_options(color),
                "sort": {"method": "name"},
            },
        ]

    cards: list[dict[str, Any]] = [
        {
            "type": "custom:mushroom-chips-card",
            "chips": [
                {
                    "type": "entity",
                    "entity": "sensor.bbox_clients_connectes",
                    "icon_color": "blue",
                },
                {
                    "type": "entity",
                    "entity": "switch.bbox_wifi",
                    "icon_color": "blue",
                },
                {
                    "type": "entity",
                    "entity": "switch.bbox_wifi_24_ghz",
                    "icon_color": "blue",
                },
                {
                    "type": "entity",
                    "entity": "switch.bbox_wifi_5_ghz",
                    "icon_color": "blue",
                },
                {
                    "type": "entity",
                    "entity": "button.bbox_actualiser",
                    "icon": "mdi:refresh",
                },
            ],
        },
        {
            "type": "custom:mushroom-template-card",
            "icon": "mdi:router-wireless",
            "icon_color": "blue",
            "primary": "{{ states('sensor.bbox_clients_connectes') }} appareil(s) connecté(s)",
            "secondary": "Bbox Bouygues",
        },
    ]
    cards += _grid_auto(
        "Connectés",
        [{"attributes": {"appareil": "client", "etat_connexion": "connecte"}}],
        "green",
    )
    cards += _grid_auto(
        "Déconnectés",
        [{"attributes": {"appareil": "client", "etat_connexion": "deconnecte"}}],
        "grey",
    )
    cards += _grid_auto(
        "Accès Internet coupé",
        [{"attributes": {"appareil": "client", "acces": "coupe"}}],
        "red",
    )
    return {
        "title": "Appareils",
        "path": "home",
        "icon": "mdi:mushroom",
        "cards": cards,
    }


def _config_for(kind: str) -> dict[str, Any]:
    views = {
        DASHBOARD_NATIVE: [_view_native()],
        DASHBOARD_AUTO: [_view_auto()],
        DASHBOARD_MUSHROOM: [_view_mushroom()],
    }
    return {"views": views.get(kind, [_view_native()])}


async def async_create_storage_dashboard(hass: HomeAssistant, kind: str) -> bool:
    """Crée ou met à jour un dashboard storage (éditable dans l\'UI HA)."""
    url_path = STORAGE_PATHS.get(kind)
    if not url_path:
        return False

    title = STORAGE_TITLES[kind]
    icon = STORAGE_ICONS[kind]
    config = _config_for(kind)

    dashboards_store: Store = Store(hass, 1, "lovelace_dashboards")
    data = await dashboards_store.async_load() or {}
    items: list[dict[str, Any]] = list(data.get("items") or [])

    existing = next((i for i in items if i.get("url_path") == url_path), None)
    entry = {
        "id": url_path,
        "url_path": url_path,
        "title": title,
        "icon": icon,
        "show_in_sidebar": True,
        "require_admin": False,
        "mode": "storage",
    }
    if existing:
        existing.update(entry)
    else:
        items.append(entry)
    data["items"] = items
    await dashboards_store.async_save(data)

    config_store: Store = Store(hass, 1, f"lovelace.{url_path}")
    await config_store.async_save(config)

    _LOGGER.info("Dashboard storage « %s » (%s) créé / mis à jour", title, url_path)
    return True


async def async_remove_storage_dashboard(hass: HomeAssistant, kind: str) -> None:
    url_path = STORAGE_PATHS.get(kind)
    if not url_path:
        return
    dashboards_store: Store = Store(hass, 1, "lovelace_dashboards")
    data = await dashboards_store.async_load() or {}
    items = [i for i in (data.get("items") or []) if i.get("url_path") != url_path]
    data["items"] = items
    await dashboards_store.async_save(data)


def _write_yaml_file(path: str, content: str) -> bool:
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                if handle.read() == content:
                    return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return True
    except OSError as err:
        _LOGGER.warning("Unable to write dashboard file %s: %s", path, err)
        return False


def _yaml_fallback(kind: str) -> str:
    return (
        f"# Fallback YAML for {kind} — prefer the storage dashboard in the sidebar.\\n"
        f"# Recreate via: Developer tools → Services → bbox_4g.create_dashboard\\n"
    )


async def async_setup_selected_dashboards(
    hass: HomeAssistant,
    selected: list[str],
    *,
    notify: bool = True,
) -> list[str]:
    """Crée les dashboards demandés (storage éditable + YAML de secours)."""
    created: list[str] = []
    for kind in selected:
        if kind not in STORAGE_PATHS:
            continue
        try:
            ok = await async_create_storage_dashboard(hass, kind)
            if ok:
                created.append(STORAGE_PATHS[kind])
        except Exception:
            _LOGGER.exception("Failed to create storage dashboard %s", kind)

        yaml_name = YAML_FILES.get(kind)
        if yaml_name:
            path = hass.config.path("lovelace", yaml_name)
            await hass.async_add_executor_job(
                _write_yaml_file, path, _yaml_fallback(kind)
            )

    if notify and created:
        paths = ", ".join(f"`/{p}`" for p in created)
        hass.components.persistent_notification.async_create(
            (
                f"**Bbox dashboards ready**\\n\\n"
                f"Created / updated: {paths}\\n\\n"
                f"1. **Restart Home Assistant** (required once after creation)\\n"
                f"2. Open the dashboard from the sidebar\\n"
                f"3. You can edit it like any other dashboard "
                f"(⋮ → Edit dashboard)\\n\\n"
                f"Variants **auto** / **mushroom** need HACS cards "
                f"*auto-entities* and/or *Mushroom*."
            ),
            title="Bbox Bouygues",
            notification_id="bbox_4g_dashboards",
        )

    return created


async def async_write_dashboard(
    hass: HomeAssistant, devices: list[dict[str, Any]]
) -> bool:
    """Compatibilité coordinator : no-op (dashboards gérés à la demande)."""
    return False
