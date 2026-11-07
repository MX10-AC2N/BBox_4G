"""Intégration « Bbox » pour les box Bouygues (Sagemcom et Huawei CPE).

L'intégration gère :

* la connexion à la box (le mot de passe est saisi dans l'interface de
  Home Assistant et conservé par Home Assistant, jamais dans un YAML) ;
* la reconnexion automatique quand la session de la box expire ;
* la demande d'un nouveau mot de passe quand les identifiants sont refusés ;
* les capteurs, la présence par appareil et les interrupteurs Wi-Fi / blocage.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv

from .bbox_api import BboxAuthError, BboxError, detect_client, wake_on_lan
from .const import (
    ATTR_MAC,
    CONF_BLOCK_SCOPE,
    CONF_FAMILY,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_USE_TLS,
    CONF_VERIFY_SSL,
    DEFAULT_BLOCK_SCOPE,
    DEFAULT_FAMILY,
    DEFAULT_USERNAME,
    DOMAIN,
    PLATFORMS,
    SERVICE_BLOCK_DEVICE,
    SERVICE_REFRESH,
    SERVICE_SET_WIFI,
    SERVICE_SET_INTERNET,
    SERVICE_SET_WIFI_ACCESS,
    SERVICE_UNBLOCK_DEVICE,
    SERVICE_WAKE_DEVICE,
)
from .coordinator import BboxCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

BLOCK_SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_MAC): cv.string})
SET_WIFI_SCHEMA = vol.Schema(
    {
        vol.Required("band"): vol.In(("all", "24", "5")),
        vol.Required("enabled"): cv.boolean,
    }
)
MAC_SCHEMA = vol.Schema({vol.Required(ATTR_MAC): cv.string})
ACCESS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MAC): cv.string,
        vol.Required("allowed"): cv.boolean,
    }
)


def _build_client(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    """Construit le client API à partir de la configuration."""
    data = {**entry.data, **entry.options}
    session_dir = hass.config.path(DOMAIN)
    try:
        os.makedirs(session_dir, exist_ok=True)
        session_file = os.path.join(session_dir, "session.json")
    except OSError:  # pragma: no cover - système de fichiers en lecture seule
        session_file = None
    return detect_client(
        host=data[CONF_HOST],
        password=data[CONF_PASSWORD],
        username=data.get(CONF_USERNAME, DEFAULT_USERNAME),
        family=data.get(CONF_FAMILY, DEFAULT_FAMILY),
        use_tls=data.get(CONF_USE_TLS) or None,
        verify=bool(data.get(CONF_VERIFY_SSL, False)),
        timeout=15,
        session_file=session_file,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Met en place l'intégration à partir d'une entrée de configuration."""
    hass.data.setdefault(DOMAIN, {})
    try:
        client = await hass.async_add_executor_job(_build_client, hass, entry)
    except BboxAuthError as err:
        # Identifiants refusés : Home Assistant va proposer de ressaisir
        # le mot de passe.
        _LOGGER.error("Credentials rejected by the router: %s", err)
        raise ConfigEntryAuthFailed(str(err)) from err
    except BboxError as err:
        # Box momentanément injoignable : on demande un nouvel essai plus
        # tard plutôt que de laisser l'intégration en échec.
        _LOGGER.error("Router unreachable (%s): %s", entry.data.get(CONF_HOST), err)
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = BboxCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator, "client": client}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Décharge l'intégration."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Recharge l'intégration après un changement d'options."""
    await hass.config_entries.async_reload(entry.entry_id)


# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------


def _coordinators(hass: HomeAssistant) -> list[BboxCoordinator]:
    """Coordonnateurs de toutes les boxes configurées."""
    entries = hass.data.get(DOMAIN, {})
    return [data["coordinator"] for data in entries.values()]


def _scope_of(coordinator: BboxCoordinator) -> str:
    """Portée du blocage configurée pour cette box."""
    options = getattr(coordinator.entry, "options", {}) or {}
    return options.get(CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE)


def _async_register_services(hass: HomeAssistant) -> None:
    """Enregistre les services personnalisés (une seule fois)."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _refresh(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass):
            await coordinator.async_request_refresh()

    async def _sur_toutes(call: ServiceCall, action) -> None:
        """Applique une action à toutes les boxes configurées.

        Les erreurs sont converties en ``HomeAssistantError`` : une
        exception brute remonterait dans les journaux de Home Assistant
        sans être rattachée au service appelé.
        """
        coordinators = _coordinators(hass)
        if not coordinators:
            raise HomeAssistantError("No Bbox router configured.")
        erreurs: list[str] = []
        for coordinator in coordinators:
            try:
                await hass.async_add_executor_job(action, coordinator)
            except BboxAuthError as err:
                raise HomeAssistantError(
                    f"Password rejected by the router: {err}"
                ) from err
            except BboxError as err:
                erreurs.append(f"{coordinator.client.host} : {err}")
                continue
            await coordinator.async_request_refresh()
        if erreurs:
            raise HomeAssistantError("; ".join(erreurs))

    def _bloquer(coordinator: BboxCoordinator, mac: str, bloque: bool, portee: str):
        """Bloquent (ou libèrent) un appareil sur une box."""
        coordinator.client.set_device_blocked(mac, bloque, portee)

    async def _block(call: ServiceCall) -> None:
        mac = call.data[ATTR_MAC]
        await _sur_toutes(
            call,
            lambda coord: _bloquer(coord, mac, True, _scope_of(coord)),
        )

    async def _unblock(call: ServiceCall) -> None:
        mac = call.data[ATTR_MAC]
        await _sur_toutes(
            call,
            lambda coord: _bloquer(coord, mac, False, _scope_of(coord)),
        )

    async def _set_wifi(call: ServiceCall) -> None:
        band = call.data["band"]
        enabled = call.data["enabled"]
        await _sur_toutes(
            call,
            lambda coord: coord.client.set_wifi(band, enabled),
        )

    async def _wake(call: ServiceCall) -> None:
        try:
            await hass.async_add_executor_job(wake_on_lan, call.data[ATTR_MAC])
        except BboxError as err:
            raise HomeAssistantError(f"Wake-on-LAN failed: {err}") from err

    async def _set_internet(call: ServiceCall) -> None:
        mac = call.data[ATTR_MAC]
        bloque = not call.data["allowed"]
        await _sur_toutes(
            call, lambda coord: _bloquer(coord, mac, bloque, "lan")
        )

    async def _set_wifi_access(call: ServiceCall) -> None:
        mac = call.data[ATTR_MAC]
        bloque = not call.data["allowed"]
        await _sur_toutes(
            call, lambda coord: _bloquer(coord, mac, bloque, "wifi")
        )

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh)
    hass.services.async_register(DOMAIN, SERVICE_WAKE_DEVICE, _wake)
    hass.services.async_register(DOMAIN, SERVICE_SET_INTERNET, _set_internet)
    hass.services.async_register(DOMAIN, SERVICE_SET_WIFI_ACCESS, _set_wifi_access)
    hass.services.async_register(DOMAIN, SERVICE_BLOCK_DEVICE, _block)
    hass.services.async_register(DOMAIN, SERVICE_UNBLOCK_DEVICE, _unblock)
    hass.services.async_register(DOMAIN, SERVICE_SET_WIFI, _set_wifi)
