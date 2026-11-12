"""Parental control: profiles, bedtime windows, daily quota, temporary override.

A *profile* (one per child, say) groups several devices (phone, tablet,
console…) that share the same bedtime windows and override:

* **bedtime windows** – a "week" window and a "weekend" window. A window may
  span midnight (21:00 → 07:00). Equal start and end disable the window.
  The kind of window is chosen from the day it *starts*: nights starting on
  Friday and Saturday use the weekend window;
* **daily quota** – set *per device*: each device has its own quota (one
  value for the week, one for the weekend = Saturday and Sunday; ``0`` means
  no quota) and its own counter, which runs while that device is connected
  and allowed;
* **override** – "restore for 1 h": lifts every restriction for a while.

Blocking reuses the router's MAC filter (same call as the manual switches).
The engine only releases the devices it blocked itself, so a device you
blocked by hand stays blocked.

Rules are evaluated once a minute and after every router refresh, so the
engine needs Home Assistant to be running: when it is stopped the router
keeps the last state.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .bbox_api import BboxError
from .const import CONF_BLOCK_SCOPE, CONF_PROFILES, DEFAULT_BLOCK_SCOPE, DOMAIN

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(minutes=1)
#: Longest gap counted as "used time" between two evaluations (seconds).
MAX_ELAPSED = 180

OVERRIDE_MINUTES = 60
BONUS_MINUTES = 15
QUOTA_MAX = 720

# Status codes
FREE = "free"
DISABLED = "disabled"
SCHEDULE = "schedule"
QUOTA = "quota"
OVERRIDE = "override"

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "wk_start": "21:00",
    "wk_end": "07:00",
    "we_start": "22:00",
    "we_end": "08:00",
    "override_until": "",
    "holiday": False,  # use the weekend rules every day
    "warn_min": 10,  # "quota almost over" alert (0 = off)
    "owned": [],  # MAC addresses blocked by the engine
}

TIME_KEYS = ("wk_start", "wk_end", "we_start", "we_end")
QUOTA_KEYS = ("wk_quota", "we_quota")

#: Per-device data of a profile: its own quota and its own counter.
DEVICE_DEFAULTS: dict[str, Any] = {
    "wk_quota": 0,
    "we_quota": 0,
    "used": 0.0,  # seconds used today
    "used_date": "",
    "bonus": 0,  # extra minutes granted today
    "warned_date": "",  # day of the last "quota almost over" alert
    "override_until": "",  # "restore for 1 h" for this device only
}


# ---------------------------------------------------------------------------
# Pure helpers (no Home Assistant state, easy to test)
# ---------------------------------------------------------------------------


def parse_hhmm(value: str) -> time:
    hour, minute = str(value).split(":")[:2]
    return time(int(hour), int(minute))


def night_kind(day: date, holiday: bool = False) -> str:
    """Window used by a block *starting* on ``day`` (Friday, Saturday = weekend).

    In holiday mode the weekend window is used every day.
    """
    return "we" if holiday or day.weekday() in (4, 5) else "wk"


def quota_kind(day: date, holiday: bool = False) -> str:
    """Quota used on ``day`` (Saturday, Sunday = weekend; holidays: always)."""
    return "we" if holiday or day.weekday() in (5, 6) else "wk"


def block_window(cfg: dict[str, Any], now: datetime) -> tuple[datetime, datetime] | None:
    """The bedtime window containing ``now`` as ``(start, end)``, if any."""
    for offset in (0, -1):
        day = now.date() + timedelta(days=offset)
        kind = night_kind(day, bool(cfg.get("holiday")))
        start_t = parse_hhmm(cfg[f"{kind}_start"])
        end_t = parse_hhmm(cfg[f"{kind}_end"])
        if start_t == end_t:
            continue
        start = datetime.combine(day, start_t, tzinfo=now.tzinfo)
        end_day = day if start_t < end_t else day + timedelta(days=1)
        end = datetime.combine(end_day, end_t, tzinfo=now.tzinfo)
        if start <= now < end:
            return start, end
    return None


def quota_minutes(dcfg: dict[str, Any], day: date, holiday: bool = False) -> int:
    return int(dcfg.get(f"{quota_kind(day, holiday)}_quota") or 0)


@dataclass
class ProfileState:
    status: str
    allowed: bool
    until: datetime | None  # end of the restriction / of the override


def compute_state(cfg: dict[str, Any], now: datetime) -> ProfileState:
    """Where a profile stands (priority: off > override > bedtime window).

    The quota is per device: see :func:`device_state`.
    """
    if not cfg.get("enabled"):
        return ProfileState(DISABLED, True, None)
    override = _parse_dt(cfg.get("override_until"))
    if override and override > now:
        return ProfileState(OVERRIDE, True, override)
    window = block_window(cfg, now)
    if window:
        return ProfileState(SCHEDULE, False, window[1])
    return ProfileState(FREE, True, None)


@dataclass
class DeviceState:
    status: str
    allowed: bool
    until: datetime | None
    used_min: float
    quota_min: int  # today's quota including the bonus (0 = unlimited)
    bonus_min: int = 0


def device_state(cfg: dict[str, Any], dcfg: dict[str, Any], now: datetime) -> DeviceState:
    """State of one device: the profile rules, then the device's own quota."""
    profile = compute_state(cfg, now)
    base = quota_minutes(dcfg, now.date(), bool(cfg.get("holiday")))
    bonus = int(dcfg.get("bonus") or 0) if base > 0 else 0
    quota = base + bonus
    used = float(dcfg.get("used") or 0) / 60
    if profile.status not in (DISABLED, OVERRIDE):
        own = _parse_dt(dcfg.get("override_until"))
        if own and own > now:  # "restore for 1 h" for this device only
            return DeviceState(OVERRIDE, True, own, used, quota, bonus)
    if profile.status == FREE and quota > 0 and used >= quota:
        midnight = datetime.combine(
            now.date() + timedelta(days=1), time(0), tzinfo=now.tzinfo
        )
        return DeviceState(QUOTA, False, midnight, used, quota, bonus)
    return DeviceState(profile.status, profile.allowed, profile.until, used, quota, bonus)


def format_minutes(minutes: float) -> str:
    """``85`` -> ``1 h 25``, ``45`` -> ``45 min``."""
    total = max(int(round(minutes)), 0)
    hours, mins = divmod(total, 60)
    return f"{hours} h {mins:02d}" if hours else f"{mins} min"


def remaining_text(state: "DeviceState") -> str:
    """Time left today for a device: ``1 h 25``, ``Épuisé`` or ``Illimité``."""
    if state.quota_min <= 0:
        return "Illimité"
    left = state.quota_min - state.used_min
    return "Épuisé" if left <= 0.5 else format_minutes(left)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return dt_util.parse_datetime(str(value))
    except (ValueError, TypeError):
        return None


def is_blocked(device: dict[str, Any] | None, scope: str) -> bool | None:
    """Blocked state of a device for the configured scope (``None`` = unknown)."""
    if not device:
        return None
    if scope == "wifi":
        blocked = device.get("blocked_wifi")
    elif scope in ("lan", "internet"):
        blocked = device.get("blocked_lan")
    else:
        blocked = None
    if blocked is None:
        blocked = device.get("blocked")
    return None if blocked is None else bool(blocked)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ParentalManager:
    """Holds the rules of every profile and applies them to the router."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._store: Store = Store(hass, 1, f"{DOMAIN}.parental.{entry.entry_id}")
        self._data: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._last_tick: float | None = None
        self._last_error = ""
        self._orphans: dict[str, list[str]] = {}
        self._event_listeners: list[Callable[[str | None, str, dict[str, Any]], None]] = []
        self._prev_dev: dict[tuple[str, str], str] = {}
        self._prev_prof: dict[str, str] = {}
        self._known_macs: set[str] | None = None

    # -- profiles -----------------------------------------------------------

    @property
    def profiles(self) -> list[dict[str, Any]]:
        """``[{"id", "name", "macs": [...]}]`` from the options."""
        raw = (self.entry.options or {}).get(CONF_PROFILES) or []
        return [
            {
                "id": str(p["id"]),
                "name": str(p.get("name") or p["id"]),
                "macs": [str(m).lower() for m in p.get("macs") or []],
            }
            for p in raw
            if isinstance(p, dict) and p.get("id")
        ]

    def profile(self, ref: str) -> dict[str, Any] | None:
        """A profile by id or by name (case-insensitive)."""
        ref = (ref or "").strip().lower()
        for prof in self.profiles:
            if ref in (prof["id"].lower(), prof["name"].lower()):
                return prof
        return None

    def cfg(self, pid: str) -> dict[str, Any]:
        cfg = self._data.setdefault(pid, {})
        for key, value in DEFAULTS.items():
            cfg.setdefault(key, list(value) if isinstance(value, list) else value)
        return cfg

    def state(self, pid: str) -> ProfileState:
        return compute_state(self.cfg(pid), dt_util.now())

    def dcfg(self, pid: str, mac: str) -> dict[str, Any]:
        """Quota settings and daily counter of one device of a profile."""
        cfg = self.cfg(pid)
        devices = cfg.setdefault("devices", {})
        mac = mac.lower()
        if mac not in devices:
            # A quota set on the whole profile (earlier versions) becomes the
            # starting value of every device.
            devices[mac] = {
                **DEVICE_DEFAULTS,
                "wk_quota": int(cfg.get("wk_quota") or 0),
                "we_quota": int(cfg.get("we_quota") or 0),
            }
        return devices[mac]

    def device_state(self, pid: str, mac: str) -> DeviceState:
        return device_state(self.cfg(pid), self.dcfg(pid, mac), dt_util.now())

    # -- listeners (entities) ------------------------------------------------

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)

        @callback
        def _remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return _remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # -- lifecycle -----------------------------------------------------------

    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        self._data = {
            pid: dict(cfg)
            for pid, cfg in (stored.get("profiles") or {}).items()
            if isinstance(cfg, dict)
        }
        if isinstance(stored.get("known_macs"), list):
            self._known_macs = {str(m).lower() for m in stored["known_macs"]}
        known = {p["id"] for p in self.profiles}
        # Profile deleted while we had devices blocked: release them below.
        self._orphans = {
            pid: list(cfg.get("owned") or [])
            for pid, cfg in self._data.items()
            if pid not in known
        }
        for pid in list(self._data):
            if pid not in known:
                del self._data[pid]

        self.entry.async_on_unload(
            async_track_time_interval(self.hass, self._async_tick, TICK)
        )
        self.entry.async_on_unload(self.coordinator.async_add_listener(self._on_update))
        await self.async_evaluate()

    @callback
    def _on_update(self) -> None:
        self.entry.async_create_background_task(
            self.hass, self.async_evaluate(), f"{DOMAIN}_parental_eval"
        )

    async def _async_tick(self, _now: datetime) -> None:
        await self.async_evaluate()

    def _payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"profiles": self._data}
        if self._known_macs is not None:
            out["known_macs"] = sorted(self._known_macs)
        return out

    async def async_stop(self) -> None:
        """Write pending changes (called before an unload / reload)."""
        await self._store.async_save(self._payload())

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._payload, 10)

    # -- events (alerts) -------------------------------------------------------

    @callback
    def async_add_event_listener(
        self, listener: Callable[[str | None, str, dict[str, Any]], None]
    ) -> Callable[[], None]:
        self._event_listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return _remove

    @callback
    def _emit(self, pid: str | None, event: str, data: dict[str, Any]) -> None:
        """Alert for the profile ``pid`` (``None`` = box level)."""
        for listener in list(self._event_listeners):
            listener(pid, event, data)

    def device_label(self, mac: str) -> str:
        device = self.coordinator.device_by_mac(mac) or {}
        return str(device.get("name") or mac)

    # -- changes made by the entities ---------------------------------------

    async def async_set(self, pid: str, key: str, value: Any) -> None:
        self.cfg(pid)[key] = value
        self._save()
        self._notify()
        await self.async_evaluate()

    async def async_changed(self) -> None:
        """A setting changed (e.g. a quota): save, refresh entities, re-evaluate."""
        self._save()
        self._notify()
        await self.async_evaluate()

    async def async_grant(self, pid: str, minutes: int, mac: str | None = None) -> None:
        """Lift the restrictions for ``minutes`` (``0`` cancels the override).

        For the whole profile, or for one device only when ``mac`` is given.
        """
        target = self.dcfg(pid, mac) if mac else self.cfg(pid)
        if minutes > 0:
            until = dt_util.now() + timedelta(minutes=minutes)
            target["override_until"] = until.isoformat()
            self._emit(
                pid,
                "override_started",
                {"device": self.device_label(mac) if mac else None, "mac": mac,
                 "minutes": minutes, "until": until.isoformat()},
            )
        else:
            target["override_until"] = ""
        self._save()
        self._notify()
        await self.async_evaluate()

    async def async_add_bonus(self, pid: str, minutes: int, mac: str | None = None) -> int:
        """Add ``minutes`` to today's quota (one device or the whole profile).

        Devices without a quota (unlimited) are left alone. Returns how many
        devices received the bonus.
        """
        prof = next((p for p in self.profiles if p["id"] == pid), None)
        if prof is None:
            return 0
        now = dt_util.now()
        cfg = self.cfg(pid)
        granted = 0
        for target in [mac.lower()] if mac else prof["macs"]:
            dcfg = self.dcfg(pid, target)
            if quota_minutes(dcfg, now.date(), bool(cfg.get("holiday"))) <= 0:
                continue
            dcfg["bonus"] = min(int(dcfg.get("bonus") or 0) + minutes, QUOTA_MAX)
            dcfg["warned_date"] = ""  # the "almost over" alert may fire again
            granted += 1
        if granted:
            self._save()
            self._notify()
            await self.async_evaluate()
        return granted

    # -- engine ----------------------------------------------------------------

    @property
    def _scope(self) -> str:
        return str((self.entry.options or {}).get(CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE))

    async def async_evaluate(self) -> None:
        async with self._lock:
            changed = await self._evaluate()
        self._notify()
        if changed:
            await self.coordinator.async_request_refresh()

    async def _evaluate(self) -> bool:
        now = dt_util.now()
        mono = _time.monotonic()
        elapsed = 0.0 if self._last_tick is None else min(mono - self._last_tick, MAX_ELAPSED)
        self._last_tick = mono

        changed = False
        in_profile: set[str] = set()
        for prof in self.profiles:
            cfg = self.cfg(prof["id"])
            in_profile.update(prof["macs"])

            today = now.date().isoformat()
            pid = prof["id"]
            pstate = compute_state(cfg, now)
            prev_p = self._prev_prof.get(pid)
            if prev_p is not None and prev_p != pstate.status:
                if pstate.status == SCHEDULE:
                    self._emit(pid, "bedtime_start", {"until": pstate.until.isoformat() if pstate.until else None})
                elif prev_p == SCHEDULE:
                    self._emit(pid, "bedtime_end", {})
            self._prev_prof[pid] = pstate.status

            for mac in prof["macs"]:
                dcfg = self.dcfg(pid, mac)
                if dcfg["used_date"] != today:
                    dcfg["used"] = 0.0
                    dcfg["used_date"] = today
                    dcfg["bonus"] = 0
                    dcfg["warned_date"] = ""
                state = device_state(cfg, dcfg, now)
                # Quota: each device counts its own time, while it is online
                # and allowed.
                if state.status == FREE and state.quota_min > 0 and elapsed:
                    if self._online(mac):
                        dcfg["used"] = float(dcfg["used"]) + elapsed
                        state = device_state(cfg, dcfg, now)

                # Alerts
                key = (pid, mac)
                prev = self._prev_dev.get(key)
                data = {
                    "device": self.device_label(mac),
                    "mac": mac,
                    "quota_min": state.quota_min,
                    "used_min": round(state.used_min),
                }
                if prev is not None and state.status == QUOTA and prev != QUOTA:
                    self._emit(pid, "quota_reached", data)
                self._prev_dev[key] = state.status
                warn = int(cfg.get("warn_min") or 0)
                if warn > 0 and state.status == FREE and state.quota_min > 0:
                    left = state.quota_min - state.used_min
                    if 0 < left <= warn and dcfg.get("warned_date") != today:
                        dcfg["warned_date"] = today
                        self._emit(pid, "quota_warning", {**data, "remaining_min": round(left)})

                changed |= await self._apply(cfg, mac, not state.allowed)

        # Devices removed from a profile (or whose profile was deleted).
        for pid, cfg in self._data.items():
            for mac in list(cfg.get("owned") or []):
                if mac not in in_profile:
                    changed |= await self._apply(cfg, mac, False)
        for pid, macs in list(self._orphans.items()):
            holder = {"owned": list(macs)}
            for mac in macs:
                if mac not in in_profile:
                    await self._apply(holder, mac, False)
            if not holder["owned"]:
                self._orphans.pop(pid, None)
            else:
                self._orphans[pid] = holder["owned"]
        self._check_new_devices()
        self._save()
        return changed

    def _check_new_devices(self) -> None:
        """Alert when a device never seen before joins the network."""
        devices = (self.coordinator.data or {}).get("devices") or []
        macs = {str(d.get("mac") or "").lower() for d in devices} - {""}
        if self._known_macs is None:
            self._known_macs = macs  # first run: nothing is "new"
            return
        for dev in devices:
            mac = str(dev.get("mac") or "").lower()
            if mac and mac not in self._known_macs:
                self._known_macs.add(mac)
                self._emit(
                    None,
                    "new_device",
                    {"mac": mac, "name": dev.get("name"), "ip": dev.get("ip"),
                     "link": dev.get("link"), "ssid": dev.get("ssid")},
                )

    def _online(self, mac: str) -> bool:
        device = self.coordinator.device_by_mac(mac)
        return bool(device and device.get("active") and not is_blocked(device, self._scope))

    async def _apply(self, cfg: dict[str, Any], mac: str, block: bool) -> bool:
        """Bring one device to the wanted state. Returns ``True`` if it changed."""
        owned: list[str] = cfg.setdefault("owned", [])
        blocked = is_blocked(self.coordinator.device_by_mac(mac), self._scope)

        if block:
            if blocked is not False:
                return False  # already blocked (by us or by hand) or unknown
            if await self._set_blocked(mac, True):
                if mac not in owned:
                    owned.append(mac)
                return True
            return False

        if mac not in owned:
            return False  # never touch what we did not block
        if blocked is False:
            owned.remove(mac)  # someone already released it
            return False
        if await self._set_blocked(mac, False):
            owned.remove(mac)
            return True
        return False

    async def _set_blocked(self, mac: str, blocked: bool) -> bool:
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_device_blocked, mac, blocked, self._scope
            )
        except BboxError as err:
            message = f"{mac}: {err}"
            if message != self._last_error:  # do not repeat every minute
                _LOGGER.warning("Parental control: unable to %s %s",
                                "block" if blocked else "release", message)
                self._last_error = message
            return False
        self._last_error = ""
        _LOGGER.info("Parental control: %s %s", "blocked" if blocked else "released", mac)
        # Mirror the change in the cached data until the next refresh, so the
        # next evaluation does not act on a stale state.
        device = self.coordinator.device_by_mac(mac)
        if device is not None:
            device["blocked"] = blocked
            key = "blocked_wifi" if self._scope == "wifi" else "blocked_lan"
            if key in device:
                device[key] = blocked
            access = "acces_wifi" if self._scope == "wifi" else "acces_internet"
            if access in device:
                device[access] = not blocked
        return True

    # -- helpers for the entities ---------------------------------------------

    def device_info(self, profile: dict[str, Any]) -> Any:
        from homeassistant.helpers.device_registry import DeviceInfo

        host = self.entry.data.get("host", "")
        return DeviceInfo(
            identifiers={(DOMAIN, f"parental_{profile['id']}")},
            name=f"Contrôle parental – {profile['name']}",
            manufacturer="Bouygues Telecom",
            model="Parental control",
            via_device=(DOMAIN, f"box_{host}"),
        )
