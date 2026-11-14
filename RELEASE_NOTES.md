# v2.3.0 – Alerts, bonus time, holiday mode

- **Alerts**: `Alertes` event entity per profile (`quota_warning`, `quota_reached`, `bedtime_start`, `bedtime_end`, `override_started`) and a box-level `Nouvel appareil` event for unknown devices joining the network.
- **+15 min** quota bonus per device (button / `add_quota_time` service); **restore for 1 h per device**; **holiday mode** per profile.
- **Statistics**: the daily "time used" sensors feed long-term statistics.
- **Wi-Fi timer** service (`wifi_timer`), persisted across restarts.
- **Rename devices** from the options.
- Dashboards: new rows for all of the above.

---

# v2.2.0 – Parental control

## New

- **Parental control** by profile (one per child, shared by its devices): bedtime windows (week / weekend, may span midnight), **daily quota set per device** (own limit and counter for each device), and a **"restore for 1 h"** override (button + `bbox_4g.grant_extra_time` service). Profiles are managed in *Configure*.
- Dashboards: labelled box chips, IPv4-only display, unnamed devices shown as "Unknown device", counters and empty-state hints, and a **Parental control** section.
- **Time left today** per device (`1 h 25` / `Épuisé` / `Illimité`) and a small "time left" table in each profile card.
- Tests (`tests/`, run with `pytest-homeassistant-custom-component`).

## Files to update

```
custom_components/bbox_4g/   (whole folder: new parental.py, parental_entities.py, number.py, time.py)
README.md
README_french.md
```

---

# v2.1.2 – Repo cleanup

## What's fixed / improved

- **manifest.json** version set to `2.1.2` (was still `2.0.0`)
- **README** (EN + FR) updated for:
  - `under_box` default mode
  - Independent device switch
  - Sidebar dashboards (no YAML view required)
  - `create_dashboard` service
- **Dashboard titles** are now distinct in the sidebar:
  - `Bbox`
  - `Bbox (auto)`
  - `Bbox (Mushroom)`
- **strings / translations** updated for `device_mode`, `dashboards`, and `create_dashboard`
- Minor cleanup in `dashboard.py` (duplicate logger, unused imports)

## Files to update

```
manifest.json
README.md
README_french.md
custom_components/bbox_4g/dashboard.py
custom_components/bbox_4g/strings.json
custom_components/bbox_4g/translations/en.json
```
