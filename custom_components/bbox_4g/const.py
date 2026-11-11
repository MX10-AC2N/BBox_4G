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
CONF_DASHBOARD = "write_dashboard"  # legacy bool
CONF_DASHBOARDS = "dashboards"  # list: native, auto, mushroom
CONF_DEVICE_MODE = "device_mode"
CONF_SEPARATE_MACS = "separate_macs"
CONF_PROFILES = "profiles"  # parental control profiles

DEFAULT_HOST = "192.168.1.1"
DEFAULT_USERNAME = "admin"
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_BLOCK_SCOPE = "lan"
DEFAULT_FAMILY = "auto"
DEFAULT_DASHBOARD = False
DEFAULT_DASHBOARDS: list[str] = []  # none by default; user selects
DEFAULT_DEVICE_MODE = "under_box"

DEVICE_MODES = ("under_box", "separate")

#: Identifiants des dashboards proposés
DASHBOARD_NATIVE = "native"
DASHBOARD_AUTO = "auto"
DASHBOARD_MUSHROOM = "mushroom"
DASHBOARD_CHOICES = (DASHBOARD_NATIVE, DASHBOARD_AUTO, DASHBOARD_MUSHROOM)

SCAN_INTERVAL_MIN = 15
SCAN_INTERVAL_MAX = 600

BLOCK_SCOPES = ("lan", "wifi", "internet", "all")
FAMILIES = ("auto", "huawei", "sagemcom")

PLATFORMS = [
    "sensor",
    "binary_sensor",
    "switch",
    "device_tracker",
    "button",
    "number",
    "time",
]

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

SERVICE_REFRESH = "refresh"
SERVICE_BLOCK_DEVICE = "block_device"
SERVICE_UNBLOCK_DEVICE = "unblock_device"
SERVICE_SET_WIFI = "set_wifi"
SERVICE_WAKE_DEVICE = "wake_device"
SERVICE_SET_INTERNET = "set_internet_access"
SERVICE_SET_WIFI_ACCESS = "set_wifi_access"
SERVICE_CREATE_DASHBOARD = "create_dashboard"
SERVICE_GRANT_EXTRA_TIME = "grant_extra_time"
