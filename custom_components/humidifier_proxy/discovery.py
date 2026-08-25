"""Automatic mapping of a device's entities onto the proxy's roles.

The config flow asks for a device and calls into here to propose a mapping. The
proposal is always shown for confirmation, so a wrong guess costs a correction
rather than a broken helper.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.components.number import ATTR_MAX, ATTR_MIN
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CURRENT_HUMIDITY_DOMAINS,
    NUMBER_DOMAINS,
    OPTION_DOMAINS,
    POWER_DOMAINS,
    TOGGLE_DOMAINS,
)

# Matched against the entity id and the registry name, lowercased. Ordered by
# confidence: an earlier hint wins over a later one.
_POWER_HINTS = ("power", "on_off", "alimentazione", "accensione")
_HUMIDITY_HINTS = ("humidity", "humid", "umidita", "umidità")
_TARGET_HINTS = ("target", "set_value", "setpoint", "set_point", "impostat")
_MODE_HINTS = ("mode", "modalita", "modalità", "modo")
_FAN_HINTS = ("fan", "speed", "ventola", "velocita", "velocità", "wind")
_SWING_HINTS = ("swing", "oscill", "louver")


@dataclass(slots=True)
class DeviceMapping:
    """A proposed mapping of one device's entities onto the proxy's roles."""

    power: str | None = None
    target_humidity: str | None = None
    current_humidity: str | None = None
    mode: str | None = None
    fan_speed: str | None = None
    oscillate: str | None = None
    extras: list[str] = field(default_factory=list)

    @property
    def assigned(self) -> set[str]:
        """Return the entity ids that already have a role."""
        return {
            entity_id
            for entity_id in (
                self.power,
                self.target_humidity,
                self.current_humidity,
                self.mode,
                self.fan_speed,
                self.oscillate,
            )
            if entity_id
        }


@dataclass(slots=True, frozen=True)
class _Candidate:
    """A device entity considered for a role."""

    entity_id: str
    domain: str
    device_class: str | None
    haystack: str

    def rank(self, hints: tuple[str, ...]) -> int | None:
        """Return how strongly this entity matches, lower being better."""
        for index, hint in enumerate(hints):
            if hint in self.haystack:
                return index
        return None


def _candidates(hass: HomeAssistant, device_id: str) -> list[_Candidate]:
    """Collect the usable entities of a device."""
    registry = er.async_get(hass)
    result: list[_Candidate] = []

    for entry in er.async_entries_for_device(registry, device_id):
        if entry.disabled_by is not None or entry.hidden_by is not None:
            continue
        name = entry.name or entry.original_name or ""
        result.append(
            _Candidate(
                entity_id=entry.entity_id,
                domain=entry.domain,
                device_class=entry.device_class or entry.original_device_class,
                haystack=f"{entry.entity_id} {name}".lower(),
            )
        )

    return result


def _pick(
    candidates: list[_Candidate],
    domains: list[str],
    hints: tuple[str, ...],
    *,
    taken: set[str],
    device_class: str | None = None,
    lone_fallback: bool = False,
) -> str | None:
    """Pick the best candidate for a role.

    A matching device class wins outright; otherwise the best name hint wins.
    `lone_fallback` accepts an unmatched entity when it is the only one left in
    the accepted domains.
    """
    pool = [
        candidate
        for candidate in candidates
        if candidate.domain in domains and candidate.entity_id not in taken
    ]
    if not pool:
        return None

    if device_class is not None:
        for candidate in pool:
            if candidate.device_class == device_class:
                return candidate.entity_id

    ranked = [
        (rank, candidate.entity_id)
        for candidate in pool
        if (rank := candidate.rank(hints)) is not None
    ]
    if ranked:
        return min(ranked)[1]

    if lone_fallback and len(pool) == 1:
        return pool[0].entity_id

    return None


def _looks_like_percentage(hass: HomeAssistant, entity_id: str) -> bool:
    """Return whether a number entity spans a plausible humidity range."""
    if (state := hass.states.get(entity_id)) is None:
        return False
    try:
        minimum = float(state.attributes[ATTR_MIN])
        maximum = float(state.attributes[ATTR_MAX])
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= minimum < maximum <= 100


def async_suggest_mapping(hass: HomeAssistant, device_id: str) -> DeviceMapping:
    """Propose a role mapping for the entities of a device."""
    candidates = _candidates(hass, device_id)
    mapping = DeviceMapping()
    taken: set[str] = set()

    def claim(entity_id: str | None) -> str | None:
        if entity_id:
            taken.add(entity_id)
        return entity_id

    mapping.current_humidity = claim(
        _pick(
            candidates,
            CURRENT_HUMIDITY_DOMAINS,
            _HUMIDITY_HINTS,
            taken=taken,
            device_class=SensorDeviceClass.HUMIDITY,
        )
    )

    # The setpoint is a number that both reads as humidity and spans 0-100.
    humidity_numbers = [
        candidate
        for candidate in candidates
        if candidate.domain in NUMBER_DOMAINS
        and candidate.entity_id not in taken
        and (
            candidate.rank(_TARGET_HINTS) is not None
            or candidate.rank(_HUMIDITY_HINTS) is not None
        )
        and _looks_like_percentage(hass, candidate.entity_id)
    ]
    mapping.target_humidity = claim(
        _pick(humidity_numbers, NUMBER_DOMAINS, _HUMIDITY_HINTS, taken=taken)
        or (humidity_numbers[0].entity_id if humidity_numbers else None)
    )

    mapping.power = claim(
        _pick(
            candidates, POWER_DOMAINS, _POWER_HINTS, taken=taken, lone_fallback=True
        )
    )
    mapping.oscillate = claim(
        _pick(candidates, TOGGLE_DOMAINS, _SWING_HINTS, taken=taken)
    )
    mapping.fan_speed = claim(
        _pick(candidates, OPTION_DOMAINS, _FAN_HINTS, taken=taken)
    )
    mapping.mode = claim(
        _pick(candidates, OPTION_DOMAINS, _MODE_HINTS, taken=taken, lone_fallback=True)
    )

    mapping.extras = [
        candidate.entity_id
        for candidate in candidates
        if candidate.entity_id not in taken
    ]

    return mapping
