"""Constants for SmartBoiler Optimizer."""

from datetime import timedelta

DOMAIN = "smartboiler_optimizer"

CONF_PRICE_ENTITY = "price_entity"
CONF_SOLAR_ENTITY = "solar_entity"
CONF_OUTDOOR_TEMP_ENTITY = "outdoor_temp_entity"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_BOILER_ENTITY = "boiler_entity"
CONF_BOILER_TOP_TEMP_ENTITY = "boiler_top_temp_entity"
CONF_BOILER_BOTTOM_TEMP_ENTITY = "boiler_bottom_temp_entity"
CONF_BOILER_ENERGY_ENTITY = "boiler_energy_entity"
CONF_BOILER_TARGET_TEMP = "boiler_target_temp"
CONF_BOILER_DEADLINE_HOUR = "boiler_deadline_hour"
CONF_BOILER_POWER = "boiler_power_w"
CONF_BASE_LOAD = "base_load_w"
CONF_BOILER_CONTROL = "boiler_control"
CONF_BACKUP_SWITCH = "backup_switch"
CONF_BACKUP_TEMP_THRESHOLD = "backup_temp_threshold"
CONF_BACKUP_MODE = "backup_mode"
BACKUP_MODE_NORMAL = "normal"
BACKUP_MODE_REVERSED = "reversed"
CONF_BOILER_CAPACITY_LITERS = "boiler_capacity_liters"
CONF_USE_MEASURED_COP = "use_measured_cop"
CONF_USE_PRICE = "use_price"
CONF_USE_WEATHER = "use_weather"
CONF_USE_SOLAR = "use_solar"
CONF_BOILER_STATUS_ENTITY = "boiler_status_entity"
CONF_BOILER_MODE_ON = "boiler_mode_on"
CONF_BOILER_SWITCH = "boiler_switch_entity"
CONF_BOILER_HEATING_STATES = "boiler_heating_states"
CONF_BOILER_MODE_OFF = "boiler_mode_off"
CONF_COP_TABLE = "cop_table"

DEFAULT_BOILER_TARGET_TEMP = 55.0
DEFAULT_BOILER_DEADLINE_HOUR = 7
DEFAULT_BOILER_POWER = 1500
DEFAULT_BASE_LOAD = 300
DEFAULT_OUTDOOR_TEMP = 7.0
DEFAULT_RATE = 0.26  # graden per minuut, tot er geleerde runs zijn
MIN_RATE = 0.05
MIN_RUN_MINUTES = 5
BOILER_MARGIN_MINUTES = 15
BOILER_MIN_MINUTES = 30
MAX_RUNS = 50
DEFAULT_BACKUP_TEMP_THRESHOLD = 5.0
DEFAULT_BACKUP_MODE = "normal"
DEFAULT_BOILER_CAPACITY_LITERS = 200
DEFAULT_USE_MEASURED_COP = True
NEUTRAL_COP = 2.5  # COP als "Weer (COP)" uit staat
RATE_BAND_C = 2.5
# Per band the last samples are kept and the median is used: a shower or a
# draw-off in the middle of a sample shifts a mean, not a median.
RATE_SAMPLE_KEEP = 5
# A rate sample spans at least this long, so a single 5-minute tick cannot
# dominate the band.
RATE_SAMPLE_MIN_MINUTES = 15
# Samples outside this range are not heating at capacity (throttled, cycling)
# or are sensor noise; they say nothing about how fast the boiler can heat.
RATE_SAMPLE_MIN_C_PER_H = 2.0
RATE_SAMPLE_MAX_C_PER_H = 50.0
# A fall of more than this while heating is hot water being drawn off.
DRAW_OFF_DROP_C = 1.0
# A temperature probe that drops out is held at its last value. After this many
# minutes a second warning is logged, because a probe that stays away is a
# sensor problem and the held value is no longer fresh.
PROBE_HOLD_WARN_MINUTES = 15
# Largest meter increase in one tick that can be real. A meter reset (for
# example a Zigbee plug that reboots and reports 0 kWh) shows as a fall or a
# jump far above this.
MAX_METER_DELTA_KWH = 2.0

# COP-tabel voor tapwater (temperatuur °C, COP)
COP_TABLE = [
    (-10.0, 1.50),
    (-7.5, 1.60),
    (-5.0, 1.75),
    (-2.5, 1.90),
    (0.0, 2.00),
    (2.5, 2.20),
    (5.0, 2.35),
    (7.5, 2.60),
    (10.0, 2.80),
    (12.5, 3.00),
    (15.0, 3.25),
    (17.5, 3.45),
    (20.0, 3.65),
    (25.0, 4.00),
]


SLOT_MINUTES = 15
UPDATE_INTERVAL = timedelta(minutes=5)
STORAGE_VERSION = 1

# Alle operatiemodi die een water_heater kan kennen; de instelling toont alleen
# de modi die de gekozen boiler zelf meldt (of allemaal, als dat niet bekend is).
BOILER_MODE_LABELS = {
    "off": "Off",
    "eco": "Eco",
    "electric": "Electric",
    "performance": "Performance",
    "high_demand": "High demand",
    "heat_pump": "Heat pump",
    "gas": "Gas",
}
DEFAULT_BOILER_MODE_ON = "heat_pump"
DEFAULT_BOILER_HEATING_STATES = ["ProducingHeat", "RequestingHeat"]
DEFAULT_BOILER_MODE_OFF = "eco"
