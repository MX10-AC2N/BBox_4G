"""Génération des tableaux de bord Lovelace de la box.

Les tableaux de bord sont des **fichiers « Vue YAML »** : ils sont
référencés une fois dans l'interface de Home Assistant
(*Paramètres → Tableaux de bord → Ajouter → Vue YAML*).

Trois variantes sont proposées :

* ``bbox.yaml``       — cartes natives (aucune carte communautaire) ;
* ``bbox-auto.yaml``  — carte communautaire « auto-entities » (liste
  entièrement dynamique) ;
* ``bbox-mushroom.yaml`` — cartes Mushroom (rendu soigné).

L'écriture automatique dans ``/config/lovelace/`` est **facultative** : elle
est désactivée par défaut (une intégration ne doit pas modifier les
tableaux de bord de l'utilisateur sans son accord). Activez l'option
« Écrire les tableaux de bord » si vous le souhaitez.

Les ``entity_id`` repris dans les cartes sont ceux **réellement enregistrés**
par Home Assistant : ils sont lus dans le registre des entités, et non
recalculés, ce qui évite les décalages après un renommage d'appareil.
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
#: Second tableau de bord, basé sur la carte communautaire « auto-entities »
#: (liste entièrement dynamique, sans déclaration d'entités à maintenir).
DASHBOARD_COMMUNAUTAIRE = "bbox-auto.yaml"
#: Troisième tableau de bord, en cartes Mushroom (rendu soigné).
DASHBOARD_MUSHROOM = "bbox-mushroom.yaml"

ENTETE = """# Tableau de bord « Bbox » — généré par l'intégration « Bbox Bouygues ».
# Ce fichier est un mode YAML : référencez-le une fois depuis
# Paramètres → Tableaux de bord → Ajouter → Vue YAML.
"""

#: Entités de la box (indépendantes des appareils).
ENTITES_BOX = [
    "switch.bbox_wifi",
    "switch.bbox_wifi_24_ghz",
    "switch.bbox_wifi_5_ghz",
    "sensor.bbox_clients_connectes",
    "sensor.bbox_appareils_connus",
    "sensor.bbox_reseau_invite",
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
    """``entity_id`` réel d'une entité, lu dans le registre.

    Le registre fait foi : si l'appareil a été renommé, l'``entity_id``
    reste celui qui a été attribué à la création — c'est exactement ce que
    référence une carte.
    """
    if hass is not None:
        try:
            trouve = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
        except Exception:  # pragma: no cover - registre indisponible
            trouve = None
        if trouve:
            return trouve
    return repli


def _ids_appareils(
    hass: HomeAssistant | None, devices: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    """Identifiants des entités de chaque appareil.

    Retourne, pour chaque MAC : ``acces_internet``, ``wifi`` (uniquement
    pour les appareils Wi-Fi), ``connexion`` et ``presence``.
    """
    resultat: dict[str, dict[str, str]] = {}
    for device in devices:
        mac = str(device.get("mac", "")).lower()
        if not mac:
            continue
        slug = _slug(device)
        entrees = {
            "acces_internet": _entity_id(
                hass,
                "switch",
                f"internet_{mac}",
                f"switch.{slug}_acces_internet",
            ),
            "connexion": _entity_id(
                hass, "switch", f"device_{mac}", f"switch.{slug}_connexion"
            ),
            "presence": _entity_id(
                hass,
                "binary_sensor",
                f"presence_{mac}",
                f"binary_sensor.{slug}_presence",
            ),
        }
        if _est_wifi(device):
            entrees["wifi"] = _entity_id(
                hass, "switch", f"wifi_device_{mac}", f"switch.{slug}_wifi"
            )
        resultat[mac] = entrees
    return resultat


def _entrees(
    ids: dict[str, dict[str, str]], etat: str | None
) -> list[str]:
    """Une entrée par appareil, filtrée sur son capteur de présence.

    Chaque appareil offre deux interrupteurs : « Accès Internet » (qui
    marche aussi pour les appareils filaires) et « Wi-Fi » (uniquement pour
    ceux qui sont rattachés à un réseau Wi-Fi). C'est le capteur de
    présence qui décide dans quelle carte l'appareil apparaît.
    """
    lignes: list[str] = []
    for mac, entrees in ids.items():
        interrupteurs = [entrees["acces_internet"]]
        if "wifi" in entrees:
            interrupteurs.append(entrees["wifi"])
        presence = entrees["presence"]
        for entity in interrupteurs:
            lignes.append(f"          - entity: {entity}")
            if etat is None:
                continue
            lignes.append("            conditions:")
            lignes.append("              - condition: state")
            lignes.append(f"                entity_id: {presence}")
            lignes.append(f'                state: "{etat}"')
    return lignes


def _entrees_avancees(ids: dict[str, dict[str, str]]) -> list[str]:
    """Les interrupteurs « connexion » historiques (portée globale)."""
    return [
        f"          - entity: {entrees['connexion']}"
        for entrees in ids.values()
    ]


def render(
    devices: list[dict[str, Any]], hass: HomeAssistant | None = None
) -> str:
    """Construit le YAML du tableau de bord à partir des appareils connus."""
    ids = _ids_appareils(hass, devices)
    lignes = [
        ENTETE,
        "title: Bbox",
        "views:",
        "  - title: Appareils",
        "    path: bbox",
        "    icon: mdi:router-wireless",
        "    cards:",
        "      - type: entity-filter",
        '        title: "Appareils connectés (filaire + Wi-Fi)"',
        "        icon: mdi:wifi",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
        "        entities:",
    ]
    lignes += _entrees(ids, "on")
    lignes += [
        "      - type: entity-filter",
        '        title: "Appareils déconnectés"',
        "        icon: mdi:wifi-off",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
        "        entities:",
    ]
    lignes += _entrees(ids, "off")
    lignes += [
        "      - type: entity-filter",
        '        title: "Bloqués par la box"',
        "        icon: mdi:account-cancel",
        "        state_filter:",
        "          - attribute: bloque",
        "            value: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
        "        entities:",
    ]
    lignes += [
        f"          - entity: {entrees['acces_internet']}"
        for entrees in ids.values()
    ]
    lignes += [
        "      - type: entities",
        '        title: "Wi-Fi de la box"',
        "        entities:",
    ]
    lignes += [f"          - entity: {entite}" for entite in ENTITES_BOX]
    lignes += [
        "      - type: entities",
        '        title: "Interrupteur global (portée de l\'option)"',
        "        entities:",
    ]
    lignes += _entrees_avancees(ids)
    return "\n".join(lignes) + "\n"


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
    except OSError as err:  # pragma: no cover - droits d'écriture
        _LOGGER.warning("Impossible d'écrire le tableau de bord : %s", err)
        return False
    return True


def _filtres_auto(attribut: str, valeur: str) -> list[str]:
    """Filtres « auto-entities » sur les interrupteurs d'appareil.

    On filtre sur l'attribut ``appareil`` (présent uniquement sur les
    interrupteurs d'un appareil) plutôt que sur le nom de l'entité : les
    identifiants changent quand un appareil est renommé, l'attribut non.
    """
    return [
        "        filter:",
        "          include:",
        "            - entity_id: switch.*",
        "              attributes:",
        "                appareil: client",
        f"                {attribut}: \"{valeur}\"",
    ]


def render_communautaire() -> str:
    """Tableau de bord basé sur la carte communautaire « auto-entities »."""
    lignes = [
        ENTETE.replace("Tableau de bord « Bbox »", "Tableau de bord « Bbox » (auto-entities)"),
        "title: Bbox (auto-entities)",
        "views:",
        "  - title: Appareils",
        "    path: bbox-auto",
        "    icon: mdi:router-wireless",
        "    cards:",
        "      - type: custom:auto-entities",
        '        title: "Appareils connectés (filaire + Wi-Fi)"',
        "        show_empty: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
    ]
    lignes += _filtres_auto("etat_connexion", "connecte")
    lignes += [
        "      - type: custom:auto-entities",
        '        title: "Appareils déconnectés"',
        "        show_empty: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
    ]
    lignes += _filtres_auto("etat_connexion", "deconnecte")
    lignes += [
        "      - type: custom:auto-entities",
        '        title: "Accès coupé"',
        "        show_empty: true",
        "        card:",
        "          type: entities",
        "          show_header_toggle: false",
    ]
    lignes += _filtres_auto("acces", "coupe")
    lignes += [
        "      - type: entities",
        '        title: "Wi-Fi de la box"',
        "        entities:",
    ]
    lignes += [f"          - entity: {entite}" for entite in ENTITES_BOX]
    return "\n".join(lignes) + "\n"


def _carte_mushroom(titre: str, attribut: str, valeur: str, icone: str) -> list[str]:
    """Une carte « Mushroom » listant les appareils selon un attribut."""
    return [
        "      - type: custom:auto-entities",
        f'        title: "{titre}"',
        "        show_empty: true",
        "        card:",
        "          type: grid",
        "          columns: 1",
        "          square: false",
        "        card_param: cards",
    ] + _filtres_auto(attribut, valeur) + [
        "        options:",
        "          type: custom:mushroom-entity-card",
        "          fill_container: true",
        f"          icon_color: {icone}",
    ]


def render_mushroom() -> str:
    """Tableau de bord en cartes Mushroom (rendu soigné, dynamique)."""
    lignes = [
        ENTETE.replace("Tableau de bord « Bbox »", "Tableau de bord « Bbox » (Mushroom)"),
        "title: Bbox (Mushroom)",
        "views:",
        "  - title: Appareils",
        "    path: bbox-mushroom",
        "    icon: mdi:router-wireless",
        "    cards:",
    ]
    lignes += _carte_mushroom(
        "Connectés (filaire + Wi-Fi)", "etat_connexion", "connecte", "green"
    )
    lignes += _carte_mushroom("Déconnectés", "etat_connexion", "deconnecte", "grey")
    lignes += _carte_mushroom("Accès coupé", "acces", "coupe", "red")
    lignes += [
        # La carte « chips » n'accepte pas de titre : on met un en-tête
        # natif à côté.
        "      - type: heading",
        '        heading: "Wi-Fi de la box"',
        "        icon: mdi:wifi",
        "      - type: custom:mushroom-chips-card",
        "        chips:",
        "          - type: entity",
        "            entity: switch.bbox_wifi",
        "            icon_color: blue",
        "          - type: entity",
        "            entity: switch.bbox_wifi_24_ghz",
        "            icon_color: blue",
        "          - type: entity",
        "            entity: switch.bbox_wifi_5_ghz",
        "            icon_color: blue",
        "          - type: back",
        "            icon: mdi:close",
        # La carte « template » attend ``primary`` / ``secondary``
        # (et non ``content``).
        "      - type: custom:mushroom-template-card",
        "        icon: mdi:router-wireless",
        "        primary: >",
        "          {% set total = states('sensor.bbox_clients_connectes') | int(0) %}",
        "          **{{ total }}** appareil(s) connecté(s)",
        "        secondary: Appuyez sur un interrupteur pour couper l'accès.",
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
