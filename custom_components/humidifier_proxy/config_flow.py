"""Config and options flow for Humidifier Proxy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CURRENT_HUMIDITY_ENTITY,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_ID,
    CONF_EXTRA_ENTITIES,
    CONF_FAN_SPEED_ENTITY,
    CONF_MODE_ENTITY,
    CONF_OSCILLATE_ENTITY,
    CONF_POWER_ENTITY,
    CONF_TARGET_HUMIDITY_ENTITY,
    CURRENT_HUMIDITY_DOMAINS,
    DEVICE_CLASS_DEHUMIDIFIER,
    DEVICE_CLASS_HUMIDIFIER,
    DOMAIN,
    NUMBER_DOMAINS,
    OPTION_DOMAINS,
    POWER_DOMAINS,
    TOGGLE_DOMAINS,
)
from .discovery import DeviceMapping, async_suggest_mapping


def _device_entities(
    hass: HomeAssistant, device_id: str, domains: list[str] | None = None
) -> list[str]:
    """Return the usable entity ids of a device, optionally filtered by domain."""
    registry = er.async_get(hass)
    return [
        entry.entity_id
        for entry in er.async_entries_for_device(registry, device_id)
        if entry.disabled_by is None
        and (domains is None or entry.domain in domains)
    ]


def _suggest(defaults: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Pre-fill a field without turning it into a hard default.

    `suggested_value` keeps optional fields clearable: a field left empty is
    absent from the result, which removes the mapping.
    """
    value = defaults.get(key)
    return {"suggested_value": value} if value not in (None, "", []) else {}


def _build_schema(
    hass: HomeAssistant, device_id: str, defaults: Mapping[str, Any]
) -> vol.Schema:
    """Build the mapping schema, restricted to the chosen device's entities."""

    def entity(key: str, domains: list[str], *, multiple: bool = False) -> Any:
        return EntitySelector(
            EntitySelectorConfig(
                include_entities=_device_entities(hass, device_id, domains),
                multiple=multiple,
            )
        )

    return vol.Schema(
        {
            vol.Required(
                CONF_DEVICE_CLASS,
                default=defaults.get(CONF_DEVICE_CLASS, DEVICE_CLASS_DEHUMIDIFIER),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[DEVICE_CLASS_DEHUMIDIFIER, DEVICE_CLASS_HUMIDIFIER],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="device_class",
                )
            ),
            vol.Required(
                CONF_POWER_ENTITY, description=_suggest(defaults, CONF_POWER_ENTITY)
            ): entity(CONF_POWER_ENTITY, POWER_DOMAINS),
            vol.Required(
                CONF_TARGET_HUMIDITY_ENTITY,
                description=_suggest(defaults, CONF_TARGET_HUMIDITY_ENTITY),
            ): entity(CONF_TARGET_HUMIDITY_ENTITY, NUMBER_DOMAINS),
            vol.Required(
                CONF_CURRENT_HUMIDITY_ENTITY,
                description=_suggest(defaults, CONF_CURRENT_HUMIDITY_ENTITY),
            ): entity(CONF_CURRENT_HUMIDITY_ENTITY, CURRENT_HUMIDITY_DOMAINS),
            vol.Optional(
                CONF_MODE_ENTITY, description=_suggest(defaults, CONF_MODE_ENTITY)
            ): entity(CONF_MODE_ENTITY, OPTION_DOMAINS),
            vol.Optional(
                CONF_FAN_SPEED_ENTITY,
                description=_suggest(defaults, CONF_FAN_SPEED_ENTITY),
            ): entity(CONF_FAN_SPEED_ENTITY, OPTION_DOMAINS),
            vol.Optional(
                CONF_OSCILLATE_ENTITY,
                description=_suggest(defaults, CONF_OSCILLATE_ENTITY),
            ): entity(CONF_OSCILLATE_ENTITY, TOGGLE_DOMAINS),
            vol.Optional(
                CONF_EXTRA_ENTITIES,
                description=_suggest(defaults, CONF_EXTRA_ENTITIES),
            ): entity(CONF_EXTRA_ENTITIES, None, multiple=True),
        }
    )


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    """Return per-field errors for an otherwise well-formed submission."""
    if user_input.get(CONF_OSCILLATE_ENTITY) and not user_input.get(
        CONF_FAN_SPEED_ENTITY
    ):
        return {CONF_OSCILLATE_ENTITY: "oscillate_requires_fan"}
    return {}


def _as_defaults(mapping: DeviceMapping) -> dict[str, Any]:
    """Turn a suggested mapping into schema defaults."""
    return {
        CONF_POWER_ENTITY: mapping.power,
        CONF_TARGET_HUMIDITY_ENTITY: mapping.target_humidity,
        CONF_CURRENT_HUMIDITY_ENTITY: mapping.current_humidity,
        CONF_MODE_ENTITY: mapping.mode,
        CONF_FAN_SPEED_ENTITY: mapping.fan_speed,
        CONF_OSCILLATE_ENTITY: mapping.oscillate,
        CONF_EXTRA_ENTITIES: mapping.extras,
    }


class HumidifierProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Humidifier Proxy config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._device_id: str | None = None
        self._defaults: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HumidifierProxyOptionsFlow:
        """Return the options flow handler."""
        return HumidifierProxyOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pick the device to wrap."""
        if user_input is not None:
            self._device_id = user_input[CONF_DEVICE_ID]
            await self.async_set_unique_id(self._device_id)
            self._abort_if_unique_id_configured()

            self._defaults = _as_defaults(
                async_suggest_mapping(self.hass, self._device_id)
            )
            return await self.async_step_mapping()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEVICE_ID): DeviceSelector(DeviceSelectorConfig())}
            ),
        )

    async def async_step_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm or correct the suggested mapping."""
        assert self._device_id is not None
        errors: dict[str, str] = {}

        if user_input is not None and not (errors := _validate(user_input)):
            return self.async_create_entry(
                title=_device_name(self.hass, self._device_id),
                data={},
                options={**user_input, CONF_DEVICE_ID: self._device_id},
            )

        return self.async_show_form(
            step_id="mapping",
            data_schema=_build_schema(
                self.hass, self._device_id, user_input or self._defaults
            ),
            errors=errors,
            description_placeholders={
                "device": _device_name(self.hass, self._device_id)
            },
        )


class HumidifierProxyOptionsFlow(config_entries.OptionsFlow):
    """Handle Humidifier Proxy reconfiguration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user correct the mapping of an existing proxy."""
        options = self.config_entry.options
        device_id: str = options[CONF_DEVICE_ID]
        errors: dict[str, str] = {}

        if user_input is not None and not (errors := _validate(user_input)):
            return self.async_create_entry(
                data={**user_input, CONF_DEVICE_ID: device_id}
            )

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(self.hass, device_id, user_input or options),
            errors=errors,
            description_placeholders={"device": _device_name(self.hass, device_id)},
        )


@callback
def _device_name(hass: HomeAssistant, device_id: str) -> str:
    """Return the display name of a device."""
    if device := dr.async_get(hass).async_get(device_id):
        return device.name_by_user or device.name or device_id
    return device_id
