"""Shared base entity for Humidifier Proxy."""

from __future__ import annotations

import logging

from homeassistant.components.select import ATTR_OPTIONS
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_DEVICE_ID, CONF_POWER_ENTITY, OFF_LIKE_STATES

_LOGGER = logging.getLogger(__name__)

INVALID_STATES = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE})

# Generic turn_on/turn_off dispatch. Using `homeassistant` rather than the
# source entity's own domain is what lets any toggleable domain be a power
# source.
HOMEASSISTANT_DOMAIN = "homeassistant"


class ProxyEntity(Entity):
    """Base class for entities that mirror a set of existing HA entities."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str) -> None:
        """Initialize the proxy and attach it to the source device."""
        self._entry = entry
        self._options = entry.options
        self._power_entity: str = entry.options[CONF_POWER_ENTITY]
        self._attr_unique_id = f"{entry.entry_id}_{key}"

        # Assigning `device_entry` links the entity to the source device without
        # adding this config entry to it, which is the pattern core helpers use.
        device_id: str = entry.options[CONF_DEVICE_ID]
        if device := dr.async_get(hass).async_get(device_id):
            self.device_entry = device

    # -- Source tracking ----------------------------------------------------

    @property
    def _tracked_entities(self) -> list[str]:
        """Return every source entity this proxy reads from."""
        return [self._power_entity]

    async def async_added_to_hass(self) -> None:
        """Subscribe to state changes of the source entities."""
        await super().async_added_to_hass()

        tracked = self._tracked_entities
        for entity_id in tracked:
            if self.hass.states.get(entity_id) is None:
                _LOGGER.warning(
                    "%s: source entity %s does not exist; the proxy stays "
                    "unavailable until it appears",
                    self.entity_id,
                    entity_id,
                )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, tracked, self._async_source_state_changed
            )
        )

    @callback
    def _async_source_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Refresh when any source entity changes."""
        self.async_write_ha_state()

    # -- Availability -------------------------------------------------------

    @property
    def available(self) -> bool:
        """Return whether the power source is usable.

        Only the power entity gates availability: a sensor that stops reporting
        while the device is idle must never make the proxy uncontrollable.
        """
        return self._source_state(self._power_entity) is not None

    # -- Source readers -----------------------------------------------------

    def _source_state(self, entity_id: str | None) -> State | None:
        """Return a usable state object, or None when unknown/unavailable."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in INVALID_STATES:
            return None
        return state

    def _numeric_state(self, entity_id: str | None) -> float | None:
        """Read a numeric source state."""
        if (state := self._source_state(entity_id)) is None:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def _numeric_attribute(
        self, entity_id: str | None, attribute: str, fallback: float
    ) -> float:
        """Read a numeric attribute (min/max/step) from a source entity."""
        if not entity_id or (state := self.hass.states.get(entity_id)) is None:
            return fallback
        try:
            return float(state.attributes.get(attribute, fallback))
        except (TypeError, ValueError):
            return fallback

    def _toggle_state(self, entity_id: str | None) -> bool | None:
        """Interpret a source entity as a boolean."""
        if (state := self._source_state(entity_id)) is None:
            return None
        value = state.state.strip().lower()
        return value == STATE_ON or value not in OFF_LIKE_STATES

    def _options_of(self, entity_id: str | None) -> list[str] | None:
        """Read the options list of a select-like source entity."""
        if not entity_id or (state := self.hass.states.get(entity_id)) is None:
            return None
        options = state.attributes.get(ATTR_OPTIONS)
        if isinstance(options, list) and options:
            return [str(option) for option in options]
        return None

    @property
    def _is_powered(self) -> bool | None:
        """Return whether the power source is on."""
        if (state := self._source_state(self._power_entity)) is None:
            return None
        return state.state == STATE_ON

    # -- Source writers -----------------------------------------------------

    async def _async_call(self, domain: str, service: str, data: dict) -> None:
        """Call a service on a source entity."""
        await self.hass.services.async_call(domain, service, data, blocking=True)

    async def _async_toggle(self, entity_id: str, turn_on: bool) -> None:
        """Turn any toggleable source on or off, whatever its domain is."""
        await self._async_call(
            HOMEASSISTANT_DOMAIN,
            SERVICE_TURN_ON if turn_on else SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
        )

    async def _async_set_power(self, turn_on: bool) -> None:
        """Turn the power source on or off."""
        await self._async_toggle(self._power_entity, turn_on)

    async def _async_select_option(self, entity_id: str, option: str) -> None:
        """Write an option to a select-like source entity."""
        await self._async_call(
            entity_id.split(".", 1)[0],
            "select_option",
            {ATTR_ENTITY_ID: entity_id, "option": option},
        )

    async def _async_set_number(self, entity_id: str, value: float) -> None:
        """Write a value to a number-like source entity."""
        await self._async_call(
            entity_id.split(".", 1)[0],
            "set_value",
            {ATTR_ENTITY_ID: entity_id, "value": value},
        )
