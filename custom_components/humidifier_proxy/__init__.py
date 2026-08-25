"""Humidifier Proxy integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_EXTRA_ENTITIES, CONF_FAN_SPEED_ENTITY, SOURCE_KEYS

_LOGGER = logging.getLogger(__name__)


def _platforms_for(options: Mapping[str, Any]) -> list[Platform]:
    """Return the platforms the current configuration needs."""
    platforms = [Platform.HUMIDIFIER]
    if options.get(CONF_FAN_SPEED_ENTITY):
        platforms.append(Platform.FAN)
    return platforms


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Humidifier Proxy from a config entry."""
    platforms = _platforms_for(entry.options)
    entry.runtime_data = platforms

    _async_cleanup_stale_entities(hass, entry, platforms)

    entry.async_on_unload(_async_track_source_renames(hass, entry))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Humidifier Proxy config entry."""
    return await hass.config_entries.async_unload_platforms(entry, entry.runtime_data)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so a new mapping takes effect immediately."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_track_source_renames(
    hass: HomeAssistant, entry: ConfigEntry
) -> CALLBACK_TYPE:
    """Keep the stored entity ids in sync with the entity registry.

    The mapping is stored as plain entity ids, so a renamed source would
    otherwise leave the proxy pointing at something that no longer exists.
    """

    @callback
    def _handle(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        data = event.data

        if data["action"] == "remove":
            _async_report_removal(entry, data["entity_id"])
            return

        # `old_entity_id` is only present when the entity id itself changed.
        if data["action"] != "update" or (old := data.get("old_entity_id")) is None:
            return

        new = data["entity_id"]
        options = dict(entry.options)
        changed = False

        for key in SOURCE_KEYS:
            if options.get(key) == old:
                options[key] = new
                changed = True

        extras: list[str] = list(options.get(CONF_EXTRA_ENTITIES) or [])
        if old in extras:
            options[CONF_EXTRA_ENTITIES] = [
                new if entity_id == old else entity_id for entity_id in extras
            ]
            changed = True

        if changed:
            _LOGGER.info("%s: following rename %s -> %s", entry.title, old, new)
            # Triggers the update listener, which reloads the entry.
            hass.config_entries.async_update_entry(entry, options=options)

    return hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _handle)


@callback
def _async_report_removal(entry: ConfigEntry, removed: str) -> None:
    """Warn when a mapped source entity disappears."""
    orphaned = [key for key in SOURCE_KEYS if entry.options.get(key) == removed]
    if removed in (entry.options.get(CONF_EXTRA_ENTITIES) or []):
        orphaned.append(CONF_EXTRA_ENTITIES)

    if orphaned:
        _LOGGER.warning(
            "%s: source entity %s was removed but is still mapped to %s; "
            "reconfigure the helper to pick a replacement",
            entry.title,
            removed,
            ", ".join(orphaned),
        )


@callback
def _async_cleanup_stale_entities(
    hass: HomeAssistant, entry: ConfigEntry, platforms: list[Platform]
) -> None:
    """Remove registry entries for platforms this configuration no longer uses."""
    registry = er.async_get(hass)
    active = {platform.value for platform in platforms}

    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.domain not in active:
            _LOGGER.debug("Removing %s", registry_entry.entity_id)
            registry.async_remove(registry_entry.entity_id)
