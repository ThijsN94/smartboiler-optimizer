"""Optimisation logic for SmartBoiler Optimizer."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import math
from statistics import median
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BACKUP_MODE_REVERSED,
    CONF_BACKUP_MODE,
    CONF_BACKUP_SWITCH,
    CONF_BACKUP_TEMP_THRESHOLD,
    CONF_BASE_LOAD,
    CONF_BOILER_BOTTOM_TEMP_ENTITY,
    CONF_BOILER_CAPACITY_LITERS,
    CONF_BOILER_CONTROL,
    CONF_BOILER_DEADLINE_HOUR,
    CONF_BOILER_ENERGY_ENTITY,
    CONF_BOILER_ENTITY,
    CONF_BOILER_POWER,
    CONF_BOILER_HEATING_STATES,
    CONF_BOILER_MODE_OFF,
    CONF_BOILER_MODE_ON,
    CONF_BOILER_SWITCH,
    CONF_BOILER_STATUS_ENTITY,
    CONF_BOILER_TARGET_TEMP,
    CONF_BOILER_TOP_TEMP_ENTITY,
    CONF_COP_TABLE,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_SOLAR_ENTITY,
    CONF_USE_MEASURED_COP,
    CONF_USE_PRICE,
    CONF_USE_SOLAR,
    CONF_USE_WEATHER,
    NEUTRAL_COP,
    CONF_WEATHER_ENTITY,
    COP_TABLE,
    DEFAULT_BOILER_CAPACITY_LITERS,
    DEFAULT_BOILER_HEATING_STATES,
    DEFAULT_BOILER_MODE_OFF,
    DEFAULT_BOILER_MODE_ON,
    DEFAULT_BOILER_TARGET_TEMP,
    DEFAULT_OUTDOOR_TEMP,
    DEFAULT_USE_MEASURED_COP,
    BOILER_MARGIN_MINUTES,
    BOILER_MIN_MINUTES,
    DEFAULT_RATE,
    DOMAIN,
    DRAW_OFF_DROP_C,
    MAX_METER_DELTA_KWH,
    MAX_RUNS,
    PROBE_HOLD_WARN_MINUTES,
    RATE_BAND_C,
    RATE_SAMPLE_KEEP,
    RATE_SAMPLE_MAX_C_PER_H,
    RATE_SAMPLE_MIN_C_PER_H,
    RATE_SAMPLE_MIN_MINUTES,
    MIN_RATE,
    MIN_RUN_MINUTES,
    SLOT_MINUTES,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PRICE_DIVISOR = 10_000_000
PRICE_PLAUSIBLE_MIN_EUR = 0.01
PRICE_PLAUSIBLE_MAX_EUR = 2.00
SWITCH_THRESHOLD = 0.05
def _heating_states(cfg: dict[str, Any]) -> tuple[str, ...]:
    """Statussen van de boilerstatus-sensor die betekenen dat de boiler verwarmt."""
    value = cfg.get(CONF_BOILER_HEATING_STATES)
    if isinstance(value, str):
        states = [part.strip() for part in value.split(",") if part.strip()]
    else:
        states = [str(part) for part in value or []]
    return tuple(states or DEFAULT_BOILER_HEATING_STATES)

RATE_MAX = 0.6
REF_OUTDOOR_C = 10.0
OUTDOOR_FACTOR_MIN = 0.5
OUTDOOR_FACTOR_MAX = 2.0
DEFAULT_BOILER_TEMP_C = 40.0


def _interpolate(points: list[tuple[float, float]], x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def _quadratic_fit(points: list[tuple[float, float]]) -> tuple[float, float, float] | None:
    sums = [sum(x**k for x, _ in points) for k in range(5)]
    moments = [sum((x**k) * y for x, y in points) for k in range(3)]
    matrix = [
        [sums[4], sums[3], sums[2]],
        [sums[3], sums[2], sums[1]],
        [sums[2], sums[1], sums[0]],
    ]
    rhs = [moments[2], moments[1], moments[0]]

    def det(m: list[list[float]]) -> float:
        return (
            m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
        )

    base = det(matrix)
    if abs(base) < 1e-12:
        return None
    coeffs = []
    for col in range(3):
        replaced = [row[:] for row in matrix]
        for r in range(3):
            replaced[r][col] = rhs[r]
        coeffs.append(det(replaced) / base)
    return coeffs[0], coeffs[1], coeffs[2]


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = dt_util.parse_datetime(value)
    else:
        return None
    if result is None:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return result


def _to_float(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _state_float(state: State | None) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    return _to_float(state.state)


def _price_divisor(raw_values: list[float]) -> float:
    """Eenheid van de prijsfeed: al euro per kWh, of 1e-7 euro zoals Zonneplan levert.

    De eenheid wordt over de hele feed bepaald, niet per item. De deler is bewust
    vastgezet op 1e-7: een kandidaat van 1e-6 past ook op veel consumentenprijzen
    (0,10 tot 0,20 €), en een plausibiliteitscontrole zou die dan tienvoudig te
    hoog maken. Bij een afwijkende feed wordt een waarschuwing gelogd.
    """
    if not raw_values:
        return PRICE_DIVISOR
    typical = median(abs(v) for v in raw_values)
    if typical <= 1000:
        return 1.0
    if not PRICE_PLAUSIBLE_MIN_EUR <= typical / PRICE_DIVISOR <= PRICE_PLAUSIBLE_MAX_EUR:
        _LOGGER.warning(
            "SmartBoiler: prijsfeed ligt buiten het verwachte bereik (mediaan %s), controleer de prijssensor",
            typical,
        )
    return PRICE_DIVISOR


def _price_forecast(state: State | None) -> list[tuple[datetime, float]]:
    """Lees de prijsvoorspelling uit een `forecast`-attribuut.

    Ondersteund: Zonneplan (`start_date` + `price_tax_included`, kwartierprijzen) en
    een forecast met `datetime` + `value` (per uur, al in €/kWh). Een uurprijs geldt
    voor vier kwartieren en wordt daarom in vier gelijke kwartieren uitgeschreven.
    """
    if state is None:
        return []
    raw = []
    for item in state.attributes.get("forecast") or []:
        start = _to_datetime(item.get("start_date") or item.get("datetime"))
        if "price_tax_included" in item:
            price = _to_float(item.get("price_tax_included"))
        else:
            price = _to_float(item.get("value"))
        if start is not None and price is not None:
            raw.append((start, price))
    divisor = _price_divisor([price for _, price in raw])
    points = sorted((start, price / divisor) for start, price in raw)
    return _to_quarter_hours(points)


def _to_quarter_hours(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Schrijf een uurreeks uit naar kwartieren; een kwartierreeks blijft ongewijzigd."""
    if len(points) < 2:
        return points
    if not all(later[0] - earlier[0] == timedelta(hours=1) for earlier, later in zip(points, points[1:])):
        return points
    step = timedelta(minutes=SLOT_MINUTES)
    return [(start + step * k, price) for start, price in points for k in range(4)]


def _series(state: State | None, attribute: str) -> list[tuple[datetime, float]]:
    if state is None:
        return []
    result = []
    for key, value in (state.attributes.get(attribute) or {}).items():
        ts = _to_datetime(key)
        number = _to_float(value)
        if ts is not None and number is not None:
            result.append((ts, number))
    return sorted(result)


def _series_value_at(series: list[tuple[datetime, float]], when: datetime, default: float) -> float:
    value = default
    for ts, number in series:
        if ts > when:
            break
        value = number
    return value


def _temp_at(temps: list[tuple[datetime, float]], when: datetime, fallback: float) -> float:
    """Voorspelde temperatuur op een moment; vóór de eerste voorspelling de eerste waarde."""
    if not temps:
        return fallback
    if when < temps[0][0]:
        return temps[0][1]
    return _series_value_at(temps, when, fallback)


def _mean_of(rows: list[dict[str, Any]], key: str, digits: int, scale: float = 1) -> float | None:
    if not rows:
        return None
    return round(sum(row[key] for row in rows) / len(rows) * scale, digits)


def _coverage_status(
    series: list[tuple[datetime, Any]],
    now: datetime,
    configured: bool,
    reference: list[datetime],
    step: timedelta = timedelta(0),
) -> dict[str, Any]:
    if not configured:
        return {"status": "Niet ingesteld", "reden": None, "laatste": None}
    last = series[-1][0].isoformat() if series else None
    required = [t for t in reference if t >= now]
    if not series or not any(ts >= now for ts, _ in series):
        return {"status": "Fout", "reden": "geen voorspelling vanaf nu", "laatste": last}
    if required and (series[0][0] > required[0] + step or series[-1][0] < required[-1]):
        return {"status": "Fout", "reden": "voorspelling dekt het resterende deel van vandaag niet", "laatste": last}
    return {"status": "Beschikbaar", "reden": None, "laatste": last}


def _normalise_cop_table(value: Any) -> list[tuple[float, float]]:
    """Zet de opgeslagen COP-tabel om naar gesorteerde (temperatuur, COP)-paren.

    Bij een onbruikbare waarde valt de integratie terug op de standaardtabel.
    """
    if not value:
        return list(COP_TABLE)
    points: list[tuple[float, float]] = []
    try:
        for item in value:
            points.append((float(item[0]), float(item[1])))
    except (TypeError, ValueError, IndexError):
        return list(COP_TABLE)
    if len(points) < 2:
        return list(COP_TABLE)
    points.sort()
    return points


def _estimate_cop(outdoor_temp: float, table: list[tuple[float, float]] | None = None) -> float:
    """Bereken de COP uit de meettabel met lineaire interpolatie."""
    points = table if table else COP_TABLE
    if not points:
        return 2.5
    if outdoor_temp <= points[0][0]:
        return points[0][1]
    if outdoor_temp >= points[-1][0]:
        return points[-1][1]
    # Lineaire interpolatie tussen de twee dichtsbijzijnde meetpunten
    for i in range(len(points) - 1):
        t1, c1 = points[i]
        t2, c2 = points[i + 1]
        if t1 <= outdoor_temp <= t2:
            if t2 == t1:
                return c1
            fraction = (outdoor_temp - t1) / (t2 - t1)
            return c1 + fraction * (c2 - c1)
    return points[-1][1]  # fallback


def _measured_cop(runs: list[dict[str, Any]], outdoor_temp: float | None) -> float | None:
    """Bereken de gemeten COP uit de 5 runs met de dichtstbijzijnde buitentemperatuur."""
    if not runs:
        return None
    valid = [r for r in runs if r.get("cop") is not None and r.get("outdoor") is not None]
    if not valid:
        return None
    reference = outdoor_temp if outdoor_temp is not None else 7.0
    nearest = sorted(valid, key=lambda run: abs(run["outdoor"] - reference))[:5]
    return round(sum(run["cop"] for run in nearest) / len(nearest), 2)


def _plan_to_storage(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    slot = plan["slot"]
    return {
        "deadline": plan["deadline"].isoformat(),
        "minutes": plan["minutes"],
        "slot": {**slot, "start": slot["start"].isoformat()} if slot else None,
    }


def _run_to_storage(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        **run,
        "start": run["start"].isoformat(),
        "last": run["last"].isoformat(),
    }


def _run_from_storage(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    start = _to_datetime(data.get("start"))
    last = _to_datetime(data.get("last"))
    if start is None or last is None:
        return None
    return {**data, "start": start, "last": last}


def _plan_from_storage(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    deadline = _to_datetime(data.get("deadline"))
    if deadline is None:
        return None
    slot = data.get("slot")
    if slot:
        start = _to_datetime(slot.get("start"))
        slot = {**slot, "start": start} if start is not None else None
    return {"deadline": deadline, "minutes": float(data.get("minutes", 0)), "slot": slot}


class SmartBoilerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Calculate the cheapest boiler moment and the heating target."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        cfg = entry.options or entry.data
        self._cop_table = _normalise_cop_table(cfg.get(CONF_COP_TABLE))
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_runs")
        self._runs: list[dict[str, Any]] = []
        self._loaded = False
        self._last_kwh: float | None = None
        self._run: dict[str, Any] | None = None
        self._thermal_ref_temp: float | None = None
        self._probe_last: dict[str, float] = {}
        self._probe_missing: dict[str, datetime] = {}
        self._probe_warned: dict[str, datetime] = {}
        self._draw_off: bool = False
        self._rate_anchor: tuple[datetime, float] | None = None
        self._boiler_end: datetime | None = None
        self._plan: dict[str, Any] | None = None
        self._cancel_start: CALLBACK_TYPE | None = None
        self._heating_seen = False
        self._backup_changed = False
        self._rate_table: dict[str, dict[str, float]] = {}
        self._outdoor_table: dict[str, dict[str, float]] = {}
        self._boiler_mode_on: str = "heat_pump"
        self._boiler_mode_off: str = "eco"
        self._boiler_capacity_liters: float = float(
            cfg.get(CONF_BOILER_CAPACITY_LITERS, DEFAULT_BOILER_CAPACITY_LITERS)
        )
        entry.async_on_unload(self._cancel_scheduled_start)

    def get_measured_cop(self, outdoor_temp: float | None) -> float | None:
        """Geef de gemeten COP uit de opgeslagen runs."""
        return _measured_cop(self._runs, outdoor_temp)

    @staticmethod
    def _price_term(price: float, cfg: dict[str, Any]) -> float:
        """De prijs die in de kosten telt; zonder dynamische prijs telt elk uur gelijk."""
        return price if cfg.get(CONF_USE_PRICE, True) else 1.0

    def _get_cop(self, outdoor_temp: float | None) -> float:
        """Bepaal de COP: eerst gemeten uit runs (indien ingeschakeld), anders de geschatte formule."""
        use_measured = self.config.get(CONF_USE_MEASURED_COP, DEFAULT_USE_MEASURED_COP)
        if use_measured:
            measured = _measured_cop(self._runs, outdoor_temp)
            if measured is not None:
                return measured
        return _estimate_cop(
            outdoor_temp if outdoor_temp is not None else DEFAULT_OUTDOOR_TEMP,
            self._cop_table,
        )

    @property
    def config(self) -> dict[str, Any]:
        return self.entry.options or self.entry.data

    def _state(self, key: str) -> State | None:
        entity_id = self.config.get(key)
        return self.hass.states.get(entity_id) if entity_id else None

    async def _async_update_data(self) -> dict[str, Any]:
        cfg = self.config
        now = dt_util.now()

        if not self._loaded:
            stored = await self._store.async_load() or {}
            self._runs = stored.get("runs", [])
            self._plan = _plan_from_storage(stored.get("plan"))
            self._boiler_end = _to_datetime(stored.get("boiler_end"))
            self._boiler_mode_on = cfg.get(CONF_BOILER_MODE_ON, DEFAULT_BOILER_MODE_ON)
            self._boiler_mode_off = cfg.get(CONF_BOILER_MODE_OFF, DEFAULT_BOILER_MODE_OFF)
            self._rate_table = stored.get("boiler_rate_table") or {}
            self._outdoor_table = stored.get("outdoor_factor_table") or {}
            self._probe_last = stored.get("probe_last") or {}
            self._run = _run_from_storage(stored.get("run"))
            self._last_kwh = stored.get("last_kwh")
            self._thermal_ref_temp = stored.get("thermal_ref_temp")
            anchor_data = stored.get("rate_anchor")
            anchor_time = _to_datetime(anchor_data[0]) if anchor_data else None
            self._rate_anchor = (anchor_time, float(anchor_data[1])) if anchor_time else None
            self._draw_off = bool(stored.get("draw_off", False))
            self._heating_seen = bool(stored.get("heating_seen", False))
            self._backup_changed = bool(stored.get("backup_changed", False))
            if not self._rate_table:
                for run in self._runs:
                    if run.get("boiler") is not None and run.get("outdoor") is not None:
                        self._learn_rate(run["boiler"], run["outdoor"], run["rate"])
            self._loaded = True

        price_state = self._state(CONF_PRICE_ENTITY)
        prices = _price_forecast(price_state)
        current_price = _state_float(price_state)

        solar = self._solar_forecast()
        outdoor_temp = _state_float(self._state(CONF_OUTDOOR_TEMP_ENTITY))
        temps = await self._temperature_forecast()


        boiler_mean, boiler_target = self._boiler_temperatures()
        self._track_run(
            now,
            _state_float(self._state(CONF_BOILER_ENERGY_ENTITY)),
            boiler_mean,
            outdoor_temp,
            temps,
        )

        # _boiler_temperatures houdt bij een dropout de laatste probewaarde vast en
        # valt niet terug op de water_heater: dat is een andere grootheid.
        boiler_mean_for_duration = boiler_mean

        # Bepaal doeltemperatuur: die komt uit de water_heater, anders uit de instelling
        boiler_target_for_duration = boiler_target
        if boiler_target_for_duration is None:
            boiler_target_for_duration = cfg.get(CONF_BOILER_TARGET_TEMP, DEFAULT_BOILER_TARGET_TEMP)

        # Echte opwarmduur, per temperatuurband opgeteld; de ondergrens van 30 minuten geldt alleen voor de planning.
        # De buitentemperatuur komt uit de voorspelling op het moment van het
        # geplande blok; is er nog geen blok, dan op nu. Zo leert en rekent het
        # model met dezelfde grootheid.
        start_temp = boiler_mean_for_duration if boiler_mean_for_duration is not None else DEFAULT_BOILER_TEMP_C
        rate_moment = now
        if (
            self._plan is not None
            and self._plan.get("slot") is not None
            and self._plan["deadline"] == self._next_deadline(now)
            and self._plan["slot"]["start"] > now
        ):
            rate_moment = self._plan["slot"]["start"]
        rate_outdoor = self._rate_outdoor_temp(temps, rate_moment, outdoor_temp)
        # Actuele buitentemperatuur voor beslissingen (backup-switch, gemeten COP):
        # de voorspelling, met de sensor als terugval.
        outdoor_now = self._outdoor_now(temps, now, outdoor_temp)
        duration_raw = self._heatup_minutes(start_temp, boiler_target_for_duration, rate_outdoor)

        # De planning reserveert minimaal BOILER_MIN_MINUTES, de sensor toont de echte duur
        duration = max(BOILER_MIN_MINUTES, duration_raw)

        plan = self._current_plan(prices, solar, temps, outdoor_temp, now, duration)
        boiler = plan["slot"] if plan else None
        planned_minutes = plan["minutes"] if plan else None

        boiler_entity_id = self.config.get(CONF_BOILER_ENTITY)
        boiler_state = self.hass.states.get(boiler_entity_id) if boiler_entity_id else None
        boiler_is_on = boiler_state is not None and boiler_state.state in ("heat_pump", "performance")

        status_entity = cfg.get(CONF_BOILER_STATUS_ENTITY)
        status_state = None
        if status_entity:
            status_state = self.hass.states.get(status_entity)
            status = status_state.state if status_state else STATE_UNKNOWN
        else:
            status = STATE_UNAVAILABLE

        if status_entity:
            boiler_is_warming = (
                boiler_is_on
                and status_state is not None
                and status_state.state in _heating_states(cfg)
            )
        else:
            boiler_is_warming = boiler_is_on

        is_warming = False
        if boiler is not None and planned_minutes is not None and planned_minutes > 0:
            slot_start = boiler["start"]
            if slot_start <= now < slot_start + timedelta(minutes=SLOT_MINUTES) and boiler_is_warming:
                is_warming = True

        block_minutes = max(planned_minutes or 0, BOILER_MIN_MINUTES)
        block_slots = max(1, math.ceil(block_minutes / SLOT_MINUTES))
        blok = (
            self._block_details(boiler["start"], block_slots, prices, solar, temps, outdoor_temp)
            if boiler
            else []
        )
        alternatives = [
            {"tijd": slot["start"].isoformat(), "prijs": round(slot["price"], 4), "kosten": round(slot["cost"], 4)}
            for slot in self._ranked_slots(prices, solar, temps, outdoor_temp, now, block_minutes, earliest=now)
            if boiler is None or slot["start"] != boiler["start"]
        ][:3]

        if cfg.get(CONF_BOILER_CONTROL):
            needed = planned_minutes if planned_minutes is not None else 0.0
            mode = self._boiler_mode_off
            mode = self._boiler_mode_off
            if boiler is not None:
                slot_start = boiler["start"]
                if slot_start <= now < slot_start + timedelta(minutes=SLOT_MINUTES):
                    mode = self._boiler_mode_on
            await self._async_control_boiler(now, boiler, needed, mode, boiler_mean, boiler_target, cfg)
        # De backup-switch hoort alleen tijdens een run door de drempel bestuurd te worden.
        # Na de run is hij uitgezet en blijft hij uit tot de volgende run; buiten een run
        # blijft de schakelaar dus met rust (andere automatiseringen kunnen hem gebruiken).
        if self._boiler_end is not None:
            await self._control_backup_switch(outdoor_now, cfg)
        self._schedule_start(boiler["start"] if boiler else None)

        reference = [start for start, _ in prices]
        timeline = self._timeline(prices, solar, temps, outdoor_temp, now, {r["tijd"] for r in blok})
        tomorrow_start = self._tomorrow_plan(prices, solar, temps, outdoor_temp, now, duration)

        return {
            "verwachting": timeline,
            "verwachting_deadline": self._next_deadline(now).isoformat(),
            "opwarm_runs": f"{len(self._runs)}/{MAX_RUNS}",
            "opwarm_tabel": self._rate_view(),
            "opwarm_buiten_tabel": self._outdoor_view(),
            "current_price": current_price,
            "boiler_start": boiler["start"] if boiler else None,
            "tomorrow_boiler_start": tomorrow_start,
            "boiler_price": boiler["price"] if boiler else None,
            "boiler_cost": boiler["cost"] if boiler else None,
            "boiler_duration": round(duration_raw),
            "boiler_planned_duration": round(planned_minutes) if planned_minutes is not None else None,
            "boiler_needed": duration_raw > 0,
            "boiler_rate": round(self._estimate_rate(start_temp, rate_outdoor), 3),
            "boiler_mean_temp": boiler_mean,
            "boiler_target_temp": boiler_target,
            "outdoor_temp": outdoor_temp,
            "outdoor_now": outdoor_now,
            "opwarm_buiten_temp": round(rate_outdoor, 1),
            "blok_details": blok,
            "alternatieven": alternatives,
            "blok_prijs": _mean_of(blok, "prijs", 4),
            "blok_zon": _mean_of(blok, "zonaandeel", 2, scale=100),
            "blok_cop": _mean_of(blok, "cop", 2),
            "blok_temp": _mean_of(blok, "temperatuur", 1),
            "status_prijs": _coverage_status(prices, now, bool(cfg.get(CONF_PRICE_ENTITY)), reference),
            "status_weer": _coverage_status(
                temps, now, bool(cfg.get(CONF_WEATHER_ENTITY)), reference, timedelta(hours=1)
            ),
            "status_zon": _coverage_status(solar, now, bool(cfg.get(CONF_SOLAR_ENTITY)), reference),
        }

    def _timeline(
        self,
        prices: list[tuple[datetime, float]],
        solar: list[tuple[datetime, float]],
        temps: list[tuple[datetime, float]],
        outdoor_temp: float | None,
        now: datetime,
        block_times: set[str],
    ) -> list[dict[str, Any]]:
        """Prijs, zon en temperatuur per kwartier, op de tijden van de dynamische prijs."""
        deadline = self._next_deadline(now)
        floor = deadline - timedelta(hours=24)
        rows: list[dict[str, Any]] = []
        for start, price in prices:
            if start < floor:
                continue
            if start >= deadline:
                break
            zon = _series_value_at(solar, start, 0.0) if solar and solar[0][0] <= start else None
            if temps and temps[0][0] <= start + timedelta(hours=1):
                temp = _temp_at(temps, start, outdoor_temp if outdoor_temp is not None else DEFAULT_OUTDOOR_TEMP)
            else:
                temp = None
            cost = None
            use_weather = self.config.get(CONF_USE_WEATHER, True)
            if temp is not None or not use_weather:
                production = zon if zon is not None else 0.0
                surplus = max(0.0, production - self.config[CONF_BASE_LOAD])
                share = min(1.0, surplus / self.config[CONF_BOILER_POWER]) if self.config[CONF_BOILER_POWER] else 0.0
                if not self.config.get(CONF_USE_SOLAR, True):
                    share = 0.0
                cop = self._get_cop(temp) if use_weather else NEUTRAL_COP
                cost = self._price_term(price, self.config) * (1 - share) / cop
            rows.append(
                {
                    "t": start.isoformat(),
                    "p": round(price, 4),
                    "z": round(zon) if zon is not None else None,
                    "c": round(temp, 1) if temp is not None else None,
                    "k": round(cost, 4) if cost is not None else None,
                    "b": 1 if start.isoformat() in block_times else 0,
                }
            )
        return rows

    async def _control_backup_switch(self, outdoor_temp: float | None, cfg: dict[str, Any]) -> None:
        """Zet de backup-switch aan (bijverwarming geblokkeerd) of uit op basis van de buitentemperatuur.

        Wordt alleen tijdens een lopende run aangeroepen. Buiten een run blijft de schakelaar
        uit, zodat hij niet in dezelfde cyclus als het uitzetten weer aangezet wordt.
        """
        switch_entity = cfg.get(CONF_BACKUP_SWITCH)
        if not switch_entity:
            return
        if outdoor_temp is None:
            return
        threshold = cfg.get(CONF_BACKUP_TEMP_THRESHOLD)
        if threshold is None:
            return
        state = self.hass.states.get(switch_entity)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        above = outdoor_temp > threshold
        want_on = not above if cfg.get(CONF_BACKUP_MODE) == BACKUP_MODE_REVERSED else above
        if want_on and state.state == "off":
            await self._set_backup(switch_entity, on=True)
        elif not want_on and state.state == "on":
            await self._set_backup(switch_entity, on=False)

    async def _set_backup(self, switch_entity: str, on: bool) -> None:
        """Zet de backup-switch en onthoud dat de integratie tijdens de run heeft geschakeld."""
        await self.hass.services.async_call(
            "switch",
            "turn_on" if on else "turn_off",
            {"entity_id": switch_entity},
            blocking=True,
        )
        self._backup_changed = True
        self._save()

    async def _finish_backup(self, cfg: dict[str, Any]) -> None:
        """Aan het eind van de run: alleen schakelen als de integratie tijdens de run heeft geschakeld."""
        switch_entity = cfg.get(CONF_BACKUP_SWITCH)
        if not switch_entity or not self._backup_changed:
            return
        end_on = cfg.get(CONF_BACKUP_MODE) == BACKUP_MODE_REVERSED
        state = self.hass.states.get(switch_entity)
        if state and state.state in ("on", "off") and (state.state == "on") != end_on:
            await self._set_backup(switch_entity, on=end_on)
        self._backup_changed = False
        self._save()

    async def _async_control_boiler(
        self,
        now: datetime,
        boiler: dict[str, Any] | None,
        planned_minutes: float,
        mode: str,
        mean_temp: float | None,
        target_temp: float | None,
        cfg: dict[str, Any],
    ) -> None:
        entity_id = self.config.get(CONF_BOILER_ENTITY)
        switch_id = cfg.get(CONF_BOILER_SWITCH)
        if entity_id:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                return
        elif not switch_id:
            return

        if self._boiler_end is not None:
            status_entity = cfg.get(CONF_BOILER_STATUS_ENTITY)
            if status_entity:
                status_state = self.hass.states.get(status_entity)
                status = status_state.state if status_state else STATE_UNKNOWN
                heating = _heating_states(cfg)
                if status in heating:
                    self._heating_seen = True
                finished = (
                    self._heating_seen
                    and status not in heating
                    and status not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                ) or now >= self._boiler_end
            else:
                reached = mean_temp is not None and target_temp is not None and mean_temp >= target_temp
                finished = reached or now >= self._boiler_end
            if finished:
                self._boiler_end = None
                self._heating_seen = False
                self._save()
                await self._set_boiler(entity_id, switch_id, self._boiler_mode_off, on=False)
                await self._finish_backup(cfg)
            return

        if boiler is None or planned_minutes <= 0:
            return
        slot_start = boiler["start"]
        if slot_start <= now < slot_start + timedelta(minutes=SLOT_MINUTES):
            self._boiler_end = now + timedelta(
                minutes=max(planned_minutes, BOILER_MIN_MINUTES) + BOILER_MARGIN_MINUTES
            )
            self._heating_seen = False
            self._backup_changed = False
            self._save()
            await self._set_boiler(entity_id, switch_id, mode, on=True)

    async def _set_boiler(
        self, entity_id: str | None, switch_id: str | None, mode: str, on: bool
    ) -> None:
        """Zet de boiler aan of uit: via de water_heater als die is ingesteld, anders via de schakelaar."""
        if entity_id:
            await self._call_water_heater("set_operation_mode", entity_id, mode)
            return
        if switch_id:
            try:
                await self.hass.services.async_call(
                    "switch",
                    "turn_on" if on else "turn_off",
                    {"entity_id": switch_id},
                    blocking=True,
                )
            except HomeAssistantError as err:
                _LOGGER.error("switch voor de boiler mislukt voor %s: %s", switch_id, err)

    async def _call_water_heater(
        self, service: str, entity_id: str, mode: str | None = None
    ) -> None:
        service_data: dict[str, str | Any] = {"entity_id": entity_id}
        if mode is not None:
            service_data["operation_mode"] = mode
        try:
            await self.hass.services.async_call("water_heater", service, service_data, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.error("water_heater.%s mislukt voor %s: %s", service, entity_id, err)

    @callback
    def _cancel_scheduled_start(self) -> None:
        if self._cancel_start is not None:
            self._cancel_start()
            self._cancel_start = None

    def _schedule_start(self, start: datetime | None) -> None:
        """Vraag op het startmoment direct een update aan, zodat de boiler op tijd aangaat."""
        if not self.config.get(CONF_BOILER_CONTROL):
            self._cancel_scheduled_start()
            return
        if start is None or start <= dt_util.now():
            return
        self._cancel_scheduled_start()
        self._cancel_start = async_track_point_in_time(self.hass, self._async_start_due, start)

    async def _async_start_due(self, _now: datetime) -> None:
        self._cancel_start = None
        await self.async_request_refresh()

    def _probe_temp(self, entity_id: str, now: datetime) -> float | None:
        """Temperatuur van één probe; bij een dropout blijft de laatste waarde staan.

        Een weggevallen probe wordt nooit stilzwijgend door een andere sensor
        vervangen. Dat is een andere grootheid, en dan klopt de gemiddelde
        tanktemperatuur — en dus de duurberekening — niet meer.
        """
        value = _state_float(self.hass.states.get(entity_id))
        if value is not None:
            self._probe_last[entity_id] = value
            if self._probe_missing.pop(entity_id, None) is not None:
                self._probe_warned.pop(entity_id, None)
                _LOGGER.info("SmartBoiler: temperatuursensor %s is terug", entity_id)
            return value

        last = self._probe_last.get(entity_id)
        if last is None:
            return None
        since = self._probe_missing.setdefault(entity_id, now)
        warned = self._probe_warned.get(entity_id)
        if warned is None or now - warned >= timedelta(minutes=PROBE_HOLD_WARN_MINUTES):
            _LOGGER.warning(
                "SmartBoiler: temperatuursensor %s is weg sinds %s — laatste waarde %.1f °C wordt vastgehouden",
                entity_id,
                since.strftime("%H:%M"),
                last,
            )
            self._probe_warned[entity_id] = now
        return last

    def _boiler_temperatures(self) -> tuple[float | None, float | None]:
        """Gemiddelde tanktemperatuur en doeltemperatuur.

        Zijn beide probes ingesteld, dan is het gemiddelde van die twee de
        tanktemperatuur: elke probe vertegenwoordigt de helft van het volume.
        Ontbreekt er één, dan wordt de laatste waarde vastgehouden en blijft het
        gemiddelde leeg zolang er nog nooit een waarde is gezien. De temperatuur
        van de water_heater is een andere grootheid en telt alleen mee als er
        geen probes zijn ingesteld.
        """
        now = dt_util.now()
        water = self._state(CONF_BOILER_ENTITY)
        top_key = self.config.get(CONF_BOILER_TOP_TEMP_ENTITY)
        bottom_key = self.config.get(CONF_BOILER_BOTTOM_TEMP_ENTITY)
        mean: float | None
        if top_key and bottom_key:
            top = self._probe_temp(top_key, now)
            bottom = self._probe_temp(bottom_key, now)
            mean = (top + bottom) / 2 if top is not None and bottom is not None else None
        elif top_key or bottom_key:
            mean = self._probe_temp(top_key or bottom_key, now)
        elif water is not None:
            mean = _to_float(water.attributes.get("current_temperature"))
        else:
            mean = None
        target = _to_float(water.attributes.get("temperature")) if water is not None else None
        return mean, target

    def _track_run(
        self,
        now: datetime,
        kwh: float | None,
        mean_temp: float | None,
        outdoor_temp: float | None,
        temps: list[tuple[datetime, float]] | None = None,
    ) -> None:
        """Leer de opwarmsnelheid en COP uit doorlopende warmtepompruns."""
        if kwh is None:
            return
        if self._last_kwh is None:
            self._last_kwh = kwh
            self._thermal_ref_temp = mean_temp
            return

        delta = kwh - self._last_kwh
        self._last_kwh = kwh
        if not 0.0 <= delta <= MAX_METER_DELTA_KWH:
            # Meterreset of stroomonderbreking: er is niets verbruikt. De run wordt
            # afgebroken, zodat het verbruik van na de reset niet aan de run hangt.
            _LOGGER.warning(
                "SmartBoiler: energiemeter sprong met %.2f kWh — run afgebroken (meterreset of stroomonderbreking)",
                delta,
            )
            self._run = None
            self._rate_anchor = None
            self._thermal_ref_temp = mean_temp
            return

        if delta > 0.001:
            if self._run is None:
                self._start_run(now)
            self._pump_tick(now, delta, mean_temp, outdoor_temp, temps or [])
            self._save()
            return

        if self._run is not None:
            self._finish_run()
            self._rate_anchor = None
            self._save()
        # Een run start vanaf de laatste meting vóór de pomp aansloeg, niet vanaf de eerste meting erna.
        if mean_temp is not None:
            self._thermal_ref_temp = mean_temp

    def _start_run(self, now: datetime) -> None:
        self._run = {
            "start": now,
            "last": now,
            "outdoor": [],
            "boiler": [],
            "thermal_rise": 0.0,
            "electrical_kwh": 0.0,
        }
        self._draw_off = False
        self._rate_anchor = None

    def _pump_tick(
        self,
        now: datetime,
        delta: float,
        mean_temp: float | None,
        outdoor_temp: float | None,
        temps: list[tuple[datetime, float]],
    ) -> None:
        """Boek één tik van een lopende run: warmte, elektriciteit en de opwarmsnelheid."""
        run = self._run
        run["last"] = now
        # De factor wordt geleerd op de voorspelling van dat moment, niet op de
        # actuele sensor: die kan leeg zijn en hoort niet bij een blok in de tijd.
        learning_temp = self._rate_outdoor_temp(temps, now, outdoor_temp)
        run["outdoor"].append(learning_temp)
        if mean_temp is not None:
            run["boiler"].append(mean_temp)
            run["thermal_rise"] += self._counted_rise(mean_temp)
            self._sample_rate(now, mean_temp, learning_temp)
        # Tapwater dat tijdens het tappen wordt opgewarmd, komt niet in de COP.
        if not self._draw_off:
            run["electrical_kwh"] += delta

    def _counted_rise(self, boiler_temp: float) -> float:
        """Temperatuurstijging (°C) die als warmte in het water telt.

        Alleen een stijging boven de laatst geteld temperatuur is warmte van de
        pomp. Een daling is nooit negatieve warmte: bij tappen volgt de referentie
        de temperatuur omlaag, en het opnieuw opwarmen telt vanaf dat dieptepunt.
        Anders zou één tapping de COP van de hele run onderuit halen.
        """
        ref = self._thermal_ref_temp
        if ref is None:
            self._thermal_ref_temp = boiler_temp
            return 0.0
        if boiler_temp > ref:
            self._draw_off = False
            self._thermal_ref_temp = boiler_temp
            return boiler_temp - ref
        if self._draw_off or ref - boiler_temp > DRAW_OFF_DROP_C:
            self._draw_off = True
            self._thermal_ref_temp = boiler_temp
        return 0.0

    def _sample_rate(self, now: datetime, boiler_temp: float, outdoor_temp: float | None) -> None:
        """Leer de opwarmsnelheid per interval van minstens 15 minuten.

        Een interval wordt ingedeeld in de temperatuurband van zijn midden, niet in
        de band van het hele run-gemiddelde. Een run van 25 naar 55 °C levert zo
        data voor alle banden die hij doorloopt. Tappen breekt het interval af.
        """
        anchor = self._rate_anchor
        if anchor is None or self._draw_off:
            self._rate_anchor = (now, boiler_temp)
            return
        anchor_time, anchor_temp = anchor
        minutes = (now - anchor_time).total_seconds() / 60
        if minutes < RATE_SAMPLE_MIN_MINUTES:
            return
        self._rate_anchor = (now, boiler_temp)
        rise = boiler_temp - anchor_temp
        if rise <= 0:
            return
        rate = rise / minutes
        if not RATE_SAMPLE_MIN_C_PER_H <= rate * 60 <= RATE_SAMPLE_MAX_C_PER_H:
            return
        self._learn_rate(
            (anchor_temp + boiler_temp) / 2,
            outdoor_temp if outdoor_temp is not None else DEFAULT_OUTDOOR_TEMP,
            rate,
        )

    def _finish_run(self) -> None:
        run = self._run
        self._run = None
        if run is None:
            return

        minutes = (run["last"] - run["start"]).total_seconds() / 60
        if minutes < MIN_RUN_MINUTES or run["thermal_rise"] <= 0:
            return

        # Thermische warmte in kWh: liters × 0,001163 × temperatuurstijging
        thermal_kwh = self._boiler_capacity_liters * 0.001163 * run["thermal_rise"]
        electrical_kwh = run["electrical_kwh"]

        cop = thermal_kwh / electrical_kwh if electrical_kwh > 0 else None

        outdoor_mean = sum(run["outdoor"]) / len(run["outdoor"]) if run["outdoor"] else None
        boiler_temps = run["boiler"]
        boiler_mean_run = sum(boiler_temps) / len(boiler_temps) if boiler_temps else None
        self._runs.append(
            {
                "outdoor": round(outdoor_mean, 1) if outdoor_mean is not None else None,
                "boiler": round(boiler_mean_run, 1) if boiler_mean_run is not None else None,
                "rate": round(run["thermal_rise"] / minutes, 3),
                "cop": round(cop, 2) if cop is not None else None,
                "thermal_kwh": round(thermal_kwh, 2),
                "electrical_kwh": round(electrical_kwh, 2),
            }
        )
        self._runs = self._runs[-MAX_RUNS:]
        self._save()

    def _save(self) -> None:
        self.hass.async_create_task(
            self._store.async_save(
                {
                    "runs": self._runs,
                    "plan": _plan_to_storage(self._plan),
                    "boiler_end": self._boiler_end.isoformat() if self._boiler_end else None,
                    "boiler_mode_on": self._boiler_mode_on,
                    "boiler_mode_off": self._boiler_mode_off,
                    "boiler_rate_table": self._rate_table,
                    "outdoor_factor_table": self._outdoor_table,
                    "probe_last": self._probe_last,
                    "run": _run_to_storage(self._run),
                    "last_kwh": self._last_kwh,
                    "thermal_ref_temp": self._thermal_ref_temp,
                    "rate_anchor": (
                        [self._rate_anchor[0].isoformat(), self._rate_anchor[1]]
                        if self._rate_anchor
                        else None
                    ),
                    "draw_off": self._draw_off,
                    "heating_seen": self._heating_seen,
                    "backup_changed": self._backup_changed,
                }
            )
        )

    def _current_plan(
        self,
        prices: list[tuple[datetime, float]],
        solar: list[tuple[datetime, float]],
        temps: list[tuple[datetime, float]],
        outdoor_temp: float | None,
        now: datetime,
        duration: float,
    ) -> dict[str, Any] | None:
        """Werk het startmoment bij tot het verstreken is; daarna blijft het staan tot de deadline."""
        deadline = self._next_deadline(now)
        plan = self._plan if self._plan is not None and self._plan["deadline"] == deadline else None
        if plan is not None and plan["slot"] is not None and plan["slot"]["start"] <= now:
            return plan
        if not prices:
            return plan

        minutes = duration
        slot_minutes = max(minutes, BOILER_MIN_MINUTES)
        ranked = self._ranked_slots(prices, solar, temps, outdoor_temp, now, slot_minutes, earliest=now)
        slot = ranked[0] if ranked else None

        if plan is not None and plan["slot"] is not None and slot is not None and slot["start"] != plan["slot"]["start"]:
            current = next((s for s in ranked if s["start"] == plan["slot"]["start"]), None)
            if current is not None and slot["cost"] >= current["cost"] * (1 - SWITCH_THRESHOLD):
                slot = current

        old_start = plan["slot"]["start"] if plan is not None and plan["slot"] is not None else None
        new_start = slot["start"] if slot is not None else None
        old_minutes = round(plan["minutes"]) if plan is not None else None
        self._plan = {"deadline": deadline, "minutes": minutes, "slot": slot}
        if old_start != new_start or old_minutes != round(minutes):
            self._save()
        return self._plan

    def _tomorrow_plan(
        self,
        prices: list[tuple[datetime, float]],
        solar: list[tuple[datetime, float]],
        temps: list[tuple[datetime, float]],
        outdoor_temp: float | None,
        now: datetime,
        duration: float,
    ) -> datetime | None:
        """Startmoment voor het blok een dag later: van de huidige deadline tot de deadline daarna."""
        window_start = self._next_deadline(now)
        window_end = window_start + timedelta(days=1)
        in_window = [start for start, _ in prices if window_start <= start < window_end]
        last_slot = window_end - timedelta(minutes=SLOT_MINUTES)
        if not in_window or min(in_window) > window_start or max(in_window) < last_slot:
            return None
        minutes = duration
        slot_minutes = max(minutes, BOILER_MIN_MINUTES)
        ranked = self._ranked_slots(
            prices, solar, temps, outdoor_temp, window_start, slot_minutes, earliest=window_start
        )
        return ranked[0]["start"] if ranked else None

    async def async_reset_plan(self) -> None:
        self._plan = None
        self._save()
        await self.async_request_refresh()

    async def async_reset_runs(self) -> None:
        """Wist de opwarmruns (COP-historie). De geleerde opwarmsnelheid blijft staan."""
        self._runs = []
        self._save()
        await self.async_request_refresh()

    def _next_deadline(self, now: datetime) -> datetime:
        deadline = now.replace(hour=int(self.config[CONF_BOILER_DEADLINE_HOUR]), minute=0, second=0, microsecond=0)
        if deadline <= now:
            deadline += timedelta(days=1)
        return deadline

    @staticmethod
    def _rate_key(temp: float) -> str:
        return f"{math.floor(temp / RATE_BAND_C) * RATE_BAND_C:.1f}"

    @staticmethod
    def _push_sample(table: dict[str, dict[str, Any]], key: str, value: float, field: str) -> None:
        """Voeg een meting toe aan een band; het getal in `field` is de mediaan van de laatste metingen.

        Oude tabelwaarden zonder `samples` tellen hun opgeslagen getal als eerste meting.
        """
        entry = table.get(key) or {}
        samples = list(entry.get("samples") or ([entry[field]] if field in entry else []))
        samples = (samples + [value])[-RATE_SAMPLE_KEEP:]
        table[key] = {
            "samples": samples,
            field: round(median(samples), 4),
            "n": entry.get("n", 0) + 1,
        }

    def _update_rate(self, temp: float, rate: float) -> None:
        self._push_sample(self._rate_table, self._rate_key(temp), rate, "rate")

    def _rate_fit(self) -> tuple[float, float] | None:
        points = [(float(k), v["rate"]) for k, v in self._rate_table.items() if v.get("n", 0) > 0]
        if len(points) < 2:
            return None
        mean_x = sum(x for x, _ in points) / len(points)
        mean_y = sum(y for _, y in points) / len(points)
        sxx = sum((x - mean_x) ** 2 for x, _ in points)
        if sxx == 0:
            return None
        slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx
        return slope, mean_y - slope * mean_x

    def _rate_for(self, temp: float) -> float:
        entry = self._rate_table.get(self._rate_key(temp))
        if entry and entry.get("n", 0) > 0:
            return max(MIN_RATE, entry["rate"])
        points = [(float(k), v["rate"]) for k, v in self._rate_table.items() if v.get("n", 0) > 0]
        if len(points) >= 4:
            coeffs = _quadratic_fit(points)
            if coeffs is not None:
                a, b, c = coeffs
                return min(RATE_MAX, max(MIN_RATE, a * temp * temp + b * temp + c))
        fit = self._rate_fit()
        if fit is not None:
            slope, intercept = fit
            return min(RATE_MAX, max(MIN_RATE, slope * temp + intercept))
        measured = [v["rate"] for v in self._rate_table.values() if v.get("n", 0) > 0]
        if len(measured) == 1:
            return max(MIN_RATE, measured[0])
        return DEFAULT_RATE

    def _rate_view(self) -> list[dict[str, Any]]:
        centers = [10.0 + i * RATE_BAND_C for i in range(int(50 / RATE_BAND_C) + 1)]
        view = []
        for center in centers:
            entry = self._rate_table.get(self._rate_key(center))
            view.append(
                {
                    "temp": center,
                    "opwarmsnelheid": round(self._rate_for(center), 3),
                    "runs": int(entry["n"]) if entry else 0,
                }
            )
        return view

    def _outdoor_view(self) -> list[dict[str, Any]]:
        view = []
        for center in [-10.0 + i * RATE_BAND_C for i in range(int(35 / RATE_BAND_C) + 1)]:
            entry = self._outdoor_table.get(self._rate_key(center))
            view.append(
                {
                    "temp": center,
                    "factor": round(self._outdoor_factor(center), 3),
                    "runs": int(entry["n"]) if entry else 0,
                }
            )
        return view

    def _update_outdoor(self, temp: float, ratio: float) -> None:
        self._push_sample(self._outdoor_table, self._rate_key(temp), ratio, "factor")

    def _outdoor_factor(self, temp: float) -> float:
        """Buitentemperatuur-factor, genormaliseerd op de band rond de referentietemperatuur."""
        points = sorted(
            (float(k) + RATE_BAND_C / 2, v["factor"]) for k, v in self._outdoor_table.items() if v.get("n", 0) > 0
        )
        if not points:
            return 1.0
        ref = min(points, key=lambda p: abs(p[0] - REF_OUTDOOR_C))[1]
        if ref <= 0:
            return 1.0
        normalized = [(x, y / ref) for x, y in points]
        return min(OUTDOOR_FACTOR_MAX, max(OUTDOOR_FACTOR_MIN, _interpolate(normalized, temp)))

    def _learn_rate(self, boiler_temp: float, outdoor_temp: float, rate: float) -> None:
        """Leer de boilertabel (zonder buiteninvloed) en de buitenfactor uit één run."""
        self._update_rate(boiler_temp, rate / self._outdoor_factor(outdoor_temp))
        base = self._rate_for(boiler_temp)
        if base > 0:
            self._update_outdoor(outdoor_temp, rate / base)

    def _rate_outdoor_temp(
        self,
        temps: list[tuple[datetime, float]],
        when: datetime,
        outdoor_temp: float | None,
    ) -> float:
        """Buitentemperatuur voor het opwarmmodel: de voorspelling op dat moment.

        Leren en rekenen gebruiken zo dezelfde grootheid. Een actuele sensor is
        voor een blok in de toekomst de verkeerde waarde, en hij kan ontbreken.
        Ontbreekt de voorspelling, dan telt de actuele sensor en anders
        DEFAULT_OUTDOOR_TEMP.
        """
        fallback = outdoor_temp if outdoor_temp is not None else DEFAULT_OUTDOOR_TEMP
        return _temp_at(temps, when, fallback)

    def _outdoor_now(
        self,
        temps: list[tuple[datetime, float]],
        when: datetime,
        outdoor_temp: float | None,
    ) -> float | None:
        """Buitentemperatuur op dit moment: de voorspelling, met de sensor als terugval.

        Geeft None als er geen voorspelling én geen sensor is. Aanroepers die
        actie ondernemen (de backup-switch) moeten dan niets doen: nooit op
        DEFAULT_OUTDOOR_TEMP sturen.
        """
        if temps:
            return _temp_at(temps, when, outdoor_temp if outdoor_temp is not None else DEFAULT_OUTDOOR_TEMP)
        return outdoor_temp

    def _estimate_rate(self, boiler_temp: float, outdoor_temp: float | None) -> float:
        """Opwarmsnelheid in graden per minuut: boilertabel × buitenfactor."""
        reference = outdoor_temp if outdoor_temp is not None else DEFAULT_OUTDOOR_TEMP
        rate = self._rate_for(boiler_temp) * self._outdoor_factor(reference)
        return min(RATE_MAX, max(MIN_RATE, rate))

    def _heatup_minutes(self, start_temp: float, target_temp: float, outdoor_temp: float | None) -> float:
        """Opwarmduur in minuten: de snelheid per temperatuurband, opgeteld over de hele weg naar het doel.

        De snelheid verandert met de temperatuur. Eén snelheid op de starttemperatuur
        onderschat de duur van een koude start, dus per band van RATE_BAND_C wordt
        de tijd apart opgeteld.
        """
        minutes = 0.0
        temp = start_temp
        while temp < target_temp - 1e-6:
            step = min(RATE_BAND_C, target_temp - temp)
            minutes += step / self._estimate_rate(temp + step / 2, outdoor_temp)
            temp += step
        return minutes

    def _solar_forecast(self) -> list[tuple[datetime, float]]:
        """Tel de zonneforecasts van alle gekozen arrays (oost, west, zuid) op."""
        entity_ids = self.config.get(CONF_SOLAR_ENTITY) or []
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        series = [_series(self.hass.states.get(eid), "watts") for eid in entity_ids]
        series = [forecast for forecast in series if forecast]
        if not series:
            return []
        timestamps = sorted({ts for forecast in series for ts, _ in forecast})
        return [
            (ts, sum(_series_value_at(forecast, ts, 0.0) for forecast in series))
            for ts in timestamps
        ]

    async def _temperature_forecast(self) -> list[tuple[datetime, float]]:
        entity_id = self.config.get(CONF_WEATHER_ENTITY)
        if not entity_id:
            return []
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": entity_id, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning("Weather forecast ophalen mislukt: %s", err)
            return []

        result = []
        for item in (response or {}).get(entity_id, {}).get("forecast", []):
            ts = _to_datetime(item.get("datetime"))
            temp = _to_float(item.get("temperature"))
            if ts is not None and temp is not None:
                result.append((ts, temp))
        return sorted(result)

    def _block_details(
        self,
        start: datetime,
        slots: int,
        prices: list[tuple[datetime, float]],
        solar: list[tuple[datetime, float]],
        temps: list[tuple[datetime, float]],
        outdoor_temp: float | None,
    ) -> list[dict[str, Any]]:
        cfg = self.config
        fallback_temp = outdoor_temp if outdoor_temp is not None else DEFAULT_OUTDOOR_TEMP
        rows: list[dict[str, Any]] = []
        for slot_start, price in prices:
            if slot_start < start:
                continue
            if len(rows) >= slots:
                break
            production = _series_value_at(solar, slot_start, 0.0)
            surplus = max(0.0, production - cfg[CONF_BASE_LOAD])
            share = min(1.0, surplus / cfg[CONF_BOILER_POWER]) if cfg[CONF_BOILER_POWER] else 0.0
            if not cfg.get(CONF_USE_SOLAR, True):
                share = 0.0
            temp = _temp_at(temps, slot_start, fallback_temp)
            cop = self._get_cop(temp) if cfg.get(CONF_USE_WEATHER, True) else NEUTRAL_COP
            rows.append(
                {
                    "tijd": slot_start.isoformat(),
                    "prijs": round(price, 4),
                    "zon_w": round(production),
                    "zonaandeel": round(share, 2),
                    "temperatuur": round(temp, 1),
                    "cop": round(cop, 2),
                    "kosten": round(self._price_term(price, cfg) * (1 - share) / cop, 4),
                }
            )
        return rows

    def _ranked_slots(
        self,
        prices: list[tuple[datetime, float]],
        solar: list[tuple[datetime, float]],
        temps: list[tuple[datetime, float]],
        outdoor_temp: float | None,
        now: datetime,
        duration: float,
        earliest: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if duration <= 0 or not prices:
            return []

        step = timedelta(minutes=SLOT_MINUTES)
        deadline = self._next_deadline(now)
        floor = earliest if earliest is not None else now - step

        slots = max(1, math.ceil(duration / SLOT_MINUTES))
        ranked: list[dict[str, Any]] = []
        for index, (start, _) in enumerate(prices):
            if start < floor:
                continue
            if start + slots * step > deadline:
                break
            block = prices[index : index + slots]
            if len(block) < slots or any(
                block[k + 1][0] - block[k][0] != step for k in range(slots - 1)
            ):
                continue

            rows = self._block_details(start, slots, prices, solar, temps, outdoor_temp)
            cost = sum(row["kosten"] for row in rows) / slots
            ranked.append(
                {
                    "start": start,
                    "cost": cost,
                    "price": sum(price for _, price in block) / slots,
                }
            )
        return sorted(ranked, key=lambda slot: slot["cost"])
