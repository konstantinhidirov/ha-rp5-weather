"""Exercise the real HA coordinator and weather entity on partial updates."""
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

try:
    from homeassistant.helpers.update_coordinator import UpdateFailed
except ImportError:
    UpdateFailed = None

if UpdateFailed is not None:
    path = Path(__file__).parents[1] / 'custom_components/rp5_weather'
    spec = importlib.util.spec_from_file_location('rp5_runtime_tests', path / '__init__.py',
                                                submodule_search_locations=[str(path)])
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    weather = importlib.import_module('rp5_runtime_tests.weather')


@unittest.skipIf(UpdateFailed is None, 'Run runtime tests inside the HA container')
class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def data(self):
        now = datetime.now(timezone.utc)
        return {'forecast': [], 'observation': {'native_temperature': 14,
                'observed_at_approx': now.isoformat()}, 'forecast_error': 'bad dates',
                'observation_error': None, 'fetched_at': now.isoformat(),
                'source_url': 'https://m.rp5.ru/'}

    async def test_partial_forecast_keeps_real_weather_temperature(self):
        coordinator = Mock(data=self.data(), last_update_success=True)
        entry = types.SimpleNamespace(unique_id='1', title='Place', data={'url': 'https://m.rp5.ru/'})
        entity = weather.RP5Weather(coordinator, entry)
        self.assertTrue(entity.available)
        self.assertEqual(entity.native_temperature, 14)
        self.assertTrue(entity.extra_state_attributes['observation_available'])
        self.assertFalse(entity.extra_state_attributes['forecast_available'])
        self.assertEqual(entity.extra_state_attributes['forecast_error'], 'bad dates')
        self.assertEqual(await entity.async_forecast_hourly(), [])

    async def test_observation_age_limit_still_applies(self):
        data = self.data()
        data['observation']['observed_at_approx'] = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        coordinator = Mock(data=data, last_update_success=True)
        entry = types.SimpleNamespace(unique_id='1', title='Place', data={'url': 'https://m.rp5.ru/'})
        entity = weather.RP5Weather(coordinator, entry)
        self.assertIsNone(entity.native_temperature)
        self.assertFalse(entity.extra_state_attributes['observation_available'])

    def coordinator(self):
        coordinator = runtime.RP5Coordinator.__new__(runtime.RP5Coordinator)
        coordinator.client = Mock(fetch=AsyncMock(return_value=self.data()))
        coordinator._partial_errors = {}
        return coordinator

    async def test_partial_error_logged_once_and_recovery_logged(self):
        coordinator = self.coordinator()
        with patch.object(runtime, '_LOGGER') as logger:
            await coordinator._async_update_data()
            await coordinator._async_update_data()
            logger.warning.assert_called_once()
            coordinator.client.fetch.assert_awaited_with(allow_partial=True)
            coordinator.client.fetch.return_value = {**self.data(), 'forecast_error': None}
            await coordinator._async_update_data()
            logger.info.assert_called_once()

    async def test_total_failure_still_raises_update_failed(self):
        coordinator = self.coordinator()
        coordinator.client.fetch.side_effect = runtime.ParseError('no usable data')
        with self.assertRaises(UpdateFailed):
            await coordinator._async_update_data()
