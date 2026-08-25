"""Constants for Humidifier Proxy."""

from typing import Final

DOMAIN: Final = "humidifier_proxy"

CONF_DEVICE_ID: Final = "device_id"
CONF_DEVICE_CLASS: Final = "device_class"
CONF_POWER_ENTITY: Final = "power_entity"
CONF_TARGET_HUMIDITY_ENTITY: Final = "target_humidity_entity"
CONF_CURRENT_HUMIDITY_ENTITY: Final = "current_humidity_entity"
CONF_MODE_ENTITY: Final = "mode_entity"
CONF_FAN_SPEED_ENTITY: Final = "fan_speed_entity"
CONF_OSCILLATE_ENTITY: Final = "oscillate_entity"
CONF_EXTRA_ENTITIES: Final = "extra_entities"

# Options holding a single entity id, followed when a source is renamed.
SOURCE_KEYS: Final = (
    CONF_POWER_ENTITY,
    CONF_TARGET_HUMIDITY_ENTITY,
    CONF_CURRENT_HUMIDITY_ENTITY,
    CONF_MODE_ENTITY,
    CONF_FAN_SPEED_ENTITY,
    CONF_OSCILLATE_ENTITY,
)

DEVICE_CLASS_DEHUMIDIFIER: Final = "dehumidifier"
DEVICE_CLASS_HUMIDIFIER: Final = "humidifier"

# Accepted source domains per role. Deliberately wide: the proxy only relies on
# generic Home Assistant semantics, never on the integration behind the entity.
POWER_DOMAINS: Final = ["switch", "input_boolean", "fan", "humidifier", "light"]
NUMBER_DOMAINS: Final = ["number", "input_number"]
CURRENT_HUMIDITY_DOMAINS: Final = ["sensor", "number", "input_number"]
OPTION_DOMAINS: Final = ["select", "input_select"]
TOGGLE_DOMAINS: Final = ["switch", "input_boolean"]

# States that count as "off" for a generic source entity.
OFF_LIKE_STATES: Final = frozenset(
    {"off", "false", "0", "no", "closed", "idle", "standby", "none"}
)
