"""Entities of the parental control (one set per profile)."""

from __future__ import annotations

from datetime import time
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .dashboard import _MAC_NAME_RE
from .parental import (
    DISABLED,
    FREE,
    OVERRIDE,
    OVERRIDE_MINUTES,
    QUOTA,
    QUOTA_KEYS,
    QUOTA_MAX,
    SCHEDULE,
    TIME_KEYS,
    ParentalManager,
    parse_hhmm,
)

TIME_NAMES = {
    "wk_start": "Coupure semaine – début",
    "wk_end": "Coupure semaine – fin",
    "we_start": "Coupure week-end – début",
    "we_end": "Coupure week-end – fin",
}
QUOTA_NAMES = {
    "wk_quota": "Quota semaine",
    "we_quota": "Quota week-end",
}


def manager_of(hass: HomeAssistant, entry: ConfigEntry) -> ParentalManager | None:
    return (hass.data.get(DOMAIN, {}).get(entry.entry_id) or {}).get("parental")


class _ParentalEntity:
    """Shared behaviour: attached to the profile device, refreshed by the manager."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: ParentalManager, profile: dict[str, Any], key: str) -> None:
        self._manager = manager
        self._pid = profile["id"]
        self._attr_unique_id = f"parental_{self._pid}_{key}"
        self._attr_device_info = manager.device_info(profile)

    @property
    def _cfg(self) -> dict[str, Any]:
        return self._manager.cfg(self._pid)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._manager.async_add_listener(self.async_write_ha_state))


class ParentalEnabledSwitch(_ParentalEntity, SwitchEntity):
    _attr_name = "Activé"
    _attr_icon = "mdi:shield-account"

    def __init__(self, manager: ParentalManager, profile: dict[str, Any]) -> None:
        super().__init__(manager, profile, "enabled")

    @property
    def is_on(self) -> bool:
        return bool(self._cfg["enabled"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._manager.async_set(self._pid, "enabled", True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._manager.async_set(self._pid, "enabled", False)


class ParentalTime(_ParentalEntity, TimeEntity):
    _attr_icon = "mdi:clock-outline"

    def __init__(self, manager: ParentalManager, profile: dict[str, Any], key: str) -> None:
        super().__init__(manager, profile, key)
        self._key = key
        self._attr_name = TIME_NAMES[key]

    @property
    def native_value(self) -> time:
        return parse_hhmm(self._cfg[self._key])

    async def async_set_value(self, value: time) -> None:
        await self._manager.async_set(self._pid, self._key, value.strftime("%H:%M"))


class _ParentalDeviceEntity(_ParentalEntity):
    """Entity of one device of a profile (own quota, own counter)."""

    def __init__(
        self,
        manager: ParentalManager,
        profile: dict[str, Any],
        mac: str,
        label: str,
        key: str,
    ) -> None:
        super().__init__(manager, profile, f"{mac}_{key}")
        self._mac = mac
        self._label = label

    @property
    def _dcfg(self) -> dict[str, Any]:
        return self._manager.dcfg(self._pid, self._mac)


class ParentalQuota(_ParentalDeviceEntity, NumberEntity):
    _attr_icon = "mdi:timer-sand"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = QUOTA_MAX
    _attr_native_step = 15
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self,
        manager: ParentalManager,
        profile: dict[str, Any],
        mac: str,
        label: str,
        key: str,
    ) -> None:
        super().__init__(manager, profile, mac, label, key)
        self._key = key
        self._attr_name = f"{QUOTA_NAMES[key]} – {label}"

    @property
    def native_value(self) -> float:
        return float(self._dcfg[self._key])

    async def async_set_native_value(self, value: float) -> None:
        self._dcfg[self._key] = int(value)
        await self._manager.async_changed()


STATUS_TEXT = {
    DISABLED: "Désactivé",
    FREE: "Libre",
    SCHEDULE: "Coupé",
    QUOTA: "Quota atteint",
    OVERRIDE: "Dérogation",
}


class ParentalStatusSensor(_ParentalEntity, SensorEntity):
    _attr_name = "Statut"

    def __init__(self, manager: ParentalManager, profile: dict[str, Any]) -> None:
        super().__init__(manager, profile, "status")

    @property
    def native_value(self) -> str:
        st = self._manager.state(self._pid)
        text = STATUS_TEXT[st.status]
        if st.status == SCHEDULE and st.until:
            return f"{text} jusqu'à {st.until:%H:%M}"
        if st.status == OVERRIDE and st.until:
            return f"{text} jusqu'à {st.until:%H:%M}"
        return text

    @property
    def icon(self) -> str:
        return {
            DISABLED: "mdi:shield-off-outline",
            FREE: "mdi:shield-check",
            SCHEDULE: "mdi:sleep",
            QUOTA: "mdi:timer-off",
            OVERRIDE: "mdi:lock-open-variant",
        }[self._manager.state(self._pid).status]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._manager.state(self._pid)
        prof = self._manager.profile(self._pid) or {}
        macs = prof.get("macs", [])
        return {
            "code": st.status,
            "autorise": st.allowed,
            "jusqu_a": st.until.isoformat() if st.until else None,
            "appareils": macs,
            "quota_atteint": [
                m for m in macs if self._manager.device_state(self._pid, m).status == QUOTA
            ],
            "bloques_par_controle": list(self._cfg.get("owned") or []),
        }


class ParentalUsedSensor(_ParentalDeviceEntity, SensorEntity):
    _attr_icon = "mdi:timer-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0

    def __init__(
        self, manager: ParentalManager, profile: dict[str, Any], mac: str, label: str
    ) -> None:
        super().__init__(manager, profile, mac, label, "used")
        self._attr_name = f"Temps utilisé – {label}"

    @property
    def native_value(self) -> int:
        return round(self._manager.device_state(self._pid, self._mac).used_min)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._manager.device_state(self._pid, self._mac)
        return {
            "mac": self._mac,
            "quota_min": st.quota_min,
            "quota_atteint": st.status == QUOTA,
        }


class ParentalOverrideButton(_ParentalEntity, ButtonEntity):
    _attr_name = "Rétablir 1 h"
    _attr_icon = "mdi:lock-clock"

    def __init__(self, manager: ParentalManager, profile: dict[str, Any]) -> None:
        super().__init__(manager, profile, "override")

    async def async_press(self) -> None:
        await self._manager.async_grant(self._pid, OVERRIDE_MINUTES)


def device_label(manager: ParentalManager, mac: str) -> str:
    """Readable name of a device (``Appareil 5E:B5`` when it has no name)."""
    device = manager.coordinator.device_by_mac(mac) or {}
    name = str(device.get("name") or "")
    if not name or _MAC_NAME_RE.match(name):
        return f"Appareil {mac[-5:].upper()}"
    return name


def build(kind: str, manager: ParentalManager) -> list[Any]:
    """Entities of one platform for every profile."""
    out: list[Any] = []
    for prof in manager.profiles:
        if kind == "switch":
            out.append(ParentalEnabledSwitch(manager, prof))
        elif kind == "time":
            out += [ParentalTime(manager, prof, k) for k in TIME_KEYS]
        elif kind == "number":
            for mac in prof["macs"]:
                label = device_label(manager, mac)
                out += [ParentalQuota(manager, prof, mac, label, k) for k in QUOTA_KEYS]
        elif kind == "sensor":
            out.append(ParentalStatusSensor(manager, prof))
            for mac in prof["macs"]:
                out.append(ParentalUsedSensor(manager, prof, mac, device_label(manager, mac)))
        elif kind == "button":
            out.append(ParentalOverrideButton(manager, prof))
    return out
