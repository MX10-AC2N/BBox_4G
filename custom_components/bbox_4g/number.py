"""Number entities: parental control daily quotas."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .parental_entities import build, manager_of


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    manager = manager_of(hass, entry)
    if manager:
        async_add_entities(build("number", manager))
