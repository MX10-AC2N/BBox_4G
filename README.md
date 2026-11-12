# Bbox 4G Bouygues for Home Assistant

🇫🇷 [Version française](README_french.md)

[![Validate](https://github.com/MX10-AC2N/BBox_4G/actions/workflows/validate.yml/badge.svg)](https://github.com/MX10-AC2N/BBox_4G/actions/workflows/validate.yml)

Custom integration (HACS) for **Bouygues Telecom routers**: see **who is connected**,
**switch Wi-Fi on/off** and **block a device**, all locally — no cloud, no extra Python dependency.

> ⚠️ **Project status**: developed and tested on a **Huawei B528s (Bbox 4G)**.
> Support for Sagemcom Bbox routers (fibre/ADSL) is present in the code but **not validated on real hardware**.
> MAC-filter blocking changes the router configuration: try it first on a device you don't care about.

## Installation (HACS)

1. HACS → ⋮ menu → **Custom repositories**
2. URL: `https://github.com/MX10-AC2N/BBox_4G` — category **Integration**
3. Install **Bbox Bouygues**, then restart Home Assistant
4. **Settings → Devices & services → Add integration → Bbox Bouygues**

| Field | Value |
|---|---|
| Router address | `192.168.8.1` (4G, usually) or `192.168.1.1` (5G); `192.168.1.254` (fibre) |
| Username | `admin` |
| Password | the router **administration** password (printed on the label), not the Wi-Fi password |

Use the **IP address**: `mabbox.bytel.fr` often does not resolve from inside a container.

## Entities

**The router (device « Bbox »)**

| Entity | Purpose |
|---|---|
| `sensor.bbox_clients_connectes` / `sensor.bbox_appareils_connus` | number of connected / known devices |
| `sensor.bbox_reseau_invite` | guest network state |
| `switch.bbox_wifi`, `…_24_ghz`, `…_5_ghz` | Wi-Fi (both bands / per band) |
| `button.bbox_actualiser`, `button.bbox_reconnecter` | force a refresh / reopen the admin session |

**Each known client** creates entities on the Bbox (default) or on its own device:

| Entity | Purpose |
|---|---|
| Presence / Suivi | connected / disconnected (`binary_sensor` + `device_tracker`) |
| Accès Internet | cuts the device's Internet output (MAC filter) |
| Wifi | allows / denies the device on Wi-Fi (SSID black list) |
| Connexion | blocking according to the **scope** set in the options |
| Réveiller | Wake-on-LAN |
| Appareil indépendant | move the client to its own Home Assistant device |

In default **under_box** mode, entity names include the client name  
(e.g. `Presence – iPhone`, `Accès Internet – NAS`) so they stay readable under the single Bbox device.

## Options

*Settings → Devices & services → Bbox Bouygues → Configure*

| Option | Default | Purpose |
|---|---|---|
| Polling interval | 60 s | delay between two reads of the router (15–600 s) |
| Device blocking scope | `lan` | `lan` MAC filter · `wifi` SSID black list · `internet` · `all` |
| Default device mode | `under_box` | `under_box` = all clients under Bbox · `separate` = one HA device per client |
| Dashboards | (none) | select **Native** / **Auto-entities** / **Mushroom** to create them in the sidebar |

## Dashboards (optional)

1. **Configure** the integration → select one or more dashboards → save  
2. **Refresh the browser** (or restart Home Assistant)  
3. The dashboard appears in the sidebar

| Variant | Requirements |
|---|---|
| **Native** | none |
| **Auto-entities** | HACS frontend card *auto-entities* |
| **Mushroom** | HACS *Mushroom* + *auto-entities* |

You can also call:

```yaml
action: bbox_4g.create_dashboard
data:
  dashboards:
    - auto
    - mushroom
```

## Parental control

Group the devices of a child (phone, tablet, console…) into a **profile**. Bedtime windows and the override are shared by the profile; the quota is set per device. Rules are applied by the integration itself, with the router's MAC filter.

*Settings → Devices & services → Bbox Bouygues → Configure → Parental control: add a profile*

Each profile creates a device **Contrôle parental – <name>** with:

| Entity | Purpose |
|---|---|
| Switch **Activé** | turns the rules on or off (a new profile starts **off**) |
| Times **Coupure semaine / week-end – début / fin** | bedtime windows, may span midnight (21:00 → 07:00). Same start and end = no window |
| Numbers **Quota semaine / week-end – <device>** | daily quota in minutes **per device** (each device of the profile has its own), `0` = no quota |
| Sensor **Statut** | `Libre`, `Coupé jusqu'à 07:00`, `Quota atteint`, `Dérogation jusqu'à 18:30`, `Désactivé` |
| Sensors **Temps restant – <device>** | time left today: `1 h 25`, `Épuisé` or `Illimité` (no quota) |
| Sensors **Temps utilisé – <device>** | minutes counted today for each device |
| Button **Rétablir 1 h** | lifts every restriction of the profile for one hour |
| Switch **Mode vacances** | uses the *weekend* window and quota every day |
| Number **Alerte avant fin de quota** | minutes before a quota ends when the alert fires (`0` = off) |
| Buttons **+15 min de quota – <device>** | adds 15 minutes to today's quota of that device (ends at midnight) |
| Buttons **Rétablir 1 h – <device>** | lifts the restrictions of that device only, for one hour |
| Event **Alertes** | `quota_warning`, `quota_reached`, `bedtime_start`, `bedtime_end`, `override_started` (see below) |

How it works:

- Nights **starting on Friday and Saturday** use the *weekend* window; the others use the *week* window. The quota "weekend" applies on Saturday and Sunday.
- The quota is **per device**: each device has its own limit and its own counter, which runs while that device is **connected and allowed**. When a device reaches its quota only that device is cut; the others keep going. A phone that stays connected on Wi-Fi while idle keeps counting: the router does not tell whether the screen is used.
- Priority: switch off → override → bedtime window → quota. The daily counter resets at midnight.
- Rules are checked every minute and after each refresh, so **Home Assistant must be running**; when it is stopped the router keeps the last state.
- The integration only releases devices **it blocked itself**: a device you blocked by hand stays blocked. If you unblock a device during a restriction, it is blocked again within a minute: use **Rétablir 1 h**.
- Devices must keep a fixed MAC address (turn off the "private / random Wi-Fi address" option on the children's phones).

Service: `bbox_4g.grant_extra_time` (`profile`, `minutes` — default 60, `0` cancels).

### Alerts and statistics

- The **Alertes** event entity of each profile fires `quota_warning` (with the minutes left), `quota_reached`, `bedtime_start`, `bedtime_end` and `override_started`. Use it as an automation trigger; the device name, MAC and minutes are in the event data.
- The box has an event entity **Nouvel appareil** (`new_device`) fired when a device never seen before joins the network (name, MAC, IP, link). Existing devices are not reported at first start.
- **Temps utilisé** sensors feed Home Assistant long-term statistics: history per day, week or month in the Energy-style statistics graph.

```yaml
automation:
  - alias: Léa quota almost over
    triggers:
      - trigger: state
        entity_id: event.controle_parental_lea_alertes
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'quota_warning' }}"
    actions:
      - action: notify.mobile_app_my_phone
        data:
          message: "{{ trigger.to_state.attributes.device }}: {{ trigger.to_state.attributes.remaining_min }} min left"
```

### Other features

- **Rename devices**: *Configure → Rename devices*. The name replaces the router's one in entities, dashboards and alerts (empty = router's name).
- **Wi-Fi timer** `bbox_4g.wifi_timer` (`band`: `all`, `24`, `5`, `guest`; `minutes`): switches a band on now and off again after the delay; survives a restart; `0` cancels. The guest network works only where the box exposes it (not on every Huawei model: you get a clear error otherwise).
- Services `bbox_4g.add_quota_time` (`profile`, optional `device`, `minutes`) and `bbox_4g.grant_extra_time` (optional `device`).

## Services

| Service | Arguments |
|---|---|
| `bbox_4g.refresh` | — |
| `bbox_4g.block_device` / `bbox_4g.unblock_device` | `mac` |
| `bbox_4g.set_wifi` | `band` (`all`, `24`, `5`), `enabled` |
| `bbox_4g.set_internet_access` | `mac`, `allowed` |
| `bbox_4g.set_wifi_access` | `mac`, `allowed` |
| `bbox_4g.wake_device` | `mac` |
| `bbox_4g.grant_extra_time` | `profile`, `device` (optional), `minutes` |
| `bbox_4g.add_quota_time` | `profile`, `device` (optional), `minutes` |
| `bbox_4g.wifi_timer` | `band`, `minutes` |
| `bbox_4g.create_dashboard` | `dashboards` (`native`, `auto`, `mushroom`) |

```yaml
action: bbox_4g.block_device
data:
  mac: aa:bb:cc:dd:ee:ff
```

## Good to know

- **Single admin session**: Huawei routers accept only one administration session. If the router's web interface is open, Home Assistant's connection may be refused (code `108003`) and vice versa. Close the web interface or press the **Reconnect** button.
- **Password rejected**: Home Assistant shows a "Fix" notification to re-enter it.
- **MAC filter in allow-list mode**: if the router uses an allow-list ("white list") with authorised devices, the integration refuses to block so it does not cut Internet for everyone.
- **TLS certificate**: self-signed and not verified by default (usual behaviour for Bouygues routers).

## Troubleshooting

| Symptom | Hint |
|---|---|
| "Unable to reach the router" | check the IP, that Home Assistant is on the same network as the router, and that no web session is open |
| "Credentials rejected" | use the router **admin** password; the router locks the account after several attempts |
| Entities "unavailable" | close the router web interface, then press **Reconnect** |
| Dashboard not in sidebar | refresh the browser or restart HA after selecting dashboards in options |
| Logs | `docker logs homeassistant \| grep -i bbox` |

## Diagnostic tool (optional)

`tools/bbox_cli.py` is a standalone script (not used by Home Assistant) to test the router from a terminal:

```bash
python3 tools/bbox_cli.py --host 192.168.8.1 --password '***' diag
python3 tools/bbox_cli.py --host 192.168.8.1 --password '***' host-table
```

## License

MIT — see [LICENSE](LICENSE). Not affiliated with Bouygues Telecom.
