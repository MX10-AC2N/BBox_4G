"""Boutons : actualiser l'état, rouvrir une session sur la box."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .bbox_api import BboxAuthError, BboxError, wake_on_lan
from .const import ATTR_MAC, DOMAIN
from .coordinator import BboxCoordinator
from .devices import (
    async_purge_disparus,
    box_device_info,
    get_device_info_for_client,
    is_client_separate,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Crée les boutons."""
    coordinator: BboxCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: dict[str, ButtonEntity] = {}

    @callback
    def _sync() -> None:
        """Crée un bouton « Réveiller » par appareil connu."""
        async_purge_disparus(hass, entities, coordinator.vanished)
        new_entities: list[ButtonEntity] = []
        for device in coordinator.devices:
            mac = device.get("mac", "")
            if not mac or mac in entities:
                continue
            nom = device.get("name", mac)
            entities[mac] = BboxWakeButton(coordinator, entry, mac, nom)
            new_entities.append(entities[mac])
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_sync))
    async_add_entities(
        [
            BboxRefreshButton(coordinator, entry),
            BboxReconnectButton(coordinator, entry),
        ]
    )
    _sync()


class _BboxButton(CoordinatorEntity, ButtonEntity):
    """Base commune."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BboxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = box_device_info(entry, coordinator.data)


class BboxWakeButton(_BboxButton):
    """Réveille un appareil par Wake-on-LAN.

    Un « paquet magique » est envoyé en diffusion sur le réseau local :
    l'ordinateur s'allume. La box n'intervient pas.
    """

    _attr_icon = "mdi:power"

    def __init__(
        self,
        coordinator: BboxCoordinator,
        entry: ConfigEntry,
        mac: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._mac = mac.lower()
        self._name = name or mac
        self._attr_unique_id = f"wake_{self._mac}"
        # Nom court si appareil indépendant, sinon « Réveiller – <client> »
        if is_client_separate(entry, self._mac):
            self._attr_name = "Réveiller"
        else:
            self._attr_name = f"Réveiller – {self._name}"
        self._attr_device_info = get_device_info_for_client(
            entry, {"mac": self._mac, "name": self._name}, coordinator.data
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Adresse MAC visée."""
        return {ATTR_MAC: self._mac}

    async def async_press(self) -> None:
        """Envoie le paquet magique."""
        try:
            await self.hass.async_add_executor_job(wake_on_lan, self._mac)
        except BboxError as err:
            raise HomeAssistantError(f"Wake-on-LAN failed: {err}") from err


class BboxRefreshButton(_BboxButton):
    """Force une nouvelle lecture de l'état de la box."""

    _attr_name = "Actualiser"
    _attr_icon = "mdi:refresh"
    _attr_unique_id = "refresh"

    async def async_press(self) -> None:
        """Relit l'état."""
        await self.coordinator.async_request_refresh()


class BboxReconnectButton(_BboxButton):
    """Rouvre une session sur la box.

    Utile quand l'interface web de la box a été ouverte : la box n'accepte
    qu'une seule session d'administration, celle de Home Assistant peut alors
    être fermée.
    """

    _attr_name = "Reconnecter"
    _attr_icon = "mdi:key-chain"
    _attr_unique_id = "reconnect"

    async def async_press(self) -> None:
        """Rouvre une session puis relit l'état."""
        try:
            await self.coordinator.async_reconnect()
        except BboxAuthError as err:
            raise HomeAssistantError(
                f"Password rejected by the router: {err}"
            ) from err
        except BboxError as err:
            raise HomeAssistantError(f"Reconnection failed: {err}") from err
