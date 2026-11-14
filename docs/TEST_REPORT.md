# Test report

Test report in French: [TEST_REPORT_french.md](TEST_REPORT_french.md)

✅ **All 18 tests passed**

| | |
|---|---|
| Date (UTC) | 2026-11-14 |
| Integration version | 2.3.0 |
| Home Assistant used for the tests | 2026.2.3 |
| Python | 3.13.18 |
| Duration | 4.3 s |

These tests run automatically on every push ([GitHub Actions](https://github.com/MX10-AC2N/BBox_4G/actions/workflows/validate.yml)). Each one starts a real Home Assistant in memory, loads the integration and talks to a **simulated router**, with a simulated clock so that nights, quotas and timers can be checked in seconds.

## Summary

| Area | Passed |
|---|---|
| Dashboards | 3 / 3 |
| Parental control, services and options | 15 / 15 |

## What is checked

### Dashboards

| Result | What is checked | Test | Time |
|:-:|---|---|--:|
| ✅ | The three dashboards (native, auto, Mushroom) contain the parental-control section, only reference entities that really exist, and every template renders in Home Assistant's real template engine. | `test_dashboards` | 0.37 s |
| ✅ | A device that is cut off appears only under "blocked devices", never also under "connected devices"; the texts and counters of the cards render correctly. | `test_sections_are_exclusive_and_texts_render` | 0.11 s |
| ✅ | The "time left today" table of each profile renders with one line per device ("Illimité" = unlimited, when there is no quota). | `test_left_table_renders` | 0.07 s |

### Parental control, services and options

| Result | What is checked | Test | Time |
|:-:|---|---|--:|
| ✅ | Bedtime windows: a night crossing midnight (21:00 → 07:00) is cut at the right times; Friday and Saturday nights use the weekend window; equal start and end disables the window; daytime windows work. | `test_window_overnight_and_kinds` | 0.01 s |
| ✅ | Time-left texts: 85 minutes show as "1 h 25", 45 as "45 min", no quota as "Illimité" and a used-up quota as "Épuisé". | `test_format_helpers` | 0.01 s |
| ✅ | Rule priority: profile off, then override, then bedtime window, then quota; the quota is per device with its own weekend value; an override lifts the quota. | `test_state_priority_and_quota_kind` | 0.01 s |
| ✅ | A profile creates its entities (switch, schedule times, quota numbers, usage sensors, buttons) and starts switched off ("Désactivé"). | `test_entities_created` | 0.06 s |
| ✅ | At bedtime the profile's devices are cut off; "Restore for 1 h" lifts the cut and it applies again afterwards; the devices are released in the morning; a device blocked by hand stays blocked. | `test_schedule_override_and_manual_block` | 0.42 s |
| ✅ | The grant_extra_time service lifts the restriction for a given time (profile name not case-sensitive), 0 minutes cancels it, and an unknown profile gives an error. | `test_service_grant` | 0.15 s |
| ✅ | Quota per device: only the phone that has a quota is cut off while the PC without quota carries on; the counter stops when the device is cut; the override lifts the quota; midnight resets everything. | `test_quota_is_per_device` | 0.78 s |
| ✅ | Deleting a profile releases the devices it had blocked, but not those blocked by hand. | `test_remove_profile_releases` | 0.18 s |
| ✅ | Options: creating a profile (errors for an empty name or no device), the saved options keep the other settings, a device cannot belong to two profiles, and the general settings step keeps the profiles. | `test_options_flow` | 0.13 s |
| ✅ | Alerts (quota almost over, quota reached, override started) fire once; the +15 min bonus and the add_quota_time service extend today's quota of one device (error for an unlimited device); "Restore for 1 h" works for a single device. | `test_alerts_bonus_and_device_override` | 0.73 s |
| ✅ | Holiday mode (calculation): the weekend window and the weekend quota apply every day. | `test_holiday_mode` | 0.03 s |
| ✅ | Holiday mode switch: with it on, a Monday evening is cut at the weekend time (22:00) and not at the school-night time (21:00). | `test_holiday_switch_entity` | 0.38 s |
| ✅ | A device never seen before triggers one "new device" event (devices already known at start are not reported); the daily usage sensor is set up for long-term statistics. | `test_new_device_and_statistics` | 0.09 s |
| ✅ | Renaming a device in the options replaces the router's name everywhere; an empty name restores it. | `test_custom_names` | 0.11 s |
| ✅ | Wi-Fi timer: the band is switched on, switched off after the delay, can be cancelled, and a band the box does not support gives a clear error with no timer left behind. | `test_wifi_timer` | 0.46 s |

## What these tests do not cover

- The behaviour of a **real router**: login, MAC filtering and Wi-Fi commands are simulated. Only the author's Huawei B528s has been used for real; Sagemcom Bbox routers are not validated.
- The **display in a browser**: dashboards are generated and their templates are rendered, but the cards (including Mushroom and auto-entities) are not drawn on screen.
- **Real time**: the clock is simulated, so very long periods (several days in a row, restarts at a particular moment) are only partly covered.
