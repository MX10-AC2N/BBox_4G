"""Capteurs de l'intégration « Bbox »."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_FIRMWARE,
    ATTR_HOST,
    ATTR_LAST_UPDATE,
    ATTR_MODEL,
    DOMAIN,
)
from .coordinator import BboxCoordinator
from .parental_entities import build as build_parental, manager_of
from .devices import box_device_info

__all__ = ["box_device_info"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Crée les capteurs."""
    coordinator: BboxCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[SensorEntity] = [
        BboxClientsSensor(coordinator, entry),
        BboxKnownDevicesSensor(coordinator, entry),
        BboxGuestNetworkSensor(coordinator, entry),
    ]
    manager = manager_of(hass, entry)
    if manager:
        entities += build_parental("sensor", manager)
    async_add_entities(entities)


class _BboxSensor(CoordinatorEntity, SensorEntity):
    """Base commune aux capteurs."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BboxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = box_device_info(entry, coordinator.data)

    @property
    def _state(self) -> dict[str, Any]:
        return self.coordinator.data or {}


class BboxClientsSensor(_BboxSensor):
    """Nombre de clients actuellement connectés à la box."""

    _attr_name = "Clients connectés"
    _attr_unique_id = "clients_connected"
    _attr_icon = "mdi:lan-connect"

    @property
    def native_value(self) -> int | None:
        """Nombre d'appareils connectés."""
        value = self._state.get("nb_connectes")
        return int(value) if isinstance(value, int) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Informations complémentaires."""
        return {
            ATTR_MODEL: self._state.get(ATTR_MODEL),
            ATTR_FIRMWARE: self._state.get(ATTR_FIRMWARE),
            ATTR_HOST: self._state.get(ATTR_HOST),
            "appareils_known": self._state.get("nb_total"),
            ATTR_LAST_UPDATE: self._state.get("timestamp"),
        }


class BboxKnownDevicesSensor(_BboxSensor):
    """Nombre d'appareils connus par la box (connectés ou non)."""

    _attr_name = "Appareils connus"
    _attr_unique_id = "devices_known"
    _attr_icon = "mdi:devices"

    @property
    def native_value(self) -> int | None:
        """Nombre total d'appareils."""
        value = self._state.get("nb_total")
        return int(value) if isinstance(value, int) else None


class BboxGuestNetworkSensor(_BboxSensor):
    """État du réseau invité."""

    _attr_name = "Réseau invité"
    _attr_unique_id = "guest_network"
    _attr_icon = "mdi:wifi"

    @property
    def native_value(self) -> str | None:
        """État du réseau invité."""
        wifi = self._state.get("wifi") or {}
        value = wifi.get("guest")
        if value is None:
            return None
        return "activé" if value else "désactivé"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """État des bandes Wi-Fi."""
        wifi = self._state.get("wifi") or {}
        return {
            "2ghz": wifi.get("24"),
            "5ghz": wifi.get("5"),
            "invite": wifi.get("guest"),
        }
