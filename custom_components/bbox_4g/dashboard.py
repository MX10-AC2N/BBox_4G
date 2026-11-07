"""Génération des tableaux de bord Lovelace de la box.

Les tableaux de bord sont des **fichiers « Vue YAML »** : ils sont
référencés une fois dans l'interface de Home Assistant
(*Paramètres → Tableaux de bord → Ajouter → Vue YAML*).

Trois variantes sont proposées :

* ``bbox.yaml``         — cartes natives (aucune carte communautaire) ;
* ``bbox-auto.yaml``    — carte communautaire « auto-entities » (liste
  entièrement dynamique, recommandée) ;
* ``bbox-mushroom.yaml`` — cartes Mushroom (rendu soigné).

L'écriture automatique dans ``/config/lovelace/`` est **facultative** : elle
est désactivée par défaut. Activez l'option « Écrire les tableaux de bord »
dans les options de l'intégration.

Les ``entity_id`` repris dans les cartes natives sont ceux **réellement
enregistrés** par Home Assistant (lus dans le registre des entités).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .helpers import slugify

_LOGGER = logging.getLogger(__name__)

DASHBOARD_FILE = "bbox.yaml"
DASHBOARD_COMMUNAUTAIRE = "bbox-auto.yaml"
DASHBOARD_MUSHROOM = "bbox-mushroom.yaml"

ENTETE = """# Tableau de bord « Bbox » — généré par l'intégration Bbox Bouygues.
# Ce fichier est une vue YAML.
#
# Comment l'ajouter dans Home Assistant :
#   1. Paramètres → Tableaux de bord → Ajouter un tableau de bord
#   2. Choisir « Vue YAML »
#   3. Indiquer le fichier : /config/lovelace/bbox.yaml
#      (ou bbox-auto.yaml / bbox-mushroom.yaml)
#
# Prérequis optionnels :
#   - bbox-auto.yaml     → carte « auto-entities » (HACS)
#   - bbox-mushroom.yaml → cartes « Mushroom » (HACS)
"""

#: Entités de la box (indépendantes des appareils clients).
ENTITES_BOX = [
    "switch.bbox_wifi",
    "switch.bbox_wifi_24_ghz",
    "switch.bbox_wifi_5_ghz",
    "sensor.bbox_clients_connectes",
    "sensor.bbox_appareils_connus",
    "sensor.bbox_reseau_invite",
    "button.bbox_actualiser",
    "button.bbox_reconnecter",
]


def _est_wifi(device: dict[str, Any]) -> bool:
    """L'appareil est-il rattaché à un réseau Wi-Fi ?"""
    if device.get("ssid"):
        return True
    lien = str(device.get("link") or "").lower()
    return "wifi" in lien or "wireless" in lien


def _slug(device: dict[str, Any]) -> str:
    """Identifiant lisible d'un appareil (servait de repli)."""
    return slugify(device.get("name") or device.get("mac") or "appareil")


def _entity_id(
    hass: HomeAssistant | None,
    domain: str,
    unique_id: str,
    repli: str,
) -> str:
    """``entity_id`` réel d'une entité, lu dans le registre."""
    if hass is not None:
        try:
            trouve = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
        except Exception:  # pragma: no cover
            trouve = None
        if trouve:
            return trouve
    return repli


def _ids_appareils(
    hass: HomeAssistant | None, devices: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Identifiants des entités de chaque appareil."""
    resultat: dict[str, dict[str, str]] = {}
    for device in devices:
        mac = str(device.get("mac", "")).lower()
        if not mac:
            continue
        slug = _slug(device)
        entrees = {
            "acces_internet": _entity_id(
                hass, "switch", f"internet_{mac}", f"switch.bbox_acces_internet_{slug}"
            ),
            "connexion": _entity_id(
                hass, "switch", f"device_{mac}", f"switch.bbox_connexion_{slug}"
            ),
            "presence": _entity_id(
                hass,
                "binary_sensor",
                f"presence_{mac}",
                f"binary_sensor.bbox_presence_{slug}",
            ),
            "separate": _entity_id(
                hass,
                "switch",
                f"separate_{mac}",
                f"switch.bbox_appareil_independant_{slug}",
            ),
        }
        if _est_wifi(device):
            entrees["wifi"] = _entity_id(
                hass, "switch", f"wifi_device_{mac}", f"switch.bbox_wifi_{slug}"
            )
        resultat[mac] = entrees
    return resultat


def _section_header(title: str, icon: str = "mdi:router-wireless") -> list[str]:
    return [
        "      - type: heading",
        f'        heading: "{title}"',
        f"        icon: {icon}",
    ]


def render(
    devices: list[dict[str, Any]], hass: HomeAssistant | None = None
) -> str:
    """Tableau de bord natif (aucune carte communautaire requise)."""
    ids = _ids_appareils(hass, devices)

    lignes = [
        ENTETE,
        "title: Bbox",
        "views:",
        "  - title: Appareils",
        "    path: bbox",
        "    icon: mdi:router-wireless",
        "    cards:",
        # --- État global de la box ---
        "      - type: entities",
        '        title: "État de la box"',
        "        show_header_toggle: false",
        "        entities:",
    ]
    for entite in ENTITES_BOX:
        lignes.append(f"          - entity: {entite}")

    # --- Appareils connectés ---
    lignes += [
        "      - type: entity-filter",
        '        title: "Appareils connectés"',
        "        icon: mdi:lan-connect",
        "        show_empty: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
        "        entities:",
    ]
    for entrees in ids.values():
        lignes.append(f"          - entity: {entrees['presence']}")
        lignes.append("            conditions:")
        lignes.append("              - condition: state")
        lignes.append(f"                entity_id: {entrees['presence']}")
        lignes.append('                state: "on"')
        lignes.append(f"          - entity: {entrees['acces_internet']}")
        lignes.append("            conditions:")
        lignes.append("              - condition: state")
        lignes.append(f"                entity_id: {entrees['presence']}")
        lignes.append('                state: "on"')
        if "wifi" in entrees:
            lignes.append(f"          - entity: {entrees['wifi']}")
            lignes.append("            conditions:")
            lignes.append("              - condition: state")
            lignes.append(f"                entity_id: {entrees['presence']}")
            lignes.append('                state: "on"')

    # --- Appareils déconnectés ---
    lignes += [
        "      - type: entity-filter",
        '        title: "Appareils déconnectés"',
        "        icon: mdi:lan-disconnect",
        "        show_empty: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
        "        entities:",
    ]
    for entrees in ids.values():
        lignes.append(f"          - entity: {entrees['presence']}")
        lignes.append("            conditions:")
        lignes.append("              - condition: state")
        lignes.append(f"                entity_id: {entrees['presence']}")
        lignes.append('                state: "off"')
        lignes.append(f"          - entity: {entrees['acces_internet']}")
        lignes.append("            conditions:")
        lignes.append("              - condition: state")
        lignes.append(f"                entity_id: {entrees['presence']}")
        lignes.append('                state: "off"')

    return "\n".join(lignes) + "\n"


def render_communautaire() -> str:
    """Tableau de bord dynamique basé sur auto-entities (recommandé)."""
    lignes = [
        ENTETE.replace("bbox.yaml", "bbox-auto.yaml"),
        "title: Bbox (auto-entities)",
        "views:",
        "  - title: Appareils",
        "    path: bbox-auto",
        "    icon: mdi:router-wireless",
        "    cards:",
        # --- En-tête box ---
        "      - type: entities",
        '        title: "État de la box"',
        "        show_header_toggle: false",
        "        entities:",
    ]
    for entite in ENTITES_BOX:
        lignes.append(f"          - entity: {entite}")

    # --- Connectés (dynamique) ---
    lignes += [
        "      - type: custom:auto-entities",
        '        title: "Appareils connectés"',
        "        show_empty: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
        "        filter:",
        "          include:",
        "            - entity_id: binary_sensor.*",
        "              attributes:",
        "                device_class: connectivity",
        '              state: "on"',
        "            - entity_id: switch.*",
        "              attributes:",
        "                appareil: client",
        '                etat_connexion: "connecte"',
        "          exclude:",
        "            - entity_id: switch.*appareil_independant*",
        "            - entity_id: switch.*separate*",
        "        sort:",
        "          method: name",
    ]

    # --- Déconnectés ---
    lignes += [
        "      - type: custom:auto-entities",
        '        title: "Appareils déconnectés"',
        "        show_empty: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
        "        filter:",
        "          include:",
        "            - entity_id: binary_sensor.*",
        "              attributes:",
        "                device_class: connectivity",
        '              state: "off"',
        "            - entity_id: switch.*",
        "              attributes:",
        "                appareil: client",
        '                etat_connexion: "deconnecte"',
        "          exclude:",
        "            - entity_id: switch.*appareil_independant*",
        "            - entity_id: switch.*separate*",
        "        sort:",
        "          method: name",
    ]

    # --- Accès Internet coupé ---
    lignes += [
        "      - type: custom:auto-entities",
        '        title: "Accès Internet coupé"',
        "        show_empty: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
        "        filter:",
        "          include:",
        "            - entity_id: switch.*",
        "              attributes:",
        "                appareil: client",
        '                acces: "coupe"',
        "        sort:",
        "          method: name",
    ]

    return "\n".join(lignes) + "\n"


def render_mushroom() -> str:
    """Tableau de bord Mushroom (rendu soigné + dynamique)."""
    lignes = [
        ENTETE.replace("bbox.yaml", "bbox-mushroom.yaml"),
        "title: Bbox (Mushroom)",
        "views:",
        "  - title: Appareils",
        "    path: bbox-mushroom",
        "    icon: mdi:router-wireless",
        "    cards:",
        # --- Chips box ---
        "      - type: custom:mushroom-chips-card",
        "        chips:",
        "          - type: entity",
        "            entity: sensor.bbox_clients_connectes",
        "            icon_color: blue",
        "          - type: entity",
        "            entity: switch.bbox_wifi",
        "            icon_color: blue",
        "          - type: entity",
        "            entity: switch.bbox_wifi_24_ghz",
        "            icon_color: blue",
        "          - type: entity",
        "            entity: switch.bbox_wifi_5_ghz",
        "            icon_color: blue",
        "          - type: entity",
        "            entity: button.bbox_actualiser",
        "            icon: mdi:refresh",
        # --- Résumé ---
        "      - type: custom:mushroom-template-card",
        "        icon: mdi:router-wireless",
        "        icon_color: blue",
        "        primary: >",
        "          {% set n = states('sensor.bbox_clients_connectes') | int(0) %}",
        "          {{ n }} appareil(s) connecté(s)",
        "        secondary: Bbox Bouygues",
        # --- Connectés ---
        "      - type: heading",
        '        heading: "Connectés"',
        "        icon: mdi:lan-connect",
        "      - type: custom:auto-entities",
        "        show_empty: true",
        "        card:",
        "          type: grid",
        "          columns: 1",
        "          square: false",
        "        card_param: cards",
        "        filter:",
        "          include:",
        "            - entity_id: switch.*",
        "              attributes:",
        "                appareil: client",
        '                etat_connexion: "connecte"',
        "          exclude:",
        "            - entity_id: switch.*appareil_independant*",
        "            - entity_id: switch.*separate*",
        "        options:",
        "          type: custom:mushroom-entity-card",
        "          fill_container: true",
        "          icon_color: green",
        "        sort:",
        "          method: name",
        # --- Déconnectés ---
        "      - type: heading",
        '        heading: "Déconnectés"',
        "        icon: mdi:lan-disconnect",
        "      - type: custom:auto-entities",
        "        show_empty: true",
        "        card:",
        "          type: grid",
        "          columns: 1",
        "          square: false",
        "        card_param: cards",
        "        filter:",
        "          include:",
        "            - entity_id: switch.*",
        "              attributes:",
        "                appareil: client",
        '                etat_connexion: "deconnecte"',
        "          exclude:",
        "            - entity_id: switch.*appareil_independant*",
        "            - entity_id: switch.*separate*",
        "        options:",
        "          type: custom:mushroom-entity-card",
        "          fill_container: true",
        "          icon_color: grey",
        "        sort:",
        "          method: name",
        # --- Accès coupé ---
        "      - type: heading",
        '        heading: "Accès Internet coupé"',
        "        icon: mdi:web-off",
        "      - type: custom:auto-entities",
        "        show_empty: true",
        "        card:",
        "          type: grid",
        "          columns: 1",
        "          square: false",
        "        card_param: cards",
        "        filter:",
        "          include:",
        "            - entity_id: switch.*",
        "              attributes:",
        "                appareil: client",
        '                acces: "coupe"',
        "        options:",
        "          type: custom:mushroom-entity-card",
        "          fill_container: true",
        "          icon_color: red",
        "        sort:",
        "          method: name",
    ]
    return "\n".join(lignes) + "\n"


def render_all(
    devices: list[dict[str, Any]], hass: HomeAssistant | None = None
) -> dict[str, str]:
    """Contenu des trois tableaux de bord."""
    return {
        DASHBOARD_FILE: render(devices, hass),
        DASHBOARD_COMMUNAUTAIRE: render_communautaire(),
        DASHBOARD_MUSHROOM: render_mushroom(),
    }


def _write(path: str, contenu: str) -> bool:
    """Écrit le fichier s'il a changé. Retourne ``True`` s'il a été écrit."""
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                if handle.read() == contenu:
                    return False
    except OSError:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(contenu)
    except OSError as err:  # pragma: no cover
        _LOGGER.warning("Unable to write the dashboard: %s", err)
        return False
    return True


async def async_write_dashboard(
    hass: HomeAssistant, devices: list[dict[str, Any]]
) -> bool:
    """Écrit (ou met à jour) les tableaux de bord Lovelace."""
    ecrit = False
    for nom, contenu in render_all(devices, hass).items():
        chemin = hass.config.path("lovelace", nom)
        if await hass.async_add_executor_job(_write, chemin, contenu):
            ecrit = True
    return ecrit
