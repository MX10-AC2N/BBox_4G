"""Capteurs binaires de présence (un par appareil connu)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ACTIVE,
    ATTR_IP,
    ATTR_LINK,
    ATTR_MAC,
    DOMAIN,
)
from .coordinator import BboxCoordinator
from .devices import async_purge_disparus, client_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Crée un capteur de présence par appareil, et les met à jour."""
    coordinator: BboxCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: dict[str, BboxPresenceSensor] = {}

    @callback
    def _sync() -> None:
        """Crée les entités manquantes à chaque évolution de la liste."""
        # Les appareils disparus de la box voient leurs entités supprimées.
        async_purge_disparus(hass, entities, coordinator.vanished)
        new_entities: list[BboxPresenceSensor] = []
        for device in coordinator.devices:
            mac = device.get("mac", "")
            if not mac or mac in entities:
                continue
            entity = BboxPresenceSensor(
                coordinator, entry, mac, device.get("name", mac), device
            )
            entities[mac] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_sync))
    _sync()


class BboxPresenceSensor(CoordinatorEntity, BinarySensorEntity):
    """Présence d'un appareil sur le réseau de la box.

    L'entité est rattachée à l'appareil lui-même : dans l'interface de
    Home Assistant, elle apparaît sous « Téléphone Ju », « NAS »…
    L'``entity_id`` en découle (``binary_sensor.telephone_ju_presence``).
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: BboxCoordinator,
        entry: ConfigEntry,
        mac: str,
        name: str,
        device: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._mac = mac.lower()
        self._name = name or mac
        self._attr_unique_id = f"presence_{self._mac}"
        self._attr_name = "Présence"
        self._attr_device_info = client_device_info(
            entry,
            {"mac": self._mac, "name": self._name, **(device or {})},
            coordinator.data,
        )

    @property
    def device(self) -> dict[str, Any]:
        """Données courantes de l'appareil."""
        return self.coordinator.device_by_mac(self._mac) or {}

    @property
    def is_on(self) -> bool | None:
        """L'appareil est-il connecté ?"""
        device = self.device
        if not device:
            return None
        return bool(device.get(ATTR_ACTIVE))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Détails de l'appareil."""
        device = self.device
        return {
            ATTR_MAC: self._mac,
            "nom": device.get("name"),
            ATTR_IP: device.get("ip"),
            ATTR_LINK: device.get("link"),
            ATTR_ACTIVE: device.get(ATTR_ACTIVE),
            "bloque": device.get("blocked"),
            "bloque_reseau": device.get("blocked_lan"),
            "bloque_wifi": device.get("blocked_wifi"),
        }
