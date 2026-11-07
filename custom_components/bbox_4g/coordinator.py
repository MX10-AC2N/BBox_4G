"""Coordonnateur de données de l'intégration « Bbox ».

Il interroge la box à intervalle régulier, gère la reconnexion automatique
quand la session Huawei expire et lève ``ConfigEntryAuthFailed`` pour que
Home Assistant redemande le mot de passe si les identifiants sont refusés.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bbox_api import BboxAuthError, BboxError
from .const import (
    CONF_DASHBOARD,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .dashboard import async_write_dashboard

#: Nombre de cycles d'absence avant de supprimer les entités d'un appareil.
DISPARITION_SEUIL = 3

_LOGGER = logging.getLogger(__name__)


class BboxCoordinator(DataUpdateCoordinator):
    """Récupère l'état de la box et le partage entre les plates-formes."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: Any
    ) -> None:
        interval = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id[:8]}",
            update_interval=timedelta(seconds=interval),
        )
        self.entry = entry
        self.client = client
        # Nombre de cycles pendant lesquels un appareil est absent de la
        # table de la box. On n'enlève ses entités qu'après plusieurs cycles
        # d'absence : une lecture incomplète (table qui échoue) ne doit pas
        # faire disparaître les entités.
        self._absent: dict[str, int] = {}
        self._known: set[str] = set()
        self._vanished: frozenset[str] = frozenset()
        # Signature de la liste d'appareils : sert à ne réécrire le tableau
        # de bord que lorsqu'elle change.
        self._dashboard_signature: tuple[tuple[str, str], ...] = ()

    async def _async_update_data(self) -> dict[str, Any]:
        """Lit l'état de la box (dans l'exécuteur pour ne pas bloquer)."""
        try:
            data = await self.hass.async_add_executor_job(self._read_state)
        except BboxAuthError as err:
            # Les identifiants sont refusés : Home Assistant va proposer
            # de ressaisir le mot de passe.
            raise ConfigEntryAuthFailed(str(err)) from err
        except BboxError as err:
            # Panne temporaire (session expirée, box injoignable…) : on
            # signale l'indisponibilité sans demander de mot de passe.
            raise UpdateFailed(str(err)) from err
        self._vanished = self._compute_vanished(
            {str(d.get("mac", "")).lower() for d in data.get("devices", [])}
        )
        await self._async_update_dashboard(data)
        return data

    def _compute_vanished(self, macs: set[str]) -> frozenset[str]:
        """Appareils disparus de la box depuis plusieurs cycles.

        Un appareil qui disparaît puis revient est remis à zéro : seule une
        absence répétée compte.
        """
        absents = self._absent
        # Tous les appareils vus sont suivis, disparus compris.
        self._known |= set(macs)
        for mac in self._known:
            if mac in macs:
                absents.pop(mac, None)
            else:
                absents[mac] = absents.get(mac, 0) + 1
        return frozenset(
            mac for mac, nombre in absents.items() if nombre >= DISPARITION_SEUIL
        )

    async def _async_update_dashboard(self, data: dict[str, Any]) -> None:
        """Régénère le tableau de bord si la liste d'appareils a changé."""
        options = getattr(self.entry, "options", {}) or {}
        if not options.get(CONF_DASHBOARD, False):
            # L'écriture automatique est facultative : par défaut on ne
            # touche pas aux tableaux de bord de l'utilisateur.
            return
        devices = data.get("devices", [])
        signature = tuple(
            (str(d.get("mac", "")), str(d.get("name", ""))) for d in devices
        )
        if signature == self._dashboard_signature:
            return
        self._dashboard_signature = signature
        try:
            await async_write_dashboard(self.hass, devices)
        except Exception as err:  # pragma: no cover - défense
            _LOGGER.warning(
                "Impossible de mettre à jour le tableau de bord : %s", err
            )

    def _read_state(self) -> dict[str, Any]:
        """Lecture réelle de l'état."""
        return self.client.state()

    async def async_reconnect(self) -> None:
        """Force l'ouverture d'une nouvelle session sur la box."""
        await self.hass.async_add_executor_job(self.client.reconnect)
        await self.async_request_refresh()

    # -- raccourcis ---------------------------------------------------------

    @property
    def vanished(self) -> frozenset[str]:
        """MAC des appareils disparus de la box (entités à supprimer)."""
        return self._vanished

    @property
    def devices(self) -> list[dict[str, Any]]:
        """Liste des appareils connus."""
        return list((self.data or {}).get("devices", []))

    def device_by_mac(self, mac: str) -> dict[str, Any] | None:
        """Retourne un appareil par son adresse MAC."""
        mac = (mac or "").lower()
        for device in self.devices:
            if device.get("mac", "").lower() == mac:
                return device
        return None
