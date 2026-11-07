"""Génération et enregistrement des tableaux de bord Lovelace.

Deux modes :

1. **Storage** (recommandé) — le dashboard apparaît directement dans la
   barre latérale, sans toucher à ``configuration.yaml``.
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

# Fichiers YAML (secours)
YAML_FILES = {
    DASHBOARD_NATIVE: "bbox.yaml",
    DASHBOARD_AUTO: "bbox-auto.yaml",
    DASHBOARD_MUSHROOM: "bbox-mushroom.yaml",
}

# url_path des dashboards storage
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
    "switch.bbox_wifi",
    "switch.bbox_wifi_24_ghz",
    "switch.bbox_wifi_5_ghz",
    "sensor.bbox_clients_connectes",
    "sensor.bbox_appareils_connus",
    "sensor.bbox_reseau_invite",
    "button.bbox_actualiser",
    "button.bbox_reconnecter",
]


# ---------------------------------------------------------------------------
# Contenu des vues (dict Python → storage + YAML)
# ---------------------------------------------------------------------------


def _view_native() -> dict[str, Any]:
    """Vue native (aucune carte communautaire)."""
    return {
        "title": "Appareils",
        "path": "bbox",
        "icon": "mdi:router-wireless",
        "cards": [
            {
                "type": "entities",
                "title": "État de la box",
                "show_header_toggle": False,
                "entities": list(ENTITES_BOX),
            },
            {
                "type": "markdown",
                "content": (
                    "### Appareils\n"
                    "Les interrupteurs de chaque appareil apparaissent sous "
                    "l’appareil **Bbox** (ou sous l’appareil indépendant).\n\n"
                    "Pour une liste **dynamique**, utilisez le dashboard "
                    "**Bbox (auto)** ou **Bbox (Mushroom)** "
                    "(cartes HACS *auto-entities* / *Mushroom*)."
                ),
            },
        ],
    }


def _view_auto() -> dict[str, Any]:
    """Vue dynamique auto-entities."""
    return {
        "title": "Appareils",
        "path": "bbox-auto",
        "icon": "mdi:router-wireless",
        "cards": [
            {
                "type": "entities",
                "title": "État de la box",
                "show_header_toggle": False,
                "entities": list(ENTITES_BOX),
            },
            {
                "type": "custom:auto-entities",
                "title": "Appareils connectés",
                "show_empty": True,
                "card": {"type": "entities", "show_header_toggle": False},
                "filter": {
                    "include": [
                        {
                            "entity_id": "binary_sensor.*",
                            "attributes": {"device_class": "connectivity"},
                            "state": "on",
                        },
                        {
                            "entity_id": "switch.*",
                            "attributes": {
                                "appareil": "client",
                                "etat_connexion": "connecte",
                            },
                        },
                    ],
                    "exclude": [
                        {"entity_id": "switch.*appareil_independant*"},
                        {"entity_id": "switch.*separate*"},
                    ],
                },
                "sort": {"method": "name"},
            },
            {
                "type": "custom:auto-entities",
                "title": "Appareils déconnectés",
                "show_empty": True,
                "card": {"type": "entities", "show_header_toggle": False},
                "filter": {
                    "include": [
                        {
                            "entity_id": "binary_sensor.*",
                            "attributes": {"device_class": "connectivity"},
                            "state": "off",
                        },
                        {
                            "entity_id": "switch.*",
                            "attributes": {
                                "appareil": "client",
                                "etat_connexion": "deconnecte",
                            },
                        },
                    ],
                    "exclude": [
                        {"entity_id": "switch.*appareil_independant*"},
                        {"entity_id": "switch.*separate*"},
                    ],
                },
                "sort": {"method": "name"},
            },
            {
                "type": "custom:auto-entities",
                "title": "Accès Internet coupé",
                "show_empty": True,
                "card": {"type": "entities", "show_header_toggle": False},
                "filter": {
                    "include": [
                        {
                            "entity_id": "switch.*",
                            "attributes": {
                                "appareil": "client",
                                "acces": "coupe",
                            },
                        }
                    ]
                },
                "sort": {"method": "name"},
            },
        ],
    }


def _view_mushroom() -> dict[str, Any]:
    """Vue Mushroom + auto-entities."""
    return {
        "title": "Appareils",
        "path": "bbox-mushroom",
        "icon": "mdi:mushroom",
        "cards": [
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
                "primary": (
                    "{% set n = states('sensor.bbox_clients_connectes') | int(0) %}"
                    "{{ n }} appareil(s) connecté(s)"
                ),
                "secondary": "Bbox Bouygues",
            },
            {
                "type": "heading",
                "heading": "Connectés",
                "icon": "mdi:lan-connect",
            },
            {
                "type": "custom:auto-entities",
                "show_empty": True,
                "card": {"type": "grid", "columns": 1, "square": False},
                "card_param": "cards",
                "filter": {
                    "include": [
                        {
                            "entity_id": "switch.*",
                            "attributes": {
                                "appareil": "client",
                                "etat_connexion": "connecte",
                            },
                        }
                    ],
                    "exclude": [
                        {"entity_id": "switch.*appareil_independant*"},
                        {"entity_id": "switch.*separate*"},
                    ],
                },
                "options": {
                    "type": "custom:mushroom-entity-card",
                    "fill_container": True,
                    "icon_color": "green",
                },
                "sort": {"method": "name"},
            },
            {
                "type": "heading",
                "heading": "Déconnectés",
                "icon": "mdi:lan-disconnect",
            },
            {
                "type": "custom:auto-entities",
                "show_empty": True,
                "card": {"type": "grid", "columns": 1, "square": False},
                "card_param": "cards",
                "filter": {
                    "include": [
                        {
                            "entity_id": "switch.*",
                            "attributes": {
                                "appareil": "client",
                                "etat_connexion": "deconnecte",
                            },
                        }
                    ],
                    "exclude": [
                        {"entity_id": "switch.*appareil_independant*"},
                        {"entity_id": "switch.*separate*"},
                    ],
                },
                "options": {
                    "type": "custom:mushroom-entity-card",
                    "fill_container": True,
                    "icon_color": "grey",
                },
                "sort": {"method": "name"},
            },
            {
                "type": "heading",
                "heading": "Accès Internet coupé",
                "icon": "mdi:web-off",
            },
            {
                "type": "custom:auto-entities",
                "show_empty": True,
                "card": {"type": "grid", "columns": 1, "square": False},
                "card_param": "cards",
                "filter": {
                    "include": [
                        {
                            "entity_id": "switch.*",
                            "attributes": {
                                "appareil": "client",
                                "acces": "coupe",
                            },
                        }
                    ]
                },
                "options": {
                    "type": "custom:mushroom-entity-card",
                    "fill_container": True,
                    "icon_color": "red",
                },
                "sort": {"method": "name"},
            },
        ],
    }


def _config_for(kind: str) -> dict[str, Any]:
    """Config Lovelace complète pour un type de dashboard."""
    views = {
        DASHBOARD_NATIVE: [_view_native()],
        DASHBOARD_AUTO: [_view_auto()],
        DASHBOARD_MUSHROOM: [_view_mushroom()],
    }
    return {"views": views.get(kind, [_view_native()])}


# ---------------------------------------------------------------------------
# Storage (dashboard visible dans la barre latérale)
# ---------------------------------------------------------------------------


async def async_create_storage_dashboard(
    hass: HomeAssistant, kind: str
) -> bool:
    """Crée (ou met à jour) un dashboard en mode storage.

    Retourne True si le dashboard a été créé / mis à jour.
    """
    url_path = STORAGE_PATHS.get(kind)
    if not url_path:
        return False

    title = STORAGE_TITLES[kind]
    icon = STORAGE_ICONS[kind]
    config = _config_for(kind)

    # 1) Enregistrement dans la liste des dashboards
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

    # 2) Contenu du dashboard
    config_store: Store = Store(hass, 1, f"lovelace.{url_path}")
    await config_store.async_save(config)

    _LOGGER.info("Dashboard storage « %s » (%s) créé / mis à jour", title, url_path)
    return True


async def async_remove_storage_dashboard(
    hass: HomeAssistant, kind: str
) -> None:
    """Retire un dashboard storage (optionnel)."""
    url_path = STORAGE_PATHS.get(kind)
    if not url_path:
        return
    dashboards_store: Store = Store(hass, 1, "lovelace_dashboards")
    data = await dashboards_store.async_load() or {}
    items = [i for i in (data.get("items") or []) if i.get("url_path") != url_path]
    data["items"] = items
    await dashboards_store.async_save(data)


# ---------------------------------------------------------------------------
# Fichiers YAML (secours)
# ---------------------------------------------------------------------------


def _yaml_dump_view(config: dict[str, Any]) -> str:
    """Sérialisation YAML minimale (sans dépendance PyYAML externe)."""
    # On s'appuie sur une représentation simple pour les structures fixes.
    import json

    # Home Assistant accepte aussi le JSON dans certains contextes ;
    # pour un vrai YAML lisible on génère à la main les parties critiques.
    # Ici on produit un YAML via une astuce stable : json + indents commentés.
    # Préférer laisser le storage mode comme chemin principal.
    lines = [
        "# Generated by Bbox Bouygues integration",
        "# Prefer the storage dashboard (sidebar). This file is a fallback.",
        f"# Path in configuration.yaml: lovelace/dashboards → filename: lovelace/{YAML_FILES.get('native', 'bbox.yaml')}",
        "",
        "title: Bbox",
        "views:",
    ]
    # Dump structure via JSON then note - for reliability use storage primarily.
    # Provide a compact readable YAML for the native view only as fallback text.
    for view in config.get("views", []):
        lines.append(f"  - title: {view.get('title', 'Appareils')}")
        lines.append(f"    path: {view.get('path', 'bbox')}")
        lines.append(f"    icon: {view.get('icon', 'mdi:router-wireless')}")
        lines.append("    cards:")
        for card in view.get("cards", []):
            ctype = card.get("type", "entities")
            lines.append(f"      - type: {ctype}")
            if "title" in card:
                lines.append(f"        title: \"{card['title']}\"")
            if ctype == "entities" and "entities" in card:
                lines.append("        show_header_toggle: false")
                lines.append("        entities:")
                for ent in card["entities"]:
                    lines.append(f"          - {ent}")
            elif ctype == "markdown" and "content" in card:
                content = str(card["content"]).replace("\n", "\\n")
                lines.append(f"        content: \"{content}\"")
            else:
                # Cartes custom : on indique le JSON pour copie manuelle
                lines.append(
                    "        # custom card — use the storage dashboard for full support"
                )
                lines.append(
                    f"        # {json.dumps(card, ensure_ascii=False)[:120]}…"
                )
    return "\n".join(lines) + "\n"


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


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


async def async_setup_selected_dashboards(
    hass: HomeAssistant,
    selected: list[str],
    *,
    notify: bool = True,
) -> list[str]:
    """Crée les dashboards demandés (storage + YAML de secours).

    Retourne la liste des url_path créés.
    """
    created: list[str] = []
    for kind in selected:
        if kind not in STORAGE_PATHS:
            continue
        try:
            ok = await async_create_storage_dashboard(hass, kind)
            if ok:
                created.append(STORAGE_PATHS[kind])
        except Exception as err:  # pragma: no cover
            _LOGGER.exception("Failed to create storage dashboard %s: %s", kind, err)

        # YAML de secours
        yaml_name = YAML_FILES.get(kind)
        if yaml_name:
            path = hass.config.path("lovelace", yaml_name)
            content = _yaml_dump_view(_config_for(kind))
            await hass.async_add_executor_job(_write_yaml_file, path, content)

    if notify and created:
        paths = ", ".join(f"`/{p}`" for p in created)
        hass.components.persistent_notification.async_create(
            (
                f"**Bbox dashboards ready**\n\n"
                f"Created / updated: {paths}\n\n"
                f"They appear in the sidebar after a **refresh of the page** "
                f"(or a restart of Home Assistant).\n\n"
                f"- **auto** / **mushroom** require the HACS cards "
                f"*auto-entities* and/or *Mushroom*.\n"
                f"- You can change the selection anytime in "
                f"*Configure integration → Dashboards*."
            ),
            title="Bbox Bouygues",
            notification_id=f"{DOMAIN}_dashboards",
        )

    return created


async def async_write_dashboard(
    hass: HomeAssistant, devices: list[dict[str, Any]]
) -> bool:
    """Compatibilité avec l’ancien appel du coordinator.

    Ne régénère les dashboards que si des types sont sélectionnés dans les
    options de **n’importe quelle** entrée Bbox.
    """
    # Le coordinator appelle encore cette fonction : on ne fait rien de
    # dynamique ici (les dashboards storage ne dépendent plus de la liste
    # d’appareils). On retourne False pour signaler « pas d’écriture ».
    return False
