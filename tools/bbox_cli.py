#!/usr/bin/env python3
"""Outil en ligne de commande pour diagnostiquer une box Bouygues.

Ce script n'est **pas** utilisé par l'intégration Home Assistant : il sert au
diagnostic et à l'exploration de l'API (``diag``, ``state``, ``snapshot``,
``diff``, ``mac-filter``…). Il réutilise le client de
``custom_components/bbox_4g/bbox_api.py``.

    python3 tools/bbox_cli.py --host 192.168.8.1 --password '***' diag
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
_API_DIR = os.path.join(os.path.dirname(HERE), "custom_components", "bbox_4g")
sys.path.insert(0, _API_DIR)

import bbox_api as _api  # noqa: E402

# Les commandes utilisent aussi les noms privés du client (``_norm_mac``…).
globals().update({k: v for k, v in vars(_api).items() if not k.startswith("__")})
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
DEFAULT_CACHE = os.path.join(HERE, "cache.json")
DEFAULT_TTL = 30




# --------------------------------------------------------------------------
# Configuration & cache
# --------------------------------------------------------------------------


def load_config(path: str = DEFAULT_CONFIG) -> dict[str, Any]:
    """Charge le fichier de configuration JSON (créé à l'installation)."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as err:
        raise BboxError(f"Configuration illisible ({path}) : {err}") from err


def save_config(config: dict[str, Any], path: str = DEFAULT_CONFIG) -> None:
    """Écrit le fichier de configuration (en 0600 : il contient le mot de passe)."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover
        pass


class Cache:
    """Petit cache disque pour éviter une connexion à chaque capteur."""

    def __init__(self, path: str = DEFAULT_CACHE, ttl: int = DEFAULT_TTL) -> None:
        self.path = path
        self.ttl = ttl

    def read(self) -> dict[str, Any] | None:
        """Retourne l'état en cache s'il est encore frais."""
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - float(data.get("timestamp", 0)) > self.ttl:
            return None
        return data

    def write(self, state: dict[str, Any]) -> None:
        """Enregistre l'état en cache."""
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
        except OSError:  # pragma: no cover
            pass

    def invalidate(self) -> None:
        """Supprime le cache (après une action)."""
        try:
            os.remove(self.path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Fabrique de client depuis la configuration
# --------------------------------------------------------------------------


def build_client(args: argparse.Namespace) -> SagemcomBbox | HuaweiCpe:
    """Construit le client à partir des arguments de la ligne de commande."""
    config = load_config(args.config)
    host = args.host or config.get("host") or CANDIDATE_HOSTS[0]
    session_file = getattr(args, "session", None) or os.path.join(
        os.path.dirname(os.path.abspath(args.config)), "session.json"
    )
    password = args.password or config.get("password") or ""
    username = args.username or config.get("username") or "admin"
    family = args.family or config.get("family") or "auto"
    use_tls = config.get("use_tls")
    if family == "sagemcom":
        client: SagemcomBbox | HuaweiCpe = SagemcomBbox(
            host, password, use_tls=bool(use_tls) if use_tls is not None else True
        )
    elif family == "huawei":
        client = HuaweiCpe(
            host,
            password,
            username=username,
            use_tls=bool(use_tls) if use_tls is not None else False,
            session_file=session_file,
        )
    else:
        # En mode « auto », on essaie les deux protocoles et les deux familles.
        client = detect_client(
            host,
            password,
            username=username,
            family=family,
            use_tls=None,
            session_file=session_file,
        )
    return client


def get_state(args: argparse.Namespace, force: bool = False) -> dict[str, Any]:
    """Retourne l'état, via le cache si possible."""
    cache = Cache(args.cache, args.ttl)
    if not force:
        cached = cache.read()
        if cached is not None:
            return cached
    client = build_client(args)
    state = client.state()
    cache.write(state)
    return state


# --------------------------------------------------------------------------
# Interface en ligne de commande
# --------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Transforme un nom en slug utilisable dans un entity_id."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return slug or "appareil"


def cmd_state(args: argparse.Namespace) -> int:
    """Affiche l'état complet au format JSON."""
    state = get_state(args, force=args.force)
    print(json.dumps(state, ensure_ascii=False))
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    """Affiche le nombre de clients connectés."""
    print(get_state(args, force=args.force)["nb_connectes"])
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Affiche le tableau des clients."""
    state = get_state(args, force=args.force)
    print(f"{state['model']} — {state['host']} — {state['nb_connectes']} connecté(s)")
    print(f"{'MAC':<20}{'État':<12}{'Nom':<28}{'IP':<16}")
    for device in state["devices"]:
        flags = []
        if device["active"]:
            flags.append("connecté")
        if device["blocked"]:
            flags.append("bloqué")
        print(
            f"{device['mac']:<20}{'/'.join(flags) or 'inactif':<12}"
            f"{device['name'][:27]:<28}{device['ip']:<16}"
        )
    return 0


def cmd_presence(args: argparse.Namespace) -> int:
    """Affiche 1 si le client est connecté, 0 sinon."""
    state = get_state(args)
    mac = _norm_mac(args.mac)
    for device in state["devices"]:
        if device["mac"] == mac:
            print(1 if device["active"] else 0)
            return 0
    print(0)
    return 0


def cmd_blocked(args: argparse.Namespace) -> int:
    """Affiche BLOCKED ou ALLOWED pour un client."""
    state = get_state(args)
    mac = _norm_mac(args.mac)
    for device in state["devices"]:
        if device["mac"] == mac:
            print("BLOCKED" if device["blocked"] else "ALLOWED")
            return 0
    print("ALLOWED")
    return 0


def _simuler(client: Any, mac: str, bloque: bool, scope: str) -> int:
    """Affiche ce qu'une commande ferait, sans rien écrire."""
    mac = _norm_mac(mac)
    print(f"Simulation — aucune écriture ne sera faite.")
    print(f"  appareil   : {mac}")
    print(f"  action     : {'bloquer' if bloque else 'libérer'}")
    print(f"  portée     : {scope}")
    if scope in ("lan", "all"):
        try:
            etat = client.mac_filter_state()
        except BboxError as err:
            etat = {"supported": False, "policy": "", "macs": [], "shape": ""}
            print(f"  filtre MAC : illisible ({err})")
        if etat.get("supported"):
            liste = etat.get("macs") or []
            print(f"  filtre MAC : politique {etat.get('policy')} ({etat.get('shape')})")
            print(f"               liste : {', '.join(liste) if liste else 'vide'}")
            if bloque:
                nouvelle = "2 (liste noire)" if mac not in liste else "2 (déjà présente)"
                print(f"  après      : politique {nouvelle}, liste + {mac}")
                if not liste and etat.get("policy") != "2":
                    print(
                        "  ⚠ La box est actuellement sur une politique "
                        f"« {etat.get('policy')} » : vérifiez dans l'interface "
                        "web que seul cet appareil perd Internet."
                    )
            else:
                print(f"  après      : {mac} retirée de la liste")
    if scope in ("wifi", "all", "internet"):
        try:
            filtres = client.wlan_mac_filters()
        except BboxError:
            filtres = []
        for ssid in filtres:
            if ssid["mode"] == "off":
                continue
            present = any(d["mac"] == mac for d in ssid["devices"])
            print(
                f"  SSID {ssid['index']} : mode {ssid['mode']}, "
                f"{mac} {'déjà dans' if present else 'absente de'} la liste"
            )
    return 0


def cmd_block(args: argparse.Namespace) -> int:
    """Bloque un client (le déconnecte du réseau)."""
    client = build_client(args)
    if getattr(args, "dry_run", False):
        return _simuler(client, args.mac, True, args.scope)
    client.set_device_blocked(args.mac, True, scope=args.scope)
    Cache(args.cache, args.ttl).invalidate()
    print("OK")
    if args.scope in ("lan", "all"):
        print(
            "⚠ Vérifiez dans l'interface web que seul l'appareil concerné a "
            "perdu l'accès à Internet, et que les autres conservent le leur."
        )
    return 0


def cmd_wifi_filters(args: argparse.Namespace) -> int:
    """Affiche le filtre MAC Wi-Fi, SSID par SSID."""
    client = build_client(args)
    try:
        filtres = client.wlan_mac_filters()
    except BboxError as err:
        print(f"ERREUR {err}")
        return 2
    for ssid in filtres:
        mode = {
            "off": "aucun filtre",
            "allow": "liste blanche",
            "block": "liste noire",
        }
        print(
            f"SSID {ssid['index']} : {mode.get(ssid['mode'], ssid['mode'])} "
            f"({len(ssid['devices'])} règle(s))"
        )
        for device in ssid["devices"]:
            nom = device["name"] or "(sans nom)"
            print(f"   - {device['mac']}  {nom}")
    return 0


def cmd_host_info(args: argparse.Namespace) -> int:
    """Affiche la table des hôtes de la box (commutateur d'accès Internet)."""
    client = build_client(args)
    if not getattr(client, "HOST_TABLES", None):
        print("Commande propre aux box Huawei.")
        return 0
    mac = _norm_mac(args.mac) if args.mac else ""
    trouve = False
    for table in client.HOST_TABLES:
        try:
            hotes = client.hosts_detailed(table)
        except BboxError as err:
            print(f"{table} : illisible ({err})")
            continue
        print(f"=== {table} : {len(hotes)} appareil(s)")
        if not hotes:
            continue
        trouve = True
        cible = None
        if mac:
            for host in hotes:
                if client._host_mac(host) == mac:
                    cible = host
                    break
            if cible is None:
                print(f"    {mac} absent de cette table")
                continue
        else:
            cible = hotes[0]
        print("    Champs : " + ", ".join(sorted(cible)))
        print("    ---")
        for cle in sorted(cible):
            print(f"    {cle} = {cible[cle]}")
    if not trouve:
        print("Aucune table d'appareils exploitable.")
        return 1
    return 0


def cmd_host_table(args: argparse.Namespace) -> int:
    """Tableau compact des hôtes : une ligne par appareil."""
    client = build_client(args)
    if not getattr(client, "HOST_TABLES", None):
        print("Commande propre aux box Huawei.")
        return 0
    hotes = client.hosts_detailed("lan/HostInfo")
    if not hotes:
        hotes = client.hosts_detailed("userhosts/Host")
    if not hotes:
        print("Aucun appareil trouvé.")
        return 1
    colonnes = ("MacAddress", "ActualName", "Active", "AssociatedSsid",
                "InterfaceType", "isLocalDevice")
    print(" | ".join(c[:18] for c in colonnes))
    for host in hotes:
        ligne = []
        for colonne in colonnes:
            valeur = host.get(colonne, "")
            if colonne == "MacAddress":
                valeur = client._host_mac(host)
            ligne.append(str(valeur)[:18])
        print(" | ".join(ligne))
    return 0


def cmd_watch_hosts(args: argparse.Namespace) -> int:
    """Surveille les tables de la box et affiche ce qui a changé.

    Sert à identifier le champ porteuse du commutateur « Accès Internet »
    de l'interface web : on lance la veille, on actionne le commutateur
    dans l'interface du routeur, et la commande affiche la différence.

    La comparaison se fait **feuille par feuille** (et non table par
    table) : c'est le seul niveau qui rende le résultat lisible.
    """
    import time as _time

    client = build_client(args)
    if not getattr(client, "HOST_TABLES", None):
        print("Commande propre aux box Huawei.")
        return 0
    tables = args.tables or list(WATCH_TABLES)

    def _relever() -> dict[str, dict[str, str]]:
        """Relit chaque table et aplatit ses feuilles."""
        releve: dict[str, dict[str, str]] = {}
        for table in tables:
            try:
                releve[table] = flatten_xml(client._api_get(table))
            except BboxError:
                releve[table] = {}
        return releve

    def _en_panne(releve: dict[str, dict[str, str]]) -> bool:
        """La moitié des tables est-elle en erreur ?"""
        en_erreur = 0
        for valeurs in releve.values():
            code = valeurs.get("code", "")
            if code and code not in ("", "0", "200"):
                en_erreur += 1
        return en_erreur >= max(1, len(tables) // 2)

    print(f"Veille pendant {args.seconds} s sur {len(tables)} tables…")
    print("Actionnez le commutateur dans l'interface du routeur.")
    avant = _relever()
    if _en_panne(avant):
        print("Session expirée au démarrage : reconnexion…")
        try:
            client.reconnect()
        except BboxError as err:
            print(f"Reconnexion impossible : {err}")
            return 1
        avant = _relever()
    _time.sleep(args.seconds)

    apres = _relever()
    if _en_panne(apres):
        print("Session expirée pendant la veille : reconnexion…")
        try:
            client.reconnect()
        except BboxError as err:
            print(f"Reconnexion impossible : {err}")
            return 1
        apres = _relever()

    trouve = False
    for table in tables:
        for cle in sorted(set(avant[table]) | set(apres[table])):
            ancienne = avant[table].get(cle)
            valeur = apres[table].get(cle)
            if ancienne == valeur:
                continue
            # On ignore les compteurs qui défilent tout seuls.
            if cle.rsplit(".", 1)[-1] in VOLATILE_FIELDS:
                continue
            trouve = True
            print(f"  [{table}] {cle} = {ancienne!r} -> {valeur!r}")
    if not trouve:
        print("Aucun changement pertinent détecté.")
    return 0


def _snapshot_tables(client: Any, tables: list[str]) -> dict[str, Any]:
    """Relit chaque table et renvoie un instantané exploitable.

    Pour chaque table on conserve les feuilles aplaties **et** le XML
    brut : le diff compare les feuilles, le XML sert à rejouer une
    analyse plus fine (ou à voir la structure complète).
    """
    instantane: dict[str, Any] = {}
    for table in tables:
        try:
            brut = client._api_get(table)
        except BboxError as err:
            instantane[table] = {"error": str(err), "valeurs": {}, "xml": ""}
            continue
        valeurs = flatten_xml(brut)
        code = valeurs.get("code", "")
        instantane[table] = {
            "error": f"[{code}] {valeurs.get('message', '')}".strip() if code else "",
            "valeurs": valeurs,
            "xml": brut,
        }
    return instantane


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Enregistre l'état de toutes les tables candidates dans un fichier.

    Cette commande sert de point de comparaison : on prend un instantané,
    on actionne le commutateur « Accès Internet » dans l'interface web
    **sans aucune contrainte de temps**, puis on prend un second
    instantané et on compare les deux (``diff``).
    """
    client = build_client(args)
    if not getattr(client, "HOST_TABLES", None):
        print("Commande propre aux box Huawei.")
        return 0
    tables = list(args.tables or WATCH_TABLES)
    instantane = _snapshot_tables(client, tables)
    contenu = {
        "horodatage": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tables": instantane,
    }
    texte = json.dumps(contenu, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(texte)
        erreurs = sum(1 for t in instantane.values() if t["error"])
        print(
            f"Instantané enregistré dans {args.out} "
            f"({len(tables)} tables, {erreurs} en erreur)."
        )
    else:
        print(texte)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """Compare deux instantanés et affiche les champs qui ont changé."""
    with open(args.avant, encoding="utf-8") as handle:
        avant = json.load(handle)
    with open(args.apres, encoding="utf-8") as handle:
        apres = json.load(handle)

    tables_avant = avant.get("tables", {})
    tables_apres = apres.get("tables", {})
    trouve = False
    angles_morts: list[str] = []
    comparees: list[str] = []
    for table in sorted(set(tables_avant) | set(tables_apres)):
        a = tables_avant.get(table, {})
        b = tables_apres.get(table, {})
        va = a.get("valeurs", {})
        vb = b.get("valeurs", {})
        for cle in sorted(set(va) | set(vb)):
            if cle.rsplit(".", 1)[-1] in VOLATILE_FIELDS:
                continue
            if va.get(cle) == vb.get(cle):
                continue
            trouve = True
            print(f"  [{table}] {cle} = {va.get(cle)!r} -> {vb.get(cle)!r}")
        if a.get("error") != b.get("error"):
            trouve = True
            print(
                f"  [{table}] erreur = {a.get('error', '')!r} -> "
                f"{b.get('error', '')!r}"
            )
        if a.get("error") and b.get("error"):
            # Table illisible des deux côtés : un changement pourrait s'y
            # cacher. On le dit, sinon le « aucune différence » serait trompeur.
            angles_morts.append(f"{table} ({b.get('error') or a.get('error')})")
        elif not a.get("error") and not b.get("error"):
            comparees.append(table)

    if comparees:
        print(f"Tables comparées ({len(comparees)}) : {', '.join(comparees)}")
    if angles_morts:
        print()
        print(
            "⚠ Tables illisibles des deux côtés : un changement pourrait s'y "
            "cacher."
        )
        for table in angles_morts:
            print(f"  - {table}")
    if not trouve:
        print("Aucune différence entre les deux instantanés.")
    return 0


#: Jetons recherché dans le JavaScript de l'interface web de la box.
UI_SCAN_TOKENS = (
    "mac-filter",
    "macfilter",
    "HostInfo",
    "hostinfo",
    "userhosts",
    "HostIPv4Enabled",
    "HostIPv6Enabled",
    "InternetAccess",
    "internetaccess",
    "AccessControl",
    "DeviceAccess",
    "blacklist",
    "whitelist",
    "MultiMacfilter",
    "wlan/multi",
    "security/acl",
    "Internet",
    "WanAccess",
    "Access",
)

#: Motifs d'URL d'API repérables dans le JavaScript.
UI_API_RE = re.compile(r"""["']((?:/)?api/[A-Za-z0-9_./-]+)["']""")
UI_PATH_RE = re.compile(
    r"""["']([a-z][a-z0-9-]*/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)["']"""
)


def cmd_ui_scan(args: argparse.Namespace) -> int:
    """Analyse le JavaScript de l'interface web pour trouver l'API utilisée.

    Le commutateur « Accès Internet » de l'interface web de la box
    appelle forcément une route de l'API : en lisant le JavaScript
    servi par la box, on découvre le chemin exact et les noms de
    champs employés, sans rien deviner.

    La commande ne modifie **rien** : elle se contente de télécharger
    les fichiers servis par l'interface et de les interroger.

    ``--grep`` ajoute des mots-clés libres à la recherche (séparés par des
    virgules) : c'est la voie la plus directe pour traquer un terme précis
    dans l'interface, par exemple ``--grep internet,ipv4,policy``.
    """
    import html as _html

    config = load_config(args.config)
    host = args.host or config.get("host") or CANDIDATE_HOSTS[0]
    http = _Http(timeout=args.timeout, verify=False)

    bases = []
    for scheme in ("https", "http"):
        bases.append(f"{scheme}://{host}{args.base}")
    pages: dict[str, str] = {}
    for base in bases:
        url = base if base.endswith("/") else base + "/"
        try:
            status, body, _headers = http.request(url, method="GET")
        except BboxError:
            continue
        if status < 400 and body:
            pages[url] = body
            print(f"Interface web trouvée : {url} ({len(body)} octets)")
            break
    if not pages:
        print(
            "Aucune interface web accessible sur la box. Vérifiez l'adresse "
            f"(--host {host}) et que la page se charge dans un navigateur."
        )
        return 1

    # -- Collecte des fichiers --------------------------------------------
    urls: list[str] = []
    pages_html: list[str] = []

    def extraire_scripts(adresse: str, page: str) -> None:
        """Enregistre les scripts et sous-pages référencés par une page."""
        for trouve in re.finditer(
            r"""<script[^>]+src=["']([^"']+)["']""", page, re.IGNORECASE
        ):
            urls.append(
                urllib.parse.urljoin(adresse, _html.unescape(trouve.group(1)))
            )
        for trouve in re.finditer(
            r"""<link[^>]+href=["']([^"']+\.js[^"']*)["']""", page, re.IGNORECASE
        ):
            urls.append(
                urllib.parse.urljoin(adresse, _html.unescape(trouve.group(1)))
            )
        # Certaines interfaces sont découpées en pages HTML : on les suit.
        for motif in (
            r"""<link[^>]+href=["']([^"']+\.html[^"']*)["']""",
            r"""<(?:iframe|frame)[^>]+src=["']([^"']+\.html[^"']*)["']""",
        ):
            for trouve in re.finditer(motif, page, re.IGNORECASE):
                pages_html.append(
                    urllib.parse.urljoin(adresse, _html.unescape(trouve.group(1)))
                )

    for adresse, page in list(pages.items()):
        extraire_scripts(adresse, page)

    # Les pages HTML supplémentaires amènent souvent leurs propres scripts.
    for url in pages_html[:8]:
        if url in pages:
            continue
        try:
            status, body, _headers = http.request(url, method="GET")
        except BboxError:
            continue
        if status >= 400 or not body:
            continue
        pages[url] = body
        extraire_scripts(url, body)

    # Un script peut charger d'autres scripts (chunks d'une application
    # monopage) : on suit ces références également.
    for _tour in range(3):
        if len(urls) >= args.max_files:
            break
        nouveaux: list[str] = []
        for url in list(dict.fromkeys(urls)):
            if len(urls) + len(nouveaux) >= args.max_files:
                break
            try:
                status, body, _headers = http.request(url, method="GET")
            except BboxError:
                continue
            if status >= 400 or not body:
                continue
            for trouve in re.finditer(
                r"""["']([A-Za-z0-9_./-]+\.js(?:\?[^"']*)?)["']""", body
            ):
                nouveau = urllib.parse.urljoin(url, _html.unescape(trouve.group(1)))
                if nouveau not in urls and nouveau not in nouveaux:
                    nouveaux.append(nouveau)
            if len(urls) + len(nouveaux) >= args.max_files:
                break
        urls.extend(nouveaux)

    # Déduplication, en gardant l'ordre.
    uniques: list[str] = []
    for url in urls:
        if url not in uniques:
            uniques.append(url)
    urls = uniques[: args.max_files]
    print(f"{len(urls)} fichier(s) JavaScript à analyser.")

    scripts: list[tuple[str, str]] = []
    for url in urls:
        try:
            status, body, _headers = http.request(url, method="GET")
        except BboxError:
            print(f"  ✗ {url} : inaccessible")
            continue
        if status >= 400 or not body:
            print(f"  ✗ {url} : HTTP {status}")
            continue
        scripts.append((url, body))
        nom = url.rsplit("/", 1)[-1][:60]
        print(f"  ✓ {nom} ({len(body)} octets)")

    # -- Recherche de routes d'API -----------------------------------------
    routes: dict[str, set[str]] = {}
    for url, body in scripts:
        for motif in (UI_API_RE, UI_PATH_RE):
            for trouve in motif.finditer(body):
                chemin = trouve.group(1)
                if len(chemin) < 4 or chemin.startswith(("http", "//")):
                    continue
                if not any(
                    mot in chemin.lower()
                    for mot in (
                        "mac",
                        "host",
                        "acl",
                        "access",
                        "filter",
                        "wlan",
                        "lan",
                        "internet",
                        "policy",
                    )
                ):
                    continue
                routes.setdefault(chemin, set()).add(url.rsplit("/", 1)[-1])

    if routes:
        print()
        print("Routes d'API liées aux hôtes / accès trouvées dans le JS :")
        for chemin in sorted(routes):
            fichiers = ", ".join(sorted(routes[chemin])[:3])
            print(f"  {chemin}   [{fichiers}]")
    else:
        print()
        print(
            "Aucune route d'API mise en évidence : l'interface charge "
            "probablement ses scripts autrement (voir la liste des fichiers "
            "ci-dessus et utilisez --grep)."
        )

    # -- Recherche de jetons ----------------------------------------------
    cibles = list(UI_SCAN_TOKENS)
    for extra in (getattr(args, "grep", None) or "").split(","):
        extra = extra.strip()
        if extra:
            cibles.append(extra)

    print()
    print("Jetons recherchés :")
    for jeton in cibles:
        occurrences = []
        for url, body in scripts:
            position = 0
            while True:
                position = body.lower().find(jeton.lower(), position)
                if position < 0:
                    break
                debut = max(0, position - 160)
                fin = min(len(body), position + len(jeton) + 160)
                extrait = " ".join(body[debut:fin].split())
                occurrences.append((url.rsplit("/", 1)[-1], extrait))
                position += len(jeton)
                if len(occurrences) >= args.max_hits:
                    break
            if len(occurrences) >= args.max_hits:
                break
        if occurrences:
            print(f"  • {jeton} ({len(occurrences)} occurrence(s))")
            for nom, extrait in occurrences[: args.max_hits]:
                print(f"      [{nom}] …{extrait}…")
    return 0


def cmd_firewall_switch(args: argparse.Namespace) -> int:
    """Affiche (ou modifie) les interrupteurs du pare-feu de la box.

    ``--mac-filter on|off`` active ou désactive le filtre MAC réseau.
    C'est un réglage **global** : le filtre MAC s'applique ensuite à tous
    les postes selon la politique en place.
    """
    client = build_client(args)
    etat = client.firewall_switch_state()
    if not etat:
        print("ERREUR Impossible de lire security/firewall-switch.")
        return 2
    action = getattr(args, "mac_filter", None)
    if action:
        if getattr(args, "dry_run", False):
            cible = "1" if action == "on" else "0"
            print(f"Simulation : "
                  f"firewallmacfilterswitch = {cible}")
            return 0
        if action == "on":
            print(
                "⚠ Activation du filtre MAC réseau : il s'applique à TOUS les "
                "postes selon la politique en place. Vérifiez ensuite que "
                "votre foyer a toujours Internet."
            )
        ok = client._set_firewall_switch({"firewallmacfilterswitch": "1" if action == "on" else "0"})
        if not ok:
            print("ERREUR La box n'a pas enregistré la modification.")
            return 1
        print("OK")
        return 0
    for cle, valeur in sorted(etat.items()):
        print(f"{cle} = {valeur}")
    return 0


def cmd_wol(args: argparse.Namespace) -> int:
    """Réveille un appareil par Wake-on-LAN."""
    try:
        wake_on_lan(args.mac, broadcast=args.broadcast, port=args.port)
    except BboxError as err:
        print(f"ERREUR {err}")
        return 2
    print(f"Paquet magique envoyé à {args.mac} ({args.broadcast}:{args.port}).")
    return 0


def cmd_mac_filter(args: argparse.Namespace) -> int:
    """Affiche l'état du filtre MAC réseau, ou teste une écriture.

    Sans option, la commande affiche l'état (politique et liste).

    Avec ``--test MAC``, elle essaie **chaque forme d'écriture possible**,
    une par une, et dit laquelle la box accepte : c'est le moyen le plus
    rapide de découvrir le format attendu par un firmware donné. L'état
    d'origine est restauré si aucune forme ne passe.
    """
    client = build_client(args)
    try:
        etat = client.mac_filter_state()
    except BboxError as err:
        print(f"ERREUR {err}")
        return 2
    if not etat["supported"]:
        print("Cette box n'expose pas le filtre MAC réseau.")
        return 1
    if not getattr(args, "test", None):
        print(f"Politique : {etat['policy']}  (forme : {etat['shape']})")
        if etat["macs"]:
            print(f"{len(etat['macs'])} MAC filtrée(s) :")
            for mac in etat["macs"]:
                print(f"  - {mac}")
        else:
            print("Aucune MAC filtrée.")
        return 0

    # --- Test d'écriture ------------------------------------------------
    # Le filtre MAC a peut-être son propre interrupteur d'activation :
    # sans lui, la box ignore les écritures. On affiche son état.
    try:
        brut = client._api_get("security/firewall-switch")
        champs = {
            element.tag.lower(): (element.text or "").strip()
            for element in ET.fromstring(brut).iter()
            if element.tag and (element.text or "").strip()
        }
        pertinent = {
            cle: valeur
            for cle, valeur in champs.items()
            if "mac" in cle or "firewall" in cle or "switch" in cle
        }
        if pertinent:
            print("État des interrupteurs du pare-feu :")
            for cle, valeur in sorted(pertinent.items()):
                marque = "  ⚠" if "mac" in cle and valeur in ("0", "false") else "   "
                print(f"{marque} {cle} = {valeur}")
            if any(
                "mac" in cle and valeur in ("0", "false")
                for cle, valeur in pertinent.items()
            ):
                print(
                    "  → le filtre MAC semble DÉSACTIVÉ : la box ignore les "
                    "écritures tant qu'il ne l'est pas."
                )
                print(
                    "    On peut l'activer avec :  "
                    "bbox_ha.py firewall-switch --mac-filter on"
                )
    except (BboxError, ET.ParseError):
        print("Interrupteurs du pare-feu : illisibles.")

    mac = _norm_mac(args.test)
    politique = getattr(args, "policy", None) or "2"
    print(f"Test d'écriture : {mac} (politique {politique})")
    try:
        brut = client._api_get("security/mac-filter")
        print("XML reçu de la box :")
        print("  " + " ".join(brut.split())[:400])
    except BboxError as err:
        print(f"XML illisible : {err}")
    print(f"État d'origine : politique {etat['policy']}, "
          f"liste {', '.join(etat['macs']) if etat['macs'] else 'vide'}")
    # Le test est un outil d'**exploration** : on essaie toutes les formes
    # possibles, y compris celles qui peuvent modifier la table avant que la
    # box ne les annule. En usage normal, seule la liste « sûre » est
    # employée (voir _mac_filter_candidates).
    candidats = client._mac_filter_candidates_exhaustive([mac], politique)
    reference = " ".join(str(client._api_get("security/mac-filter")).split())
    print(f"{len(candidats)} écritures essayées…")
    reactives = 0
    refusees = 0
    for numero, xml in enumerate(candidats, 1):
        apercu = " ".join(xml.split())
        try:
            corps = client._api_post("security/mac-filter", xml)
            reponse = " ".join(str(corps).split())
        except BboxError as err:
            corps = ""
            reponse = f"erreur : {err}"
        # La box applique l'écriture de façon asynchrone : quand elle
        # refuse une forme, elle vide la table *puis* la restaure une
        # fraction de seconde plus tard. On attend donc que le résultat
        # se stabilise avant de relire.
        time.sleep(0.6)
        etat_apres = " ".join(str(client._api_get("security/mac-filter")).split())
        if not client._is_ok(corps):
            # La box a explicitement refusé cette forme (code d'erreur).
            # Aucune conclusion positive n'est possible, même si la table
            # contient la MAC : elle l'a simplement restaurée.
            refusees += 1
            if refusees <= 3:
                print(f"  ✗ forme {numero} : refusée par la box")
                print(f"    XML envoyé : {apercu[:300]}")
                print(f"    Réponse    : {reponse[:300]}")
            continue
        if etat_apres == reference:
            # La box n'a rien changé : forme ignorée, on passe à la suite.
            continue
        # La box a réagi : on montre tout (les trois premières seulement,
        # pour que la sortie reste lisible dans un terminal).
        reactives += 1
        if reactives <= 3:
            print(f"  ✔ forme {numero} : la table a changé !")
            print(f"    XML envoyé : {apercu[:300]}")
            print(f"    Réponse    : {reponse[:300]}")
            print(f"    Table      : {etat_apres[:300]}")
        verification = client.mac_filter_state()
        if set(verification["macs"]) == {mac}:
            print(f"  ✔ forme {numero} acceptée (politique {verification['policy']})")
            print("    (commande « block --scope lan » pour l'utiliser)")
            return 0
    if reactives > 3:
        print(f"  … {reactives} écritures ont fait réagir la box.")
    if refusees:
        print(f"  … {refusees} écritures ont été explicitement refusées "
              f"(code d'erreur).")
    # Aucune forme n'a passé : on restaure l'état d'origine.
    client._ecrire_mac_filtre(list(etat["macs"]), etat["policy"])
    print("Aucune des écritures n'a été acceptée : état d'origine restauré.")
    return 1


def cmd_unblock(args: argparse.Namespace) -> int:
    """Autorise de nouveau un client."""
    client = build_client(args)
    if getattr(args, "dry_run", False):
        return _simuler(client, args.mac, False, args.scope)
    client.set_device_blocked(args.mac, False, scope=args.scope)
    Cache(args.cache, args.ttl).invalidate()
    print("OK")
    return 0


def cmd_wifi_state(args: argparse.Namespace) -> int:
    """Affiche ON, OFF ou UNKNOWN pour une bande Wi-Fi."""
    state = get_state(args)
    band = args.band
    if band == "all":
        values = [v for v in state["wifi"].values() if v is not None]
        if not values:
            print("UNKNOWN")
        else:
            print("ON" if any(values) else "OFF")
        return 0
    value = state["wifi"].get(band)
    if value is None:
        print("UNKNOWN")
    else:
        print("ON" if value else "OFF")
    return 0


def cmd_wifi(args: argparse.Namespace) -> int:
    """Allume ou éteint une bande Wi-Fi."""
    client = build_client(args)
    client.set_wifi(args.band, args.action == "on")
    Cache(args.cache, args.ttl).invalidate()
    print("OK")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    """Ferme la session d'administration ouverte sur la box.

    Cette commande ne doit échouer dans aucun cas : c'est l'échappatoire
    quand la box refuse une nouvelle connexion parce qu'une session est
    déjà ouverte. On construit donc le client sans tenter de se connecter.
    """
    config = load_config(args.config)
    host = args.host or config.get("host") or CANDIDATE_HOSTS[0]
    session_file = getattr(args, "session", None) or os.path.join(
        os.path.dirname(os.path.abspath(args.config)), "session.json"
    )
    password = args.password or config.get("password") or ""
    username = args.username or config.get("username") or "admin"
    family = args.family or config.get("family") or "auto"
    if family == "sagemcom":
        print("Cette box n'utilise pas de session persistante.")
        return 0
    client = HuaweiCpe(
        host,
        password,
        username=username,
        use_tls=bool(config.get("use_tls")) if config.get("use_tls") is not None else False,
        session_file=session_file,
    )
    try:
        client.logout()
    except BboxError as err:
        print(f"Aucune session à fermer ({err})")
        return 0
    Cache(args.cache, args.ttl).invalidate()
    print("OK")
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    """Affiche la réponse brute d'un chemin d'API (diagnostic avancé).

    ``--post`` envoie un XML arbitraire au lieu d'interroger : c'est
    l'outil pour découvrir le format exact qu'une table attend, en
    voyant la réponse brute de la box (code d'erreur compris).
    """
    client = build_client(args)
    contenu = getattr(args, "post", None)
    if contenu:
        try:
            corps = client._api_post(args.path, contenu)
        except BboxError as err:
            print(f"ERREUR {err}")
            return 2
        print(corps)
        return 0
    print(client.raw(args.path))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Sonde une liste de points d'entrée de l'API et affiche les réponses."""
    client = build_client(args)
    paths = args.paths or WLAN_PROBE_PATHS
    if getattr(args, "hosts_only", False):
        paths = HOST_PROBE_PATHS
    if getattr(args, "security_only", False):
        paths = SECURITY_PROBE_PATHS
    for path in paths:
        try:
            raw = client.raw(path)
        except BboxError as err:
            print(f"{path:<40} ✗ {err}")
            continue
        code, message = ("", "")
        parse = getattr(client, "_parse_error", None)
        if parse is not None:
            code, message = parse(raw)
        if code:
            print(f"{path:<40} ✗ code {code} — {message}")
        else:
            content = " ".join(str(raw).strip().split())[:110]
            print(f"{path:<40} ✓ {content}")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    """Détecte la box et l'API disponible."""
    config = load_config(args.config)
    password = args.password or config.get("password") or ""
    username = args.username or config.get("username") or "admin"
    hosts = [args.host] if args.host else (config.get("hosts") or CANDIDATE_HOSTS)
    for host in hosts:
        try:
            client = detect_client(
                host, password, username=username, family=args.family or "auto"
            )
        except BboxError as err:
            print(f"{host}: aucune API reconnue ({err})")
            continue
        print(f"{host}: {client.name} détecté")
        state = client.state()
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    print("Aucune box Bouygues détectée.")
    return 1


def _tcp_check(host: str, ports: tuple[int, ...] = (443, 80)) -> tuple[bool, list[str]]:
    """Teste l'accessibilité TCP d'un hôte."""
    import socket

    target = host.split(":")[0]
    explicit = None
    if ":" in host:
        try:
            explicit = int(host.rsplit(":", 1)[1])
        except ValueError:
            explicit = None
    if explicit:
        ports = (explicit,)
    messages: list[str] = []
    reachable = False
    for port in ports:
        try:
            with socket.create_connection((target, port), timeout=4):
                messages.append(f"port {port} ouvert")
                reachable = True
        except OSError as err:
            messages.append(f"port {port} : {err.strerror or err}")
    return reachable, messages


def _dns_check(name: str = "mabbox.bytel.fr") -> str | None:
    """Résout un nom d'hôte (renvoie l'adresse ou None)."""
    import socket

    try:
        infos = socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return None
    return ", ".join(sorted({info[4][0] for info in infos}))


def run_diagnostic(args: argparse.Namespace) -> int:
    """Rapport de diagnostic complet : réseau, API, fonctions disponibles."""
    settings = load_config(args.config)
    host = args.host or settings.get("host") or ""
    password = args.password if args.password is not None else settings.get("password", "")
    username = args.username or settings.get("username", "admin")
    family = args.family or settings.get("family", "auto")
    hosts = [host] if host else (settings.get("hosts") or CANDIDATE_HOSTS)

    print("=" * 70)
    print(" Diagnostic Bbox (Bouygues) ↔ Home Assistant")
    print("=" * 70)
    print(f"Python        : {sys.version.split()[0]}")
    print(f"Configuration : {args.config}")
    print(f"Cache         : {args.cache}")
    print(f"Session       : {getattr(args, 'session', None) or '(à côté de la configuration)'}")
    print(f"Adresse       : {host or '(à déterminer)'}")

    resolved = _dns_check()
    print("\n[1] Résolution DNS de mabbox.bytel.fr (information)")
    if resolved:
        print(f"  ✓ résolu : {resolved}")
    else:
        print("  ! non résolu depuis ce conteneur — c'est normal :")
        print("    utilisez l'adresse IP de la box (ou ajoutez une entrée")
        print("    extra_hosts dans votre docker-compose.yml).")

    client = None
    print("\n[2] Recherche de la box")
    for candidate in hosts:
        reachable, messages = _tcp_check(candidate)
        print(f"  --- {candidate} : {'joignable' if reachable else 'injoignable'}")
        for message in messages:
            print(f"      {message}")
        if not reachable:
            continue
        try:
            client = detect_client(
                candidate,
                password,
                username=username,
                family=family,
                session_file=getattr(args, "session", None),
            )
            break
        except BboxError as err:
            print(f"      ✗ {err}")

    if client is None:
        print("\n✗ Aucune box Bouygues détectée.")
        print("  → Vérifiez l'adresse IP de la box et le mot de passe.")
        return 1

    print(f"\n[3] API : {client.name} détectée")
    try:
        state = client.state()
    except BboxError as err:
        print(f"  ✗ lecture de l'état impossible : {err}")
        return 1
    print(f"  ✓ Modèle        : {state['model']}")
    if state.get("firmware"):
        print(f"  ✓ Firmware      : {state['firmware']}")
    print(f"  ✓ Clients       : {state['nb_connectes']} connecté(s) / {state['nb_total']}")
    print(f"  ✓ Wi-Fi         : {state['wifi']}")

    print("\n[4] Fonctions disponibles")
    if client.family == "sagemcom":
        print("  ✓ extinction/allumage du Wi-Fi (wireless)")
        print("  ✓ blocage/déblocage d'un client (parentalcontrol)")
    else:
        print("  Points d'entrée exposés par le firmware :")
        for path in WLAN_PROBE_PATHS:
            try:
                raw = client.raw(path)
            except BboxError as err:
                print(f"    {path:<38} ✗ {err}")
                continue
            code, message = client._parse_error(raw)
            if code:
                print(f"    {path:<38} ✗ code {code}")
            else:
                content = " ".join(str(raw).strip().split())[:70]
                print(f"    {path:<38} ✓ {content}")
        macs = [d["mac"] for d in state["devices"]]
        if macs:
            _, supported = client.mac_filter()
            if supported:
                print("  ✓ filtre MAC : un appareil peut être déconnecté")
                print(f"      (testez : bbox_ha.py block {macs[0]})")
            else:
                print("  ✗ filtre MAC non exposé par ce firmware")
                print("      → la déconnexion d'un appareil précis n'est pas")
                print("        possible par l'API ; coupez le Wi-Fi depuis")
                print("        l'interface web de la box.")
        wifi_raw = client.wireless().get("raw")
        if wifi_raw:
            print("  · état Wi-Fi brut :")
            for line in str(wifi_raw).strip().splitlines()[:6]:
                print(f"      {line}")
        print("  · coupure du Wi-Fi : testez-la avec l'interrupteur")
        print("    « Bbox WiFi » du tableau de bord (non testé ici pour")
        print("    ne pas couper votre réseau par erreur).")

    print("\n" + "=" * 70)
    print(" RÉSULTAT : la box répond et l'intégration peut être installée.")
    print("=" * 70)
    return 0


def cmd_diag(args: argparse.Namespace) -> int:
    """Rapport de diagnostic complet."""
    try:
        return run_diagnostic(args)
    except BboxError as err:
        print(f"ERREUR: {err}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    """Construit l'analyseur d'arguments."""
    parser = argparse.ArgumentParser(
        description="Client local des box Bouygues (Bbox Sagemcom et Bbox 4G/5G Huawei)."
    )
    parser.add_argument(
        "--dump",
        default="",
        metavar="FICHIER",
        help=(
            "écrit la sortie complète de la commande dans ce fichier "
            "(la console n'affiche qu'un résumé)"
        ),
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="fichier de configuration")
    parser.add_argument("--cache", default=DEFAULT_CACHE, help="fichier de cache")
    parser.add_argument("--session", default=None, help="fichier de session")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL, help="durée de cache (s)")
    parser.add_argument("--host", help="adresse IP de la box")
    parser.add_argument("--password", help="mot de passe administrateur de la box")
    parser.add_argument("--username", default=None, help="identifiant (box Huawei)")
    parser.add_argument(
        "--family",
        choices=["auto", "sagemcom", "huawei"],
        default=None,
        help="famille d'API à utiliser",
    )
    parser.add_argument("--force", action="store_true", help="ignore le cache")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("state", help="état complet au format JSON").set_defaults(func=cmd_state)
    sub.add_parser("count", help="nombre de clients connectés").set_defaults(func=cmd_count)
    sub.add_parser("list", help="tableau des clients").set_defaults(func=cmd_list)
    sub.add_parser("detect", help="détecte la box").set_defaults(func=cmd_detect)
    sub.add_parser("diag", help="rapport de diagnostic").set_defaults(func=cmd_diag)
    sub.add_parser(
        "logout", help="ferme la session d'administration sur la box"
    ).set_defaults(func=cmd_logout)

    p_presence = sub.add_parser("presence", help="1 si le client est connecté")
    p_presence.add_argument("mac")
    p_presence.set_defaults(func=cmd_presence)

    p_blocked = sub.add_parser("blocked", help="BLOCKED ou ALLOWED")
    p_blocked.add_argument("mac")
    p_blocked.set_defaults(func=cmd_blocked)

    p_block = sub.add_parser("block", help="bloque (déconnecte) un client")
    p_block.add_argument("mac")
    p_block.add_argument(
        "--scope",
        choices=["lan", "wifi", "internet", "all", "both"],
        default="lan",
        help=(
            "portée du blocage : lan (filtre MAC réseau), wifi (liste noire "
            "Wi-Fi), internet (commutateur « Accès Internet » du routeur), "
            "all (défaut : lan)"
        ),
    )
    p_block.add_argument(
        "--dry-run",
        action="store_true",
        help="affiche ce qui serait écrit, sans rien modifier",
    )
    p_block.set_defaults(func=cmd_block)

    p_unblock = sub.add_parser("unblock", help="autorise un client")
    p_unblock.add_argument("mac")
    p_unblock.add_argument(
        "--scope",
        choices=["lan", "wifi", "internet", "all", "both"],
        default="lan",
        help=(
            "portée du blocage : lan (filtre MAC réseau), wifi (liste noire "
            "Wi-Fi), internet (commutateur « Accès Internet » du routeur), "
            "all (défaut : lan)"
        ),
    )
    p_unblock.add_argument(
        "--dry-run",
        action="store_true",
        help="affiche ce qui serait écrit, sans rien modifier",
    )
    p_unblock.set_defaults(func=cmd_unblock)

    p_snap = sub.add_parser(
        "snapshot",
        help="enregistre l'état de toutes les tables candidates dans un fichier",
    )
    p_snap.add_argument(
        "--out", help="fichier de destination (JSON). Sans option : sur stdout"
    )
    p_snap.add_argument(
        "--tables",
        nargs="*",
        help="tables à relever (défaut : les tables candidates)",
    )
    p_snap.set_defaults(func=cmd_snapshot)

    p_diff = sub.add_parser(
        "diff", help="compare deux instantanés et affiche les changements"
    )
    p_diff.add_argument("avant", help="fichier du premier instantané")
    p_diff.add_argument("apres", help="fichier du second instantané")
    p_diff.set_defaults(func=cmd_diff)

    p_ui = sub.add_parser(
        "ui-scan",
        help="analyse le JavaScript de l'interface web pour trouver l'API",
    )
    p_ui.add_argument(
        "--base", default="/", help="chemin de l'interface web (défaut : /)"
    )
    p_ui.add_argument(
        "--max-files", type=int, default=40, help="nombre maximum de fichiers JS"
    )
    p_ui.add_argument(
        "--max-hits", type=int, default=5, help="occurrences affichées par jeton"
    )
    p_ui.add_argument(
        "--timeout", type=int, default=15, help="délai réseau (secondes)"
    )
    p_fw = sub.add_parser(
        "firewall-switch", help="interrupteurs du pare-feu (dont le filtre MAC)"
    )
    p_fw.add_argument(
        "--mac-filter",
        choices=["on", "off"],
        help="active ou désactive le filtre MAC réseau",
    )
    p_fw.add_argument(
        "--dry-run",
        action="store_true",
        help="affiche ce qui serait écrit, sans rien modifier",
    )
    p_fw.set_defaults(func=cmd_firewall_switch)

    p_wol = sub.add_parser(
        "wol", help="réveille un appareil par Wake-on-LAN (paquet magique)"
    )
    p_wol.add_argument("mac")
    p_wol.add_argument(
        "--broadcast", default="255.255.255.255", help="adresse de diffusion"
    )
    p_wol.add_argument("--port", type=int, default=9, help="port UDP (défaut : 9)")
    p_wol.set_defaults(func=cmd_wol)

    p_mf = sub.add_parser(
        "mac-filter", help="état du filtre MAC réseau (politique et liste)"
    )
    p_mf.add_argument(
        "--test",
        default="",
        help="essaie d'écrire cette MAC et rapporte la forme acceptée",
    )
    p_mf.add_argument(
        "--policy",
        default="2",
        help="politique à écrire pendant le test (défaut : 2, liste noire)",
    )
    p_mf.set_defaults(func=cmd_mac_filter)

    p_ui.add_argument(
        "--grep",
        default="",
        help="mots-clés supplémentaires à chercher, séparés par des virgules",
    )
    p_ui.set_defaults(func=cmd_ui_scan)

    p_wifi_filters = sub.add_parser(
        "wifi-filters", help="filtre MAC Wi-Fi, SSID par SSID"
    )
    p_wifi_filters.set_defaults(func=cmd_wifi_filters)

    p_host_info = sub.add_parser(
        "host-info", help="détail des champs d'un appareil"
    )
    p_host_info.add_argument(
        "mac", nargs="?", help="adresse MAC (par défaut : le premier appareil)"
    )
    p_host_info.set_defaults(func=cmd_host_info)

    p_host_table = sub.add_parser(
        "host-table", help="tableau compact des appareils (une ligne chacun)"
    )
    p_host_table.set_defaults(func=cmd_host_table)

    p_watch = sub.add_parser(
        "watch-hosts",
        help="surveille la table des hôtes pendant N secondes (repère le "
        "commutateur d'accès Internet)",
    )
    p_watch.add_argument(
        "--seconds", type=int, default=20, help="durée de la veille (défaut 20)"
    )
    p_watch.add_argument(
        "--tables",
        nargs="*",
        help="tables à surveiller (défaut : les tables candidates)",
    )
    p_watch.set_defaults(func=cmd_watch_hosts)

    p_wifi_state = sub.add_parser("wifi-state", help="ON ou OFF")
    p_wifi_state.add_argument("band", choices=["24", "5", "guest", "all"])
    p_wifi_state.set_defaults(func=cmd_wifi_state)

    p_probe = sub.add_parser(
        "probe", help="sonde plusieurs points d'entrée de l'API"
    )
    p_probe.add_argument("paths", nargs="*", help="chemins d'API à sonder")
    p_probe.add_argument(
        "--hosts",
        dest="hosts_only",
        action="store_true",
        help="sonde les tables d'hôtes et de contrôle d'accès",
    )
    p_probe.add_argument(
        "--security",
        dest="security_only",
        action="store_true",
        help="sonde les tables de sécurité (pare-feu, filtre MAC, NAT…)",
    )
    p_probe.set_defaults(func=cmd_probe)

    p_raw = sub.add_parser("raw", help="réponse brute d'un chemin d'API")
    p_raw.add_argument("path")
    p_raw.add_argument(
        "--post",
        default="",
        help="envoie ce XML au chemin au lieu d'interroger (diagnostic)",
    )
    p_raw.set_defaults(func=cmd_raw)

    p_wifi = sub.add_parser("wifi", help="allume ou éteint le Wi-Fi")
    p_wifi.add_argument("band", choices=["24", "5", "guest", "all"])
    p_wifi.add_argument("action", choices=["on", "off"])
    p_wifi.set_defaults(func=cmd_wifi)

    # L'option « --out » est acceptée avant **ou** après la sous-commande.
    # SUPPRESS évite que la valeur par défaut du sous-analyseur n'écrase
    # celle donnée au niveau global.
    for sous in sub.choices.values():
        sous.add_argument(
            "--dump",
            default=argparse.SUPPRESS,
            metavar="FICHIER",
            help="écrit la sortie complète dans ce fichier",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée.

    L'option globale ``--out FICHIER`` redirige la sortie complète de la
    commande dans un fichier : la console n'affiche alors qu'un résumé.
    Pratique quand la sortie dépasse la taille d'un terminal (SONDE de
    sécurité, diagnostic du filtre MAC…).
    """
    args = build_parser().parse_args(argv)
    sortie = getattr(args, "dump", None)
    if not sortie:
        try:
            return args.func(args)
        except BboxError as err:
            print(f"ERREUR: {err}", file=sys.stderr)
            return 2

    # Redirection : on capture tout ce qui est imprimé.
    tampon = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(tampon):
            code = args.func(args)
    except BboxError as err:
        tampon.write(f"ERREUR: {err}\n")
        code = 2
    texte = tampon.getvalue()
    try:
        with open(sortie, "w", encoding="utf-8") as handle:
            handle.write(texte)
    except OSError as err:
        print(f"ERREUR: impossible d'écrire {sortie} : {err}", file=sys.stderr)
        return 2
    lignes = texte.count("\n")
    print(f"→ sortie complète écrite dans {sortie} ({lignes} lignes)")
    # On montre malgré tout les lignes les plus utiles : celles qui
    # signalent un résultat.
    utiles = [
        ligne
        for ligne in texte.splitlines()
        if ligne.startswith(("  ✔", "  ✗", "✔", "✗", "ERREUR", "  →"))
    ]
    for ligne in utiles[:6]:
        print(ligne[:200])
    if len(utiles) > 6:
        print(f"… et {len(utiles) - 6} autre(s) ligne(s) dans le fichier.")
    return code


if __name__ == "__main__":
    sys.exit(main())
