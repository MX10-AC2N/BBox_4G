"""Client API des box Bouygues.

Client local des box Bouygues (Huawei 4G/5G et Sagemcom).
"""

from __future__ import annotations

import base64
import copy
import functools
import hashlib
import http.cookiejar
import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

__all__ = [
    "BboxError",
    "SagemcomBbox",
    "HuaweiCpe",
    "BboxAuthError",
    "detect_client",
    "wake_on_lan",
]

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

#: Journal du module : le client est aussi utilisé par Home Assistant, où
#: un ``print`` polluerait la sortie standard de l'intégration.
_LOGGER = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
DEFAULT_CACHE = os.path.join(HERE, "cache.json")

DEFAULT_TTL = 30  # secondes de cache pour éviter les connexions en rafale

#: Hôtes IPs usuels des box Bouygues.
CANDIDATE_HOSTS = [
    "192.168.1.254",  # Bbox fibre/ADSL (Sagemcom)
    "192.168.8.1",  # Bbox 4G (Huawei B528/B628/E5180)
    "192.168.1.1",  # Bbox 5G (TCL / Arcadyan)
]

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _ipv4_only(value: str) -> str:
    """Keep the IPv4 of a ``"192.168.1.2;fe80::1"`` style value."""
    for part in re.split(r"[;,\s]+", value or ""):
        if IPV4_RE.match(part):
            return part
    return (value or "").split(";")[0].strip() if ":" not in (value or "").split(";")[0] else ""


#: Endpoints Huawei utiles pour la surveillance du Wi-Fi et des clients.
#: Ils ne sont pas tous exposés par tous les firmwares : le diagnostic les
#: sonde un par un et indique lesquels répondent.
WLAN_PROBE_PATHS = [
    "wlan/wifi-feature-switch",
    "wlan/status-switch-settings",
    "wlan/basic-settings",
    "wlan/wifi-network-switch",
    "wlan/host-list",
    "wlan/station-information",
    "wlan/mac-filter",
    "wlan/multi-macfilter-settings",
    "security/mac-filter",
    "device/information",
]

#: Points d'entrée candidats pour la **table des hôtes** et le **contrôle
#: d'accès Internet**. Sur les B528s, ``lan/HostInfo`` ne porte aucun champ
#: d'accès Internet et ``userhosts/Host`` renvoie 100003 (droits insuffisants)
#: : on sonde donc largement, pour voir si un autre chemin expose le
#: commutateur « Accès Internet ».
HOST_PROBE_PATHS = [
    "lan/HostInfo",
    "lan/Host",
    "lan/hostlist",
    "lan/HostInfoEx",
    "device/HostInfo",
    "userhosts/Host",
    "userhosts/UserHost",
    "userhosts/HostInfo",
    "hosts/Host",
    "homehost/Host",
    "security/acl",
    "security/acls",
    "security/host-access-control",
    "security/host-access",
    "security/device-access",
    "security/access-control",
    "security/internet-access",
    "security/lan-ip-filter",
    "security/white-lan-ip-filter",
    "security/firewall-switch",
    "security/feature-switch",
    "security/url-filter",
    "wan/HostInfo",
    "wan/hosts",
]

#: Points d'entrée candidats pour le **filtre MAC** et le contrôle d'accès.
#: Sur les B528s, ``security/mac-filter`` se lit mais refuse l'écriture :
#: on sonde donc les variantes de chemin.
SECURITY_PROBE_PATHS = [
    "security/mac-filter",
    "security/macfilter",
    "security/mac-filter-switch",
    "security/mac-filter-rule",
    "security/mac-filter-list",
    "security/mac_filter",
    "security/blacklist",
    "security/whitelist",
    "security/black-white-filter",
    "security/filter",
    "security/firewall-switch",
    "security/feature-switch",
    "security/url-filter",
    "security/white-url-filter",
    "security/upnp",
    "security/dmz",
    "security/sip",
    "security/nat",
    "security/virtual-servers",
    "security/special-applications",
    "security/bridgemode",
    "security/lan-ip-filter",
    "security/white-lan-ip-filter",
    "device/reboot",
    "device/autorun_version",
]

#: Points d'entrée où lire l'état du Wi-Fi, par ordre de préférence.
WIFI_STATE_CANDIDATES = [
    # Sur les B528s, c'est le seul endpoint qui donne l'état par bande
    # (<radio><wifienable>) et qui accepte la commande.
    ("wlan/status-switch-settings", None),
    ("wlan/wifi-network-switch", None),
    ("wlan/wifi-feature-switch", None),
    ("wlan/basic-settings", None),
]

#: Noms possibles du champ booléen d'état du Wi-Fi.
WIFI_STATE_FIELDS = (
    "WifiEnable",
    "WifiEnabled",
    "WifiFeatureSwitch",
    "WlanEnable",
    "WifiStatus",
    "WifiStatusSwitch",
    "WifiConnectionStatus",
    "WlanConnectionStatus",
    "WifiBasicStatus",
)

#: Codes d'erreur de l'API Huawei CPE (traduits).
#: Tables surveillées par la commande ``watch-hosts`` : c'est là que le
#: commutateur « Accès Internet » du routeur peut se trouver.
WATCH_TABLES = (
    "lan/HostInfo",
    "userhosts/Host",
    "wlan/host-list",
    "wlan/station-information",
    "wlan/multi-macfilter-settings",
    "wlan/multi-macfilter-settings-ex",
    "security/mac-filter",
    "wlan/mac-filter",
    # Points d'entrée « contrôle d'accès » relevés dans l'API Huawei CPE :
    # ce sont les candidats les plus plausibles pour le commutateur
    # « Accès Internet » par appareil.
    "security/acl",
    "security/acls",
    "security/lan-ip-filter",
    "security/white-lan-ip-filter",
    "security/firewall-switch",
    "security/feature-switch",
    # Variantes « accès Internet par appareil » : rarement documentées,
    # mais c'est là que la box range ce que son interface appelle
    # « Accès Internet ».
    "security/host-access-control",
    "security/host-access",
    "security/device-access",
    "security/access-control",
    "security/internet-access",
    "userhosts/UserHost",
    "lan/Host",
)

#: Champs dont la valeur change tout seuls (horloges, baux DHCP).
VOLATILE_FIELDS = (
    "AssociatedTime",
    "LeaseTime",
    "DHCPLastTime",
    "ConnectedTime",
    "Time",
    "Timestamp",
)

#: Durée (secondes) de mise en cache des informations de la box.
DEVICE_INFO_TTL = 300

#: Nombre d'emplacements MAC par SSID dans ``wlan/multi-macfilter-settings``.
WLAN_MAC_FILTER_SLOTS = 10
#: ``WifiMacFilterStatus`` : 0 = filtre désactivé, 1 = liste blanche
#: (seules les MAC listées se connectent), 2 = liste noire (les MAC listées
#: sont refusées). Documenté par Salamek/huawei-lte-api et Huawei CPE.
WLAN_FILTER_MODE = {"0": "off", "1": "allow", "2": "block"}

#: La session d'administration a expiré ou a été fermée : il suffit de
#: s'authentifier à nouveau (sans redemander le mot de passe).
HUAWEI_SESSION_ERRORS = {"100002", "108003"}
#: Les identifiants sont erronés ou le compte est bloqué : il faut que
#: l'utilisateur saisisse à nouveau le mot de passe.
HUAWEI_AUTH_ERRORS = {"108001", "108002", "108006", "108007"}

HUAWEI_ERRORS = {
    "100002": "session invalide",
    "100003": "accès refusé (droits insuffisants)",
    "108001": "identifiant inconnu",
    "108002": "mot de passe incorrect",
    "108003": "déjà connecté",
    "108004": "échec de modification du mot de passe",
    "108005": "trop d'utilisateurs déjà connectés",
    "108006": "identifiant et/ou mot de passe incorrects",
    "108007": "trop de tentatives de connexion — compte temporairement verrouillé",
    "115002": "mot de passe à modifier",
    "125001": "jeton de session invalide",
    "125002": "jeton de session invalide",
    "125003": "jeton de session invalide",
}


class BboxError(Exception):
    """Erreur générique (réseau, authentification, API)."""


def _synchronized(func):
    """Sérialise les appels à un client.

    Les box Huawei émettent des jetons de vérification **à usage unique** :
    deux appels simultanés peuvent se voler leur jeton et faire échouer la
    requête. Le coordinateur, les services et les commandes passent par le
    même client depuis des fils différents : on les sérialise.
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return func(self, *args, **kwargs)

    return wrapper


# --------------------------------------------------------------------------
# Utilitaires réseau
# --------------------------------------------------------------------------


class BboxAuthError(BboxError):
    """Les identifiants ont été refusés par la box.

    Distincte de :class:`BboxError` : c'est le seul cas qui doit déclencher
    une nouvelle demande de mot de passe à l'utilisateur (re-authentification
    dans Home Assistant).
    """


def _ssl_context(verify: bool = False) -> ssl.SSLContext:
    """Contexte TLS.

    Les box Bouygues servent un certificat auto-signé pour ``mabbox.bytel.fr``
    et utilisent des clés Diffie-Hellman trop faibles (``DH_KEY_TOO_SMALL``).
    On désactive donc la vérification et on abaisse le niveau de sécurité
    OpenSSL (``SECLEVEL=1``), ce qui est le contournement utilisé par les
    intégrations Home Assistant qui fonctionnent avec ces box.
    """
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    # On tente d'abord le niveau de sécurité le plus strict qui accepte
    # les clés faibles des box ; on ne redescend à 0 qu'en cas d'échec
    # (le second ``set_ciphers`` écraserait le premier).
    for niveau in ("DEFAULT:@SECLEVEL=1", "DEFAULT:@SECLEVEL=0"):
        try:
            ctx.set_ciphers(niveau)
            break
        except ssl.SSLError:  # pragma: no cover - vieux OpenSSL
            continue
    return ctx


class _Http:
    """Client HTTP minimal : cookies, TLS assoupli, timeout."""

    def __init__(self, timeout: int = 15, verify: bool = False) -> None:
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=_ssl_context(verify)),
        )
        self.opener.addheaders = [("User-Agent", "Mozilla/5.0 (HomeAssistant-bbox)")]

    def request(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, dict[str, str]]:
        """Effectue une requête et renvoie (code, corps, en-têtes)."""
        req = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, body, dict(resp.headers)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            return err.code, body, dict(err.headers or {})
        except urllib.error.URLError as err:
            raise BboxError(f"Impossible de joindre {url} : {err.reason}") from err
        except (TimeoutError, OSError) as err:
            raise BboxError(f"Erreur réseau vers {url} : {err}") from err

    def cookie_header(self) -> str:
        """Retourne l'en-tête Cookie courant."""
        return "; ".join(f"{c.name}={c.value}" for c in self.jar)


def flatten_xml(text: str) -> dict[str, str]:
    """Aplatit un document XML en un dictionnaire ``chemin -> valeur``.

    Exemple : ``Ssids.Ssid[0].WifiMacFilterMac0 -> "28:0C:50:E7:63:14"``.
    Permet de comparer deux réponses de n'importe quel endpoint, quelle que
    soit sa structure.
    """
    resultat: dict[str, str] = {}
    try:
        root = ET.fromstring(text or "")
    except ET.ParseError:
        return resultat

    def walk(element: ET.Element, prefix: str) -> None:
        enfants = list(element)
        if not enfants:
            resultat[prefix or element.tag] = (element.text or "").strip()
            return
        comptes: dict[str, int] = {}
        for enfant in enfants:
            comptes[enfant.tag] = comptes.get(enfant.tag, 0) + 1
        vus: dict[str, int] = {}
        for enfant in enfants:
            rang = vus.get(enfant.tag, 0)
            vus[enfant.tag] = rang + 1
            suffixe = f"[{rang}]" if comptes[enfant.tag] > 1 else ""
            chemin = (
                f"{prefix}.{enfant.tag}{suffixe}"
                if prefix
                else f"{enfant.tag}{suffixe}"
            )
            walk(enfant, chemin)

    walk(root, "")
    return resultat


def _norm_mac(mac: str) -> str:
    """Normalise une adresse MAC en minuscules, séparateur ':'."""
    mac = (mac or "").strip().replace("-", ":").lower()
    return mac


def _norm_host(mac: str) -> str:
    """Normalise une adresse MAC en format compact (sans séparateur)."""
    return _norm_mac(mac).replace(":", "")


# --------------------------------------------------------------------------
# Client Sagemcom / Bbox fibre
# --------------------------------------------------------------------------


class SagemcomBbox:
    """API locale ``/api/v1`` des Bbox Sagemcom (fibre et ADSL)."""

    family = "sagemcom"
    name = "Bbox Sagemcom"

    def __init__(
        self,
        host: str,
        password: str,
        use_tls: bool = True,
        verify: bool = False,
        timeout: int = 15,
    ) -> None:
        self.host = host
        self.password = password
        self.use_tls = use_tls
        self.http = _Http(timeout=timeout, verify=verify)
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self.model = "Bbox"
        # Sérialise les appels (le coordinateur et les services partagent
        # le même client).
        self._lock = threading.RLock()

    # -- bas niveau --------------------------------------------------------

    @property
    def base_url(self) -> str:
        """URL de base de l'API."""
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{self.host}/api/v1"

    def _call(
        self,
        path: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
    ) -> Any:
        """Appelle l'API et renvoie le JSON décodé (ou le texte brut)."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        query: dict[str, Any] = {}
        if path not in ("login", "device/token"):
            query["btoken"] = self.token()
        if params:
            query.update(params)
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {}
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        status, body, _ = self.http.request(url, method=method, data=data, headers=headers)
        if status in (401, 403):
            # Erreur d'identifiants : elle doit déclencher la demande de
            # nouveau mot de passe, pas une simple panne réseau.
            raise BboxAuthError(
                f"Authentification refusée par la box (HTTP {status})"
            )
        if status >= 400:
            raise BboxError(f"Erreur HTTP {status} sur {path} : {body[:200]}")
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    # -- authentification ---------------------------------------------------

    @_synchronized
    def login(self) -> None:
        """Ouvre une session (cookie BBOX_ID)."""
        self._call("login", method="POST", form={"password": self.password, "remember": 1})
        self._token = None

    @_synchronized
    def reconnect(self) -> None:
        """Rouvre une session sur la box.

        Équivalent du bouton « Reconnecter » de l'intégration : indispensable
        quand l'interface web de la box a été ouverte (une seule session
        d'administration à la fois).
        """
        self.login()

    def token(self) -> str:
        """Récupère (et met en cache) le ``btoken`` d'écriture."""
        if self._token and time.time() < self._token_expiry - 5:
            return self._token
        data = self._call("device/token")
        try:
            device = data[0]["device"]
            self._token = device["token"]
            self._token_expiry = self._parse_expiry(device)
        except (KeyError, IndexError, TypeError, ValueError) as err:
            raise BboxError(f"Réponse de /device/token inattendue : {data}") from err
        return self._token

    @staticmethod
    def _parse_expiry(device: dict[str, Any]) -> float:
        """Calcule l'heure d'expiration du btoken.

        L'API renvoie soit une durée de vie, soit une date ISO 8601.
        """
        expires = device.get("expires")
        if expires:
            try:
                from datetime import datetime

                moment = datetime.fromisoformat(str(expires))
                if moment.tzinfo is None:
                    from datetime import timezone

                    moment = moment.replace(tzinfo=timezone.utc)
                return moment.timestamp()
            except ValueError:
                pass
        try:
            return time.time() + float(device.get("ttl", 120))
        except (TypeError, ValueError):
            return time.time() + 120

    @_synchronized
    def ensure_login(self) -> None:
        """Vérifie la session, se reconnecte si besoin."""
        try:
            self.token()
        except BboxError:
            self.login()
            self.token()

    # -- lecture -----------------------------------------------------------

    def device_info(self) -> dict[str, Any]:
        """Informations matérielles de la box."""
        try:
            data = self._call("device")
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return data[0].get("device", {})
            if isinstance(data, dict):
                return data.get("device", {})
        except BboxError:
            pass
        return {}

    @_synchronized
    def hosts(self) -> list[dict[str, Any]]:
        """Liste brute des clients connus de la box."""
        data = self._call("hosts")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        if isinstance(data, dict):
            hosts = data.get("hosts", {})
            if isinstance(hosts, dict):
                return list(hosts.get("list", []))
            if isinstance(hosts, list):
                return list(hosts)
        return []

    @_synchronized
    def wireless(self) -> dict[str, Any]:
        """État de la radio Wi-Fi."""
        data = self._call("wireless")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        if isinstance(data, dict):
            return data.get("wireless", {})
        return {}

    # -- écriture ----------------------------------------------------------

    # -- filtre MAC au niveau Wi-Fi ----------------------------------------

    def wlan_blocked_macs(self, ssid_index: str = "0") -> list[str]:
        """MAC bloquées par le filtre Wi-Fi du SSID demandé."""
        for ssid in self.wlan_mac_filters():
            if ssid["index"] == str(ssid_index):
                return [d["mac"] for d in ssid["devices"]]
        return []

    @_synchronized
    def set_device_blocked(
        self, mac: str, blocked: bool, scope: str = "lan"
    ) -> None:
        """Active (ou désactive) le contrôle parental d'un client.

        Sur les box Sagemcom, le contrôle parental est déjà propre au Wi-Fi :
        la portée demandée n'a donc pas d'effet particulier.

        C'est l'équivalent de « bloquer l'accès de cet appareil au réseau »
        dans l'interface de la box : l'appareil est déconnecté du Wi-Fi.
        """
        self.ensure_login()
        self._call(
            "parentalcontrol/hosts",
            method="PUT",
            # ``enable`` à 1 signifie « contrôle parental actif » : l'hôte
            # est bloqué. C'est la lecture inverse qui était appliquée
            # jusqu'ici (l'état affiché ne correspondait pas à l'action).
            form={"macaddress": _norm_mac(mac), "enable": 1 if blocked else 0},
        )

    @_synchronized
    def set_wifi(self, band: str, enabled: bool) -> None:
        """Allume ou éteint le Wi-Fi.

        ``band`` vaut ``24``, ``5``, ``guest`` ou ``all``.
        """
        self.ensure_login()
        state = 1 if enabled else 0
        if band == "all":
            self._call("wireless", method="PUT", params={"radio.enable": state})
        elif band == "24":
            self._call("wireless/24", method="PUT", params={"radio.enable": state})
        elif band == "5":
            self._call("wireless/5", method="PUT", params={"radio.enable": state})
        elif band == "guest":
            self._call("wireless/guestenable", method="PUT", params={"radio.enable": state})
        else:
            raise BboxError(f"Bande Wi-Fi inconnue : {band}")

    def describe(self) -> dict[str, Any]:
        """Informations d'identification fournies par la box."""
        info = self.device_info()
        return {
            "model": info.get("modelname", ""),
            "firmware": (info.get("main", {}) or {}).get("version", ""),
        }

    # -- état normalisé ----------------------------------------------------

    @_synchronized
    def state(self) -> dict[str, Any]:
        """Retourne l'état normalisé de la box."""
        self.ensure_login()
        hosts_raw = self.hosts()
        wifi_raw = self.wireless()
        radio = wifi_raw.get("radio", {}) if isinstance(wifi_raw, dict) else {}

        def _radio_on(key: str) -> bool:
            """Lit l'état d'une radio, que la valeur soit un dict ou un booléen."""
            value = radio.get(key)
            if isinstance(value, dict):
                return bool(value.get("enable", 0))
            return bool(value)

        wifi = {
            "24": _radio_on("24"),
            "5": _radio_on("5"),
            "guest": _radio_on("guest"),
        }
        devices = []
        for host in hosts_raw:
            mac = _norm_mac(host.get("macaddress", ""))
            if not mac:
                continue
            name = (
                host.get("userfriendlyname")
                or host.get("hostname")
                or host.get("devicetype")
                or mac
            )
            parental = host.get("parentalcontrol", {}) or {}
            devices.append(
                {
                    "mac": mac,
                    "name": str(name),
                    "ip": _ipv4_only(host.get("ipaddress", "")),
                    "active": int(host.get("active", 0) or 0) == 1,
                    "link": host.get("link", ""),
                    "guest": bool(host.get("guest", 0)),
                    "blocked": bool(parental.get("enable", 0)),
                }
            )
        devices.sort(key=lambda d: (not d["active"], d["name"].lower()))
        info = self.device_info()
        model = info.get("modelname") or info.get("producttype") or "Bbox"
        return {
            "ok": True,
            "family": self.family,
            "host": self.host,
            "model": model,
            "firmware": (info.get("main", {}) or {}).get("version", ""),
            "wifi": wifi,
            "nb_connectes": sum(1 for d in devices if d["active"]),
            "nb_total": len(devices),
            "devices": devices,
            "timestamp": time.time(),
        }


# --------------------------------------------------------------------------
# Client Huawei CPE (Bbox 4G / 5G)
# --------------------------------------------------------------------------


class HuaweiCpe:
    """API locale XML ``/api/...`` des box 4G/5G Bouygues (Huawei, Alcatel…)."""

    family = "huawei"
    name = "Bbox 4G/5G (Huawei CPE)"

    def __init__(
        self,
        host: str,
        password: str,
        username: str = "admin",
        use_tls: bool = False,
        verify: bool = False,
        timeout: int = 15,
        session_file: str | None = None,
    ) -> None:
        self.host = host
        self.password = password
        self.username = username
        self.use_tls = use_tls
        self.session_file = session_file
        self.http = _Http(timeout=timeout, verify=verify)
        self.model = "Huawei CPE"
        self._ses_info = ""
        self._saved_session: dict[str, Any] = {}
        self._tok_info = ""       # dernier jeton reçu
        self._ses_tok_info = ""   # jeton d'origine (SesTokInfo)
        # Les box Huawei émettent des jetons de vérification à usage unique :
        # chaque réponse en fournit de nouveaux et chaque requête en consomme
        # un. Il faut donc conserver une réserve, pas un seul jeton.
        self._tokens: list[str] = []
        # Paires de jetons (firmwares qui en renvoient deux par réponse).
        self._token_pairs: list[tuple[str, str]] = []
        # Session fraîche (SesInfo, TokInfo) réservée aux écritures.
        self._session_fraiche: tuple[str, str] | None = None
        # Recette POST qui a marché (type de contenu, nom de variante).
        self._post_recette: tuple[str, str] | None = None
        # Politique de filtre MAC d'origine (0/1) : elle est restaurée quand
        # le dernier appareil bloqué est libéré.
        self._mac_original_policy: str | None = None
        # Garde-fou : évite la récursion infinie entre l'erreur « session
        # expirée » et la tentative de re-connexion.
        self._reauthenticating = False
        # Table des hôtes réellement utilisée (lan/HostInfo ou userhosts/Host)
        self._hosts_endpoint = ""
        self._password_type = 4
        # Cache des informations de la box (modèle, firmware) : elles ne
        # changent pas et coûteraient une requête à chaque cycle.
        self._device_info_cache: dict[str, Any] | None = None
        self._device_info_cache_at: float = 0.0
        # Sérialise les accès : les jetons de vérification Huawei sont à
        # usage unique, deux appels concurrents se les voleraient.
        self._lock = threading.RLock()

    # -- bas niveau --------------------------------------------------------

    @property
    def base_url(self) -> str:
        """URL de base de l'API."""
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{self.host}"

    def _headers(self, token: str | None = None) -> dict[str, str]:
        """En-têtes communs aux requêtes de l'API Huawei."""
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }
        if self._ses_info:
            headers["Cookie"] = self._ses_info
        if token:
            headers["__RequestVerificationToken"] = token
        return headers

    def _current_token(self) -> str:
        """Jeton de vérification à utiliser pour la requête courante.

        Les jetons sont consommés : on prend le plus ancien de la réserve
        (c'est le plus proche de sa péremption) et on ne le réutilise que
        s'il est le dernier.
        """
        if len(self._tokens) > 1:
            return self._tokens.pop(0)
        if self._tokens:
            return self._tokens[0]
        if self._tok_info:
            return self._tok_info
        return self._ses_tok_info

    def _is_token_error(self, body: str) -> bool:
        """La réponse indique-t-elle un problème de jeton ou de session ?"""
        code, _ = self._parse_error(body)
        return code in ("100002", "100003", "125001", "125002", "125003")

    def _refresh_session(self) -> None:
        """Récupère un nouveau jeu de jetons (la session est conservée)."""
        try:
            url = f"{self.base_url}/api/webserver/SesTokInfo"
            status, body, headers = self.http.request(
                url, method="GET", headers=self._headers()
            )
            if status < 400:
                self._absorb_tokens(headers)
                try:
                    root = ET.fromstring(body)
                    token = (root.findtext(".//TokInfo") or "").strip()
                    if token:
                        self._ses_tok_info = token
                        self._tokens.insert(0, token)
                except ET.ParseError:
                    pass
        except BboxError:
            pass

    def _api_get(self, path: str, _retry: bool = True) -> str:
        """GET XML sur l'API Huawei."""
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        token = self._current_token()
        status, body, headers = self.http.request(
            url, method="GET", headers=self._headers(token)
        )
        self._absorb_tokens(headers)
        if status >= 400:
            raise BboxError(f"Erreur HTTP {status} sur {path}")
        if _retry and self._is_token_error(body):
            code, _ = self._parse_error(body)
            if code in HUAWEI_SESSION_ERRORS:
                # Session expirée : on s'authentifie à nouveau avec le mot de
                # passe enregistré, puis on réessaie une seule fois.
                self._reauthenticate()
                return self._api_get(path, _retry=False)
            # Jeton périmé : on en récupère de nouveaux et on réessaie.
            self._refresh_session()
            return self._api_get(path, _retry=False)
        return body

    def _session_post(self) -> tuple[str, str] | None:
        """Session **fraîche** destinée aux écritures (SesInfo + TokInfo).

        Sur les B528s, l'interface web ne réutilise pas la session des
        lectures pour les écritures : elle appelle
        ``api/webserver/SesTokInfo`` juste avant, puis envoie le
        ``SesInfo`` comme cookie et le ``TokInfo`` comme jeton. Reprendre
        la session des GET donne « 125002 — jeton CSRF invalide ».
        """
        url = f"{self.base_url}/api/webserver/SesTokInfo"
        session_avant = self._ses_info
        try:
            status, body, headers = self.http.request(
                url, method="GET", headers=self._headers()
            )
        except BboxError:
            return None
        finally:
            # La session d'écriture ne remplace PAS la session des
            # lectures : les deux coexistent (comme dans l'interface web).
            self._ses_info = session_avant
        if status >= 400:
            return None
        self._absorb_tokens(headers)
        self._ses_info = session_avant
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return None
        ses = (root.findtext(".//SesInfo") or "").strip()
        tok = (root.findtext(".//TokInfo") or "").strip()
        if not ses or not tok:
            return None
        return ses, tok

    def _noms_variantes(self, ecriture: bool = True) -> list[str]:
        """Noms des variantes d'en-têtes disponibles pour un POST.

        La session fraîche n'est proposée que pour les **écritures de
        configuration** : la connexion et la déconnexion utilisent la
        session d'administration.
        """
        noms: list[str] = []
        if ecriture:
            noms.append("fraiche")
        if self._token_pairs:
            noms.append("paire")
            noms.append("paire-#")
        if self._current_token_disponible():
            noms.append("simple")
        return noms

    def _current_token_disponible(self) -> bool:
        """Y a-t-il un jeton à envoyer ? (sans le consommer)."""
        return bool(self._tokens or self._tok_info or self._ses_tok_info)

    def _jeton_pour(self, nom: str) -> dict[str, str] | None:
        """En-têtes de la variante demandée, calculés à l'instant."""
        if nom == "fraiche":
            fresh = self._session_post()
            if fresh is None:
                return None
            return {"Cookie": fresh[0], "__RequestVerificationToken": fresh[1]}
        if nom == "paire" and self._token_pairs:
            premier, second = self._token_pairs[-1]
            return {
                "__RequestVerificationTokenone": premier,
                "__RequestVerificationTokentwo": second,
            }
        if nom == "paire-#" and self._token_pairs:
            premier, second = self._token_pairs[-1]
            return {"__RequestVerificationToken": f"{premier}#{second}"}
        token = self._current_token()
        return {"__RequestVerificationToken": token} if token else {}

    def _variantes_jeton(self) -> list[tuple[str, dict[str, str]]]:
        """Variantes d'en-têtes (cookie + jeton) à essayer sur un POST.

        Chaque variante porte un **nom** : la recette qui a fonctionné est
        mémorisée par ce nom, car la liste des variantes disponibles change
        d'un appel à l'autre (les jetons sont consommés).

        Sur les B528s, l'interface web utilise une **session fraîche** pour
        les écritures : elle appelle ``webserver/SesTokInfo`` juste avant,
        puis envoie le ``SesInfo`` comme cookie et le ``TokInfo`` comme
        jeton. C'est la première variante proposée, car c'est celle qui
        marche sur ce firmware.
        """
        variantes: list[tuple[str, dict[str, str]]] = []
        # 1. Session fraîche dédiée aux écritures (méthode de l'UI B528s).
        variantes.append(("fraiche", {}))
        # 2. Paire de jetons (firmwares qui en renvoient deux).
        if self._token_pairs:
            premier, second = self._token_pairs[-1]
            variantes.append(
                (
                    "paire",
                    {
                        "__RequestVerificationTokenone": premier,
                        "__RequestVerificationTokentwo": second,
                    },
                )
            )
            variantes.append(
                ("paire-#", {"__RequestVerificationToken": f"{premier}#{second}"})
            )
        # 3. Jeton simple (méthode historique).
        token = self._current_token()
        if token:
            variantes.append(("simple", {"__RequestVerificationToken": token}))
        return variantes

    def _api_post(
        self,
        path: str,
        xml_body: str,
        _retry: bool = True,
        ecriture: bool = True,
    ) -> str:
        """POST XML sur l'API Huawei.

        On essaie plusieurs combinaisons de **type de contenu** et de
        **jeton** : la box répond « 125002 » (jeton CSRF invalide) quand
        la requête n'est pas présentée comme elle l'attend, et un code
        d'erreur dans le corps d'une réponse HTTP 200 ne doit pas faire
        abandonner les autres formes.
        """
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        dernier = ""
        types = ("application/xml", "text/xml", "application/x-www-form-urlencoded")
        # Si une écriture a déjà marché, on réutilise sa recette.
        if self._post_recette is not None:
            type_connu, variante_connue = self._post_recette
            types = (type_connu,)
        noms = self._noms_variantes(ecriture)
        if self._post_recette is not None and variante_connue not in noms:
            # La recette mémorisée ne s'applique pas ici : on repart de zéro.
            self._post_recette = None
            variante_connue = None
            types = (
                "application/xml",
                "text/xml",
                "application/x-www-form-urlencoded",
            )
        if self._post_recette is not None:
            couples = [(types[0], variante_connue)]
        else:
            couples = [(t, n) for t in types for n in noms]
        for content_type, nom in couples:
            if self._post_recette is not None and nom != variante_connue:
                continue
            jeton = self._jeton_pour(nom)
            if jeton is None:
                dernier = "session fraîche indisponible"
                continue
            headers = self._headers()
            # La variante peut imposer son propre cookie (session fraîche).
            if "Cookie" in jeton:
                headers["Cookie"] = jeton["Cookie"]
            headers.update({k: v for k, v in jeton.items() if k != "Cookie"})
            headers["Content-Type"] = content_type
            status, body, resp_headers = self.http.request(
                url, method="POST", data=xml_body.encode(), headers=headers
            )
            self._absorb_tokens(resp_headers)
            if status >= 400:
                dernier = f"HTTP {status} — {body[:160]}"
                continue
            if self._is_token_error(body):
                # Jeton refusé : on passe à la variante suivante.
                dernier = body[:160]
                continue
            # Réussite : on mémorise la recette.
            if ecriture:
                self._post_recette = (content_type, nom)
            return body
        if _retry:
            code, _message = self._parse_error(dernier)
            if code in HUAWEI_SESSION_ERRORS:
                self._reauthenticate()
                return self._api_post(path, xml_body, _retry=False)
            self._refresh_session()
            self._session_fraiche = self._session_post()
            return self._api_post(path, xml_body, _retry=False)
        raise BboxError(
            f"La box refuse l'écriture sur {path} : {dernier or 'réponse vide'}"
        )

    def _absorb_tokens(self, headers: dict[str, str]) -> None:
        """Mémorise les jetons et la session renvoyés dans les en-têtes.

        La box renvoie un ou deux nouveaux jetons par réponse
        (``__RequestVerificationTokenone`` / ``...two``). Les firmwares
        plus anciens utilisent un en-tête unique dont les valeurs sont
        séparées par « # » : chacune est valable pour une seule requête.
        """
        lowered = {k.lower(): v for k, v in headers.items()}
        tokens: list[str] = []
        first = lowered.get("__requestverificationtokenone")
        second = lowered.get("__requestverificationtokentwo")
        if first:
            tokens.append(first.strip())
            if second:
                tokens.append(second.strip())
                self._token_pairs.append((first.strip(), second.strip()))
                if len(self._token_pairs) > 4:
                    self._token_pairs.pop(0)
        else:
            legacy = lowered.get("__requestverificationtoken")
            if legacy:
                tokens.extend(part.strip() for part in legacy.split("#") if part.strip())
        for token in tokens:
            self._tokens.append(token)
            self._tok_info = token
        cookie = lowered.get("set-cookie", "")
        if cookie and "SessionID" in cookie:
            self._ses_info = cookie.split(";")[0]

    # -- persistance de la session -----------------------------------------

    def _save_session(self) -> None:
        """Enregistre la session (cookie + jeton) pour la réutiliser.

        Les box Huawei n'acceptent qu'une seule session d'administration
        ouverte à la fois : sans cela, chaque nouvelle commande provoquerait
        un refus « 108003 — utilisateur déjà connecté ».
        """
        if not self.session_file:
            return
        data = {
            "ses_info": self._ses_info,
            "tok_info": self._tok_info,
            "ses_tok_info": self._ses_tok_info,
            "tokens": list(self._tokens),
            "mac_original_policy": self._mac_original_policy,
            "timestamp": time.time(),
        }
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.session_file)), exist_ok=True)
            with open(self.session_file, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.chmod(self.session_file, 0o600)
        except OSError:  # pragma: no cover
            pass

    def _load_session(self) -> bool:
        """Restaure une session enregistrée si elle est encore valide."""
        if not self.session_file or not os.path.exists(self.session_file):
            return False
        try:
            with open(self.session_file, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return False
        # On garde la session en mémoire même si elle est expirée : elle
        # pourra servir à fermer une session restée ouverte sur la box.
        self._saved_session = data
        if time.time() - float(data.get("timestamp", 0)) > 900:
            return False
        self._ses_info = data.get("ses_info", "")
        self._tok_info = data.get("tok_info", "")
        self._ses_tok_info = data.get("ses_tok_info", "")
        self._tokens = list(data.get("tokens", []))
        self._mac_original_policy = data.get("mac_original_policy") or None
        if not self._ses_info:
            return False
        # Vérification : la session doit être acceptée par la box.
        try:
            info = self._parse_state_login(self._api_get("user/state-login"))
        except BboxError:
            self._clear_session()
            return False
        if info.get("state") == "0":
            return True
        self._clear_session()
        return False

    def _load_saved_session(self) -> bool:
        """Charge le fichier de session sans interroger la box.

        Utile pour fermer une session restée ouverte : on ne veut pas
        avoir besoin d'une connexion valide pour y mettre fin.
        """
        if not self.session_file or not os.path.exists(self.session_file):
            return False
        try:
            with open(self.session_file, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return False
        self._saved_session = data
        self._ses_info = data.get("ses_info", "")
        self._tok_info = data.get("tok_info", "")
        self._ses_tok_info = data.get("ses_tok_info", "")
        self._tokens = list(data.get("tokens", []))
        self._mac_original_policy = data.get("mac_original_policy") or None
        return bool(self._ses_info)

    def _clear_session(self) -> None:
        """Oublie la session enregistrée."""
        if self.session_file and os.path.exists(self.session_file):
            try:
                os.remove(self.session_file)
            except OSError:  # pragma: no cover
                pass

    @_synchronized
    def logout(self) -> None:
        """Ferme la session d'administration sur la box.

        Utile pour libérer la box : tant qu'une session est ouverte, le
        navigateur ne peut plus se connecter et les autres clients sont
        refusés (code 108003). On tente successivement la session courante
        puis la session éventuellement enregistrée sur le disque.
        """
        body = '<?xml version="1.0" encoding="UTF-8"?><request><Logout>1</Logout></request>'
        if not self._saved_session:
            # La session enregistrée peut être périmée : on la charge quand
            # même, c'est elle qui permet de fermer la session ouverte sur la
            # box.
            self._load_saved_session()
        for cookie in (self._ses_info, self._saved_session.get("ses_info", "")):
            if not cookie:
                continue
            previous = self._ses_info
            self._ses_info = cookie
            try:
                self._api_post("user/logout", body, ecriture=False)
            except BboxError:
                pass
            self._ses_info = previous
        self._clear_session()

    # -- authentification ---------------------------------------------------

    def _fetch_session(self) -> None:
        """Récupère ``SesInfo`` et ``TokInfo``."""
        url = f"{self.base_url}/api/webserver/SesTokInfo"
        status, body, headers = self.http.request(url, method="GET", headers=self._headers())
        if status >= 400:
            raise BboxError(f"SesTokInfo : erreur HTTP {status}")
        self._absorb_tokens(headers)
        try:
            root = ET.fromstring(body)
        except ET.ParseError as err:
            raise BboxError(f"SesTokInfo : réponse illisible ({body[:120]})") from err
        ses = root.findtext(".//SesInfo") or ""
        tok = root.findtext(".//TokInfo") or ""
        if not ses:
            raise BboxError("SesTokInfo : session introuvable")
        self._ses_info = ses.strip()
        self._ses_tok_info = tok.strip()

    def _parse_state_login(self, xml_text: str) -> dict[str, Any]:
        """Extrait l'état de connexion déclaré par la box."""
        info: dict[str, Any] = {}
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return info
        for key, tag in (
            ("state", "State"),
            ("username", "Username"),
            ("password_type", "password_type"),
            ("extern_password_type", "extern_password_type"),
        ):
            value = root.findtext(f".//{tag}")
            if value is not None and value.strip():
                info[key] = value.strip()
        return info

    def _encode_password(self, token: str, hex_digest: bool = True) -> str:
        """Encode le mot de passe selon le schéma Huawei (password_type 4).

        Formule canonique (firmware HiLink) :
        ``base64(sha256_hex(user + base64(sha256_hex(mdp)) + token))``.
        Certains firmwares utilize le condensat binaire au lieu de l'hexadécimal :
        les deux variantes sont essayées.
        """
        if hex_digest:
            pw_hash = hashlib.sha256(self.password.encode()).hexdigest()
        else:
            pw_hash = base64.b64encode(hashlib.sha256(self.password.encode()).digest()).decode()
        pw_b64 = base64.b64encode(pw_hash.encode()).decode()
        digest = hashlib.sha256(f"{self.username}{pw_b64}{token}".encode())
        if hex_digest:
            return base64.b64encode(digest.hexdigest().encode()).decode()
        return base64.b64encode(digest.digest()).decode()

    def _login_body(self, password: str, password_type: int) -> str:
        """Construit le corps XML de la requête de connexion."""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<request>"
            f"<Username>{self.username}</Username>"
            f"<Password>{password}</Password>"
            f"<password_type>{password_type}</password_type>"
            "</request>"
        )

    @_synchronized
    def login(self) -> None:
        """Ouvre une session sur la box Huawei.

        Les tentatives sont volontairement limitées : la box verrouille le
        compte après quelques échecs (code 108007).
        """
        self._fetch_session()

        info = self._parse_state_login(self._api_get("user/state-login"))
        if info.get("username"):
            self.username = info["username"]
        try:
            self._password_type = int(
                info.get("password_type") or info.get("extern_password_type") or 4
            )
        except ValueError:
            self._password_type = 4
        # « State » vaut 0 quand une session est déjà active.
        if info.get("state") == "0":
            return

        last_error = ""
        # 1) Tentative canonique (jeton de SesTokInfo, empreinte hexadécimale).
        candidates = [
            (self._ses_tok_info or self._tok_info, self._password_type, True),
        ]
        # 2) Variantes de jeton : uniquement en cas de problème de jeton.
        for token in self._token_candidates():
            if token and token != candidates[0][0]:
                candidates.append((token, self._password_type, True))
        # 3) Variante d'empreinte (certains firmwares calculent le
        #    condensat en binaire et non en hexadécimal).
        candidates.append((candidates[0][0], self._password_type, False))
        if self._password_type != 4:
            candidates.append((candidates[0][0], 4, True))
            candidates.append((candidates[0][0], 0, True))

        for index, (token, ptype, hex_digest) in enumerate(candidates):
            retry = 0
            while True:
                if ptype == 4:
                    encoded = self._encode_password(token, hex_digest=hex_digest)
                else:
                    encoded = base64.b64encode(self.password.encode()).decode()
                body = self._api_post(
                    "user/login",
                    self._login_body(encoded, ptype),
                    ecriture=False,
                )
                if self._is_ok(body):
                    self._save_session()
                    return
                code, message = self._parse_error(body)
                last_error = f"[{code}] {message}" if code else message

                if code == "108003":
                    # Une session d'administration est déjà ouverte sur la box.
                    if retry == 0:
                        # On tente de la fermer, puis on réessaie avec la
                        # même variante de hachage. La session à fermer peut
                        # venir d'un autre programme : on charge donc la
                        # session enregistrée sur le disque si besoin.
                        if not self._saved_session:
                            try:
                                self._load_session()
                            except BboxError:
                                pass
                        self.logout()
                        time.sleep(2)
                        retry += 1
                        continue
                    raise BboxError(
                        f"Connexion refusée par la box ({last_error}). "
                        "Une session d'administration est déjà ouverte sur la "
                        "box : fermez l'interface web (http://" + self.host + ") "
                        "puis attendez une minute, ou lancez "
                        "« python3 /config/bbox/bbox_ha.py logout »."
                    )
                if code in ("108001", "108002", "108006"):
                    # Identifiants refusés : inutile (et risqué) d'insister.
                    raise BboxAuthError(
                        f"Connexion refusée par la box ({last_error}). "
                        "L'identifiant « "
                        + self.username
                        + " » ou le mot de passe est incorrect. Vérifiez "
                        "l'étiquette de la box (identifiant d'administration) "
                        "ou réinitialisez le mot de passe d'administration "
                        "depuis l'interface web."
                    )
                if code == "108007":
                    raise BboxError(
                        f"Connexion refusée par la box ({last_error}). "
                        "Trop de tentatives : attendez quelques minutes avant "
                        "de réessayer, la box verrouille temporairement le "
                        "compte."
                    )
                # Autre erreur (jeton périmé, par exemple) : variante suivante.
                break
            if index >= len(candidates) - 1:
                break
        code, message = self._parse_error(last_error) if last_error else ("", "")
        if code in HUAWEI_AUTH_ERRORS:
            raise BboxAuthError(
                f"Identifiants refusés par la box ({message or code})."
            )
        raise BboxError(
            "Connexion refusée par la box Huawei. Réponse : "
            + (last_error or "aucune")
        )

    def _raise_for_error(self, body: str, path: str) -> None:
        """Lève l'exception adaptée à un refus de l'API."""
        code, message = self._parse_error(body)
        if code in HUAWEI_AUTH_ERRORS:
            raise BboxAuthError(
                f"Identifiants refusés par la box ({message or code})."
            )
        raise BboxError(f"Erreur de l'API sur {path} : {message or body[:120]}")

    @staticmethod
    def _parse_error(body: str) -> tuple[str, str]:
        """Extrait le code et le message d'erreur d'une réponse Huawei."""
        code = ""
        message = ""
        try:
            root = ET.fromstring(body or "")
            code = (root.findtext(".//code") or "").strip()
            message = (root.findtext(".//message") or "").strip()
        except ET.ParseError:
            pass
        if code and code in HUAWEI_ERRORS:
            message = HUAWEI_ERRORS[code]
        count = ""
        try:
            root = ET.fromstring(body or "")
            count = (root.findtext(".//count") or "").strip()
        except ET.ParseError:
            pass
        if count:
            message += f" (count={count})"
        return code, message

    @_synchronized
    def describe(self) -> dict[str, Any]:
        """Informations d'identification fournies par la box."""
        try:
            info = self._parse_state_login(self._api_get("user/state-login"))
        except BboxError:
            return {}
        return {
            "username": info.get("username", ""),
            "state": info.get("state", ""),
            "password_type": info.get("password_type", ""),
        }

    def _token_candidates(self) -> list[str]:
        """Variantes possibles du jeton de vérification."""
        candidates = [self._ses_tok_info, self._tok_info]
        result: list[str] = []
        for token in candidates:
            if not token:
                continue
            if token not in result:
                result.append(token)
            if len(token) > 32 and token[32:] not in result:
                result.append(token[32:])
        return result

    @staticmethod
    def _is_ok(body: str) -> bool:
        """Interprète la réponse XML de l'API Huawei."""
        low = (body or "").lower()
        if "<error" in low:
            return False
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return "ok" in low
        response = (root.findtext(".//response") or "").strip().lower()
        if response in ("ok", ""):
            return not root.findall(".//error")
        return False

    @_synchronized
    def ensure_login(self) -> None:
        """Reconnecte si nécessaire, en réutilisant la session enregistrée."""
        if self._ses_info:
            return
        if self._load_session():
            return
        self.login()

    def _reauthenticate(self) -> None:
        """Ré-authentifie la box avec le mot de passe déjà connu.

        Utilisé quand la session expire : la box ferme la session d'un coup
        (``100002``), on en rouvre une nouvelle silencieusement.
        """
        if self._reauthenticating:
            # On est déjà en train de se reconnecter (login() appelle l'API) :
            # on ne réessaie pas, on laisse remonter l'erreur.
            return
        self._reauthenticating = True
        try:
            # On ferme d'abord la session sur la box : elle n'en accepte
            # qu'une seule, et l'ancienne doit être libérée avant d'en
            # ouvrir une nouvelle (sinon : erreur 108003).
            try:
                self.logout()
            except BboxError:
                pass
            self._clear_session()
            self.login()
        finally:
            self._reauthenticating = False

    @_synchronized
    def reconnect(self) -> None:
        """Rouvre une session sur la box (session expirée ou fermée)."""
        self._reauthenticate()

    @staticmethod
    def is_auth_error(error: BaseException) -> bool:
        """L'erreur correspond-elle à un refus d'identifiants ?

        Dans ce cas, Home Assistant doit redemander le mot de passe.
        """
        if isinstance(error, BboxAuthError):
            return True
        code = ""
        for mot in re.findall(r"code[^0-9]*(\d{6})", str(error)):
            code = mot
        return code in HUAWEI_AUTH_ERRORS

    # -- lecture -----------------------------------------------------------

    @staticmethod
    def _parse_hosts(xml_text: str) -> list[dict[str, str]]:
        """Extrait la liste des clients d'une réponse XML Huawei.

        Le format exact varie selon le firmware ; on reste tolérant : tout
        élément dont un descendant porte une adresse MAC devient un client.
        """
        results: list[dict[str, str]] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return results
        for element in root.iter():
            mac = None
            for child in element:
                if child.text and MAC_RE.match(child.text.strip()):
                    mac = child.text.strip().lower()
                    break
            if mac is None:
                continue
            record = {"mac": mac, "ip": "", "name": ""}
            for child in element:
                tag = child.tag.lower()
                text = (child.text or "").strip()
                if not text:
                    continue
                if MAC_RE.match(text):
                    record["mac"] = text.lower()
                elif IPV4_RE.match(text) and not record["ip"]:
                    record["ip"] = text
                elif "name" in tag and not record["name"]:
                    record["name"] = text
            results.append(record)
        # Déduplication (une même MAC peut apparaître dans deux conteneurs).
        unique: dict[str, dict[str, str]] = {}
        for record in results:
            unique.setdefault(record["mac"], record)
        return list(unique.values())

    @_synchronized
    def hosts(self) -> list[dict[str, Any]]:
        """Clients connus de la box (filaire et Wi-Fi).

        ``lan/HostInfo`` est la table principale : elle donne pour chaque
        client son nom lisible (``ActualName``), son adresse IP, son type
        d'interface (``InterfaceType`` : ``Wireless`` ou ``Ethernet``) et son
        état (``Active``). ``wlan/host-list`` complète avec les hôtes Wi-Fi
        actuellement associés.
        """
        devices: dict[str, dict[str, Any]] = {}

        def _valeur(host: dict[str, Any], *noms: str) -> str:
            for nom in noms:
                valeur = (host.get(nom) or "").strip()
                if valeur:
                    return valeur
            return ""

        table: list[dict[str, Any]] = []
        erreur: BboxError | None = None
        try:
            table = self.hosts_detailed("lan/HostInfo")
        except BboxError as err:
            erreur = err
        for host in table:
            mac = self._host_mac(host)
            if not mac:
                continue
            interface = _valeur(host, "InterfaceType")
            devices[mac] = {
                "mac": mac,
                "name": _valeur(host, "ActualName", "HostName") or mac,
                "ip": _ipv4_only(
                    _valeur(host, "IpAddress", "IPAddress", "HostIPv4Address")
                ),
                "active": _valeur(host, "Active") == "1",
                "link": (
                    "Wifi"
                    if "wireless" in interface.lower()
                    else ("Ethernet" if interface else "")
                ),
                "ssid": _valeur(host, "AssociatedSsid", "AssociatedSSID", "SSID"),
                "guest": False,
            }

        try:
            connected = self._parse_hosts(self._api_get("wlan/host-list"))
        except BboxError as err:
            connected = []
            erreur = erreur or err
        if not devices and erreur is not None:
            # Aucune table n'a répondu : la box est injoignable ou la session
            # est morte. Mieux vaut le signaler que d'annoncer « 0 client ».
            raise erreur
        for host in connected:
            mac = host["mac"]
            if mac in devices:
                devices[mac]["active"] = True
            else:
                devices[mac] = {
                    "mac": mac,
                    "name": host.get("name") or mac,
                    "ip": host.get("ip", ""),
                    "active": True,
                    "link": "Wifi",
                    "ssid": "",
                    "guest": False,
                }
        return list(devices.values())

    def device_info(self) -> dict[str, Any]:
        """Modèle, fabricant et version logicielle de la box.

        Ces informations ne changent pas : elles sont mises en cache
        quelques minutes, pour ne pas interroger la box à chaque cycle de
        lecture (sur une box 4G, chaque requête compte).
        """
        maintenant = time.time()
        if self._device_info_cache is not None and (
            maintenant - self._device_info_cache_at < DEVICE_INFO_TTL
        ):
            return dict(self._device_info_cache)
        try:
            xml_text = self._api_get("device/information")
        except BboxError:
            return {}
        info: dict[str, Any] = {}
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return {}
        for element in root.iter():
            if element.text and element.text.strip() and element.tag:
                info.setdefault(element.tag, element.text.strip())
        self._device_info_cache = dict(info)
        self._device_info_cache_at = time.time()
        return info

    def blocked_macs(self) -> list[str]:
        """Liste des MAC filtrées (interdites d'accès)."""
        macs, _ = self.mac_filter()
        return macs

    def mac_filter(self) -> tuple[list[str], bool]:
        """Lit le filtre MAC et indique si le firmware l'expose.

        Le second élément de la tuile vaut ``False`` si la box ne gère pas
        cette fonction : dans ce cas on ne tente surtout pas d'écrire un
        filtre (on risquerait d'effacer les règles existantes).
        """
        macs, _policy, supported = self.mac_filter_full()
        return macs, supported

    @staticmethod
    def _split_macs(text: str) -> list[str]:
        """Découpe un texte qui peut contenir plusieurs adresses MAC.

        La liste des MAC filtrés est tantôt une chaîne plate séparée par des
        virgules (``aa:bb:cc:dd:ee:ff,11:22:..``), tantôt une suite
        d'éléments XML. Les deux formes sont gérées ici.
        """
        trouvees: list[str] = []
        for morceau in re.split(r"[,;\s]+", (text or "").strip()):
            morceau = morceau.strip()
            if MAC_RE.match(morceau):
                trouvees.append(morceau.lower())
        return trouvees

    @_synchronized
    def mac_filter_state(self) -> dict[str, Any]:
        """État complet du filtre MAC réseau (``security/mac-filter``).

        Renvoie la politique, la liste des MAC filtrées, le texte brut de la
        liste (utile pour écrire exactement dans la même forme) et la forme
        détectée : ``plat`` (chaîne) ou ``elements`` (liste XML).
        """
        etat: dict[str, Any] = {
            "policy": "2",
            "macs": [],
            "raw": "",
            "shape": "inconnu",
            "supported": False,
        }
        try:
            xml_text = self._api_get("security/mac-filter")
        except BboxError:
            return etat
        code, _message = self._parse_error(xml_text)
        if code:
            return etat
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return etat

        macs: list[str] = []
        policy = "2"
        for element in root.iter():
            text = (element.text or "").strip()
            if element.tag.lower() == "policy" and text:
                policy = text
                continue
            if element.tag.lower() in ("macfilters", "macfilterlist"):
                if len(list(element)):
                    # Liste d'éléments <macfilter> : chaque MAC est déjà
                    # repérée plus bas, via la boucle sur les textes.
                    etat["shape"] = "elements"
                else:
                    # Chaîne plate : « aa:bb:..,cc:dd:.. ».
                    etat["raw"] = text
                    etat["shape"] = "plat"
                    macs.extend(self._split_macs(text))
                continue
            if text and MAC_RE.match(text):
                macs.append(text.lower())
        if len(root.findall(".//macfilter")):
            etat["shape"] = "elements"

        etat["policy"] = policy
        etat["macs"] = macs
        etat["supported"] = True
        if not macs and policy in ("0", "1"):
            # Liste vide en politique neutre : c'est l'état « pas de filtre »,
            # on le mémorise pour pouvoir le restaurer plus tard.
            self._mac_original_policy = policy
        return etat

    def mac_filter_full(self) -> tuple[list[str], str, bool]:
        """Filtre MAC : liste des MAC, politique courante, disponibilité.

        Politique Huawei : ``0`` = désactivé, ``1`` = liste blanche
        (autorise uniquement les MAC listés), ``2`` = liste noire (interdit
        les MAC listés).
        """
        etat = self.mac_filter_state()
        return etat["macs"], etat["policy"], etat["supported"]

    @staticmethod
    def _parse_bool(value: str | None) -> bool | None:
        """Interprète une valeur booléenne de l'API Huawei."""
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in ("1", "true", "on", "enable", "enabled", "yes", "connected"):
            return True
        if text in ("0", "false", "off", "disable", "disabled", "no", "disconnected"):
            return False
        return None

    @_synchronized
    def wireless(self) -> dict[str, Any]:
        """État du Wi-Fi.

        Selon le firmware, l'indicateur peut se lire sur plusieurs points
        d'entrée (``wlan/wifi-network-switch``, ``wlan/wifi-feature-switch``,
        ``wlan/status-switch-settings``, ``wlan/basic-settings``) et porter
        des noms de champs différents. On les essaie dans l'ordre et on ne
        confond jamais « inconnu » avec « coupé ».
        """
        result: dict[str, Any] = {"24": None, "5": None, "guest": False}
        xml_text = ""
        for path, _fields in WIFI_STATE_CANDIDATES:
            try:
                candidate = self._api_get(path)
            except BboxError:
                continue
            if self._is_token_error(candidate):
                continue
            xml_text = candidate
            break
        if not xml_text:
            return result
        result["raw"] = xml_text
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            enabled = self._parse_bool(
                "1" if "wifienable>1<" in (xml_text or "").lower() else None
            )
            result["24"] = enabled
            result["5"] = enabled
            return result

        # Cas le plus courant sur B528s : une balise <radio> par bande.
        radios = root.findall(".//radio")
        if radios:
            for radio in radios:
                enabled = self._parse_bool(radio.findtext("wifienable"))
                band = self._radio_band(radio)
                if band:
                    result[band] = enabled
            if result["24"] is not None or result["5"] is not None:
                return result

        for tag in WIFI_STATE_FIELDS:
            parsed = self._parse_bool(root.findtext(f".//{tag}"))
            if parsed is not None:
                result["24"] = parsed
                result["5"] = parsed
                break
        # Réseau invité : on cherche un champ qui en parle. Toutes les
        # box Huawei ne l'exposent pas ; on ne devine pas.
        for element in root.iter():
            tag = (element.tag or "").lower()
            if "guest" not in tag:
                continue
            parsed = self._parse_bool(element.text)
            if parsed is not None:
                result["guest"] = parsed
                break
        return result

    @staticmethod
    def _radio_band(radio: ET.Element) -> str | None:
        """Détermine la bande d'une radio Wi-Fi (``24`` ou ``5``).

        On regarde d'abord les champs qui nomment la bande, puis l'identifiant
        de la radio, et enfin son index (0 = 2,4 GHz, 1 = 5 GHz sur les CPE
        Huawei).
        """
        band_fields = ("band", "wifiband", "frequencyband", "radioname")
        for child in radio:
            if child.tag.lower() in band_fields and child.text:
                text = child.text.strip().lower()
                if any(key in text for key in ("2.4", "2g", "24")):
                    return "24"
                if any(key in text for key in ("5g", "802.11a")):
                    return "5"
        identifier = " ".join(
            f"{child.tag}={child.text or ''}"
            for child in radio
            if child.tag.lower() in ("id", "radioid", "name")
        ).lower()
        if any(key in identifier for key in ("2.4", "2g")):
            return "24"
        if any(key in identifier for key in ("5g", "802.11a")):
            return "5"
        index = (radio.findtext("index") or "").strip()
        if index == "0":
            return "24"
        if index == "1":
            return "5"
        return None

    def raw(self, path: str) -> str:
        """Renvoie la réponse brute d'un chemin d'API (pour diagnostic)."""
        path = path.strip("/")
        if path.startswith("api/"):
            path = path[4:]
        if self.family == "sagemcom":
            return str(self._call(path))
        return self._api_get(path)

    # -- écriture ----------------------------------------------------------

    # -- accès Internet par appareil ---------------------------------------

    #: Tables où la box expose les appareils et leur accès Internet.
    #: ``lan/HostInfo`` est la table des clients (celle de l'interface web),
    #: ``userhosts/Host`` celle des réservations DHCP (souvent vide).
    HOST_TABLES = ("lan/HostInfo", "userhosts/Host")

    @staticmethod
    def _host_mac(host: dict[str, Any]) -> str:
        """Adresse MAC d'une ligne d'hôte (le champ varie selon le firmware)."""
        for champ in ("HostMACAddress", "HostHw", "MACAddress", "MacAddress"):
            value = (host.get(champ) or "").strip()
            if value:
                return value.lower()
        return ""

    def hosts_detailed(self, endpoint: str | None = None) -> list[dict[str, Any]]:
        """Table des hôtes de la box, avec **tous** les champs.

        C'est cette table qui porte le commutateur « Accès Internet » par
        appareil visible dans l'interface web du routeur.

        Sur les B528s, ``lan/HostInfo`` contient les clients-connectés ;
        ``userhosts/Host`` ne contient que les réservations DHCP (souvent
        vide). On interroge les deux et on renvoie la première non vide.
        """
        tables = [endpoint] if endpoint else list(self.HOST_TABLES)
        for chemin in tables:
            try:
                raw = self._api_get(chemin)
            except BboxError:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            hosts: list[dict[str, Any]] = []
            for host in root.iter():
                if not list(host):
                    continue
                entry: dict[str, Any] = {}
                for child in host:
                    entry[child.tag] = (child.text or "").strip()
                if self._host_mac(entry):
                    hosts.append(entry)
            if hosts:
                self._hosts_endpoint = chemin
                return hosts
        self._hosts_endpoint = ""
        return []

    def internet_access_map(self) -> dict[str, bool]:
        """État de l'accès Internet de chaque appareil (MAC → autorisé).

        Certains firmwares portent un champ dédié dans la table des
        hôtes. Sur les B528s, ce champ n'existe pas : c'est le **filtre
        MAC** de la box qui régit l'accès Internet (documentation Huawei :
        « les règles de filtre MAC activent ou coupent l'accès des
       forgeurs au service Internet »). On retombe donc dessus.
        """
        resultat: dict[str, bool] = {}
        hotes = self.hosts_detailed()
        for host in hotes:
            adresse = self._host_mac(host)
            if not adresse:
                continue
            champ = self._internet_field(host)
            if champ is None:
                continue
            resultat[adresse] = host.get(champ, "1") != "0"
        if resultat:
            return resultat
        # Pas de champ dédié : on lit l'état réel du filtre Wi-Fi, qui est
        # le mécanisme du commutateur « Accès Internet » sur les B528s.
        try:
            filtres = self.wlan_mac_filters()
        except BboxError:
            filtres = []
        if filtres:
            connectes = {
                self._host_mac(host): host
                for host in hotes
                if self._host_mac(host)
            }
            for ssid in filtres:
                liste = {d["mac"] for d in ssid["devices"]}
                if ssid["mode"] == "block":
                    coupes = liste
                elif ssid["mode"] == "allow":
                    coupes = set(connectes) - liste
                else:
                    continue
                for adresse in coupes:
                    if adresse in connectes:
                        resultat[adresse] = False
            for adresse in connectes:
                resultat.setdefault(adresse, True)
            if resultat:
                return resultat
        # Dernier recours : le filtre MAC réseau.
        etat = self.mac_filter_state()
        if not etat["supported"]:
            return resultat
        filtrees = set(etat["macs"])
        for host in hotes:
            adresse = self._host_mac(host)
            if adresse:
                resultat[adresse] = adresse not in filtrees
        return resultat

    def host_by_mac(self, mac: str) -> dict[str, Any] | None:
        """Retourne la ligne d'hôte correspondant à une adresse MAC.

        Toutes les tables sont interrogées : l'appareil peut figurer dans
        ``lan/HostInfo`` (clients) comme dans ``userhosts/Host``.
        """
        mac = _norm_mac(mac)
        premier: dict[str, Any] | None = None
        for table in self.HOST_TABLES:
            for host in self.hosts_detailed(table):
                if self._host_mac(host) != mac:
                    continue
                if self._internet_field(host):
                    # Ligne la plus complète : elle porte l'accès Internet.
                    return host
                if premier is None:
                    premier = host
        return premier

    @staticmethod
    def _internet_field(host: dict[str, Any]) -> str | None:
        """Champ qui porte l'autorisation d'accès Internet d'un hôte."""
        for champ in (
            "HostIPv4Enabled",
            "HostIPv6Enabled",
            "HostInternetEnabled",
            "HostIPv4Access",
            "InternetAccess",
            "InternetEnabled",
            "AccessInternet",
            "WanAccess",
            "HostWanAccess",
            # Dernier recours : sur certains firmware, « HostStatus » vaut
            # « hôte autorisé » (0 = accès coupé).
            "HostStatus",
            "Status",
        ):
            if champ in host:
                return champ
        return None

    def mac_filter_access_map(self) -> dict[str, bool]:
        """Accès Internet de chaque appareil, d'après le filtre MAC réseau.

        Seules les machines **explicitement** interdites sont marquées
        « coupées » : la politique de la box (``policy``) n'étant pas
        documentée par l'interface, on ne conclut que pour le cas que
        l'on provoque soi-même (liste noire).
        """
        etat = self.mac_filter_state()
        if not etat["supported"]:
            return {}
        filtrees = set(etat["macs"])
        liste_noire = etat.get("policy") == "2"
        resultat: dict[str, bool] = {}
        for host in self.hosts_detailed():
            mac = self._host_mac(host)
            if not mac:
                continue
            resultat[mac] = not (liste_noire and mac in filtrees)
        return resultat

    def wifi_access_map(self) -> dict[str, bool]:
        """Accès Wi-Fi de chaque appareil, d'après la liste noire du SSID.

        C'est le mécanisme du commutateur « Accès Internet » de l'interface
        web des B528s : l'appareil listé dans la liste noire de son SSID ne
        peut plus s'y connecter.
        """
        resultat: dict[str, bool] = {}
        try:
            filtres = self.wlan_mac_filters()
        except BboxError:
            return resultat
        if not filtres:
            return resultat
        par_index = {ssid["index"]: ssid for ssid in filtres}
        noms = self._ssid_names()
        index_par_nom = {nom.lower(): index for index, nom in noms.items()}
        for host in self.hosts_detailed():
            mac = self._host_mac(host)
            if not mac:
                continue
            nom = (host.get("AssociatedSsid") or "").strip()
            index = index_par_nom.get(nom.lower(), "0")
            ssid = par_index.get(index)
            if ssid is None:
                resultat[mac] = True
                continue
            liste = {d["mac"] for d in ssid["devices"]}
            if ssid["mode"] == "block":
                resultat[mac] = mac not in liste
            elif ssid["mode"] == "allow":
                resultat[mac] = mac in liste
            else:
                resultat[mac] = True
        # Un appareil listé dans un SSID en liste noire est refusé partout,
        # même s'il ne figure plus dans la table des hôtes : la box le
        # connaît (c'est elle qui l'a refusé).
        for ssid in filtres:
            if ssid["mode"] != "block":
                continue
            for device in ssid["devices"]:
                resultat[device["mac"]] = False
        return resultat

    def set_internet_access(self, mac: str, allowed: bool) -> None:
        """Active ou coupe l'**accès Internet** d'un appareil.

        C'est le commutateur « Accès Internet » de l'interface web du
        routeur. Sur les B528s, la box ne le propose qu'aux appareils
        **Wi-Fi** : le couper revient à retirer l'appareil du réseau Wi-Fi,
        il perd donc du même coup sa sortie Internet.

        Trois mécanismes possibles, du plus précis au plus large :

        * un champ dédié dans la table des hôtes (certains firmwares) ;
        * la **liste noire Wi-Fi du SSID** : c'est le mécanisme réellement
          utilisé par l'interface web des B528s. Vérifié empiriquement :
          basculer « Accès Internet » dans l'interface écrit la MAC de
          l'appareil dans ``wlan/multi-macfilter-settings`` (emplacement
          ``WifiMacFilterMac{i}`` du SSID concerné), et l'appareil se
          déconnecte du Wi-Fi ;
        * le **filtre MAC réseau** (``security/mac-filter``) : contrôle
          d'accès Internet poste par poste selon la documentation Huawei,
          mais l'appareil conserve alors son adresse IP sur le LAN.

        Sur les B528s, on utilise le deuxième mécanisme, à l'identique de
        l'interface web.
        """
        mac = _norm_mac(mac)
        # On cherche, dans toutes les tables, celle qui contient à la fois
        # l'appareil et un champ d'accès Internet.
        table = ""
        cible: dict[str, Any] | None = None
        avant: list[dict[str, Any]] = []
        for chemin in self.HOST_TABLES:
            hotes = self.hosts_detailed(chemin)
            for host in hotes:
                if self._host_mac(host) == mac and self._internet_field(host):
                    table = chemin
                    cible = host
                    avant = hotes
                    break
            if cible is not None:
                break
        if cible is None:
            # Aucun champ dédié : on passe par la liste noire Wi-Fi du SSID
            # de l'appareil, exactement comme le fait l'interface web.
            nom = ""
            for host in self.hosts_detailed():
                if self._host_mac(host) == mac:
                    nom = (
                        host.get("ActualName")
                        or host.get("HostName")
                        or ""
                    ).strip()
                    break
            index = self._ssid_index_for(mac)
            self._set_device_blocked_wifi_both(
                mac, not allowed, ssid_index=index, name=nom
            )
            return
        champ = self._internet_field(cible)
        assert champ is not None  # noqa: S101 - garanti ci-dessus

        valeur_initiale = cible.get(champ, "")
        # « 1 » = autorisé, « 0 » = coupé (convention Huawei).
        nouvelle_valeur = "1" if allowed else "0"

        index = cible.get("Index", "")
        variantes = []
        if index:
            variantes.append(
                f"<request><Index>{index}</Index>"
                f"<{champ}>{nouvelle_valeur}</{champ}></request>"
            )
        variantes.append(
            f"<request><HostMACAddress>{mac}</HostMACAddress>"
            f"<{champ}>{nouvelle_valeur}</{champ}></request>"
        )
        variantes.append(
            f"<request><HostHw>{mac}</HostHw>"
            f"<{champ}>{nouvelle_valeur}</{champ}></request>"
        )
        complet = "".join(
            f"<{cle}>{valeur}</{cle}>" for cle, valeur in cible.items()
        )
        variantes.append(f"<request><Host>{complet}</Host></request>")

        for payload in variantes:
            try:
                body = self._api_post(table, payload)
            except BboxError:
                continue
            if not self._is_ok(body):
                continue
            apres = self.hosts_detailed(table)
            if len(apres) != len(avant):
                # La table a été altérée : on ne continue pas.
                break
            ligne = None
            for host in apres:
                if self._host_mac(host) == mac:
                    ligne = host
                    break
            if ligne is None:
                break
            if ligne.get(champ, "") == nouvelle_valeur:
                return

        # Échec : on restaure la valeur d'origine.
        try:
            self._api_post(
                table,
                f"<request><HostMACAddress>{mac}</HostMACAddress>"
                f"<{champ}>{valeur_initiale}</{champ}></request>",
            )
        except BboxError:
            pass
        raise BboxError(
            "La box n'a pas accepté la modification de l'accès Internet "
            f"(table « {table} », champ « {champ} », valeur "
            f"{valeur_initiale!r}). Configuration d'origine restaurée."
        )

    # -- filtre MAC au niveau Wi-Fi ----------------------------------------

    def wlan_mac_filters(self) -> list[dict[str, Any]]:
        """Filtre MAC Wi-Fi, par SSID.

        Chaque SSID (réseau Wi-Fi de la box) possède sa propre liste et son
        propre mode :

        * ``off``   : aucun filtre ;
        * ``allow`` : liste blanche, seules les MAC citées se connectent ;
        * ``block`` : liste noire, les MAC citées sont refusées.

        Les règles déjà présentes sont renvoyées telles quelles : l'appelant
        peut les conserver.
        """
        raw = self._api_get("wlan/multi-macfilter-settings")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as err:
            raise BboxError(
                "Réponse illisible de wlan/multi-macfilter-settings"
            ) from err
        result: list[dict[str, Any]] = []
        for ssid in root.findall(".//Ssid"):
            status = (ssid.findtext("WifiMacFilterStatus") or "0").strip()
            devices = []
            for slot in range(WLAN_MAC_FILTER_SLOTS):
                mac = (ssid.findtext(f"WifiMacFilterMac{slot}") or "").strip()
                if not mac:
                    continue
                devices.append(
                    {
                        "mac": mac.lower(),
                        "name": (ssid.findtext(f"wifihostname{slot}") or "").strip(),
                    }
                )
            result.append(
                {
                    "index": (ssid.findtext("Index") or "").strip(),
                    "status": status,
                    "mode": WLAN_FILTER_MODE.get(status, "block"),
                    "devices": devices,
                }
            )
        return result

    def wlan_blocked_macs(self, ssid_index: str = "0") -> list[str]:
        """MAC bloquées par le filtre Wi-Fi du SSID demandé."""
        for ssid in self.wlan_mac_filters():
            if ssid["index"] == str(ssid_index):
                return [d["mac"] for d in ssid["devices"]]
        return []

    @staticmethod
    def _write_status(ssid: ET.Element, status: str) -> None:
        """Fixe le mode du filtre d'un SSID."""
        element = ssid.find("WifiMacFilterStatus")
        if element is None:
            element = ET.SubElement(ssid, "WifiMacFilterStatus")
        element.text = status

    def _ssid_index_for(self, mac: str) -> str:
        """Indice du SSID auquel un appareil est rattaché.

        L'interface web de la box n'affiche le commutateur « Accès
        Internet » que pour les appareils **Wi-Fi**, et elle agit sur le
        réseau Wi-Fi auquel l'appareil est associé. On retrouve donc son
        SSID dans la table des hôtes, puis son indice dans la
        configuration Wi-Fi.
        """
        mac = _norm_mac(mac)
        nom = ""
        for host in self.hosts_detailed():
            if self._host_mac(host) != mac:
                continue
            nom = (
                host.get("AssociatedSsid")
                or host.get("AssociatedSSID")
                or host.get("SSID")
                or ""
            ).strip()
            if nom:
                break
        if not nom:
            return "0"
        for index, ssid_name in self._ssid_names().items():
            if ssid_name.lower() == nom.lower():
                return index
        return "0"

    def _ssid_names(self) -> dict[str, str]:
        """Correspondence indice → nom de SSID, lue dans l'API si possible.

        Les tables de configuration Wi-Fi ne portent pas toujours le nom du
        réseau : on essaie quelques emplacements connus et, à défaut, on
        renvoie une table vide (le SSID principal est alors l'indice 0).
        """
        for table in (
            "wlan/wifi-network-settings",
            "wlan/ssid-info",
            "wlan/basic-settings",
            "wlan/wlan-basic-settings",
        ):
            try:
                brut = self._api_get(table)
            except BboxError:
                continue
            noms: dict[str, str] = {}
            try:
                root = ET.fromstring(brut)
            except ET.ParseError:
                continue
            for position, ssid in enumerate(root.findall(".//Ssid")):
                index = (ssid.findtext("Index") or "").strip() or str(position)
                nom = ""
                for champ in ("SSIDName", "SsidName", "Name", "ssidname", "SSID"):
                    nom = (ssid.findtext(champ) or "").strip()
                    if nom:
                        break
                if nom:
                    noms[index] = nom
            if noms:
                return noms
        return {}

    def set_device_blocked_wifi(
        self,
        mac: str,
        blocked: bool,
        ssid_index: str = "0",
        name: str = "",
    ) -> None:
        """Bloque ou libère un appareil au niveau du Wi-Fi.

        On relit toute la structure ``wlan/multi-macfilter-settings``, on ne
        modifie que le SSID visé, puis on la renvoie **en entier** : les
        règles des autres SSID et les autres entrées du SSID visé sont
        conservées à l'identique. Après l'écriture, la structure est relue
        pour vérifier que le résultat est bien celui attendu ; sinon la
        version d'origine est réécrite.

        Le mode du SSID est respecté :

        * liste blanche (``allow``) : bloquer un appareil revient à le retirer
          des autorisés (et à désactiver le filtre si elle devient vide, pour
          ne pas couper tout le monde) ;
        * liste noire (``block``) : l'appareil est ajouté ou retiré de la
          liste, le mode est conservé ;
        * aucun filtre (``off``) : bloquer un appareil bascule le SSID en
          liste noire.
        """
        mac = _norm_mac(mac)
        raw = self._api_get("wlan/multi-macfilter-settings")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as err:
            raise BboxError(
                "Réponse illisible de wlan/multi-macfilter-settings"
            ) from err

        container = root.find(".//Ssids")
        if container is None:
            raise BboxError(
                "wlan/multi-macfilter-settings ne contient pas de <Ssids> : "
                "le filtre Wi-Fi n'est pas disponible sur cette box."
            )
        target = None
        for ssid in container.findall("Ssid"):
            if (ssid.findtext("Index") or "").strip() == str(ssid_index):
                target = ssid
                break
        if target is None:
            raise BboxError(
                f"Aucun SSID d'indice {ssid_index} dans la configuration Wi-Fi."
            )

        # Copie de l'état d'origine, pour pouvoir le restaurer.
        original_container = copy.deepcopy(container)

        status = (target.findtext("WifiMacFilterStatus") or "0").strip()
        mode = WLAN_FILTER_MODE.get(status, "block")
        slots: list[tuple[str, str]] = []
        for slot in range(WLAN_MAC_FILTER_SLOTS):
            slots.append(
                (
                    (target.findtext(f"WifiMacFilterMac{slot}") or "").strip(),
                    (target.findtext(f"wifihostname{slot}") or "").strip(),
                )
            )

        def _write(slot: int, mac_value: str, host: str) -> None:
            """Écrit une paire MAC / nom dans un emplacement."""
            element = target.find(f"WifiMacFilterMac{slot}")
            if element is None:
                element = ET.SubElement(target, f"WifiMacFilterMac{slot}")
            element.text = mac_value
            element = target.find(f"wifihostname{slot}")
            if element is None:
                element = ET.SubElement(target, f"wifihostname{slot}")
            element.text = host

        def _free_slot() -> int | None:
            """Premier emplacement libre."""
            for index, (value, _host) in enumerate(slots):
                if not value:
                    return index
            return None

        if mode == "allow":
            # Liste blanche : seules les MAC présentes sont autorisées.
            if blocked:
                autorises = [
                    (slot, value)
                    for slot, (value, _host) in enumerate(slots)
                    if value
                ]
                if not any(value.lower() == mac for _slot, value in autorises):
                    # L'appareil n'est pas autorisé : il ne peut pas se
                    # connecter, il n'y a rien à faire.
                    return
                if len(autorises) == 1:
                    # C'est le seul appareil autorisé : le retirer viderait la
                    # liste blanche et, en mode liste blanche, cela reviendrait
                    # à interdire le Wi-Fi à tout le monde. On refuse plutôt
                    # que de saboter le réseau.
                    raise BboxError(
                        f"Le SSID {ssid_index} est en liste blanche et "
                        f"{mac} est son seul appareil autorisé : le bloquer "
                        "interdirait le Wi-Fi à tous les autres. Passez le "
                        "SSID en liste noire dans l'interface de la box, ou "
                        "utilisez le blocage réseau (portée « lan »)."
                    )
                # On retire l'appareil des autorisés.
                for slot, value in autorises:
                    if value.lower() == mac:
                        _write(slot, "", "")
            else:
                # On l'autorise de nouveau (on l'ajoute s'il manque).
                if not any(value.lower() == mac for value, _ in slots):
                    slot = _free_slot()
                    if slot is None:
                        raise BboxError(
                            f"Le filtre Wi-Fi du SSID {ssid_index} est plein "
                            f"({WLAN_MAC_FILTER_SLOTS} emplacements)."
                        )
                    _write(slot, mac, name)
                self._write_status(target, "1")
        elif mode == "off":
            # Aucun filtre : bloquer un appareil bascule en liste noire.
            if blocked:
                if not any(value.lower() == mac for value, _ in slots):
                    slot = _free_slot()
                    if slot is None:
                        raise BboxError(
                            f"Le filtre Wi-Fi du SSID {ssid_index} est plein "
                            f"({WLAN_MAC_FILTER_SLOTS} emplacements)."
                        )
                    _write(slot, mac, name)
                self._write_status(target, "2")
            # Autoriser : le filtre est désactivé, rien à faire.
        else:
            # Liste noire : les MAC présentes sont refusées.
            if blocked:
                if not any(value.lower() == mac for value, _ in slots):
                    slot = _free_slot()
                    if slot is None:
                        raise BboxError(
                            f"Le filtre Wi-Fi du SSID {ssid_index} est plein "
                            f"({WLAN_MAC_FILTER_SLOTS} emplacements)."
                        )
                    _write(slot, mac, name)
                self._write_status(target, "2")
            else:
                for slot, (value, _host) in enumerate(slots):
                    if value.lower() == mac:
                        _write(slot, "", "")
                # La liste noire vide ne bloque plus rien : on garde le mode
                # (l'utilisateur a choisi une liste noire).

        payload_root = ET.Element("request")
        payload_root.append(copy.deepcopy(container))
        payload = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            + ET.tostring(payload_root, encoding="unicode")
        )
        body = self._api_post("wlan/multi-macfilter-settings", payload)
        if not self._is_ok(body):
            code, message = self._parse_error(body)
            raise BboxError(
                f"Refus de l'API filtre Wi-Fi ({message or body[:120]})"
                + (f" [code {code}]" if code else "")
            )

        # Contrôle après écriture : on relit et on compare.
        try:
            after = self.wlan_mac_filters()
        except BboxError:
            return
        for ssid in after:
            if ssid["index"] != str(ssid_index):
                continue
            present = any(d["mac"] == mac for d in ssid["devices"])
            if ssid["mode"] == "allow":
                # En liste blanche, être présent signifie « autorisé ».
                if present == (not blocked):
                    return
            elif present == blocked:
                return
        # Le résultat ne correspond pas : on restaure la version d'origine.
        restore_root = ET.Element("request")
        if original_container is not None:
            restore_root.append(copy.deepcopy(original_container))
            restore = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                + ET.tostring(restore_root, encoding="unicode")
            )
            try:
                self._api_post("wlan/multi-macfilter-settings", restore)
            except BboxError:
                pass
        raise BboxError(
            "Le filtre Wi-Fi n'a pas été appliqué par la box : "
            "configuration d'origine restaurée."
        )

    def _sync_macfilter_ex(
        self,
        ssid_index: str,
        mac: str,
        blocked: bool,
        name: str = "",
    ) -> bool:
        """Reporte la règle dans la table ``wlan/multi-macfilter-settings-ex``.

        L'interface web de la box écrit **les deux** tables
        (``multi-macfilter-settings`` et sa variante ``-ex``) lorsqu'elle
        bascule le commutateur « Accès Internet ». On en fait de même : la
        table ``-ex`` porte la liste noire sous forme d'élément imbriqué
        (``<wifimacblacklist><WifiMacFilterMac0>…``).

        On ne touche qu'à l'emplacement de l'appareil concerné : les autres
        règles de la liste sont conservées. La table est relue après
        écriture pour vérifier que la box a bien enregistré la modification.
        """
        mac = _norm_mac(mac)
        try:
            brut = self._api_get("wlan/multi-macfilter-settings-ex")
        except BboxError:
            return False
        try:
            root = ET.fromstring(brut)
        except ET.ParseError:
            return False
        container = root.find(".//Ssids")
        if container is None:
            return False
        cible = None
        for position, ssid in enumerate(container.findall("Ssid")):
            index = (ssid.findtext("Index") or "").strip()
            if index == str(ssid_index) or (not index and position == int(ssid_index)):
                cible = ssid
                break
        if cible is None:
            return False
        liste = cible.find("wifimacblacklist")
        if liste is None:
            liste = ET.SubElement(cible, "wifimacblacklist")
        emplacements: list[tuple[ET.Element | None, ET.Element | None]] = []
        for slot in range(WLAN_MAC_FILTER_SLOTS):
            emplacements.append(
                (
                    liste.find(f"WifiMacFilterMac{slot}"),
                    liste.find(f"wifihostname{slot}"),
                )
            )
        modifie = False
        if blocked:
            present = any(
                element is not None
                and (element.text or "").strip().lower() == mac
                for element, _hote in emplacements
            )
            if not present:
                for slot, (element, hote) in enumerate(emplacements):
                    if element is not None and (element.text or "").strip():
                        continue
                    if element is None:
                        element = ET.SubElement(liste, f"WifiMacFilterMac{slot}")
                        if hote is None:
                            hote = ET.SubElement(liste, f"wifihostname{slot}")
                    element.text = mac.upper()
                    if hote is not None:
                        hote.text = name
                    modifie = True
                    break
        else:
            for element, hote in emplacements:
                if element is not None and (element.text or "").strip().lower() == mac:
                    element.text = ""
                    if hote is not None:
                        hote.text = ""
                    modifie = True
        if not modifie:
            return True
        # On renvoie la structure complète, comme l'interface web.
        request = ET.Element("request")
        for enfant in list(root):
            request.append(copy.deepcopy(enfant))
        payload = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            + ET.tostring(request, encoding="unicode")
        )
        try:
            corps = self._api_post("wlan/multi-macfilter-settings-ex", payload)
        except BboxError:
            return False
        if not self._is_ok(corps):
            return False
        try:
            verification = self._api_get("wlan/multi-macfilter-settings-ex")
        except BboxError:
            return False
        present = mac.upper() in verification.upper()
        return present if blocked else not present

    def _set_device_blocked_wifi_both(
        self,
        mac: str,
        blocked: bool,
        ssid_index: str,
        name: str,
    ) -> None:
        """Applique une règle Wi-Fi sur les deux tables de la box.

        L'interface web écrit ``multi-macfilter-settings`` **et**
        ``multi-macfilter-settings-ex`` : on fait pareil, pour que le
        réglage soit pris en compte quelle que soit la table que la box
        utilise réellement.
        """
        self.set_device_blocked_wifi(mac, blocked, ssid_index=ssid_index, name=name)
        try:
            self._sync_macfilter_ex(ssid_index, mac, blocked, name)
        except BboxError:
            pass

    @_synchronized
    def set_device_blocked(
        self, mac: str, blocked: bool, scope: str = "lan"
    ) -> None:
        """Ajoute (ou retire) une MAC du filtre « accès interdit ».

        ``scope`` vaut :

        * ``lan`` — filtre MAC réseau de la box : d'après la documentation
          Huawei, le filtre MAC détermine **qui accède au service Internet**.
          L'appareil reste donc joignable sur le LAN, mais il perd Internet
          (c'est le commutateur « Accès Internet » du routeur) ;
        * ``internet`` — même mécanisme, avec lecture d'état dédiée quand la
          box expose un champ d'accès Internet dans la table des hôtes ;
        * ``wifi`` — filtre Wi-Fi du SSID : l'appareil ne peut plus se
          connecter au Wi-Fi, mais reste joignable en filaire ;
        * ``all`` — les deux.
        """
        if scope == "both":
            # Ancienne appellation de « all ».
            scope = "all"
        if scope in ("wifi", "all"):
            self._set_device_blocked_wifi_both(
                mac, blocked, ssid_index=self._ssid_index_for(mac), name=""
            )
        if scope in ("lan", "all"):
            self._set_device_blocked_lan(mac, blocked)
        if scope == "internet":
            # Commutateur « Accès Internet » du routeur : l'appareil reste
            # sur le réseau local, mais il ne peut plus sortir sur Internet.
            self.set_internet_access(mac, allowed=not blocked)
        elif scope == "all":
            # L'appareil est déjà coupé du réseau : si la box ne le
            # connaît pas dans sa table des hôtes, l'accès Internet n'a plus
            # lieu d'être coupé séparément.
            try:
                self.set_internet_access(mac, allowed=not blocked)
            except BboxError as err:
                _LOGGER.warning(
                    "Internet access not changed for %s: %s", mac, err
                )
        if scope not in ("lan", "wifi", "internet", "all"):
            raise BboxError(f"Portée de blocage inconnue : {scope}")

    def firewall_switch_state(self) -> dict[str, str]:
        """Interrupteurs du pare-feu de la box (``security/firewall-switch``).

        Le filtre MAC y possède son propre interrupteur
        (``firewallmacfilterswitch``) : tant qu'il est éteint, la box
        **ignore** les écritures du filtre MAC. C'est ce qui arrive quand
        le commutateur « Accès Internet » de l'interface web ne modifie
        rien.
        """
        try:
            brut = self._api_get("security/firewall-switch")
            root = ET.fromstring(brut)
        except (BboxError, ET.ParseError):
            return {}
        return {
            element.tag: (element.text or "").strip()
            for element in root.iter()
            if element.tag and (element.text or "").strip()
        }

    def _set_firewall_switch(self, valeurs: dict[str, str]) -> bool:
        """Écrit ``security/firewall-switch`` en reprenant sa structure.

        Seuls les champs fournis sont modifiés ; tous les autres sont
        renvoyés tels quels, comme le fait l'interface web.
        """
        try:
            brut = self._api_get("security/firewall-switch")
            root = ET.fromstring(brut)
        except (BboxError, ET.ParseError):
            return False
        trouves = 0
        for element in root.iter():
            cle = element.tag.lower()
            if cle in valeurs:
                element.text = str(valeurs[cle])
                trouves += 1
        if not trouves:
            return False
        request = ET.Element("request")
        for enfant in list(root):
            request.append(copy.deepcopy(enfant))
        payload = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            + ET.tostring(request, encoding="unicode")
        )
        try:
            corps = self._api_post("security/firewall-switch", payload)
        except BboxError:
            return False
        if not self._is_ok(corps):
            return False
        # Contrôle : on relit et on compare les champs écrits.
        etat = self.firewall_switch_state()
        return all(etat.get(cle) == str(valeur) for cle, valeur in valeurs.items())

    def _mac_filter_payload(
        self,
        macs: list[str],
        policy: str,
        separator: str = ",",
        majuscules: bool = False,
    ) -> str | None:
        """Construit une écriture du filtre MAC **en reprenant la structure
        de la box**.

        La table ``security/mac-filter`` renvoie par exemple :

        .. code-block:: xml

            <response>
              <MacFilterCollection>
                <policy>1</policy>
                <macfilters></macfilters>
              </MacFilterCollection>
            </response>

        Il faut donc renvoyer la **même arborescence** (l'habillage
        ``<MacFilterCollection>`` compris), en ne changeant que le contenu
        de ``<policy>`` et de ``<macfilters>``. C'est la méthode
        lecture-modification-écriture, la seule qui ne dépende d'aucune
        hypothèse sur le format attendu.
        """
        try:
            brut = self._api_get("security/mac-filter")
            root = ET.fromstring(brut)
        except (BboxError, ET.ParseError):
            return None
        element_policy = None
        element_macs = None
        for element in root.iter():
            tag = element.tag.lower()
            if tag == "policy" and element_policy is None:
                element_policy = element
            elif tag in ("macfilters", "macfilterlist") and element_macs is None:
                element_macs = element
        if element_policy is None or element_macs is None:
            return None
        valeurs = [m.upper() if majuscules else m for m in macs]
        element_policy.text = policy
        for enfant in list(element_macs):
            element_macs.remove(enfant)
        element_macs.text = separator.join(valeurs)
        request = ET.Element("request")
        for enfant in list(root):
            request.append(copy.deepcopy(enfant))
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            + ET.tostring(request, encoding="unicode")
        )

    def _mac_filter_candidates(self, macs: list[str], policy: str) -> list[str]:
        """Écritures **sans risque** du filtre MAC réseau.

        La forme acceptée par les B528s est connue et mesurée : c'est une
        liste d'éléments

        .. code-block:: xml

            <macfilter><value>MAC</value><status>1</status></macfilter>

        On ne garde ici que des variantes **de cette** forme, plus quelques
        variantes « liste plate » de la même grammaire.

        On évite délibérément les formes ``<MACAddress>`` / ``<DeviceName>`` :
        la box les refuse (code ``9003``) et **vide au passage la table**,
        qu'elle restaure ensuite — un accident inutile en production.
        """
        candidats: list[str] = []
        elements = "".join(
            f"<macfilter><value>{m}</value><status>1</status></macfilter>"
            for m in macs
        )
        elements_id = "".join(
            f"<macfilter><status>1</status><ID>{index + 1}</ID>"
            f"<value>{m}</value></macfilter>"
            for index, m in enumerate(macs)
        )
        for corps in (elements, elements_id):
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{corps}</macfilters>"
                "</request>"
            )
            candidats.append(
                "<request>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{corps}</macfilters>"
                "</request>"
            )
        # Variantes « liste plate » : la structure de la première version
        # du firmware.
        for separateur in (",", ";"):
            corps = separateur.join(macs)
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{corps}</macfilters>"
                "</request>"
            )
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                "<MacFilterCollection>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{corps}</macfilters>"
                "</MacFilterCollection>"
                "</request>"
            )
        return candidats

    def _mac_filter_candidates_exhaustive(
        self, macs: list[str], policy: str
    ) -> list[str]:
        """Toutes les écritures possibles du filtre MAC réseau.

        Réservé au **diagnostic** (``mac-filter --test``) : la box ignore
        silencieusement une écriture qu'elle ne comprend pas, on lui
        propose donc beaucoup de formes pour découvrir celle qu'elle
        attend. Sur un firmware inconnu, certaines de ces formes peuvent
        modifier la table avant d'être annulées par la box : n'utilisez
        cette liste que pour explorer, jamais pour agir.
        """
        candidats: list[str] = []
        # 0. Forme **confirmée** sur les B528s (mesurée sur la box) :
        #    <macfilters><macfilter><value>MAC</value><status>1</status>
        #    </macfilter></macfilters> — la box y ajoute elle-même le <ID>.
        elements = "".join(
            f"<macfilter><value>{m}</value><status>1</status></macfilter>"
            for m in macs
        )
        candidats.append(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<request>"
            f"<policy>{policy}</policy>"
            f"<macfilters>{elements}</macfilters>"
            "</request>"
        )
        # 1. Structure de la box, liste en chaîne plate.
        for separateur in (",", ";", " "):
            for majuscules in (False, True):
                xml = self._mac_filter_payload(
                    macs, policy, separator=separateur, majuscules=majuscules
                )
                if xml:
                    candidats.append(xml)
        # 2. Sans l'habillage <MacFilterCollection>.
        for separateur in (",", ";"):
            corps = separateur.join(macs)
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{corps}</macfilters>"
                "</request>"
            )
        # 3. Avec l'habillage <MacFilterCollection> et une liste en chaîne.
        for separateur in (",", ";"):
            corps = separateur.join(macs)
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                "<MacFilterCollection>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{corps}</macfilters>"
                "</MacFilterCollection>"
                "</request>"
            )
        # 4. En éléments <MACAddress> / <DeviceName> (forme des ONT Huawei).
        elements_mac = "".join(
            f"<macfilter><MACAddress>{m}</MACAddress>"
            f"<DeviceName>mac{m}</DeviceName></macfilter>"
            for m in macs
        )
        for habillage in (
            "<MacFilterCollection>{corps}</MacFilterCollection>",
            "{corps}",
        ):
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                + habillage.format(
                    corps=(
                        f"<policy>{policy}</policy>"
                        f"<macfilters>{elements_mac}</macfilters>"
                    )
                )
                + "</request>"
            )
        # 5. Avec un séparateur final (avec une seule MAC, le séparateur
        #    n'apparaît jamais entre deux éléments : seule la version
        #    terminée par un séparateur se distingue).
        for finale in (",", ";", " "):
            corps = "".join(f"{m}{finale}" for m in macs)
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{corps}</macfilters>"
                "</request>"
            )
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                "<MacFilterCollection>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{corps}</macfilters>"
                "</MacFilterCollection>"
                "</request>"
            )
        # 6. Sans séparateurs, ou avec des tirets (formats vus sur d'autres
        #    firmwares Huawei).
        for variante in (
            "".join(m.replace(":", "") for m in macs),
            "-".join(m.upper().replace(":", "-") for m in macs),
        ):
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{variante}</macfilters>"
                "</request>"
            )
            candidats.append(
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<request>"
                "<MacFilterCollection>"
                f"<policy>{policy}</policy>"
                f"<macfilters>{variante}</macfilters>"
                "</MacFilterCollection>"
                "</request>"
            )
        candidats.append(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<request>"
            "<MacFilterCollection>"
            f"<policy>{policy}</policy>"
            f"<macfilters>{elements}</macfilters>"
            "</MacFilterCollection>"
            "</request>"
        )
        candidats.append(
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<request>"
            f"<policy>{policy}</policy>"
            f"<macfilters>{elements}</macfilters>"
            "</request>"
        )
        # 7. Formes « liste d'éléments » variantes (url-filter).
        candidats.extend(self._mac_filter_element_candidates(macs, policy))
        return candidats

    def _mac_filter_element_candidates(
        self, macs: list[str], policy: str
    ) -> list[str]:
        """Écritures du filtre MAC sous forme de **liste d'éléments**.

        La box utilise cette grammaire pour ``security/url-filter`` :

        .. code-block:: xml

            <response><urlfilters><urlfilter>
              <status>1</status><ID>1</ID><value>www.exemple.com</value>
            </urlfilter></urlfilters></response>

        Le filtre MAC suit très probablement la même logique : une liste
        d'éléments, chacun avec un ``<status>``, un ``<ID>`` et la valeur.
        On essaie donc les variantes de nom d'élément, de champ, et
        d'options (identifiant, nom de l'appareil).
        """
        candidats: list[str] = []
        tags = ("macfilter", "macFilter")
        champs = ("value", "MACAddress", "mac")
        for tag in tags:
            for champ in champs:
                for avec_id in (True, False):
                    for avec_nom in (False, True):
                        for avec_policy in (True, False):
                            if tag == "macFilter" and (champ != "value" or avec_nom):
                                # Une seule variante suffit pour ce nom de tag.
                                continue
                            elements = ""
                            for position, mac in enumerate(macs, 1):
                                morceaux = ["<status>1</status>"]
                                if avec_id:
                                    morceaux.append(f"<ID>{position}</ID>")
                                morceaux.append(f"<{champ}>{mac}</{champ}>")
                                if avec_nom:
                                    morceaux.append(
                                        f"<name>mac{mac}</name>"
                                        f"<DeviceName>mac{mac}</DeviceName>"
                                    )
                                elements += (
                                    f"<{tag}>" + "".join(morceaux) + f"</{tag}>"
                                )
                            corps = f"<macfilters>{elements}</macfilters>"
                            if avec_policy:
                                corps = f"<policy>{policy}</policy>" + corps
                            candidats.append(
                                '<?xml version="1.0" encoding="UTF-8"?>'
                                "<request>" + corps + "</request>"
                            )
                            # Sans la déclaration XML : certains analyseurs
                            # de CPE y sont sensibles.
                            candidats.append("<request>" + corps + "</request>")
        return candidats

    def _ecrire_mac_filtre(self, macs: list[str], policy: str) -> bool:
        """Écrit la liste des MAC filtrées et vérifie le résultat.

        La forme exacte attendue par la box n'est pas documentée. On essaie
        donc plusieurs écritures, de la plus fidèle (structure reprise
        telle quelle de la réponse de la box) aux plus simples, et l'on ne
        retient que celle que la box relit fidèlement.

        Renvoie ``True`` si la liste a bien été appliquée.
        """
        macs = [m.lower() for m in macs]
        candidats = self._mac_filter_candidates(macs, policy)

        dernier = ""
        for xml in candidats:
            dernier = xml
            try:
                body = self._api_post("security/mac-filter", xml)
            except BboxError:
                continue
            if not self._is_ok(body):
                # Forme refusée : la box vide la table puis la restaure.
                continue
            # La box applique l'écriture **de façon asynchrone** : pendant
            # une fraction de seconde, la table peut refléter l'état
            # intermédiaire (liste vidée) ou l'état d'origine. On laisse
            # donc le résultat se stabiliser avant de conclure.
            for attente in (0.6, 1.0):
                time.sleep(attente)
                verification = self.mac_filter_state()
                if not verification["supported"]:
                    break
                if set(verification["macs"]) == set(macs):
                    return True
        # On garde la trace du dernier essai : elle sert de diagnostic.
        self._last_mac_filter_payload = dernier
        return False

    #: Dernière écriture du filtre MAC tentée (diagnostic).
    _last_mac_filter_payload = ""

    def _set_device_blocked_lan(self, mac: str, blocked: bool) -> None:
        """Filtre réseau de la box (identique pour toutes les familles).

        L'écriture reprend **exactement la forme employée par la box**,
        mesurée sur une B528s : la liste des MAC filtrées est une suite
        d'éléments XML

        .. code-block:: xml

            <macfilters><macfilter><status>1</status><ID>1</ID>
              <value>aa:bb:cc:dd:ee:ff</value></macfilter></macfilters>

        et on écrit

        .. code-block:: xml

            <request><policy>2</policy><macfilters>
              <macfilter><value>aa:bb:cc:dd:ee:ff</value><status>1</status>
              </macfilter></macfilters></request>

        (la box ajoute elle-même le ``<ID>``). La table est relue après
        écriture pour vérifier que la modification est bien passée ; sinon
        la valeur d'origine est réécrite.
        """
        self.ensure_login()
        etat = self.mac_filter_state()
        if not etat["supported"]:
            raise BboxError(
                "Le filtre MAC n'est pas accessible sur ce firmware "
                "(security/mac-filter). La déconnexion d'un appareil précis "
                "n'est pas possible par l'API ; coupez le Wi-Fi depuis "
                "l'interface web de la box, ou utilisez le contrôle parental "
                "de l'interface."
            )
        mac = _norm_mac(mac)
        current = list(etat["macs"])
        policy = etat["policy"]

        # Garde-fou : la politique ``1`` est une liste **blanche** (seules
        # les MAC listées ont Internet). Passer en liste noire (``2``)
        # en gardant ces MAC inverserait le sens du filtre et couperait
        # Internet à tous les appareils autorisés. On refuse donc tant que
        # la box est en liste blanche non vide.
        if policy == "1" and current and blocked:
            raise BboxError(
                "La box est en liste blanche (politique 1) et autorise "
                f"déjà {len(current)} appareil(s) : la repasser en liste "
                "noire leur couperait Internet à tous. Basculez d'abord le "
                "filtre MAC en mode « liste noire / interdire » depuis "
                "l'interface web de la box."
            )
        if policy == "1" and current and not blocked:
            # On ne libère que l'appareil demandé : la politique reste
            # inchangée tant que la liste n'est pas vide.
            pass

        if blocked and mac not in current:
            current.append(mac)
        elif not blocked and mac in current:
            current.remove(mac)
        else:
            # Déjà dans l'état demandé : rien à écrire.
            return

        # On repasse en liste noire quand il y a des MAC bloqués, et on
        # restaure la politique d'origine sinon (une liste vide en politique
        # « liste blanche » n'interdit rien : c'est l'état « pas de filtre »).
        if current:
            new_policy = "2"
        else:
            new_policy = self._mac_original_policy or (
                policy if policy in ("0", "1") else "1"
            )

        if not self._ecrire_mac_filtre(current, new_policy):
            # Le résultat ne correspond pas : on restaure la version
            # d'origine (la politique et la liste initiales).
            self._ecrire_mac_filtre(list(etat["macs"]), policy)
            raise BboxError(
                "Le filtre MAC n'a pas été appliqué par la box : "
                "configuration d'origine restaurée. Dernière écriture "
                f"essayée : {self._last_mac_filter_payload[:220]}"
            )
        if blocked:
            _LOGGER.warning(
                "MAC filter updated: check in the router web interface that "
                "only the targeted device lost Internet access and that "
                "the others kept theirs."
            )

    @_synchronized
    def set_wifi(self, band: str, enabled: bool) -> None:
        """Allume ou éteint le Wi-Fi de la box.

        Sur les B528s, l'endpoint ``wlan/wifi-network-switch`` est restreint
        (erreur 100003). L'état et la commande passent par
        ``wlan/status-switch-settings``, qui expose une balise ``<radio>``
        par bande avec un champ ``<wifienable>``. On relit la configuration,
        on ne modifie que le champ visé, puis on la renvoie telle quelle :
        les autres réglages de la box ne sont jamais écrasés.
        """
        self.ensure_login()
        state = "1" if enabled else "0"

        raw = self._api_get("wlan/status-switch-settings")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as err:
            raise BboxError(f"Réponse illisible de status-switch-settings") from err

        radios = root.findall(".//radio")
        if not radios:
            raise BboxError(
                "Aucune balise <radio> dans wlan/status-switch-settings : "
                "cette box ne semble pas exposer la commande Wi-Fi par l'API."
            )

        targets = []
        for radio in radios:
            radio_band = self._radio_band(radio)
            if band == "all" or radio_band == band:
                targets.append(radio)
        if not targets:
            raise BboxError(
                f"Aucune radio Wi-Fi de type « {band} » dans la configuration "
                "de la box."
            )

        for target in targets:
            element = target.find("wifienable")
            if element is None:
                element = ET.SubElement(target, "wifienable")
            element.text = state

        # On renvoie la structure existante, enveloppée dans <request>.
        container = root.find(".//radios")
        if container is None:
            container = target
        payload_root = ET.Element("request")
        payload_root.append(copy.deepcopy(container))
        payload = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            + ET.tostring(payload_root, encoding="unicode")
        )
        body = self._api_post("wlan/status-switch-settings", payload)
        if not self._is_ok(body):
            code, message = self._parse_error(body)
            raise BboxError(
                f"Refus de l'API Wi-Fi ({message or body[:120]})"
                + (f" [code {code}]" if code else "")
            )

    # -- état normalisé ----------------------------------------------------

    @_synchronized
    def state(self) -> dict[str, Any]:
        """Retourne l'état normalisé de la box."""
        self.ensure_login()
        blocked = set(self.blocked_macs())
        blocked_wifi: set[str] = set()
        try:
            # Tous les SSID : un appareil peut être listé sur un réseau
            # secondaire sans être sur le réseau principal.
            for ssid in self.wlan_mac_filters():
                if ssid["mode"] == "block":
                    blocked_wifi |= {d["mac"] for d in ssid["devices"]}
        except BboxError:
            # Le filtre Wi-Fi n'est pas exposé sur toutes les box.
            blocked_wifi = set()
        try:
            internet = self.internet_access_map()
        except BboxError:
            internet = {}
        try:
            acces_internet = self.mac_filter_access_map()
        except BboxError:
            acces_internet = {}
        try:
            acces_wifi = self.wifi_access_map()
        except BboxError:
            acces_wifi = {}
        devices = self.hosts()
        for device in devices:
            device["blocked_lan"] = device["mac"] in blocked
            device["blocked_wifi"] = device["mac"] in blocked_wifi
            device["blocked"] = device["blocked_lan"] or device["blocked_wifi"]
            device["internet_allowed"] = internet.get(device["mac"])
            device["acces_internet"] = acces_internet.get(device["mac"])
            device["acces_wifi"] = acces_wifi.get(device["mac"])
        devices.sort(key=lambda d: (not d["active"], d["name"].lower()))
        info = self.device_info()
        model = (
            info.get("DeviceName")
            or info.get("ProductFamily")
            or info.get("DeviceManufacturer")
            or self.model
        )
        firmware = info.get("SoftwareVersion", "")
        return {
            "ok": True,
            "family": self.family,
            "host": self.host,
            "model": model,
            "firmware": firmware,
            "wifi": self.wireless(),
            "nb_connectes": sum(1 for d in devices if d["active"]),
            "nb_total": len(devices),
            "devices": devices,
            "timestamp": time.time(),
        }


# --------------------------------------------------------------------------
# Détection
# --------------------------------------------------------------------------


def detect_client(
    host: str,
    password: str,
    username: str = "admin",
    family: str = "auto",
    use_tls: bool | None = None,
    verify: bool = False,
    timeout: int = 8,
    session_file: str | None = None,
) -> SagemcomBbox | HuaweiCpe:
    """Cherche la famille d'API qui répond sur ``host``.

    ``family`` peut forcer ``sagemcom`` ou ``huawei`` ; ``auto`` essaie les
    deux. ``use_tls`` force (ou interdit) le HTTPS.
    """
    errors: list[str] = []
    auth_error: BboxAuthError | None = None

    attempts: list[tuple[str, bool]] = []
    if family in ("auto", "sagemcom"):
        attempts.extend(("sagemcom", tls) for tls in ([use_tls] if use_tls is not None else [True, False]))
    if family in ("auto", "huawei"):
        attempts.extend(("huawei", tls) for tls in ([use_tls] if use_tls is not None else [False, True]))

    for name, tls in attempts:
        try:
            if name == "sagemcom":
                client: SagemcomBbox | HuaweiCpe = SagemcomBbox(
                    host, password, use_tls=tls, verify=verify, timeout=timeout
                )
            else:
                client = HuaweiCpe(
                    host,
                    password,
                    username=username,
                    use_tls=tls,
                    verify=verify,
                    timeout=timeout,
                    session_file=session_file,
                )
            # ensure_login() réutilise la session déjà ouverte sur la box
            # (une seule session d'administration est admise à la fois) ;
            # login() seul provoquerait un refus « 108003 ».
            client.ensure_login()
            client.state()
            return client
        except BboxAuthError as err:
            # Mauvais mot de passe : inutile d'insister sur les autres
            # variantes, mais on continue pour distinguer une box qui
            # répondrait dans une autre famille d'API.
            auth_error = auth_error or err
            errors.append(f"{name}{'s' if tls else ''}: {err}")
        except BboxError as err:
            errors.append(f"{name}{'s' if tls else ''}: {err}")
        except Exception as err:  # pragma: no cover - filet de sécurité
            errors.append(f"{name}: {err!r}")
    if auth_error is not None:
        raise auth_error
    raise BboxError(" | ".join(errors) or "Aucune API reconnue")


def wake_on_lan(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> bool:
    """Envoie un paquet magique (Wake-on-LAN) à une adresse MAC.

    L'appareil reste joignable sur le LAN : c'est un « packet magique »
    envoyé en diffusion UDP qui demande à l'ordinateur de s'allumer. La
    box n'intervient pas, seule l'expression réseau compte.
    """
    import socket

    propre = _norm_mac(mac).replace(":", "").replace("-", "")
    if len(propre) != 12:
        raise BboxError(f"Adresse MAC invalide : {mac}")
    try:
        octets = bytes.fromhex(propre)
    except ValueError as err:
        raise BboxError(f"Adresse MAC invalide : {mac}") from err
    paquet = b"\xff" * 6 + octets * 16
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(paquet, (broadcast, port))
    finally:
        sock.close()
    return True
