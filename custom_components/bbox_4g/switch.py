"""Interrupteurs : Wi-Fi (par bande), blocage d'un appareil et mode appareil indépendant."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .bbox_api import BboxAuthError, BboxError
from .const import (
    ATTR_MAC,
    CONF_BLOCK_SCOPE,
    CONF_DEVICE_MODE,
    CONF_SEPARATE_MACS,
    DEFAULT_BLOCK_SCOPE,
    DEFAULT_DEVICE_MODE,
    DOMAIN,
)
from .coordinator import BboxCoordinator
from .parental_entities import build as build_parental, manager_of
from .devices import box_device_info, get_device_info_for_client, is_client_separate

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    """Crée les interrupteurs Wi-Fi et les interrupteurs de blocage."""
    coordinator: BboxCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    # Interrupteur « connexion » d'un appareil, indexé par sa MAC.
    entities: dict[str, SwitchEntity] = {}
    # Interrupteurs « Accès Internet », « Wifi » et « Appareil indépendant »,
    # indexés par « type:mac ».
    variantes: dict[str, SwitchEntity] = {}

    @callback
    def _sync() -> None:
        """Crée les interrupteurs au fil des appareils connus."""
        # Les appareils disparus de la box voient leurs entités supprimées.
        for mac in list(entities):
            if mac not in coordinator.vanished:
                continue
            entite = entities.pop(mac)
            hass.async_create_task(entite.async_remove(force_remove=True))
        for cle in list(variantes):
            if cle.split(":", 1)[1] not in coordinator.vanished:
                continue
            entite = variantes.pop(cle)
            hass.async_create_task(entite.async_remove(force_remove=True))

        new_entities: list[SwitchEntity] = []
        for device in coordinator.devices:
            mac = device.get("mac", "")
            if not mac or mac in entities:
                continue
            nom = device.get("name", mac)
            entities[mac] = BboxDeviceSwitch(coordinator, entry, mac, nom)
            new_entities.append(entities[mac])
            # Un interrupteur « Accès Internet » par appareil (Wi-Fi ou filaire).
            cle = f"internet:{mac}"
            variantes[cle] = BboxInternetSwitch(coordinator, entry, mac, nom)
            new_entities.append(variantes[cle])
            # Un interrupteur « Wi-Fi » pour les appareils Wi-Fi uniquement.
            if _est_wifi(device):
                cle = f"wifi:{mac}"
                variantes[cle] = BboxDeviceWifiSwitch(coordinator, entry, mac, nom)
                new_entities.append(variantes[cle])
            # Interrupteur « Appareil indépendant » (toujours présent).
            cle = f"separate:{mac}"
            variantes[cle] = BboxSeparateDeviceSwitch(coordinator, entry, mac, nom)
            new_entities.append(variantes[cle])
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_sync))
    manager = manager_of(hass, entry)
    async_add_entities(
        [
            BboxWifiSwitch(coordinator, entry, band="all"),
            BboxWifiSwitch(coordinator, entry, band="24"),
            BboxWifiSwitch(coordinator, entry, band="5"),
            *(build_parental("switch", manager) if manager else []),
        ]
    )
    _sync()


class _BboxSwitch(CoordinatorEntity, SwitchEntity):
    """Base commune."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BboxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = box_device_info(entry, coordinator.data)

    async def _async_write(self, func, *args) -> None:
        """Exécute une commande sur la box et rafraîchit l'état."""
        try:
            await self.hass.async_add_executor_job(func, *args)
        except BboxAuthError as err:
            raise HomeAssistantError(
                f"Password rejected by the router: {err}"
            ) from err
        except BboxError as err:
            raise HomeAssistantError(f"Command rejected by the router: {err}") from err
        await self.coordinator.async_request_refresh()


class BboxWifiSwitch(_BboxSwitch):
    """Allume ou éteint le Wi-Fi de la box."""

    _attr_icon = "mdi:wifi"

    def __init__(
        self, coordinator: BboxCoordinator, entry: ConfigEntry, band: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._band = band
        self._attr_unique_id = f"wifi_{band}"
        # « Wifi » sans trait d'union : Home Assistant transforme le
        # trait d'union en « _ » dans l'entity_id (« wi_fi »), ce qui
        # rendrait les identifiants illisibles.
        if band == "all":
            self._attr_name = "Wifi"
        else:
            self._attr_name = f"Wifi {band} GHz"

    @property
    def _wifi(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("wifi") or {}

    @property
    def is_on(self) -> bool | None:
        """État du Wi-Fi (inconnu si la box ne répond pas)."""
        wifi = self._wifi
        if self._band == "all":
            values = [v for v in (wifi.get("24"), wifi.get("5")) if v is not None]
            if not values:
                return None
            return any(values)
        value = wifi.get(self._band)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Allume le Wi-Fi."""
        await self._async_write(self.coordinator.client.set_wifi, self._band, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Éteint le Wi-Fi."""
        await self._async_write(self.coordinator.client.set_wifi, self._band, False)


def _est_wifi(device: dict[str, Any]) -> bool:
    """L'appareil est-il rattaché à un réseau Wi-Fi ?"""
    if device.get("ssid"):
        return True
    lien = str(device.get("link") or "").lower()
    return "wifi" in lien or "wireless" in lien


class BboxInternetSwitch(_BboxSwitch):
    """Interrupteur « Accès Internet » d'un appareil.

    Contrairement au commutateur de l'interface web du routeur, celui-ci
    fonctionne aussi pour les appareils **filaires** : il passe par le
    filtre MAC réseau, qui laisse à l'appareil son adresse IP et son accès
    au LAN mais coupe sa sortie Internet.

    * ``on``  : l'appareil atteint Internet ;
    * ``off`` : sa sortie Internet est coupée.

    L'appareil reste sur le réseau : seule sa sortie Internet est coupée.
    """

    _attr_icon = "mdi:web-off"

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
        self._attr_unique_id = f"internet_{self._mac}"
        # Nom court si appareil indépendant, sinon « Accès Internet – <client> »
        if is_client_separate(entry, self._mac):
            self._attr_name = "Accès Internet"
        else:
            self._attr_name = f"Accès Internet – {self._name}"
        self._attr_device_info = get_device_info_for_client(
            entry, {"mac": self._mac, "name": self._name}, coordinator.data
        )

    @property
    def is_on(self) -> bool | None:
        """L'appareil a-t-il encore accès à Internet ?"""
        device = self.coordinator.device_by_mac(self._mac)
        if not device:
            return None
        value = device.get("acces_internet")
        if value is None:
            value = device.get("internet_allowed")
        return None if value is None else bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Détails de l'appareil."""
        device = self.coordinator.device_by_mac(self._mac) or {}
        return {
            ATTR_MAC: self._mac,
            "appareil": "client",
            "nom": device.get("name"),
            "ip": device.get("ip"),
            "lien": device.get("link"),
            "ssid": device.get("ssid"),
            "connecte": bool(device.get("active")),
            "etat_connexion": (
                "connecte" if device.get("active") else "deconnecte"
            ),
            "acces_internet": device.get("acces_internet"),
            "acces": (
                "autorise" if device.get("acces_internet") is not False else "coupe"
            ),
            "portee": "internet",
        }

    @property
    def icon(self) -> str:
        """Icône adaptée à l'état."""
        return "mdi:web" if self.is_on else "mdi:web-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Rétablit l'accès à Internet."""
        await self._async_write(
            self.coordinator.client.set_device_blocked, self._mac, False, "lan"
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Coupe l'accès à Internet (l'appareil reste joignable sur le LAN)."""
        await self._async_write(
            self.coordinator.client.set_device_blocked, self._mac, True, "lan"
        )


class BboxDeviceWifiSwitch(_BboxSwitch):
    """Interrupteur « Wi-Fi » d'un appareil.

    Il joue exactement le rôle du commutateur du routeur : l'appareil est
    ajouté (ou retiré) de la **liste noire** du SSID auquel il est
    rattaché. Il est donc déconnecté du Wi-Fi, et perd par la même occasion
    son accès à Internet.

    Seuls les appareils Wi-Fi ont cet interrupteur.
    """

    _attr_icon = "mdi:wifi"

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
        self._attr_unique_id = f"wifi_device_{self._mac}"
        # Nom court si appareil indépendant, sinon « Wifi – <client> »
        if is_client_separate(entry, self._mac):
            self._attr_name = "Wifi"
        else:
            self._attr_name = f"Wifi – {self._name}"
        self._attr_device_info = get_device_info_for_client(
            entry, {"mac": self._mac, "name": self._name}, coordinator.data
        )

    @property
    def is_on(self) -> bool | None:
        """L'appareil est-il autorisé à se connecter au Wi-Fi ?"""
        device = self.coordinator.device_by_mac(self._mac)
        if not device:
            return None
        value = device.get("acces_wifi")
        if value is None:
            return None if device.get("blocked_wifi") is None else not device.get(
                "blocked_wifi"
            )
        return bool(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Détails de l'appareil."""
        device = self.coordinator.device_by_mac(self._mac) or {}
        return {
            ATTR_MAC: self._mac,
            "appareil": "client",
            "nom": device.get("name"),
            "ip": device.get("ip"),
            "ssid": device.get("ssid"),
            "lien": device.get("link"),
            "connecte": bool(device.get("active")),
            "etat_connexion": (
                "connecte" if device.get("active") else "deconnecte"
            ),
            "bloque_wifi": bool(device.get("blocked_wifi")),
            "acces_wifi": device.get("acces_wifi"),
            "acces": "autorise" if device.get("acces_wifi") is not False else "coupe",
            "portee": "wifi",
        }

    @property
    def icon(self) -> str:
        """Icône adaptée à l'état."""
        return "mdi:wifi" if self.is_on else "mdi:wifi-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Réautorise l'appareil à se connecter au Wi-Fi."""
        await self._async_write(
            self.coordinator.client.set_device_blocked, self._mac, False, "wifi"
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Déconnecte l'appareil du Wi-Fi."""
        await self._async_write(
            self.coordinator.client.set_device_blocked, self._mac, True, "wifi"
        )


class BboxDeviceSwitch(_BboxSwitch):
    """Interrupteur « connexion » d'un appareil.

    L'état de l'interrupteur dit si l'appareil est **connecté** :

    * ``on``  : l'appareil est sur le réseau ;
    * ``off`` : l'appareil est déconnecté (endormi ou bloqué).

    Éteindre l'interrupteur **déconnecte** l'appareil (il est bloqué sur la
    box) ; le rallumer le libère. C'est ce qui permet d'avoir deux cartes
    dans le tableau de bord — « connectés » et « déconnectés » — qui
    pilotent le même interrupteur.

    La portée du blocage suit l'option « Portée du blocage » de
    l'intégration : réseau entier (``lan``), Wi-Fi seul (``wifi``) ou les
    deux (``both``).
    """

    _attr_icon = "mdi:account-cancel"

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
        self._attr_unique_id = f"device_{self._mac}"
        # Nom court si appareil indépendant, sinon « Connexion – <client> »
        if is_client_separate(entry, self._mac):
            self._attr_name = "Connexion"
        else:
            self._attr_name = f"Connexion – {self._name}"
        self._attr_device_info = get_device_info_for_client(
            entry, {"mac": self._mac, "name": self._name}, coordinator.data
        )

    @property
    def is_on(self) -> bool | None:
        """L'appareil est-il autorisé ?

        L'état suit **la portée choisie** dans les options :
        - si la portée est « lan » / « internet » → on regarde le filtre MAC
        - si la portée est « wifi » → on regarde la liste noire Wi-Fi
        - sinon on regarde le flag générique ``blocked``.
        """
        device = self.coordinator.device_by_mac(self._mac)
        if not device:
            return None
        options = getattr(self._entry, "options", {}) or {}
        scope = options.get(CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE)
        if scope in ("lan", "internet"):
            blocked = device.get("blocked_lan")
            if blocked is None:
                blocked = device.get("blocked")
        elif scope == "wifi":
            blocked = device.get("blocked_wifi")
            if blocked is None:
                blocked = device.get("blocked")
        else:
            blocked = device.get("blocked")
        if blocked is None:
            # Pas d'info de blocage : on se base sur la présence.
            return bool(device.get("active"))
        return not bool(blocked)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Détails de l'appareil."""
        device = self.coordinator.device_by_mac(self._mac) or {}
        options = getattr(self._entry, "options", {}) or {}
        return {
            ATTR_MAC: self._mac,
            "appareil": "client",
            "nom": device.get("name"),
            "ip": device.get("ip"),
            "lien": device.get("link"),
            "ssid": device.get("ssid"),
            "connecte": bool(device.get("active")),
            "bloque": bool(device.get("blocked")),
            "bloque_reseau": device.get("blocked_lan"),
            "bloque_wifi": device.get("blocked_wifi"),
            "portee": options.get(CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE),
        }

    @property
    def icon(self) -> str:
        """Icône adaptée à l'état."""
        return "mdi:lan-connect" if self.is_on else "mdi:lan-disconnect"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Libère l'appareil (supprime le blocage)."""
        options = getattr(self._entry, "options", {}) or {}
        scope = options.get(CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE)
        await self._async_write(
            self.coordinator.client.set_device_blocked, self._mac, False, scope
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Bloque l'appareil selon la portée configurée."""
        options = getattr(self._entry, "options", {}) or {}
        scope = options.get(CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE)
        await self._async_write(
            self.coordinator.client.set_device_blocked, self._mac, True, scope
        )


class BboxSeparateDeviceSwitch(_BboxSwitch):
    """Interrupteur « Appareil indépendant ».

    * ``on``  : l'appareil obtient son propre device Home Assistant.
    * ``off`` : les entités de l'appareil restent rattachées à la Bbox.

    Le changement nécessite un rechargement de l'intégration (le DeviceInfo
    est fixé à la création des entités).
    """

    _attr_icon = "mdi:devices"
    _attr_entity_category = None  # visible par défaut

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
        self._attr_unique_id = f"separate_{self._mac}"
        # Nom court si appareil indépendant, sinon « Appareil indépendant – <client> »
        if is_client_separate(entry, self._mac):
            self._attr_name = "Appareil indépendant"
        else:
            self._attr_name = f"Appareil indépendant – {self._name}"
        # Ce switch est toujours rattaché à la Bbox (ou au client selon le mode)
        self._attr_device_info = get_device_info_for_client(
            entry, {"mac": self._mac, "name": self._name}, coordinator.data
        )

    @property
    def is_on(self) -> bool:
        """L'appareil est-il actuellement en mode indépendant ?"""
        return is_client_separate(self._entry, self._mac)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Informations utiles."""
        options = getattr(self._entry, "options", {}) or {}
        return {
            ATTR_MAC: self._mac,
            "mode_global": options.get(CONF_DEVICE_MODE, DEFAULT_DEVICE_MODE),
            "dans_exceptions": self._mac
            in {m.lower() for m in options.get(CONF_SEPARATE_MACS, [])},
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Passe l'appareil en mode indépendant et recharge l'intégration."""
        await self._set_separate(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Repasse l'appareil sous la Bbox et recharge l'intégration."""
        await self._set_separate(False)

    async def _set_separate(self, separate: bool) -> None:
        """Met à jour la liste ``separate_macs`` puis recharge l'intégration."""
        options = dict(getattr(self._entry, "options", {}) or {})
        current = {m.lower() for m in options.get(CONF_SEPARATE_MACS, [])}

        if separate:
            current.add(self._mac)
        else:
            current.discard(self._mac)

        options[CONF_SEPARATE_MACS] = sorted(current)

        # On conserve les autres options.
        options.setdefault(
            CONF_DEVICE_MODE, options.get(CONF_DEVICE_MODE, DEFAULT_DEVICE_MODE)
        )

        self.hass.config_entries.async_update_entry(self._entry, options=options)
        # Le DeviceInfo est fixé à la création : il faut recharger.
        await self.hass.config_entries.async_reload(self._entry.entry_id)
