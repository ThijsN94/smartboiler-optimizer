"""Buttons for SmartBoiler Optimizer."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SmartBoilerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartBoilerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ResetPlanButton(coordinator, entry)])


class ResetPlanButton(ButtonEntity):
    """Wist het opgeslagen plan en laat de boilertijd opnieuw berekenen."""

    _attr_has_entity_name = True
    _attr_name = "Herbereken boilertijd"

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_reset_plan"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="SmartBoiler Optimizer",
        )

    async def async_press(self) -> None:
        await self._coordinator.async_reset_plan()
