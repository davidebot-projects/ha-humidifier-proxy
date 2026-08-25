"""Humidifier platform for Humidifier Proxy."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from homeassistant.components.humidifier import (
    DEFAULT_MAX_HUMIDITY,
    DEFAULT_MIN_HUMIDITY,
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.components.number import ATTR_MAX, ATTR_MIN, ATTR_STEP
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .const import (
    CONF_CURRENT_HUMIDITY_ENTITY,
    CONF_DEVICE_CLASS,
    CONF_EXTRA_ENTITIES,
    CONF_MODE_ENTITY,
    CONF_TARGET_HUMIDITY_ENTITY,
    DEVICE_CLASS_DEHUMIDIFIER,
)
from .entity import ProxyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Humidifier Proxy entity."""
    async_add_entities([HumidifierProxyEntity(hass, entry)])


class HumidifierProxyEntity(ProxyEntity, HumidifierEntity):
    """Expose a device's controls as one humidifier entity.

    This is the entity HomeKit turns into a Dehumidifier accessory. Everything
    the accessory cannot carry - LED, child lock, timer, defrost, fault - is
    surfaced as a state attribute instead of a second entity, so the whole
    device stays readable from one place without inflating the HomeKit bridge.
    """

    # This is the device's primary entity, so it takes the device's own name.
    _attr_name = None

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the proxy."""
        super().__init__(hass, entry, "humidifier")

        options = entry.options
        self._target_entity: str = options[CONF_TARGET_HUMIDITY_ENTITY]
        self._humidity_entity: str = options[CONF_CURRENT_HUMIDITY_ENTITY]
        self._mode_entity: str | None = options.get(CONF_MODE_ENTITY)
        self._extra_entities: list[str] = list(options.get(CONF_EXTRA_ENTITIES) or [])
        self._extra_keys = _attribute_keys(hass, self._extra_entities)

        self._is_dehumidifier = options[CONF_DEVICE_CLASS] == DEVICE_CLASS_DEHUMIDIFIER
        self._attr_device_class = (
            HumidifierDeviceClass.DEHUMIDIFIER
            if self._is_dehumidifier
            else HumidifierDeviceClass.HUMIDIFIER
        )
        self._attr_supported_features = (
            HumidifierEntityFeature.MODES
            if self._mode_entity
            else HumidifierEntityFeature(0)
        )

        # Remembered so the mode list does not vanish, and with it the entity's
        # capability, while the source select is briefly unavailable.
        self._last_modes: list[str] | None = None

    @property
    def _tracked_entities(self) -> list[str]:
        """Return every source entity this proxy reads from."""
        tracked = [self._power_entity, self._target_entity, self._humidity_entity]
        if self._mode_entity:
            tracked.append(self._mode_entity)
        return tracked + self._extra_entities

    # -- State --------------------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        """Return whether the source power entity is on."""
        return self._is_powered

    @property
    def current_humidity(self) -> float | None:
        """Return the measured relative humidity."""
        return self._numeric_state(self._humidity_entity)

    @property
    def target_humidity(self) -> float | None:
        """Return the humidity setpoint."""
        return self._numeric_state(self._target_entity)

    @property
    def min_humidity(self) -> float:
        """Return the minimum supported setpoint."""
        return self._numeric_attribute(
            self._target_entity, ATTR_MIN, DEFAULT_MIN_HUMIDITY
        )

    @property
    def max_humidity(self) -> float:
        """Return the maximum supported setpoint."""
        return self._numeric_attribute(
            self._target_entity, ATTR_MAX, DEFAULT_MAX_HUMIDITY
        )

    @property
    def target_humidity_step(self) -> float | None:
        """Return the setpoint step."""
        return self._numeric_attribute(self._target_entity, ATTR_STEP, 1.0) or 1.0

    @property
    def action(self) -> HumidifierAction | None:
        """Return what the device is currently doing."""
        if (powered := self._is_powered) is None:
            return None
        if not powered:
            return HumidifierAction.OFF

        current = self.current_humidity
        target = self.target_humidity
        if current is None or target is None:
            return None

        working = current > target if self._is_dehumidifier else current < target
        if not working:
            return HumidifierAction.IDLE
        return (
            HumidifierAction.DRYING
            if self._is_dehumidifier
            else HumidifierAction.HUMIDIFYING
        )

    @property
    def mode(self) -> str | None:
        """Return the current operating mode."""
        if (state := self._source_state(self._mode_entity)) is None:
            return None
        return state.state

    @property
    def available_modes(self) -> list[str] | None:
        """Return the selectable operating modes."""
        if not self._mode_entity:
            return None
        if (options := self._options_of(self._mode_entity)) is not None:
            self._last_modes = options
        return self._last_modes

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Mirror the device's remaining entities as attributes."""
        return {
            key: state.state
            for entity_id, key in self._extra_keys.items()
            if (state := self.hass.states.get(entity_id)) is not None
        }

    # -- Commands -----------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        await self._async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        await self._async_set_power(False)

    async def async_set_humidity(self, humidity: int) -> None:
        """Write a new setpoint to the source number entity."""
        await self._async_set_number(
            self._target_entity, self._normalize_humidity(float(humidity))
        )

    async def async_set_mode(self, mode: str) -> None:
        """Write a new operating mode to the source select entity."""
        if self._mode_entity:
            await self._async_select_option(self._mode_entity, mode)

    # -- Helpers ------------------------------------------------------------

    def _normalize_humidity(self, humidity: float) -> float:
        """Clamp and snap a setpoint onto the source number's grid.

        Home Assistant only validates min/max, never the step, and HomeKit's
        slider always moves in whole percent - so a device that accepts 25-90 in
        steps of 5 would otherwise be sent values it silently rejects or rounds.
        """
        d_min = Decimal(str(self.min_humidity))
        d_max = Decimal(str(self.max_humidity))
        d_step = Decimal(str(self.target_humidity_step or 1.0))
        if d_step <= 0:
            d_step = Decimal("1")

        d_value = min(max(Decimal(str(humidity)), d_min), d_max)
        steps = ((d_value - d_min) / d_step).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        normalized = d_min + steps * d_step

        # Rounding up can overshoot the maximum (min 25, max 85, step 10 -> 90).
        # Step back onto the grid rather than writing an unsupported value.
        while normalized > d_max:
            normalized -= d_step

        return float(max(normalized, d_min))


def _attribute_keys(hass: HomeAssistant, entity_ids: list[str]) -> dict[str, str]:
    """Map each mirrored entity to a short, stable attribute name.

    The registry name is used rather than the state's `friendly_name`, which on
    a device whose entities set `has_entity_name` already carries the device
    name and would turn every key into `aquaria_s1_wi_fi_bluetooth_defrost`.
    """
    registry = er.async_get(hass)
    keys: dict[str, str] = {}
    used: set[str] = set()

    for entity_id in entity_ids:
        object_id = entity_id.split(".", 1)[1]
        entry = registry.async_get(entity_id)
        name = (entry.name or entry.original_name) if entry else None

        key = slugify(name) if name else ""
        if not key or key in used:
            # Object ids are unique by construction, so this always resolves.
            key = object_id

        used.add(key)
        keys[entity_id] = key

    return keys
