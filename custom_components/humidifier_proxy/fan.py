"""Fan platform for Humidifier Proxy.

HomeKit's Dehumidifier accessory carries no fan speed or swing - Home
Assistant's bridge does not wire those characteristics - and a bare `select`
entity crosses over as one switch per option. Wrapping the speed select in a
real `fan` entity gives a single native Fan accessory with a rotation-speed
slider, and lets the bridge fold the device's temperature and humidity sensors
into that same accessory instead of creating one each.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .const import CONF_FAN_SPEED_ENTITY, CONF_OSCILLATE_ENTITY
from .entity import ProxyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Humidifier Proxy fan entity."""
    async_add_entities([FanProxyEntity(hass, entry)])


class FanProxyEntity(ProxyEntity, FanEntity):
    """Expose a power toggle plus a speed select as one fan entity."""

    # Named through the translation key, not _attr_name, which would win over it.
    _attr_translation_key = "fan"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the fan proxy."""
        super().__init__(hass, entry, "fan")

        options = entry.options
        self._speed_entity: str = options[CONF_FAN_SPEED_ENTITY]
        self._oscillate_entity: str | None = options.get(CONF_OSCILLATE_ENTITY)

        features = (
            FanEntityFeature.SET_SPEED
            | FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
        )
        if self._oscillate_entity:
            features |= FanEntityFeature.OSCILLATE
        self._attr_supported_features = features

        # Remembered so the speed list survives a momentary source outage.
        self._last_speeds: list[str] = []

    @property
    def _tracked_entities(self) -> list[str]:
        """Return every source entity this proxy reads from."""
        tracked = [self._power_entity, self._speed_entity]
        if self._oscillate_entity:
            tracked.append(self._oscillate_entity)
        return tracked

    # -- State --------------------------------------------------------------

    @property
    def _speeds(self) -> list[str]:
        """Return the ordered speed options, slowest first."""
        if (options := self._options_of(self._speed_entity)) is not None:
            self._last_speeds = options
        return self._last_speeds

    @property
    def speed_count(self) -> int:
        """Return the number of supported speeds."""
        return max(len(self._speeds), 1)

    @property
    def is_on(self) -> bool | None:
        """Return whether the device is powered on."""
        return self._is_powered

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a percentage."""
        if not self._is_powered:
            return 0
        speeds = self._speeds
        state = self._source_state(self._speed_entity)
        if state is None or state.state not in speeds:
            return None
        return ordered_list_item_to_percentage(speeds, state.state)

    @property
    def oscillating(self) -> bool | None:
        """Return whether the fan is oscillating."""
        if not self._oscillate_entity:
            return None
        return self._toggle_state(self._oscillate_entity)

    # -- Commands -----------------------------------------------------------

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed, powering the device off at zero."""
        if percentage == 0:
            await self._async_set_power(False)
            return

        await self._async_select_speed(percentage)
        if not self._is_powered:
            await self._async_set_power(True)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally at a given speed."""
        if percentage:
            await self._async_select_speed(percentage)
        await self._async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self._async_set_power(False)

    async def async_oscillate(self, oscillating: bool) -> None:
        """Start or stop oscillation."""
        if self._oscillate_entity:
            await self._async_toggle(self._oscillate_entity, oscillating)

    async def _async_select_speed(self, percentage: int) -> None:
        """Translate a percentage into a source option and write it."""
        if speeds := self._speeds:
            await self._async_select_option(
                self._speed_entity, percentage_to_ordered_list_item(speeds, percentage)
            )
