"""Exercise production logic with simulated Home Assistant state/services."""
import ast
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum, IntFlag
import logging
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'custom_components/humidifier_proxy'


class Action(str, Enum):
    OFF = 'off'
    IDLE = 'idle'
    DRYING = 'drying'
    HUMIDIFYING = 'humidifying'


class DeviceClass(str, Enum):
    DEHUMIDIFIER = 'dehumidifier'
    HUMIDIFIER = 'humidifier'


class Feature(IntFlag):
    MODES = 1


class Entity:
    pass


class ServiceValidationError(Exception):
    pass


def load_definitions(filename, names, env):
    """Compile unchanged production definitions against small HA doubles."""
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    for node in ast.parse((ROOT / filename).read_text()).body:
        name = getattr(node, 'name', None)
        if isinstance(node, ast.Assign):
            name = node.targets[0].id
        if name in names:
            nodes.append(node)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, filename, 'exec'), env)


def environment():
    env = dict(Entity=Entity, HumidifierEntity=Entity, Decimal=Decimal,
               ROUND_HALF_UP=ROUND_HALF_UP, isfinite=isfinite,
               callback=lambda f: f, ServiceValidationError=ServiceValidationError,
               HumidifierAction=Action, HumidifierDeviceClass=DeviceClass,
               HumidifierEntityFeature=Feature, DEFAULT_MIN_HUMIDITY=0,
               DEFAULT_MAX_HUMIDITY=100, DEVICE_CLASS_DEHUMIDIFIER='dehumidifier',
               STATE_UNKNOWN='unknown', STATE_UNAVAILABLE='unavailable', STATE_ON='on',
               HOMEASSISTANT_DOMAIN='homeassistant',
               OFF_LIKE_STATES={'off', 'false', '0'},
               _LOGGER=logging.getLogger(__name__),
               dr=SimpleNamespace(async_get=lambda hass: SimpleNamespace(async_get=lambda _: None)),
               _attribute_keys=lambda hass, entities: {})
    for filename in ('entity.py', 'humidifier.py'):
        for node in ast.parse((ROOT / filename).read_text()).body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name.startswith(('ATTR_', 'CONF_', 'SERVICE_')):
                        env[alias.name] = alias.name.split('_', 1)[1].lower()
    load_definitions('entity.py', {'INVALID_STATES', 'ProxyEntity'}, env)
    load_definitions('humidifier.py', {'HumidifierProxyEntity'}, env)
    return env


class HumidifierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = environment()
        self.target = SimpleNamespace(state='50', attributes={'min': 25, 'max': 90, 'step': 5})
        self.states = {
            'number.target': self.target,
            'switch.power': SimpleNamespace(state='on', attributes={}),
            'sensor.humidity': SimpleNamespace(state='60', attributes={}),
        }
        self.calls = []

        async def service(domain, name, data, blocking):
            self.calls.append((domain, name, data))

        self.hass = SimpleNamespace(states=self.states, services=SimpleNamespace(async_call=service))
        self.entry = SimpleNamespace(entry_id='test', options={
            'device_id': 'device', 'power_entity': 'switch.power',
            'target_humidity_entity': 'number.target',
            'current_humidity_entity': 'sensor.humidity', 'device_class': 'dehumidifier',
        })
        self.proxy = self.env['HumidifierProxyEntity'](self.hass, self.entry)
        self.proxy.hass = self.hass

    async def test_snapping_and_clamping(self):
        for value, expected in ((53, 55), (52.5, 55), (25, 25), (90, 90),
                                (0, 25), (100, 90)):
            with self.subTest(value=value):
                await self.proxy.async_set_humidity(value)
                self.assertEqual(self.calls[-1][2]['value'], expected)

    async def test_rounding_cannot_exceed_non_grid_maximum(self):
        self.target.attributes = {'min': 25, 'max': 88, 'step': 5}
        await self.proxy.async_set_humidity(88)
        self.assertEqual(self.calls[-1][2]['value'], 85)

    async def test_fractional_grid(self):
        self.target.attributes = {'min': 25, 'max': 90, 'step': 0.5}
        self.assertEqual(self.proxy._normalize_humidity(53.25), 53.5)

    async def test_limits_survive_source_outage_and_removal(self):
        for source in (SimpleNamespace(state='unavailable', attributes={}), None):
            with self.subTest(source=source):
                if source is None:
                    self.states.pop('number.target', None)
                else:
                    self.states['number.target'] = source
                self.assertEqual((self.proxy.min_humidity, self.proxy.max_humidity,
                                  self.proxy.target_humidity_step), (25, 90, 5))
                self.assertTrue(self.proxy.available)
                with self.assertRaises(ServiceValidationError):
                    await self.proxy.async_set_humidity(55)
        self.assertEqual(self.calls, [])

    async def test_missing_attributes_keep_last_valid_grid(self):
        self.target.attributes = {}
        await self.proxy.async_set_humidity(53)
        self.assertEqual(self.calls[-1][2]['value'], 55)

    async def test_no_commands_before_first_valid_source_grid(self):
        self.target.attributes = {}
        proxy = self.env['HumidifierProxyEntity'](self.hass, self.entry)
        proxy.hass = self.hass
        with self.assertRaises(ServiceValidationError):
            await proxy.async_set_humidity(55)
        self.assertEqual(self.calls, [])
        self.target.attributes = {'min': 25, 'max': 90, 'step': 5}
        await proxy.async_set_humidity(53)
        self.assertEqual(self.calls[-1][2]['value'], 55)

    async def test_invalid_limits_keep_last_valid_grid(self):
        for attrs in ({'min': 90, 'max': 25, 'step': 5},
                      {'min': 25, 'max': 90, 'step': 0},
                      {'min': 25, 'max': 90, 'step': -5},
                      {'min': 'nan', 'max': 90, 'step': 5},
                      {'min': 25, 'max': 'inf', 'step': 5},
                      {'min': -10, 'max': 90, 'step': 5}):
            with self.subTest(attrs=attrs):
                self.target.attributes = attrs
                self.assertEqual(self.proxy._humidity_limits, (25, 90, 5))

    async def test_recovery_updates_limits(self):
        self.states['number.target'] = SimpleNamespace(state='unavailable', attributes={})
        self.assertEqual(self.proxy.min_humidity, 25)
        self.states['number.target'] = SimpleNamespace(state='40', attributes={'min': 30, 'max': 80, 'step': 10})
        await self.proxy.async_set_humidity(53)
        self.assertEqual(self.calls[-1][2]['value'], 50)
        self.assertEqual(self.proxy._humidity_limits, (30, 80, 10))

    async def test_non_finite_commands_are_rejected(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value), self.assertRaises(ServiceValidationError):
                await self.proxy.async_set_humidity(value)
        self.assertEqual(self.calls, [])

    async def test_invalid_target_readings_block_commands(self):
        for state in ('nan', 'inf', '-inf', 'bad', 'unknown', 'unavailable'):
            self.target.state = state
            with self.subTest(state=state), self.assertRaises(ServiceValidationError):
                await self.proxy.async_set_humidity(55)
        self.assertEqual(self.calls, [])

    async def test_invalid_sensor_readings_are_unknown(self):
        for state in ('nan', 'inf', '-inf', 'bad', 'unknown', 'unavailable'):
            self.states['sensor.humidity'].state = state
            self.assertIsNone(self.proxy.current_humidity)
            self.assertIsNone(self.proxy.action)
            self.assertTrue(self.proxy.available)

    async def test_power_commands_remain_accessible_without_target(self):
        self.states.pop('number.target')
        await self.proxy.async_turn_off()
        await self.proxy.async_turn_on()
        self.assertEqual([(domain, name) for domain, name, _ in self.calls],
                         [('homeassistant', 'turn_off'), ('homeassistant', 'turn_on')])

    async def test_power_unavailable_gates_availability(self):
        self.states['switch.power'].state = 'unavailable'
        self.assertFalse(self.proxy.available)
        self.assertIsNone(self.proxy.is_on)

    async def test_action_estimate(self):
        self.assertEqual(self.proxy.action, Action.DRYING)
        self.states['sensor.humidity'].state = '50'
        self.assertEqual(self.proxy.action, Action.IDLE)
        self.states['switch.power'].state = 'off'
        self.assertEqual(self.proxy.action, Action.OFF)


if __name__ == '__main__':
    unittest.main()
