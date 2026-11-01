"""Exercise the real HA coordinator and weather entity on partial updates."""
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

try:
    from homeassistant.core import HomeAssistant
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
        coordinator._normal_update_interval = timedelta(hours=1)
        coordinator.update_interval = coordinator._normal_update_interval
        return coordinator

    async def test_total_transient_failure_retries_until_recovered(self):
        coordinator = self.coordinator()
        coordinator._normal_update_interval = timedelta(hours=2)
        for status in [503, 504]:
            coordinator.client.fetch.side_effect = runtime.RP5FetchError(f'HTTP {status}', retryable=True)
            with self.assertRaises(UpdateFailed):
                await coordinator._async_update_data()
            self.assertEqual(coordinator.update_interval, timedelta(minutes=3))
        coordinator.client.fetch.side_effect = None
        await coordinator._async_update_data()
        self.assertEqual(coordinator.update_interval, timedelta(hours=2))

    async def test_partial_transient_failure_retries_without_hiding_observation(self):
        coordinator = self.coordinator()
        coordinator.client.fetch.return_value = {**self.data(), 'retry_needed': True}
        data = await coordinator._async_update_data()
        self.assertEqual(data['observation']['native_temperature'], 14)
        self.assertEqual(coordinator.update_interval, timedelta(minutes=3))
        coordinator.client.fetch.return_value = self.data()
        await coordinator._async_update_data()
        self.assertEqual(coordinator.update_interval, timedelta(hours=1))

    async def test_other_failure_leaves_retry_mode(self):
        coordinator = self.coordinator()
        coordinator.update_interval = timedelta(minutes=3)
        for error in [runtime.RP5FetchError('HTTP 403'), runtime.ParseError('bad dates'), TimeoutError()]:
            with self.subTest(error=error):
                coordinator.client.fetch.side_effect = error
                with self.assertRaises(UpdateFailed):
                    await coordinator._async_update_data()
                self.assertEqual(coordinator.update_interval, timedelta(hours=1))

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


@unittest.skipIf(UpdateFailed is None, 'Run scheduling tests inside the HA container')
class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.entry = Mock(options={'update_interval': 120},
                          data={'url': 'https://rp5.ru/Погода_в_Астрахани'}, pref_disable_polling=False)
        with patch.object(runtime, 'async_get_clientsession', return_value=None):
            self.coordinator = runtime.RP5Coordinator(self.hass, self.entry)
        self.coordinator.client.fetch = AsyncMock()

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()
        self.directory.cleanup()

    def timer(self, seconds):
        # Inspect the actual HA scheduler's TimerHandle, without waiting minutes.
        handle = self.coordinator._unsub_refresh.__self__
        remaining = handle.when() - self.hass.loop.time()
        self.assertGreater(remaining, seconds - 2)
        self.assertLess(remaining, seconds + 1)
        return handle

    async def test_native_timer_retries_recovers_and_cancels_on_shutdown(self):
        coordinator = self.coordinator
        coordinator.async_add_listener(Mock())
        previous = self.timer(7200)
        for status in [503, 504]:
            coordinator.client.fetch.side_effect = runtime.RP5FetchError(f'HTTP {status}', retryable=True)
            await coordinator.async_refresh()
            self.assertFalse(coordinator.last_update_success)
            self.assertTrue(previous.cancelled())
            previous = self.timer(180)

        coordinator.client.fetch.side_effect = None
        coordinator.client.fetch.return_value = {'retry_needed': True, 'observation': {'native_temperature': 14}}
        await coordinator.async_refresh()
        self.assertTrue(coordinator.last_update_success)
        self.assertEqual(coordinator.data['observation']['native_temperature'], 14)
        self.assertTrue(previous.cancelled())
        previous = self.timer(180)

        coordinator.client.fetch.return_value = {'retry_needed': False}
        await coordinator.async_refresh()
        self.assertTrue(previous.cancelled())
        previous = self.timer(7200)
        await coordinator.async_shutdown()
        self.assertTrue(previous.cancelled())
        await coordinator.async_refresh()
        self.assertEqual(coordinator.client.fetch.await_count, 4)

    async def test_disabled_polling_does_not_start_a_retry_timer(self):
        coordinator = self.coordinator
        self.entry.pref_disable_polling = True
        coordinator.async_add_listener(Mock())
        coordinator.client.fetch.side_effect = runtime.RP5FetchError('HTTP 503', retryable=True)
        await coordinator.async_refresh()
        self.assertIsNone(coordinator._unsub_refresh)
