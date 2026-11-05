"""Config flow for SmartBoiler Optimizer."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
import yaml

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_BACKUP_SWITCH,
    CONF_BACKUP_MODE,
    CONF_BACKUP_TEMP_THRESHOLD,
    CONF_BASE_LOAD,
    CONF_BOILER_BOTTOM_TEMP_ENTITY,
    CONF_BOILER_CAPACITY_LITERS,
    CONF_BOILER_CONTROL,
    CONF_BOILER_DEADLINE_HOUR,
    CONF_BOILER_ENERGY_ENTITY,
    CONF_BOILER_ENTITY,
    CONF_USE_PRICE,
    CONF_USE_SOLAR,
    CONF_USE_WEATHER,
    CONF_BOILER_POWER,
    CONF_BOILER_STATUS_ENTITY,
    CONF_BOILER_MODE_OFF,
    CONF_BOILER_MODE_ON,
    CONF_BOILER_HEATING_STATES,
    CONF_BOILER_SWITCH,
    CONF_BOILER_TARGET_TEMP,
    CONF_BOILER_TOP_TEMP_ENTITY,
    CONF_COP_TABLE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_SOLAR_ENTITY,
    CONF_USE_MEASURED_COP,
    CONF_WEATHER_ENTITY,
    COP_TABLE,
    DEFAULT_BACKUP_MODE,
    DEFAULT_BACKUP_TEMP_THRESHOLD,
    DEFAULT_BASE_LOAD,
    DEFAULT_BOILER_CAPACITY_LITERS,
    DEFAULT_BOILER_DEADLINE_HOUR,
    DEFAULT_BOILER_POWER,
    BOILER_MODE_LABELS,
    DEFAULT_BOILER_HEATING_STATES,
    DEFAULT_BOILER_MODE_OFF,
    DEFAULT_BOILER_MODE_ON,
    DEFAULT_BOILER_TARGET_TEMP,
    DEFAULT_USE_MEASURED_COP,
    DOMAIN,
)

SECTION_SOURCES = "bronnen"
SECTION_BOILER = "boiler"
SECTION_PLANNING = "planning"
SECTION_BACKUP = "backup"
SECTION_LEARNING = "leren"
SECTION_COP = "cop_tabel"
SECTION_KEYS = (
    SECTION_SOURCES,
    SECTION_BOILER,
    SECTION_PLANNING,
    SECTION_BACKUP,
    SECTION_LEARNING,
)

CONF_CONFIRM_RESET_RUNS = "confirm_reset_runs"
CONF_COP_YAML = "cop_yaml"


def _entity(domain: str) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _number(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> selector.NumberSelector:
    config: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**config))


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def _flatten(data: dict[str, Any]) -> dict[str, Any]:
    """Secties nesten hun velden; de integratie bewaart één vlakke mapping.

    De COP-tabel wordt apart verwerkt (als één lijst met paren), niet als losse velden.
    """
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if key == SECTION_COP:
            continue
        if key in SECTION_KEYS and isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def _cop_table_to_yaml(table: list[Any]) -> str:
    """Schrijf de tabel als YAML-lijst, één [temperatuur, COP] per regel."""
    lines = []
    for point in sorted(table, key=lambda item: float(item[0])):
        lines.append(f"- [{float(point[0]):g}, {float(point[1]):g}]")
    return "\n".join(lines)


def _cop_table_from_yaml(text: str) -> tuple[list[list[float]], str | None]:
    """Lees een YAML-lijst van [temperatuur, COP]-paren, met dezelfde controles als het formulier."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return [], "yaml_invalid"
    if data is None:
        data = []
    if not isinstance(data, list):
        return [], "yaml_invalid"

    points: list[list[float]] = []
    for item in data:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in item)
        ):
            return [], "yaml_invalid"
        points.append([float(item[0]), float(item[1])])

    if len(points) < 2:
        return [], "too_few_points"
    if len({point[0] for point in points}) != len(points):
        return [], "duplicate_temp"
    points.sort()
    return points, None


def _mode_options(boiler_modes: list[str] | None, current: str) -> list[dict[str, str]]:
    """Modi voor een keuzelijst: alleen wat de boiler meldt, anders alle modi."""
    values = [mode for mode in BOILER_MODE_LABELS if boiler_modes and mode in boiler_modes]
    values += [mode for mode in boiler_modes or [] if mode not in BOILER_MODE_LABELS]
    if not values:
        values = list(BOILER_MODE_LABELS)
    if current not in values:
        values.append(current)
    return [{"value": value, "label": BOILER_MODE_LABELS.get(value, value)} for value in values]


def _mode_selector(boiler_modes: list[str] | None, current: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=_mode_options(boiler_modes, current),
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _boiler_operation_list(hass: HomeAssistant, entity_id: str | None) -> list[str] | None:
    """Geef de operatiemodi die de gekozen boiler meldt, of None als die niet bekend zijn."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    modes = state.attributes.get("operation_list") if state else None
    return [str(mode) for mode in modes] if isinstance(modes, list) else None


def _heating_states_text(value: Any) -> str:
    """Toon de opgeslagen statussen als komma-gescheiden tekst."""
    if isinstance(value, str):
        return value
    return ", ".join(value or DEFAULT_BOILER_HEATING_STATES)


def _parse_heating_states(text: str | None) -> list[str]:
    """Lees de komma-gescheiden tekst; een lege invoer geeft de standaard."""
    states = [part.strip() for part in (text or "").split(",") if part.strip()]
    return states or list(DEFAULT_BOILER_HEATING_STATES)


def _schema(
    defaults: dict[str, Any],
    cop_text: str | None = None,
    modes: list[str] | None = None,
) -> vol.Schema:
    def suggest(value: Any) -> dict[str, Any]:
        return {} if value is None else {"suggested_value": value}

    def suggested(key: str) -> dict[str, Any]:
        return suggest(defaults.get(key))

    schema: dict[Any, Any] = {
        vol.Required(SECTION_SOURCES): section(
            vol.Schema(
                {
                    vol.Required(
                        CONF_PRICE_ENTITY, description=suggested(CONF_PRICE_ENTITY)
                    ): _entity("sensor"),
                    vol.Optional(
                        CONF_SOLAR_ENTITY,
                        description={"suggested_value": _as_list(defaults.get(CONF_SOLAR_ENTITY))},
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", multiple=True)
                    ),
                    vol.Optional(
                        CONF_WEATHER_ENTITY, description=suggested(CONF_WEATHER_ENTITY)
                    ): _entity("weather"),
                    vol.Optional(
                        CONF_OUTDOOR_TEMP_ENTITY, description=suggested(CONF_OUTDOOR_TEMP_ENTITY)
                    ): _entity("sensor"),
                }
            ),
        ),
        vol.Required(SECTION_BOILER): section(
            vol.Schema(
                {
                    vol.Optional(
                        CONF_BOILER_ENTITY, description=suggested(CONF_BOILER_ENTITY)
                    ): _entity("water_heater"),
                    vol.Optional(
                        CONF_BOILER_SWITCH, description=suggested(CONF_BOILER_SWITCH)
                    ): _entity("switch"),
                    vol.Required(
                        CONF_BOILER_MODE_ON,
                        default=defaults.get(CONF_BOILER_MODE_ON, DEFAULT_BOILER_MODE_ON),
                    ): _mode_selector(modes, defaults.get(CONF_BOILER_MODE_ON, DEFAULT_BOILER_MODE_ON)),
                    vol.Required(
                        CONF_BOILER_MODE_OFF,
                        default=defaults.get(CONF_BOILER_MODE_OFF, DEFAULT_BOILER_MODE_OFF),
                    ): _mode_selector(modes, defaults.get(CONF_BOILER_MODE_OFF, DEFAULT_BOILER_MODE_OFF)),
                    vol.Optional(
                        CONF_BOILER_TOP_TEMP_ENTITY,
                        description=suggested(CONF_BOILER_TOP_TEMP_ENTITY),
                    ): _entity("sensor"),
                    vol.Optional(
                        CONF_BOILER_BOTTOM_TEMP_ENTITY,
                        description=suggested(CONF_BOILER_BOTTOM_TEMP_ENTITY),
                    ): _entity("sensor"),
                    vol.Optional(
                        CONF_BOILER_STATUS_ENTITY,
                        description=suggested(CONF_BOILER_STATUS_ENTITY),
                    ): _entity("sensor"),
                    vol.Required(
                        CONF_BOILER_HEATING_STATES,
                        default=_heating_states_text(defaults.get(CONF_BOILER_HEATING_STATES)),
                    ): selector.TextSelector(),
                    vol.Optional(
                        CONF_BOILER_ENERGY_ENTITY,
                        description=suggested(CONF_BOILER_ENERGY_ENTITY),
                    ): _entity("sensor"),
                    vol.Required(
                        CONF_BOILER_TARGET_TEMP,
                        default=defaults.get(CONF_BOILER_TARGET_TEMP, DEFAULT_BOILER_TARGET_TEMP),
                    ): _number(40, 65, 1, "°C"),
                    vol.Required(
                        CONF_BOILER_CAPACITY_LITERS,
                        default=defaults.get(
                            CONF_BOILER_CAPACITY_LITERS, DEFAULT_BOILER_CAPACITY_LITERS
                        ),
                    ): _number(50, 5000, 10, "liter"),
                }
            ),
        ),
        vol.Required(SECTION_PLANNING): section(
            vol.Schema(
                {
                    vol.Required(
                        CONF_BOILER_DEADLINE_HOUR,
                        default=defaults.get(
                            CONF_BOILER_DEADLINE_HOUR, DEFAULT_BOILER_DEADLINE_HOUR
                        ),
                    ): _number(0, 23, 1, "u"),
                    vol.Required(
                        CONF_BOILER_POWER,
                        default=defaults.get(CONF_BOILER_POWER, DEFAULT_BOILER_POWER),
                    ): _number(100, 6000, 50, "W"),
                    vol.Required(
                        CONF_BASE_LOAD,
                        default=defaults.get(CONF_BASE_LOAD, DEFAULT_BASE_LOAD),
                    ): _number(0, 3000, 50, "W"),
                    vol.Required(
                        CONF_BOILER_CONTROL,
                        default=defaults.get(CONF_BOILER_CONTROL, False),
                    ): selector.BooleanSelector(),
                }
            ),
        ),
        vol.Required(SECTION_BACKUP): section(
            vol.Schema(
                {
                    vol.Optional(
                        CONF_BACKUP_SWITCH, description=suggested(CONF_BACKUP_SWITCH)
                    ): _entity("switch"),
                    vol.Required(
                        CONF_BACKUP_MODE,
                        default=defaults.get(CONF_BACKUP_MODE, DEFAULT_BACKUP_MODE),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": "normal", "label": "Aan > Uit"},
                                {"value": "reversed", "label": "Uit > Aan"},
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_BACKUP_TEMP_THRESHOLD,
                        default=defaults.get(
                            CONF_BACKUP_TEMP_THRESHOLD, DEFAULT_BACKUP_TEMP_THRESHOLD
                        ),
                    ): _number(0, 20, 0.5, "°C"),
                }
            ),
        ),
        vol.Required(SECTION_LEARNING): section(
            vol.Schema(
                {
                    vol.Required(
                        CONF_USE_MEASURED_COP,
                        default=defaults.get(CONF_USE_MEASURED_COP, DEFAULT_USE_MEASURED_COP),
                    ): selector.BooleanSelector(),
                }
            ),
        ),
    }

    if cop_text is not None:
        schema[vol.Required(SECTION_COP)] = section(
            vol.Schema(
                {
                    vol.Required(CONF_COP_YAML, default=cop_text): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            {"collapsed": True},
        )

    toggles: dict[Any, Any] = {}
    if cop_text is not None:
        toggles = {
            vol.Required(CONF_USE_PRICE, default=defaults.get(CONF_USE_PRICE, True)): selector.BooleanSelector(),
            vol.Required(CONF_USE_WEATHER, default=defaults.get(CONF_USE_WEATHER, True)): selector.BooleanSelector(),
            vol.Required(CONF_USE_SOLAR, default=defaults.get(CONF_USE_SOLAR, True)): selector.BooleanSelector(),
        }
    return vol.Schema({**toggles, **schema})


class SmartBoilerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            flat = _flatten(user_input)
            flat[CONF_BOILER_HEATING_STATES] = _parse_heating_states(flat.get(CONF_BOILER_HEATING_STATES))
            return self.async_create_entry(title="SmartBoiler Optimizer", data=flat)

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return SmartBoilerOptionsFlow()


class SmartBoilerOptionsFlow(config_entries.OptionsFlow):
    """Change sensors and settings after setup."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(step_id="init", menu_options=["settings", "runs"])

    async def async_step_settings(self, user_input: dict[str, Any] | None = None):
        defaults = self.config_entry.options or self.config_entry.data
        modes = _boiler_operation_list(self.hass, defaults.get(CONF_BOILER_ENTITY))
        if user_input is not None:
            text = (user_input.get(SECTION_COP) or {}).get(CONF_COP_YAML) or ""
            table, error = _cop_table_from_yaml(text)
            if error:
                return self.async_show_form(
                    step_id="settings",
                    data_schema=_schema(dict(defaults), cop_text=text, modes=modes),
                    errors={"base": error},
                )
            flat = _flatten(user_input)
            flat[CONF_COP_TABLE] = table
            flat[CONF_BOILER_HEATING_STATES] = _parse_heating_states(flat.get(CONF_BOILER_HEATING_STATES))
            return self.async_create_entry(title="", data=flat)

        table = defaults.get(CONF_COP_TABLE) or COP_TABLE
        return self.async_show_form(
            step_id="settings",
            data_schema=_schema(
                dict(defaults), cop_text=_cop_table_to_yaml(table), modes=modes
            ),
        )

    async def async_step_runs(self, user_input: dict[str, Any] | None = None):
        """Wis de opwarmruns, maar alleen na een expliciete bevestiging."""
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        if user_input is not None:
            if not user_input.get(CONF_CONFIRM_RESET_RUNS):
                return self.async_abort(reason="runs_kept")
            await coordinator.async_reset_runs()
            return self.async_abort(reason="runs_cleared")

        runs = (coordinator.data or {}).get("opwarm_runs", "0/50")
        return self.async_show_form(
            step_id="runs",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONFIRM_RESET_RUNS, default=False): selector.BooleanSelector(),
                }
            ),
            description_placeholders={"runs": runs},
        )
