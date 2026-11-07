"""Suivi des appareils connectés (plate-forme ``device_tracker``).

Chaque appareil connu de la box devient une entité ``ScannerEntity`` : c'est
la méthode recommandée par Home Assistant pour un routeur. Elle expose la
présence (``is_connected``) et la MAC, ce qui permet notamment d'alimenter
les ``person`` et le suivi de zone.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_ACTIVE, ATTR_IP, ATTR_LINK, ATTR_MAC, DOMAIN
from .coordinator import BboxCoordinator
from .devices import async_purge_disparus, get_device_info_for_client, is_client_separate


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Crée une entité de suivi par appareil."""
    coordinator: BboxCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: dict[str, BboxScannerTracker] = {}

    @callback
    def _sync() -> None:
        """Crée les entités manquantes à chaque évolution de la liste."""
        async_purge_disparus(hass, entities, coordinator.vanished)
        new_entities: list[BboxScannerTracker] = []
        for device in coordinator.devices:
            mac = device.get("mac", "")
            if not mac or mac in entities:
                continue
            entity = BboxScannerTracker(
                coordinator, entry, mac, device.get("name", mac), device
            )
            entities[mac] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_sync))
    _sync()


class BboxScannerTracker(CoordinatorEntity, ScannerEntity):
    """Présence d'un appareil suivi par la box.

    Une entité par appareil : l'interface de Home Assistant les range sous
    l'appareil concerné (ou sous la Bbox selon le mode configuré) et la
    présence alimente directement les ``person``.
    """

    _attr_has_entity_name = True
    # Par défaut, Home Assistant désactive les entités de suivi : on veut
    # qu'elles soient visibles.
    _attr_entity_registry_enabled_default = True

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
        self._attr_unique_id = f"tracker_{self._mac}"
        self._attr_mac_address = self._mac
        # Nom court si appareil indépendant, sinon « Suivi – <client> »
        if is_client_separate(entry, self._mac):
            self._attr_name = "Suivi"
        else:
            self._attr_name = f"Suivi – {self._name}"
        self._attr_device_info = get_device_info_for_client(
            entry,
            {"mac": self._mac, "name": self._name, **(device or {})},
            coordinator.data,
        )

    @property
    def source_type(self) -> SourceType:
        """Type de source : routeur."""
        return SourceType.ROUTER

    @property
    def device(self) -> dict[str, Any]:
        """Données courantes de l'appareil."""
        return self.coordinator.device_by_mac(self._mac) or {}

    @property
    def is_connected(self) -> bool:
        """L'appareil est-il connecté à la box ?"""
        return bool(self.device.get(ATTR_ACTIVE))

    @property
    def hostname(self) -> str | None:
        """Nom d'hôte de l'appareil."""
        device = self.device
        return device.get("name") or self._name

    @property
    def ip_address(self) -> str | None:
        """Adresse IP de l'appareil."""
        return self.device.get(ATTR_IP)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Détails de l'appareil."""
        device = self.device
        return {
            ATTR_MAC: self._mac,
            "nom": device.get("name"),
            ATTR_IP: device.get(ATTR_IP),
            ATTR_LINK: device.get(ATTR_LINK),
            ATTR_ACTIVE: device.get(ATTR_ACTIVE),
            "bloque": device.get("blocked"),
        }
