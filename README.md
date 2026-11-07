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

**The router**

| Entity | Purpose |
|---|---|
| `sensor.bbox_clients_connectes` / `sensor.bbox_appareils_connus` | number of connected / known devices |
| `sensor.bbox_reseau_invite` | guest network state |
| `switch.bbox_wifi`, `…_24_ghz`, `…_5_ghz` | Wi-Fi (both bands / per band) |
| `button.bbox_actualiser`, `button.bbox_reconnecter` | force a refresh / reopen the admin session |

**Each known device** is created as a Home Assistant device (identified by its MAC address):

| Entity | Purpose |
|---|---|
| `device_tracker.<device>_suivi` | presence (usable by *People* and *Zones*) |
| `binary_sensor.<device>_presence` | connected / disconnected |
| `switch.<device>_acces_internet` | cuts the device's Internet output (MAC filter) |
| `switch.<device>_wifi` | allows / denies the device on Wi-Fi (SSID black list) |
| `switch.<device>_connexion` | blocking according to the **scope** set in the options |
| `button.<device>_reveiller` | Wake-on-LAN |

> Entity names are currently in French (this is why IDs look like `…_acces_internet`).
> Entities of a device that has disappeared from the router for several polls are removed automatically.

## Options

*Settings → Devices & services → Bbox Bouygues → Configure*

| Option | Default | Purpose |
|---|---|---|
| Polling interval | 60 s | delay between two reads of the router (15–600 s) |
| Device blocking scope | `lan` | `lan` MAC filter · `wifi` SSID black list · `internet` · `all` |
| Write the dashboards | off | generates Lovelace YAML files in `/config/lovelace/` |

## Services

| Service | Arguments |
|---|---|
| `bbox_4g.refresh` | — |
| `bbox_4g.block_device` / `bbox_4g.unblock_device` | `mac` |
| `bbox_4g.set_wifi` | `band` (`all`, `24`, `5`), `enabled` |
| `bbox_4g.set_internet_access` | `mac`, `allowed` |
| `bbox_4g.set_wifi_access` | `mac`, `allowed` |
| `bbox_4g.wake_device` | `mac` |

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

## Dashboards (optional)

With the "Write the dashboards" option, three files are created (`bbox.yaml` native, `bbox-auto.yaml` using *auto-entities*, `bbox-mushroom.yaml` using *Mushroom*).
Add one with **Settings → Dashboards → Add dashboard → YAML view**.

## Troubleshooting

| Symptom | Hint |
|---|---|
| "Unable to reach the router" | check the IP, that Home Assistant is on the same network as the router, and that no web session is open |
| "Credentials rejected" | use the router **admin** password; the router locks the account after several attempts |
| Entities "unavailable" | close the router web interface, then press **Reconnect** |
| Logs | `docker logs homeassistant \| grep -i bbox` |

## Diagnostic tool (optional)

`tools/bbox_cli.py` is a standalone script (not used by Home Assistant) to test the router from a terminal:

```bash
python3 tools/bbox_cli.py --host 192.168.8.1 --password '***' diag
python3 tools/bbox_cli.py --host 192.168.8.1 --password '***' host-table
```

## License

MIT — see [LICENSE](LICENSE). Not affiliated with Bouygues Telecom.
