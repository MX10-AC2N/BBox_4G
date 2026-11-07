"""Tableaux de bord Lovelace — création via l'API Home Assistant.

Les dashboards sont en mode **storage** : visibles dans la barre latérale
et **éditables** depuis l'interface (⋮ → Edit dashboard).

On privilégie l'API interne Lovelace (DashboardsCollection + LovelaceStorage)
plutôt que d'écrire à la main dans ``.storage``, ce qui évite les configs
incomplètes / non enregistrées auprès du frontend.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    DASHBOARD_AUTO,
    DASHBOARD_CHOICES,
    DASHBOARD_MUSHROOM,
    DASHBOARD_NATIVE,
)

_LOGGER = logging.getLogger(__name__)

# url_path DOIT contenir un tiret (règle HA récente pour les nouveaux dashboards)
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


# ---------------------------------------------------------------------------
# Configs Lovelace minimales et valides
# ---------------------------------------------------------------------------


def _box_card() -> dict[str, Any]:
    return {
        "type": "entities",
        "title": "Etat de la box",
        "show_header_toggle": False,
        "entities": list(ENTITES_BOX),
    }


def _auto_card(title: str, attrs: dict[str, str]) -> dict[str, Any]:
    """auto-entities : filtre UNIQUEMENT sur nos attributs custom."""
    return {
        "type": "custom:auto-entities",
        "show_empty": True,
        "card": {
            "type": "entities",
            "title": title,
            "show_header_toggle": False,
        },
        "filter": {
            "include": [{"attributes": dict(attrs)}],
            "exclude": [
                {"entity_id": "*appareil_independant*"},
                {"entity_id": "*separate*"},
            ],
        },
        "sort": {"method": "friendly_name"},
    }


def _config_native() -> dict[str, Any]:
    """Aucune carte custom — doit toujours s'afficher."""
    return {
        "views": [
            {
                "title": "Home",
                "path": "home",
                "type": "masonry",
                "cards": [
                    _box_card(),
                    {
                        "type": "markdown",
                        "content": (
                            "### Clients\n"
                            "Les commandes de chaque appareil sont sous le device "
                            "**Bbox** (mode under_box) ou sous l'appareil independant.\n\n"
                            "Utilisez **Bbox (auto)** ou **Bbox (Mushroom)** pour une "
                            "liste dynamique (cartes HACS requises)."
                        ),
                    },
                ],
            }
        ]
    }


def _config_auto() -> dict[str, Any]:
    return {
        "views": [
            {
                "title": "Home",
                "path": "home",
                "type": "masonry",
                "cards": [
                    _box_card(),
                    _auto_card(
                        "Appareils connectes",
                        {"appareil": "client", "etat_connexion": "connecte"},
                    ),
                    _auto_card(
                        "Appareils deconnectes",
                        {"appareil": "client", "etat_connexion": "deconnecte"},
                    ),
                    _auto_card(
                        "Acces Internet coupe",
                        {"appareil": "client", "acces": "coupe"},
                    ),
                ],
            }
        ]
    }


def _config_mushroom() -> dict[str, Any]:
    """Mushroom + auto-entities — structure simple, sans heading card."""
    def _mush(title: str, attrs: dict[str, str], color: str) -> dict[str, Any]:
        return {
            "type": "custom:auto-entities",
            "show_empty": True,
            "card": {
                "type": "entities",
                "title": title,
                "show_header_toggle": False,
            },
            "filter": {
                "include": [{"attributes": dict(attrs)}],
                "exclude": [
                    {"entity_id": "*appareil_independant*"},
                    {"entity_id": "*separate*"},
                ],
            },
            "options": {
                "type": "custom:mushroom-entity-card",
                "icon_color": color,
            },
            "sort": {"method": "friendly_name"},
        }

    return {
        "views": [
            {
                "title": "Home",
                "path": "home",
                "type": "masonry",
                "cards": [
                    {
                        "type": "custom:mushroom-chips-card",
                        "chips": [
                            {
                                "type": "entity",
                                "entity": "sensor.bbox_clients_connectes",
                            },
                            {"type": "entity", "entity": "switch.bbox_wifi"},
                            {
                                "type": "entity",
                                "entity": "button.bbox_actualiser",
                            },
                        ],
                    },
                    _mush(
                        "Connectes",
                        {"appareil": "client", "etat_connexion": "connecte"},
                        "green",
                    ),
                    _mush(
                        "Deconnectes",
                        {"appareil": "client", "etat_connexion": "deconnecte"},
                        "grey",
                    ),
                    _mush(
                        "Acces coupe",
                        {"appareil": "client", "acces": "coupe"},
                        "red",
                    ),
                ],
            }
        ]
    }


def _config_for(kind: str) -> dict[str, Any]:
    mapping = {
        DASHBOARD_NATIVE: _config_native,
        DASHBOARD_AUTO: _config_auto,
        DASHBOARD_MUSHROOM: _config_mushroom,
    }
    return mapping.get(kind, _config_native)()


# ---------------------------------------------------------------------------
# Creation via API HA
# ---------------------------------------------------------------------------


async def async_create_storage_dashboard(
    hass: HomeAssistant, kind: str
) -> bool:
    """Cree / met a jour un dashboard storage editable dans l'UI."""
    url_path = STORAGE_PATHS.get(kind)
    if not url_path:
        return False

    title = STORAGE_TITLES[kind]
    icon = STORAGE_ICONS[kind]
    config = _config_for(kind)

    # --- Methode 1 : API Lovelace interne (preferee) ---
    try:
        ok = await _create_via_lovelace_api(hass, url_path, title, icon, config)
        if ok:
            _LOGGER.info(
                "Dashboard '%s' (%s) cree via API Lovelace", title, url_path
            )
            return True
    except Exception as err:  # pragma: no cover
        _LOGGER.warning(
            "API Lovelace indisponible pour %s (%s) — fallback Store",
            url_path,
            err,
        )

    # --- Methode 2 : Store direct (fallback) ---
    try:
        ok = await _create_via_store(hass, url_path, title, icon, config)
        if ok:
            _LOGGER.info(
                "Dashboard '%s' (%s) cree via Store (redemarrer HA)",
                title,
                url_path,
            )
            return True
    except Exception:
        _LOGGER.exception("Echec creation dashboard %s", url_path)

    return False


async def _create_via_lovelace_api(
    hass: HomeAssistant,
    url_path: str,
    title: str,
    icon: str,
    config: dict[str, Any],
) -> bool:
    """Utilise DashboardsCollection + LovelaceStorage si disponibles."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        return False

    # 1) Enregistrer le dashboard dans la collection
    #    structure attendue par HA (mode storage)
    dashboards = getattr(lovelace_data, "dashboards", None)
    # dashboards est un dict url_path -> LovelaceConfig
    # La collection d'items est souvent accessible autrement

    # Approche robuste : utiliser le websocket handler en interne
    # via la collection stockee
    collection = getattr(lovelace_data, "dashboards_collection", None)
    if collection is None:
        # Chercher dans hass.data d'autres cles possibles
        for key, val in list(hass.data.items()):
            if type(val).__name__ == "DashboardsCollection":
                collection = val
                break

    if collection is not None:
        items = collection.async_items()
        existing = next(
            (i for i in items if i.get("url_path") == url_path), None
        )
        if existing is None:
            await collection.async_create_item(
                {
                    "url_path": url_path,
                    "title": title,
                    "icon": icon,
                    "show_in_sidebar": True,
                    "require_admin": False,
                    "mode": "storage",
                }
            )
        else:
            await collection.async_update_item(
                existing["id"],
                {
                    "title": title,
                    "icon": icon,
                    "show_in_sidebar": True,
                    "require_admin": False,
                },
            )

    # 2) Sauver la config de la vue
    from homeassistant.components.lovelace.dashboard import LovelaceStorage

    # LovelaceStorage(hass, config_dict) ou (hass, url_path) selon version
    storage_obj = None
    if isinstance(dashboards, dict) and url_path in dashboards:
        storage_obj = dashboards[url_path]
    if storage_obj is None:
        # Instancier et enregistrer
        try:
            storage_obj = LovelaceStorage(hass, url_path)
        except TypeError:
            # Signature (hass, item_dict)
            storage_obj = LovelaceStorage(
                hass,
                {
                    "id": url_path,
                    "url_path": url_path,
                    "title": title,
                    "icon": icon,
                    "show_in_sidebar": True,
                    "require_admin": False,
                    "mode": "storage",
                },
            )
        if isinstance(dashboards, dict):
            dashboards[url_path] = storage_obj

    if hasattr(storage_obj, "async_save"):
        await storage_obj.async_save(config)
        return True

    return False


async def _create_via_store(
    hass: HomeAssistant,
    url_path: str,
    title: str,
    icon: str,
    config: dict[str, Any],
) -> bool:
    """Fallback : ecriture Store + enregistrement panel frontend."""
    from homeassistant.helpers.storage import Store

    # Liste des dashboards
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

    # Contenu
    config_store: Store = Store(hass, 1, f"lovelace.{url_path}")
    await config_store.async_save(config)

    # Enregistrer le panel si possible (sinon visible apres restart)
    try:
        from homeassistant.components.lovelace import _register_panel
        from homeassistant.components.lovelace.const import MODE_STORAGE

        _register_panel(hass, url_path, MODE_STORAGE, entry, True)
    except Exception:  # pragma: no cover
        _LOGGER.debug(
            "Panel %s non enregistre a chaud — restart HA requis", url_path
        )

    return True


async def async_setup_selected_dashboards(
    hass: HomeAssistant,
    selected: list[str],
    *,
    notify: bool = True,
) -> list[str]:
    """Cree les dashboards demandes."""
    created: list[str] = []
    for kind in selected:
        if kind not in DASHBOARD_CHOICES:
            continue
        try:
            if await async_create_storage_dashboard(hass, kind):
                created.append(STORAGE_PATHS[kind])
        except Exception:
            _LOGGER.exception("Dashboard %s : echec", kind)

    if notify and created:
        paths = ", ".join(f"`/{p}`" for p in created)
        hass.components.persistent_notification.async_create(
            (
                f"**Dashboards Bbox prets**\n\n"
                f"Crees / mis a jour : {paths}\n\n"
                f"1. **Redemarrez Home Assistant** une fois\n"
                f"2. Ouvrez le dashboard dans la barre laterale\n"
                f"3. Menu ⋮ → **Edit dashboard** pour le personnaliser\n\n"
                f"**auto** / **mushroom** necessitent les cartes HACS "
                f"*auto-entities* et *Mushroom*.\n"
                f"Commencez par **native** si un dashboard plante."
            ),
            title="Bbox Bouygues",
            notification_id="bbox_4g_dashboards",
        )

    return created


async def async_write_dashboard(
    hass: HomeAssistant, devices: list[dict[str, Any]]
) -> bool:
    """Compat coordinator : no-op."""
    return False
