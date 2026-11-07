"""Config flow de l'intégration « Bbox ».

L'utilisateur saisit l'adresse de la box, son identifiant et son mot de
passe : le mot de passe n'apparaît donc jamais dans un fichier YAML, il est
conservé par Home Assistant dans son propre stockage et redemandé par
l'interface dès qu'il faut le ressaisir.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback

from .bbox_api import BboxAuthError, BboxError, detect_client
from .const import (
    BLOCK_SCOPES,
    CONF_BLOCK_SCOPE,
    CONF_DEVICE_MODE,
    CONF_FAMILY,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_DASHBOARD,
    CONF_SCAN_INTERVAL,
    CONF_SEPARATE_MACS,
    CONF_USERNAME,
    CONF_USE_TLS,
    CONF_VERIFY_SSL,
    DEFAULT_BLOCK_SCOPE,
    DEFAULT_DEVICE_MODE,
    DEFAULT_FAMILY,
    DEFAULT_HOST,
    DEFAULT_DASHBOARD,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DEVICE_MODES,
    DOMAIN,
    FAMILIES,
    SCAN_INTERVAL_MAX,
    SCAN_INTERVAL_MIN,
)

_LOGGER = logging.getLogger(__name__)


def _is_session_busy(error: BaseException) -> bool:
    """La box refuse-t-elle la connexion car une session est déjà ouverte ?

    Les box Huawei n'acceptent qu'une seule session d'administration : si
    l'interface web de la box est ouverte, ou si une autre instance
    interroge déjà la box, la connexion est refusée (code ``108003``).
    """
    return "108003" in str(error)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Schéma du formulaire principal."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)
            ): str,
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
            ): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(
                CONF_FAMILY, default=defaults.get(CONF_FAMILY, DEFAULT_FAMILY)
            ): vol.In(FAMILIES),
            vol.Optional(
                CONF_USE_TLS, default=defaults.get(CONF_USE_TLS, False)
            ): bool,
            vol.Optional(
                CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, False)
            ): bool,
        }
    )


class BboxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Gestion de la configuration et de la re-authentification."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._reauth_entry: config_entries.ConfigEntry | None = None

    # -- étape principale ---------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Première connexion : adresse, identifiant et mot de passe."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_USERNAME: user_input.get(CONF_USERNAME, DEFAULT_USERNAME).strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_FAMILY: user_input.get(CONF_FAMILY, DEFAULT_FAMILY),
                CONF_USE_TLS: user_input.get(CONF_USE_TLS, False),
                CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, False),
            }
            try:
                await self.hass.async_add_executor_job(self._validate, data)
            except BboxAuthError:
                errors["base"] = "invalid_auth"
            except BboxError as err:
                errors["base"] = (
                    "already_connected" if _is_session_busy(err) else "cannot_connect"
                )
            except Exception:  # pragma: no cover - filet de sécurité
                _LOGGER.exception("Erreur inattendue pendant la validation")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(data[CONF_HOST])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Bbox {data[CONF_HOST]}", data=data
                )

        return self.async_show_form(
            step_id="user", data_schema=_user_schema(), errors=errors
        )

    # -- re-authentification ------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Le mot de passe est refusé : on le redemande."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Formulaire de re-authentification (mot de passe uniquement)."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if entry is None:
            entry = self._get_reauth_entry()
            self._reauth_entry = entry

        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await self.hass.async_add_executor_job(self._validate, data)
            except BboxAuthError:
                errors["base"] = "invalid_auth"
            except BboxError as err:
                errors["base"] = (
                    "already_connected" if _is_session_busy(err) else "cannot_connect"
                )
            else:
                # ``async_update_reload_and_abort`` met à jour l'entrée
                # *et* la recharge : inutile de faire les deux (le
                # rechargement déclenchait un second rechargement via le
                # listener d'options).
                return await self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={CONF_HOST: entry.data.get(CONF_HOST, "")},
        )

    # -- options ------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Retourne le formulaire d'options."""
        return BboxOptionsFlow()

    # -- validation ---------------------------------------------------------

    def _validate(self, data: dict[str, Any]) -> None:
        """Vérifie que la box répond et que le mot de passe est accepté.

        La session enregistrée est réutilisée : la box n'accepte qu'une
        seule session d'administration à la fois, en ouvrir une deuxième
        serait refusé (« 108003 »).
        """
        client = detect_client(
            host=data[CONF_HOST],
            password=data[CONF_PASSWORD],
            username=data.get(CONF_USERNAME, DEFAULT_USERNAME),
            family=data.get(CONF_FAMILY, DEFAULT_FAMILY),
            use_tls=data.get(CONF_USE_TLS) or None,
            timeout=10,
            session_file=self.hass.config.path(DOMAIN, "session.json"),
        )
        client.state()


class BboxOptionsFlow(config_entries.OptionsFlow):
    """Réglages avancés : périodicité, portée du blocage, mode des appareils."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Affiche et enregistre les options."""
        if user_input is not None:
            interval = int(user_input[CONF_SCAN_INTERVAL])
            # On conserve la liste des exceptions déjà configurées
            # (gérée par le switch « Appareil indépendant »).
            current_options = dict(getattr(self.config_entry, "options", {}) or {})
            separate_macs = current_options.get(CONF_SEPARATE_MACS, [])

            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: max(
                        SCAN_INTERVAL_MIN, min(SCAN_INTERVAL_MAX, interval)
                    ),
                    CONF_BLOCK_SCOPE: user_input.get(
                        CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE
                    ),
                    CONF_DASHBOARD: user_input.get(
                        CONF_DASHBOARD, DEFAULT_DASHBOARD
                    ),
                    CONF_DEVICE_MODE: user_input.get(
                        CONF_DEVICE_MODE, DEFAULT_DEVICE_MODE
                    ),
                    CONF_SEPARATE_MACS: separate_macs,
                },
            )

        options = getattr(self, "config_entry", None)
        current = dict(options.options) if options else {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=SCAN_INTERVAL_MIN, max=SCAN_INTERVAL_MAX),
                ),
                vol.Required(
                    CONF_BLOCK_SCOPE,
                    default=current.get(CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE),
                ): vol.In(BLOCK_SCOPES),
                vol.Required(
                    CONF_DASHBOARD,
                    default=current.get(CONF_DASHBOARD, DEFAULT_DASHBOARD),
                ): bool,
                vol.Required(
                    CONF_DEVICE_MODE,
                    default=current.get(CONF_DEVICE_MODE, DEFAULT_DEVICE_MODE),
                ): vol.In(DEVICE_MODES),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
