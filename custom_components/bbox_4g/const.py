"""Constantes de l'intégration « Bbox » (box Bouygues)."""

from __future__ import annotations

DOMAIN = "bbox_4g"

# Clés de configuration (flow utilisateur)
CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USE_TLS = "use_tls"
CONF_VERIFY_SSL = "verify_ssl"
CONF_FAMILY = "family"

# Clés d'options
CONF_SCAN_INTERVAL = "scan_interval"
CONF_BLOCK_SCOPE = "block_scope"
CONF_DASHBOARD = "write_dashboard"
CONF_DEVICE_MODE = "device_mode"
CONF_SEPARATE_MACS = "separate_macs"

DEFAULT_HOST = "192.168.1.1"
DEFAULT_USERNAME = "admin"
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_BLOCK_SCOPE = "lan"
DEFAULT_FAMILY = "auto"
#: Écrire automatiquement les tableaux de bord dans ``/config/lovelace/``.
#: Désactivé par défaut : une intégration ne doit pas modifier les
#: tableaux de bord de l'utilisateur sans son accord.
DEFAULT_DASHBOARD = False
#: Mode par défaut des appareils clients.
#: ``under_box`` : toutes les entités sont rattachées à l'appareil Bbox.
#: ``separate``  : chaque client devient un appareil HA indépendant.
DEFAULT_DEVICE_MODE = "under_box"

#: Modes possibles pour les appareils clients.
DEVICE_MODES = ("under_box", "separate")

#: Périodicité d'interrogation de la box (secondes).
SCAN_INTERVAL_MIN = 15
SCAN_INTERVAL_MAX = 600

#: Portée du blocage d'un appareil :
#: ``lan``      filtre MAC réseau de la box : l'appareil reste joignable
#:              sur le LAN mais perd l'accès à Internet ;
#: ``wifi``     filtre Wi-Fi du SSID (le Wi-Fi lui est refusé) ;
#: ``internet`` commutateur « Accès Internet » du routeur : sur les B528s,
#:              c'est la liste noire Wi-Fi du SSID de l'appareil — c'est
#:              exactement ce que fait l'interface web ;
#: ``all``      les trois à la fois.
BLOCK_SCOPES = ("lan", "wifi", "internet", "all")

#: Familles d'API supportées.
FAMILIES = ("auto", "huawei", "sagemcom")

PLATFORMS = ["sensor", "binary_sensor", "switch", "device_tracker", "button"]

# Attributs exposés sur les entités « appareil »
ATTR_MAC = "mac"
ATTR_DEVICE_NAME = "device_name"
ATTR_IP = "ip_address"
ATTR_LINK = "link"
ATTR_BLOCKED = "blocked"
ATTR_ACTIVE = "active"
ATTR_MODEL = "model"
ATTR_FIRMWARE = "firmware"
ATTR_HOST = "host"
ATTR_LAST_UPDATE = "last_update"

#: Services
SERVICE_REFRESH = "refresh"
SERVICE_BLOCK_DEVICE = "block_device"
SERVICE_UNBLOCK_DEVICE = "unblock_device"
SERVICE_SET_WIFI = "set_wifi"
SERVICE_WAKE_DEVICE = "wake_device"
SERVICE_SET_INTERNET = "set_internet_access"
SERVICE_SET_WIFI_ACCESS = "set_wifi_access"
SERVICE_SET_DEVICE_MODE = "set_device_mode"
