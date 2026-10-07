# Humidifier Proxy

Turns a device that exposes its controls as loose `switch` / `number` / `select`
/ `sensor` entities into a native Home Assistant **humidifier** — and, in Apple
Home, into a proper **Dehumidifier** accessory.

It is **source-agnostic**: LocalTuya, MQTT, ESPHome, Shelly, Modbus, REST or any
other integration works, because the proxy only relies on generic Home Assistant
semantics — on/off, a numeric state with `min`/`max`/`step`, an `options` list.

It does **not** implement humidity-control logic. The physical device keeps full
responsibility for regulating humidity.

## The problem it solves

A dehumidifier exposed as a dozen separate entities works in Home Assistant but
is second-class everywhere else: no humidifier card, no humidity intents, and in
Apple Home each entity becomes its own tile — a `select` alone turns into one
switch per option. Humidifier Proxy re-assembles them into the entity types Home
Assistant and HomeKit actually understand, while keeping the accessory count
down.

## What it creates

| Entity | When | HomeKit |
| --- | --- | --- |
| `humidifier.*` | always | **Dehumidifier** accessory: on/off, current and target humidity |
| `fan.*` | a fan-speed source is mapped | **Fan** accessory: speed slider, plus swing when an oscillation source is mapped |

Every remaining entity of the device — LED, child lock, timer, defrost, fault,
temperature — is mirrored as an **attribute of the humidifier entity**, keyed by
its short registry name (`defrost`, `fault`, `temperature`, …). The whole device
stays readable from one place, and the HomeKit bridge gains no extra
accessories.

## Setup

**Settings → Devices & services → Add integration → Humidifier Proxy**

1. **Pick the device.** One selector, nothing else.
2. **Confirm the mapping.** The device's entities are inspected and a mapping is
   proposed by domain, device class and name. Correct anything that looks wrong.

Every selector is restricted to entities of the chosen device, so there is
nothing to type and nothing to mistype. The same form is available later under
**Configure**; clearing an optional field removes that feature.

| Field | Required | Accepted source domains |
| --- | --- | --- |
| Device class | yes | Dehumidifier / Humidifier |
| Power | yes | `switch`, `input_boolean`, `fan`, `light`, `humidifier` |
| Target humidity | yes | `number`, `input_number` |
| Current humidity | yes | `sensor`, `number`, `input_number` |
| Mode | no | `select`, `input_select` |
| Fan speed | no | `select`, `input_select` |
| Oscillation | no | `switch`, `input_boolean` |
| Also report as attributes | no | anything else on the device |

## Behaviour worth knowing

- **Setpoint snapping.** Minimum, maximum and step are inherited from the source
  number entity, and every requested target is snapped onto that grid before it
  is written — including a step-down when rounding would overshoot the maximum.
  Home Assistant only validates min/max, never the step, and HomeKit's slider
  always moves in whole percent. The last valid minimum, maximum and step are
  retained if the source becomes unavailable or loses these attributes.
  Non-finite numeric readings are ignored. Setpoint commands are rejected while
  the target source is unavailable, has an invalid numeric state, or has never
  supplied a valid minimum, maximum and step; power
  commands remain accessible when their own source is available.
- **Action.** `drying` / `humidifying` / `idle` / `off`, derived from power plus
  measured vs target humidity. This is an estimate, not a compressor status: it
  does not detect a full tank, defrost or modes that ignore the humidity target.
- **Availability follows the power entity only.** A sensor that stops reporting
  while the device is idle must never make the proxy uncontrollable.
- **Renames are followed.** If a source entity is renamed, the stored mapping is
  rewritten automatically; if one is deleted, it is reported in the log instead
  of silently turning the proxy unavailable.
- **Naming follows the Home Assistant convention.** The humidifier is the
  device's primary entity and takes its name, so it becomes
  `humidifier.<device>`. The fan is named through a translation key, which means
  its entity id follows the language Home Assistant is running in —
  `fan.<device>_fan` in English, `fan.<device>_ventola` in Italian. Look the real
  ids up under **Settings → Devices & services → Entities** rather than assuming
  them. Renaming the device renames everything at once.

## Worked example: Olimpia Splendid Aquaria S1 via LocalTuya

LocalTuya exposes thirteen entities for this dehumidifier. The proposed mapping
uses six and mirrors the other seven:

| Role | Entity |
| --- | --- |
| Power | `switch.*_power` |
| Target humidity | `number.*_target_humidity` (25–90, step 5) |
| Current humidity | `sensor.*_humidity` |
| Mode | `select.*_mode` (Dehumidify / Laundry) |
| Fan speed | `select.*_fan_speed` (Slow / Medium / Fast) |
| Oscillation | `switch.*_swing` |
| *attributes* | `led`, `lock`, `timer`, `temperature`, `fault`, `auto`, `defrost` |

The device accepts 25–90 % in steps of 5, so a request for 53 % is snapped to
55 % before it is written — including from HomeKit.

## HomeKit

For one native **Dehumidifier** accessory, expose only `humidifier.*`:

```yaml
- name: HASS Clima
  port: 21063
  mode: bridge
  filter:
    include_entities:
      - humidifier.aquaria_s1_wi_fi_bluetooth
```

Replace the entity id with the real proxy id from Home Assistant. This provides
power, current humidity and target humidity, and lets HomePod recognize the
accessory as a dehumidifier. For a humidifier source, the accessory is instead
a humidifier. The entity already publishes `current_humidity`, so no
`linked_humidity_sensor` is needed.

The bridge does not expose operating modes, fan speed or swing on this
accessory. You can still control them from Home Assistant. A low humidity target
keeps the device working while measured humidity remains above it; it does not
select Laundry mode or change the fan speed.

### Optional fan accessory

If you also want speed and swing in Apple Home, add the proxy's `fan.*` entity
to the bridge filter. This creates a second accessory. Fan entity ids depend
on Home Assistant's language, as described under
[Behaviour worth knowing](#behaviour-worth-knowing).

You can configure that fan as an air purifier and explicitly link source
sensors to it:

```yaml
entity_config:
  fan.aquaria_s1_wi_fi_bluetooth_ventola:
    type: air_purifier
    linked_temperature_sensor: sensor.your_temperature_sensor
    linked_humidity_sensor: sensor.your_humidity_sensor
```

Replace all example ids with your actual entities. Use this only if you want
the additional fan accessory; it is not required for the dehumidifier.

If the device is already in Apple Home natively, do not expose it twice.

## Requirements

- Home Assistant **2025.3.0** or newer.
- **2026.2** or newer for the target-humidity step to reach the UI; on older
  cores the value is still honoured when writing.
- **2026.3** or newer for the bundled brand icon.

## Installation

Via **HACS → Custom repositories → Integration**, then search for *Humidifier
Proxy*, download and restart. Or copy `custom_components/humidifier_proxy` to
`/config/custom_components/` and restart.

## What it does not do

It is not a hygrostat. It does not decide when to run the device, implement
hysteresis, or replace the control logic of the hardware.

## Development checks

From the repository directory:

```sh
python3 -m unittest discover -s tests -v
```

Tests cover setpoint snapping, source limits, invalid numeric values, outages,
recovery and power availability.

## License

MIT. See [LICENSE](LICENSE).
