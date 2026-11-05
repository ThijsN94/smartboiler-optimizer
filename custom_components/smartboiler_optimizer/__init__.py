"""SmartBoiler Optimizer integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_BOILER_BOTTOM_TEMP_ENTITY,
    CONF_BOILER_ENERGY_ENTITY,
    CONF_BOILER_ENTITY,
    CONF_BOILER_TOP_TEMP_ENTITY,
    DOMAIN,
)
from .coordinator import SmartBoilerCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
TRIGGER_KEYS = (
    CONF_BOILER_ENTITY,
    CONF_BOILER_TOP_TEMP_ENTITY,
    CONF_BOILER_BOTTOM_TEMP_ENTITY,
    CONF_BOILER_ENERGY_ENTITY,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SmartBoilerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    config = entry.options or entry.data
    trigger_entities = [config[key] for key in TRIGGER_KEYS if config.get(key)]
    if trigger_entities:

        @callback
        def _on_boiler_change(event: Event) -> None:
            hass.async_create_task(coordinator.async_request_refresh())

        entry.async_on_unload(
            async_track_state_change_event(hass, trigger_entities, _on_boiler_change)
        )

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
