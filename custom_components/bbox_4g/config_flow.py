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
    CONF_PROFILES,
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
from .dashboard import _MAC_NAME_RE, async_setup_selected_dashboards
from .helpers import slugify

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
    """Options : réglages, dashboards et contrôle parental (profils)."""

    def __init__(self) -> None:
        self._edit_id: str | None = None

    # -- menu ---------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["settings", "parental_add"]
        if self._profiles():
            menu += ["parental_edit", "parental_remove"]
        return self.async_show_menu(step_id="init", menu_options=menu)

    # -- parental control profiles -----------------------------------------

    def _profiles(self) -> list[dict[str, Any]]:
        return [dict(p) for p in (self.config_entry.options or {}).get(CONF_PROFILES) or []]

    def _save_profiles(self, profiles: list[dict[str, Any]]) -> ConfigFlowResult:
        options = {**(self.config_entry.options or {}), CONF_PROFILES: profiles}
        return self.async_create_entry(title="", data=options)

    def _device_choices(self, current: list[str]) -> dict[str, str]:
        """Known devices not already used by another profile."""
        taken = {
            m.lower()
            for p in self._profiles()
            if p["id"] != self._edit_id
            for m in p.get("macs") or []
        }
        coordinator = (
            self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id) or {}
        ).get("coordinator")
        choices: dict[str, str] = {}
        for dev in (coordinator.devices if coordinator else []):
            mac = str(dev.get("mac") or "").lower()
            if not mac or mac in taken:
                continue
            name = str(dev.get("name") or mac)
            unnamed = bool(_MAC_NAME_RE.match(name))
            choices[mac] = mac if unnamed else f"{name} ({mac[-5:]})"
        for mac in current:  # keep devices that vanished from the router table
            choices.setdefault(mac.lower(), mac)
        return dict(sorted(choices.items(), key=lambda kv: kv[1].lower()))

    def _profile_form(
        self, step_id: str, name: str, macs: list[str], errors: dict[str, str]
    ) -> ConfigFlowResult:
        schema = vol.Schema(
            {
                vol.Required("name", default=name): str,
                vol.Required("macs", default=macs): cv.multi_select(
                    self._device_choices(macs)
                ),
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    def _validate_profile(self, user_input: dict[str, Any]) -> dict[str, str]:
        name = str(user_input.get("name") or "").strip()
        if not name:
            return {"base": "name_required"}
        if any(
            p["name"].lower() == name.lower() and p["id"] != self._edit_id
            for p in self._profiles()
        ):
            return {"base": "name_exists"}
        if not user_input.get("macs"):
            return {"base": "no_device"}
        return {}

    async def async_step_parental_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._edit_id = None
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate_profile(user_input)
            if not errors:
                profiles = self._profiles()
                base = f"p_{slugify(user_input['name'])}"
                pid, n = base, 2
                while any(p["id"] == pid for p in profiles):
                    pid, n = f"{base}_{n}", n + 1
                profiles.append(
                    {
                        "id": pid,
                        "name": user_input["name"].strip(),
                        "macs": sorted(m.lower() for m in user_input["macs"]),
                    }
                )
                return self._save_profiles(profiles)
        return self._profile_form(
            "parental_add",
            (user_input or {}).get("name", ""),
            list((user_input or {}).get("macs", [])),
            errors,
        )

    def _pick_form(self, step_id: str) -> ConfigFlowResult:
        names = {p["id"]: p["name"] for p in self._profiles()}
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required("profile"): vol.In(names)}),
        )

    async def async_step_parental_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self._pick_form("parental_edit")
        self._edit_id = user_input["profile"]
        return await self.async_step_parental_edit_form()

    async def async_step_parental_edit_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        profiles = self._profiles()
        current = next((p for p in profiles if p["id"] == self._edit_id), None)
        if current is None:
            return await self.async_step_init()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate_profile(user_input)
            if not errors:
                current["name"] = user_input["name"].strip()
                current["macs"] = sorted(m.lower() for m in user_input["macs"])
                return self._save_profiles(profiles)
            current = {**current, **user_input}
        return self._profile_form(
            "parental_edit_form", current["name"], list(current.get("macs") or []), errors
        )

    async def async_step_parental_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self._pick_form("parental_remove")
        profiles = [p for p in self._profiles() if p["id"] != user_input["profile"]]
        return self._save_profiles(profiles)

    # -- general settings -----------------------------------------------------

    async def async_step_settings(
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
                **current_options,
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
        return self.async_show_form(step_id="settings", data_schema=schema)
