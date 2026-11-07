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
