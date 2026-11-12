"""Wi-Fi timer: switch a radio on now and off again after a while.

Example: guest network on for 3 hours. The end time is stored, so a restart of
Home Assistant does not lose the timer. Which bands exist depends on the box
(``24``, ``5`` and ``all`` on Huawei routers; ``guest`` only where the box
exposes it).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .bbox_api import BboxError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BANDS = ("all", "24", "5", "guest")
RETRY = timedelta(minutes=5)


class WifiTimers:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._store: Store = Store(hass, 1, f"{DOMAIN}.wifi_timers.{entry.entry_id}")
        self._ends: dict[str, str] = {}
        self._cancel: dict[str, Callable[[], None]] = {}

    @property
    def timers(self) -> dict[str, str]:
        """``{band: end (ISO)}`` of the running timers."""
        return dict(self._ends)

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        for band, end in (stored.get("timers") or {}).items():
            if band in BANDS and dt_util.parse_datetime(str(end)):
                self._ends[band] = str(end)
                self._schedule(band)

    async def async_stop(self) -> None:
        for cancel in self._cancel.values():
            cancel()
        self._cancel.clear()
        await self._store.async_save({"timers": self._ends})

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"timers": self._ends}, 5)

    def _schedule(self, band: str, when: datetime | None = None) -> None:
        if band in self._cancel:
            self._cancel.pop(band)()
        end = when or dt_util.parse_datetime(self._ends[band])
        # An end time already in the past fires right away.
        end = max(end, dt_util.utcnow() + timedelta(seconds=1))

        async def _fire(_now: datetime, band: str = band) -> None:
            await self._expire(band)

        self._cancel[band] = async_track_point_in_utc_time(self.hass, _fire, end)

    async def async_set(self, band: str, minutes: int) -> None:
        """Switch ``band`` on and schedule its switch-off (``0`` cancels)."""
        if band not in BANDS:
            raise HomeAssistantError(f"Unknown Wi-Fi band: {band}")
        if minutes <= 0:
            self._ends.pop(band, None)
            if band in self._cancel:
                self._cancel.pop(band)()
            self._save()
            return
        try:
            await self.hass.async_add_executor_job(self.coordinator.client.set_wifi, band, True)
        except BboxError as err:
            raise HomeAssistantError(f"Unable to switch the Wi-Fi ({band}) on: {err}") from err
        end = dt_util.utcnow() + timedelta(minutes=minutes)
        self._ends[band] = end.isoformat()
        self._schedule(band, end)
        self._save()
        await self.coordinator.async_request_refresh()

    async def _expire(self, band: str) -> None:
        self._cancel.pop(band, None)
        try:
            await self.hass.async_add_executor_job(self.coordinator.client.set_wifi, band, False)
        except BboxError as err:
            _LOGGER.warning("Wi-Fi timer (%s): unable to switch off, retrying: %s", band, err)
            self._schedule(band, dt_util.utcnow() + RETRY)
            return
        self._ends.pop(band, None)
        self._save()
        _LOGGER.info("Wi-Fi timer (%s): switched off", band)
        await self.coordinator.async_request_refresh()
