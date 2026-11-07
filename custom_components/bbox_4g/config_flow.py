"""Config flow de l'intégration « Bbox »."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .bbox_api import BboxAuthError, BboxError, detect_client
from .const import (
    BLOCK_SCOPES,
    CONF_BLOCK_SCOPE,
    CONF_DASHBOARDS,
    CONF_DEVICE_MODE,
    CONF_FAMILY,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_USE_TLS,
    CONF_VERIFY_SSL,
    DASHBOARD_AUTO,
    DASHBOARD_CHOICES,
    DASHBOARD_MUSHROOM,
    DASHBOARD_NATIVE,
    DEFAULT_BLOCK_SCOPE,
    DEFAULT_DASHBOARDS,
    DEFAULT_DEVICE_MODE,
    DEFAULT_FAMILY,
    DEFAULT_HOST,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DEVICE_MODES,
    DOMAIN,
    FAMILIES,
    SCAN_INTERVAL_MAX,
    SCAN_INTERVAL_MIN,
)
from .dashboard import async_setup_selected_dashboards

_LOGGER = logging.getLogger(__name__)

DASHBOARD_LABELS = {
    DASHBOARD_NATIVE: "Native (no extra cards)",
    DASHBOARD_AUTO: "Auto-entities (dynamic list) — requires HACS auto-entities",
    DASHBOARD_MUSHROOM: "Mushroom (nice UI) — requires HACS Mushroom + auto-entities",
}


def _is_session_busy(error: BaseException) -> bool:
    return "108003" in str(error)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
            except Exception:
                _LOGGER.exception("Unexpected error during validation")
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

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._reauth_entry or self._get_reauth_entry()
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
                return await self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={CONF_HOST: entry.data.get(CONF_HOST, "")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return BboxOptionsFlow()

    def _validate(self, data: dict[str, Any]) -> None:
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
    """Options : polling, blocage, mode appareils, dashboards."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            interval = int(user_input[CONF_SCAN_INTERVAL])
            current_options = dict(getattr(self.config_entry, "options", {}) or {})
            separate_macs = current_options.get("separate_macs", [])

            selected = user_input.get(CONF_DASHBOARDS, [])
            if isinstance(selected, str):
                selected = [selected]
            selected = [s for s in selected if s in DASHBOARD_CHOICES]

            new_options = {
                CONF_SCAN_INTERVAL: max(
                    SCAN_INTERVAL_MIN, min(SCAN_INTERVAL_MAX, interval)
                ),
                CONF_BLOCK_SCOPE: user_input.get(
                    CONF_BLOCK_SCOPE, DEFAULT_BLOCK_SCOPE
                ),
                CONF_DEVICE_MODE: user_input.get(
                    CONF_DEVICE_MODE, DEFAULT_DEVICE_MODE
                ),
                CONF_DASHBOARDS: selected,
                "separate_macs": separate_macs,
            }

            result = self.async_create_entry(title="", data=new_options)

            # Créer / mettre à jour les dashboards choisis
            if selected:
                self.hass.async_create_task(
                    async_setup_selected_dashboards(
                        self.hass, selected, notify=True
                    )
                )

            return result

        options = getattr(self, "config_entry", None)
        current = dict(options.options) if options else {}
        current_dashboards = current.get(CONF_DASHBOARDS, DEFAULT_DASHBOARDS)

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
                    CONF_DEVICE_MODE,
                    default=current.get(CONF_DEVICE_MODE, DEFAULT_DEVICE_MODE),
                ): vol.In(DEVICE_MODES),
                vol.Optional(
                    CONF_DASHBOARDS,
                    default=current_dashboards,
                ): cv.multi_select(DASHBOARD_LABELS),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
