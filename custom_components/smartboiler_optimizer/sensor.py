"""Sensors for SmartBoiler Optimizer."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SmartBoilerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SmartBoilerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BoilerStartSensor(coordinator, entry),
            CurrentPriceSensor(coordinator, entry),
            BoilerDurationSensor(coordinator, entry),
            ForecastStatusSensor(coordinator, entry, "status_prijs", "Status dynamische prijs"),
            ForecastStatusSensor(coordinator, entry, "status_weer", "Status weer"),
            ForecastStatusSensor(coordinator, entry, "status_zon", "Status zonneforecast"),
            BlockMetricSensor(coordinator, entry, "blok_prijs", "Opwarmblok prijs", "€/kWh", "mdi:cash"),
            BlockMetricSensor(coordinator, entry, "blok_zon", "Opwarmblok zon", "%", "mdi:white-balance-sunny"),
            BlockMetricSensor(coordinator, entry, "blok_cop", "Opwarmblok COP", None, "mdi:heat-pump"),
            BlockMetricSensor(coordinator, entry, "blok_temp", "Opwarmblok buitentemperatuur", "°C", "mdi:thermometer"),
            ForecastTimelineSensor(coordinator, entry),
            BoilerCopSensor(coordinator, entry),
            BoilerTomorrowStartSensor(coordinator, entry),
            OpwarmRunsSensor(coordinator, entry),
        ]
    )


class SmartBoilerSensor(CoordinatorEntity[SmartBoilerCoordinator], SensorEntity):
    """Base sensor for SmartBoiler Optimizer."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="SmartBoiler Optimizer",
        )


def _current_cost(rows: list[dict[str, Any]], now: datetime) -> float | None:
    """Kosten per kWh warmte van het kwartier waarin `now` valt; None als die niet bekend is."""
    for row in rows:
        start = datetime.fromisoformat(row["t"])
        if start <= now < start + timedelta(minutes=15):
            return row.get("k")
    return None


class ForecastTimelineSensor(SmartBoilerSensor):
    """Kosten per kWh warmte van het huidige kwartier; de verwachting staat als attribuut."""

    _attr_native_unit_of_measurement = "€/kWh warmte"
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "verwachting_sensor", "Opwarmverwachting")

    @property
    def native_value(self):
        return _current_cost(self.coordinator.data.get("verwachting", []), dt_util.now())

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "huidige_stroomprijs": self.coordinator.data.get("current_price"),
            "verwachting": self.coordinator.data.get("verwachting", []),
            "deadline": self.coordinator.data.get("verwachting_deadline"),
        }


class ForecastStatusSensor(SmartBoilerSensor):
    """Beschikbaar of Fout voor een forecastbron."""

    def __init__(
        self,
        coordinator: SmartBoilerCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, entry, key, name)
        self._key = key

    @property
    def native_value(self):
        return (self.coordinator.data.get(self._key) or {}).get("status")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data.get(self._key) or {}
        return {"reden": data.get("reden"), "laatste_moment": data.get("laatste")}


class BoilerStartSensor(SmartBoilerSensor):
    """Moment waarop de boiler het goedkoopst opgewarmd kan worden."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "boiler_start", "Boiler opwarmen om")

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_start")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "gemiddelde_prijs": self.coordinator.data.get("boiler_price"),
            "kosten_per_kwh_warmte": self.coordinator.data.get("boiler_cost"),
            "geschatte_duur_min": self.coordinator.data.get("boiler_duration"),
            "blok": self.coordinator.data.get("blok_details"),
            "alternatieven": self.coordinator.data.get("alternatieven"),
        }


class BlockMetricSensor(SmartBoilerSensor):
    """Eén getal uit het gekozen opwarmblok."""

    def __init__(
        self,
        coordinator: SmartBoilerCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        unit: str | None,
        icon: str,
    ) -> None:
        super().__init__(coordinator, entry, key, name)
        self._key = key
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)


class BoilerDurationSensor(SmartBoilerSensor):
    """Geschatte opwarmduur van de boiler."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "boiler_duration", "Boiler opwarmduur")

    @property
    def native_value(self):
        return self.coordinator.data.get("boiler_duration")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "opwarmsnelheid_c_per_min": self.coordinator.data.get("boiler_rate"),
            "gemiddelde_boilertemperatuur": self.coordinator.data.get("boiler_mean_temp"),
            "doeltemperatuur_boiler": self.coordinator.data.get("boiler_target_temp"),
            "buitentemperatuur": self.coordinator.data.get("outdoor_temp"),
            "buitentemperatuur_opwarmen": self.coordinator.data.get("opwarm_buiten_temp"),
            "geplande_blokduur_min": self.coordinator.data.get("boiler_planned_duration"),
            "opwarmsnelheid_tabel": self.coordinator.data.get("opwarm_tabel"),
            "buitentemperatuur_factor_tabel": self.coordinator.data.get("opwarm_buiten_tabel"),
        }


class CurrentPriceSensor(SmartBoilerSensor):
    """Huidige dynamische stroomprijs."""

    _attr_native_unit_of_measurement = "€/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "current_price", "Huidige stroomprijs")

    @property
    def native_value(self):
        return self.coordinator.data.get("current_price")


class BoilerCopSensor(SmartBoilerSensor):
    """Gemeten COP uit eerdere opwarmruns."""

    _attr_device_class = SensorDeviceClass.COEFICIENT_OF_PERFORMANCE if hasattr(
        SensorDeviceClass, "COEFICIENT_OF_PERFORMANCE"
    ) else None
    _attr_native_unit_of_measurement = None
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heat-pump"

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "measured_cop", "COP boiler")
        self._attr_name = "Gemeten COP boiler"

    @property
    def native_value(self):
        return self.coordinator.get_measured_cop(self.coordinator.data.get("outdoor_now"))

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "boiler_capaciteit_liters": self.coordinator._boiler_capacity_liters,
        }


class OpwarmRunsSensor(SmartBoilerSensor):
    """Aantal opgeslagen opwarmruns, als 'n/50'."""

    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "opwarm_runs", "Opwarmruns verzameld")

    @property
    def native_value(self):
        return self.coordinator.data.get("opwarm_runs")


class BoilerTomorrowStartSensor(SmartBoilerSensor):
    """Startmoment voor de boiler voor morgen (indien al bekend)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: SmartBoilerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "tomorrow_boiler_start", "Boiler opwarmen volgend blok")

    @property
    def native_value(self):
        return self.coordinator.data.get("tomorrow_boiler_start")
