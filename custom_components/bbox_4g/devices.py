"""Identités des appareils exposés par l'intégration.

Home Assistant dérive l'``entity_id`` du **nom de l'appareil** auquel
l'entité est rattachée et du **nom de l'entité**. Deux conséquences
importantes :

* un attribut ``_attr_entity_id`` n'existe pas dans Home Assistant : il est
  silencieusement ignoré. Pour obtenir un ``entity_id`` stable, il faut
  donc agir sur le nom de l'appareil et le nom de l'entité ;
* rattacher une entité à un appareil ajoute toujours son nom devant
  l'``entity_id`` — sauf si le nom de l'entité commence déjà par celui de
  l'appareil (Home Assistant retire alors le préfixe).

On applique donc :

* la **box** est un appareil nommé « Bbox » → ses entités gardent des
  identifiants stables (``sensor.bbox_clients_connectes``…) ;
* chaque **client** est un appareil à part entière, nommé d'après l'appareil
  connu de la box → ses entités deviennent ``switch.<appareil>_acces_internet``,
  ``binary_sensor.<appareil>_presence``, etc.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo

from .const import ATTR_IP, ATTR_LINK, ATTR_MAC, ATTR_MODEL, DOMAIN


def box_device_info(entry: Any, data: dict[str, Any] | None) -> DeviceInfo:
    """Construit les informations de l'appareil « box ».

    Le nom est volontairement court (« Bbox ») : il entre dans l'
    ``entity_id`` de toutes les entités de la box. S'il contenait l'adresse
    IP, les identifiants changeraient à chaque box.
    """
    data = data or {}
    host = entry.data.get("host", "")
    return DeviceInfo(
        identifiers={(DOMAIN, f"box_{host}")},
        manufacturer="Bouygues Telecom",
        model=data.get(ATTR_MODEL) or "Box Bouygues",
        sw_version=data.get("firmware") or None,
        name="Bbox",
        configuration_url=f"http://{host}",
    )


def client_device_info(
    entry: Any, device: dict[str, Any], data: dict[str, Any] | None = None
) -> DeviceInfo:
    """Construit les informations de l'appareil « client » (téléphone, PC…).

    L'identifiant est l'adresse MAC : elle est stable, et permet à Home
    Assistant de rattacher automatiquement l'appareil au routeur.
    """
    mac = str(device.get(ATTR_MAC, "")).lower()
    nom = str(device.get("name") or mac)
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        manufacturer=str(device.get("manufacturer") or "Inconnu"),
        model=str(device.get("model") or "Client réseau"),
        name=nom,
        via_device=(DOMAIN, f"box_{entry.data.get('host', '')}"),
        connections={(CONNECTION_NETWORK_MAC, mac)},
        configuration_url=None,
    )


def async_purge_disparus(
    hass: Any, entities: dict[str, Any], disparus: frozenset[str]
) -> list[Any]:
    """Supprime les entités des appareils disparus de la box.

    Une entité supprimée disparaît du registre : l'interface ne garde pas
    de « unavailable » obsolètes.
    """
    supprimees = []
    for mac in list(entities):
        if mac not in disparus:
            continue
        entite = entities.pop(mac)
        supprimees.append(entite)
        try:
            hass.async_create_task(entite.async_remove(force_remove=True))
        except Exception:  # pragma: no cover - défense
            pass
    return supprimees


def client_attributes(device: dict[str, Any]) -> dict[str, Any]:
    """Attributs communs à toutes les entités d'un client."""
    return {
        ATTR_MAC: str(device.get(ATTR_MAC, "")).lower(),
        "nom": device.get("name"),
        ATTR_IP: device.get(ATTR_IP),
        ATTR_LINK: device.get(ATTR_LINK),
    }
