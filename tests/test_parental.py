from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.bbox_4g import parental as P

TZ = ZoneInfo("Europe/Paris")
LEA = "aa:aa:aa:aa:aa:01"
LEA2 = "aa:aa:aa:aa:aa:02"
OTHER = "bb:bb:bb:bb:bb:01"


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


# 2026-11-09 is a Monday
async def test_window_overnight_and_kinds():
    cfg = dict(P.DEFAULTS)
    assert P.block_window(cfg, at(2026, 11, 9, 20, 59)) is None
    s, e = P.block_window(cfg, at(2026, 11, 9, 21, 0))
    assert e == at(2026, 11, 10, 7, 0)
    assert P.block_window(cfg, at(2026, 11, 10, 6, 59)) is not None
    assert P.block_window(cfg, at(2026, 11, 10, 7, 0)) is None
    # Friday night uses the weekend window 22:00 -> 08:00 (Saturday)
    assert P.block_window(cfg, at(2026, 11, 13, 21, 30)) is None
    s, e = P.block_window(cfg, at(2026, 11, 14, 7, 30))
    assert e == at(2026, 11, 14, 8, 0)
    # Sunday night is a week night again
    s, e = P.block_window(cfg, at(2026, 11, 15, 21, 5))
    assert e == at(2026, 11, 16, 7, 0)
    # equal start/end disables
    cfg["wk_start"] = cfg["wk_end"] = "07:00"
    assert P.block_window(cfg, at(2026, 11, 9, 23, 0)) is None
    # daytime window
    cfg.update(wk_start="08:00", wk_end="16:00")
    assert P.block_window(cfg, at(2026, 11, 9, 9, 0)) is not None
    assert P.block_window(cfg, at(2026, 11, 9, 16, 0)) is None


async def test_format_helpers():
    assert P.format_minutes(85) == "1 h 25" and P.format_minutes(45) == "45 min" and P.format_minutes(60) == "1 h 00"
    mk = lambda q, u: P.DeviceState(P.FREE, True, None, u, q)
    assert P.remaining_text(mk(0, 5)) == "Illimité"
    assert P.remaining_text(mk(120, 35)) == "1 h 25"
    assert P.remaining_text(mk(30, 30)) == "Épuisé"


async def test_state_priority_and_quota_kind():
    cfg = dict(P.DEFAULTS, enabled=True)
    dev = dict(P.DEVICE_DEFAULTS, wk_quota=60, we_quota=120, used=3600.0)
    assert P.device_state(cfg, dev, at(2026, 11, 9, 12, 0)).status == P.QUOTA
    assert P.device_state(cfg, dev, at(2026, 11, 14, 12, 0)).status == P.FREE  # Sat: 120
    assert P.device_state(cfg, dev, at(2026, 11, 9, 22, 0)).status == P.SCHEDULE  # window > quota
    cfg["override_until"] = at(2026, 11, 9, 23, 0).isoformat()
    assert P.device_state(cfg, dev, at(2026, 11, 9, 22, 0)).status == P.OVERRIDE
    assert P.device_state(cfg, dev, at(2026, 11, 9, 12, 0)).status == P.OVERRIDE  # override lifts quota
    cfg["enabled"] = False
    assert P.device_state(cfg, dev, at(2026, 11, 9, 22, 0)).status == P.DISABLED


class FakeClient:
    host = "192.168.1.1"

    def __init__(self):
        self.blocked = {OTHER}  # blocked by hand
        self.calls = []
        self.active = {LEA, LEA2, OTHER}
        self.extra = []  # extra devices (new device tests)
        self.wifi = []   # set_wifi calls

    def state(self):
        devs = []
        for mac, name in ((LEA, "Tel Léa"), (LEA2, "PC Léa"), (OTHER, "Tel Papa")):
            b = mac in self.blocked
            devs.append({"mac": mac, "name": name, "ip": "192.168.1.10", "active": mac in self.active and not b,
                         "link": "Wifi", "ssid": "x", "guest": False,
                         "blocked_lan": b, "blocked_wifi": False, "blocked": b,
                         "acces_internet": not b, "internet_allowed": not b, "acces_wifi": True})
        devs += self.extra
        return {"ok": True, "devices": devs, "nb_connectes": 2, "nb_total": 3,
                "wifi": {"24": True, "5": True, "guest": False}, "model": "B528", "firmware": "1",
                "host": "x", "timestamp": "t"}

    def set_device_blocked(self, mac, blocked, scope="lan"):
        self.calls.append((mac, blocked, scope))
        (self.blocked.add if blocked else self.blocked.discard)(mac)

    def set_wifi(self, band, enabled):
        self.wifi.append((band, enabled))

    def reconnect(self): pass


async def setup(hass, freezer, client, profiles=None, options=None):
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(at(2026, 11, 9, 20, 55))
    entry = MockConfigEntry(domain="bbox_4g", data={"host": "192.168.1.1", "password": "x"},
                            options={"profiles": profiles if profiles is not None else
                                     [{"id": "p_lea", "name": "Léa", "macs": [LEA, LEA2]}], **(options or {})},
                            unique_id="192.168.1.1")
    entry.add_to_hass(hass)
    with patch("custom_components.bbox_4g.detect_client", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def settle(hass):
    for _ in range(3):
        await hass.async_block_till_done(wait_background_tasks=True)
        await hass.async_add_executor_job(lambda: None)
        await hass.async_block_till_done(wait_background_tasks=True)


async def tick(hass, freezer, minutes=1):
    for _ in range(minutes):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await settle(hass)


def ent(hass, domain, key):
    return er.async_get(hass).async_get_entity_id(domain, "bbox_4g", f"parental_p_lea_{key}")


def dent(hass, domain, mac, key):
    return ent(hass, domain, f"{mac}_{key}")


async def test_entities_created(hass, freezer):
    await setup(hass, freezer, FakeClient())
    for dom, key in (("switch", "enabled"), ("sensor", "status"), ("button", "override"),
                     ("time", "wk_start"), ("time", "we_end")):
        assert ent(hass, dom, key), (dom, key)
    for mac in (LEA, LEA2):
        for dom, key in (("number", "wk_quota"), ("number", "we_quota"), ("sensor", "used")):
            assert dent(hass, dom, mac, key), (mac, dom, key)
    assert hass.states.get(ent(hass, "sensor", "status")).state == "Désactivé"
    print(ent(hass, "switch", "enabled"), ent(hass, "button", "override"))


async def test_schedule_override_and_manual_block(hass, freezer):
    c = FakeClient()
    await setup(hass, freezer, c)
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "enabled")}, blocking=True)
    await settle(hass)
    status = ent(hass, "sensor", "status")
    assert hass.states.get(status).state == "Libre"
    assert not c.calls
    await tick(hass, freezer, 6)  # 21:01
    assert {LEA, LEA2} <= c.blocked and OTHER in c.blocked
    assert hass.states.get(status).state == "Coupé jusqu'à 07:00"
    # override 1h
    await hass.services.async_call("button", "press", {"entity_id": ent(hass, "button", "override")}, blocking=True)
    await settle(hass)
    await tick(hass, freezer, 1)
    assert LEA not in c.blocked and LEA2 not in c.blocked
    assert OTHER in c.blocked  # manual block untouched
    assert hass.states.get(status).state.startswith("Dérogation jusqu'à 22:0")
    await tick(hass, freezer, 61)  # override over, still night
    assert {LEA, LEA2} <= c.blocked
    # morning: released, OTHER stays blocked
    freezer.move_to(at(2026, 11, 10, 7, 1)); async_fire_time_changed(hass); await settle(hass)
    await tick(hass, freezer, 1)
    assert LEA not in c.blocked and LEA2 not in c.blocked and OTHER in c.blocked
    assert hass.states.get(status).state == "Libre"


async def test_service_grant(hass, freezer):
    c = FakeClient()
    await setup(hass, freezer, c)
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "enabled")}, blocking=True)
    await tick(hass, freezer, 6)
    assert LEA in c.blocked
    await hass.services.async_call("bbox_4g", "grant_extra_time", {"profile": "léa", "minutes": 30}, blocking=True)
    await settle(hass)
    assert LEA not in c.blocked
    await hass.services.async_call("bbox_4g", "grant_extra_time", {"profile": "Léa", "minutes": 0}, blocking=True)
    await settle(hass)
    assert LEA in c.blocked
    with pytest.raises(Exception):
        await hass.services.async_call("bbox_4g", "grant_extra_time", {"profile": "nope"}, blocking=True)


async def test_quota_is_per_device(hass, freezer):
    c = FakeClient()
    await setup(hass, freezer, c)
    # no window; 30 min for the phone only, the PC has no quota
    for k in ("wk_start", "wk_end"):
        await hass.services.async_call("time", "set_value", {"entity_id": ent(hass, "time", k), "time": "07:00:00"}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": dent(hass, "number", LEA, "wk_quota"), "value": 30}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "enabled")}, blocking=True)
    await tick(hass, freezer, 1)
    used = dent(hass, "sensor", LEA, "used")
    used2 = dent(hass, "sensor", LEA2, "used")
    await tick(hass, freezer, 10)
    assert 9 <= int(hass.states.get(used).state) <= 11
    assert int(hass.states.get(used2).state) == 0  # no quota -> not counted
    left, left2 = dent(hass, "sensor", LEA, "remaining"), dent(hass, "sensor", LEA2, "remaining")
    assert hass.states.get(left).state in ("19 min", "20 min", "21 min"), hass.states.get(left).state
    assert hass.states.get(left2).state == "Illimité"
    assert not c.calls
    await tick(hass, freezer, 25)
    assert LEA in c.blocked and LEA2 not in c.blocked  # only the phone is cut
    assert hass.states.get(used).attributes["quota_atteint"] is True
    assert hass.states.get(left).state == "Épuisé"
    assert hass.states.get(ent(hass, "sensor", "status")).attributes["quota_atteint"] == [LEA]
    # blocked device is offline: its counter stops
    before = hass.states.get(used).state
    await tick(hass, freezer, 5)
    assert hass.states.get(used).state == before
    # the override lifts the quota, then it applies again
    await hass.services.async_call("button", "press", {"entity_id": ent(hass, "button", "override")}, blocking=True)
    await settle(hass)
    assert LEA not in c.blocked
    await tick(hass, freezer, 61)
    assert LEA in c.blocked
    # a second device gets its own quota
    await hass.services.async_call("number", "set_value", {"entity_id": dent(hass, "number", LEA2, "wk_quota"), "value": 15}, blocking=True)
    await tick(hass, freezer, 20)
    assert LEA2 in c.blocked
    # new day resets both
    freezer.move_to(at(2026, 11, 10, 0, 1)); async_fire_time_changed(hass); await settle(hass)
    await tick(hass, freezer, 1)
    assert LEA not in c.blocked and LEA2 not in c.blocked
    assert hass.states.get(used).state in ("0", "1")


async def test_remove_profile_releases(hass, freezer):
    c = FakeClient()
    entry = await setup(hass, freezer, c)
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "enabled")}, blocking=True)
    await tick(hass, freezer, 6)
    assert LEA in c.blocked
    with patch("custom_components.bbox_4g.detect_client", return_value=c):
        hass.config_entries.async_update_entry(entry, options={**entry.options, "profiles": []})
        await hass.async_block_till_done()
    await tick(hass, freezer, 2)
    assert LEA not in c.blocked and LEA2 not in c.blocked and OTHER in c.blocked


async def test_options_flow(hass, freezer):
    c = FakeClient()
    entry = await setup(hass, freezer, c, profiles=[], options={"scan_interval": 60, "block_scope": "lan"})
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] == "menu" and r["menu_options"] == ["settings", "device_names", "parental_add"]
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "parental_add"})
    assert r["type"] == "form"
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"name": " ", "macs": [LEA]})
    assert r["errors"] == {"base": "name_required"}
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"name": "Léa", "macs": []})
    assert r["errors"] == {"base": "no_device"}
    with patch("custom_components.bbox_4g.detect_client", return_value=c):
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"name": "Léa", "macs": [LEA, LEA2]})
        await hass.async_block_till_done()
    assert r["type"] == "create_entry"
    assert entry.options["profiles"] == [{"id": "p_lea", "name": "Léa", "macs": [LEA, LEA2]}]
    assert entry.options["block_scope"] == "lan"
    assert ent(hass, "switch", "enabled")
    # second profile cannot reuse Léa's devices
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert "parental_edit" in r["menu_options"]
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "parental_add"})
    choices = list(r["data_schema"].schema["macs"].options)
    assert choices == [OTHER], choices
    # settings step keeps profiles
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "settings"})
    with patch("custom_components.bbox_4g.detect_client", return_value=c):
        r = await hass.config_entries.options.async_configure(
            r["flow_id"], {"scan_interval": 60, "block_scope": "lan", "device_mode": "under_box", "dashboards": []})
        await settle(hass)
    assert entry.options["profiles"]


def manager_of(hass, entry):
    return hass.data["bbox_4g"][entry.entry_id]["parental"]


async def test_alerts_bonus_and_device_override(hass, freezer):
    c = FakeClient()
    entry = await setup(hass, freezer, c)
    m = manager_of(hass, entry)
    seen = []
    m.async_add_event_listener(lambda pid, ev, data: seen.append((pid, ev, data)))
    for k in ("wk_start", "wk_end"):
        await hass.services.async_call("time", "set_value", {"entity_id": ent(hass, "time", k), "time": "07:00:00"}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": dent(hass, "number", LEA, "wk_quota"), "value": 30}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": ent(hass, "number", "warn_min"), "value": 10}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "enabled")}, blocking=True)
    await tick(hass, freezer, 22)  # ~21 min used -> 9 min left: warning
    kinds = [e for _, e, _ in seen]
    assert kinds.count("quota_warning") == 1, kinds
    warn = next(d for _, e, d in seen if e == "quota_warning")
    assert warn["mac"] == LEA and warn["device"] == "Tel Léa" and warn["remaining_min"] <= 10
    await tick(hass, freezer, 12)
    assert [e for _, e, _ in seen].count("quota_reached") == 1
    assert LEA in c.blocked
    left = dent(hass, "sensor", LEA, "remaining")
    assert hass.states.get(left).state == "Épuisé"
    # +15 min button gives the phone more time (only the phone)
    await hass.services.async_call("button", "press", {"entity_id": dent(hass, "button", LEA, "bonus")}, blocking=True)
    await settle(hass)
    assert LEA not in c.blocked
    assert hass.states.get(left).state in ("14 min", "15 min"), hass.states.get(left).state
    assert hass.states.get(left).attributes["bonus_min"] == 15
    # the "almost over" alert can fire again after a bonus
    await tick(hass, freezer, 7)
    assert [e for _, e, _ in seen].count("quota_warning") == 2
    # service: unlimited device -> error; named device ok
    with pytest.raises(Exception):
        await hass.services.async_call("bbox_4g", "add_quota_time", {"profile": "Léa", "device": "PC Léa"}, blocking=True)
    await hass.services.async_call("bbox_4g", "add_quota_time", {"profile": "Léa", "device": "tel léa", "minutes": 30}, blocking=True)
    await settle(hass)
    assert hass.states.get(left).attributes["bonus_min"] == 45
    # per-device override: only the phone is released
    await tick(hass, freezer, 60)
    assert LEA in c.blocked
    await hass.services.async_call("button", "press", {"entity_id": dent(hass, "button", LEA, "override")}, blocking=True)
    await settle(hass)
    assert LEA not in c.blocked
    assert any(e == "override_started" and d["mac"] == LEA for _, e, d in seen)
    # profile status keeps its own code
    assert hass.states.get(ent(hass, "sensor", "status")).attributes["code"] in ("free", "quota")


async def test_holiday_mode(hass, freezer):
    cfg = dict(P.DEFAULTS, enabled=True, holiday=True)
    # Monday 21:30 is a school night normally (21:00): holiday -> weekend window 22:00
    assert P.block_window(cfg, at(2026, 11, 9, 21, 30)) is None
    assert P.block_window(cfg, at(2026, 11, 9, 22, 30)) is not None
    assert P.block_window(dict(cfg, holiday=False), at(2026, 11, 9, 21, 30)) is not None
    d = dict(P.DEVICE_DEFAULTS, wk_quota=60, we_quota=180)
    assert P.device_state(cfg, d, at(2026, 11, 9, 12, 0)).quota_min == 180
    assert P.device_state(dict(cfg, holiday=False), d, at(2026, 11, 9, 12, 0)).quota_min == 60


async def test_holiday_switch_entity(hass, freezer):
    c = FakeClient()
    await setup(hass, freezer, c)
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "enabled")}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": ent(hass, "switch", "holiday")}, blocking=True)
    await tick(hass, freezer, 6)  # 21:01 on a Monday: holiday -> window starts at 22:00
    assert not c.calls
    await tick(hass, freezer, 60)  # 22:01
    assert LEA in c.blocked


async def test_new_device_and_statistics(hass, freezer):
    c = FakeClient()
    entry = await setup(hass, freezer, c)
    m = manager_of(hass, entry)
    seen = []
    m.async_add_event_listener(lambda pid, ev, data: seen.append((pid, ev, data)))
    await m.async_evaluate()
    assert not seen  # existing devices are not "new"
    c.extra.append({"mac": "cc:cc:cc:cc:cc:09", "name": "Inconnu", "ip": "192.168.1.99", "active": True,
                    "link": "Wifi", "ssid": "x", "guest": False, "blocked_lan": False, "blocked_wifi": False,
                    "blocked": False, "acces_internet": True, "internet_allowed": True, "acces_wifi": True})
    await hass.data["bbox_4g"][entry.entry_id]["coordinator"].async_refresh()
    await settle(hass)
    assert [(p, e, d["mac"]) for p, e, d in seen] == [(None, "new_device", "cc:cc:cc:cc:cc:09")]
    await tick(hass, freezer, 2)
    assert len(seen) == 1  # only once
    # entities exist and the daily counter feeds long-term statistics
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("event", "bbox_4g", "new_device")
    assert ent(hass, "event", "alerts")
    used = hass.states.get(dent(hass, "sensor", LEA, "used"))
    assert used.attributes["state_class"] == "total_increasing"


async def test_custom_names(hass, freezer):
    c = FakeClient()
    entry = await setup(hass, freezer, c)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "device_names"})
    assert LEA in r["data_schema"].schema["device"].container
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"device": LEA})
    assert r["step_id"] == "device_names_form"
    with patch("custom_components.bbox_4g.detect_client", return_value=c):
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"name": "Téléphone de Léa"})
        await hass.async_block_till_done()
    assert entry.options["device_names"] == {LEA: "Téléphone de Léa"}
    coord = hass.data["bbox_4g"][entry.entry_id]["coordinator"]
    assert coord.device_by_mac(LEA)["name"] == "Téléphone de Léa"
    assert manager_of(hass, entry).device_label(LEA) == "Téléphone de Léa"
    # empty name resets
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "device_names"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"device": LEA})
    with patch("custom_components.bbox_4g.detect_client", return_value=c):
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"name": ""})
        await hass.async_block_till_done()
    assert entry.options["device_names"] == {}
    assert hass.data["bbox_4g"][entry.entry_id]["coordinator"].device_by_mac(LEA)["name"] == "Tel Léa"


async def test_wifi_timer(hass, freezer):
    c = FakeClient()
    entry = await setup(hass, freezer, c)
    await hass.services.async_call("bbox_4g", "wifi_timer", {"band": "5", "minutes": 30}, blocking=True)
    await settle(hass)
    assert c.wifi == [("5", True)]
    timers = hass.data["bbox_4g"][entry.entry_id]["wifi_timers"]
    assert "5" in timers.timers
    await tick(hass, freezer, 29)
    assert c.wifi == [("5", True)]
    await tick(hass, freezer, 2)
    assert c.wifi == [("5", True), ("5", False)]
    assert not timers.timers
    # cancel
    await hass.services.async_call("bbox_4g", "wifi_timer", {"band": "24", "minutes": 60}, blocking=True)
    await hass.services.async_call("bbox_4g", "wifi_timer", {"band": "24", "minutes": 0}, blocking=True)
    await tick(hass, freezer, 70)
    assert c.wifi[-1] == ("24", True) and ("24", False) not in c.wifi
    # unsupported band (e.g. guest on Huawei): clear error, no timer left behind
    from custom_components.bbox_4g.bbox_api import BboxError
    def boom(band, enabled):
        raise BboxError("no guest radio")
    c.set_wifi = boom
    with pytest.raises(Exception):
        await hass.services.async_call("bbox_4g", "wifi_timer", {"band": "guest", "minutes": 60}, blocking=True)
    assert "guest" not in timers.timers
