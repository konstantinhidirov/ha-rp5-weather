"""Run with HA installed; exercise the native selectors and duplicate guard."""
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

try:
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import AbortFlow
except ImportError:
    HomeAssistant = None

if HomeAssistant is not None:
    package = types.ModuleType('rp5_flow_tests')
    package.__path__ = [str(Path(__file__).parents[1] / 'custom_components/rp5_weather')]
    sys.modules[package.__name__] = package
    module = importlib.import_module('rp5_flow_tests.config_flow')

URL = 'https://m.rp5.ru/%D0%9F%D0%BE%D0%B3%D0%BE%D0%B4%D0%B0_%D0%B2_%D0%93%D0%BE%D1%80%D0%BE%D0%B4%D0%B5'
LOCATIONS = [{'url': URL, 'label': 'Город, страна, область (аэропорт)'}]


@unittest.skipIf(HomeAssistant is None, 'Run config flow tests inside the HA container')
class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = Mock()
        self.hass.config_entries.async_entry_for_domain_unique_id.return_value = None
        self.flow = module.RP5ConfigFlow()
        self.flow.hass = self.hass
        self.flow.handler = 'rp5_weather'
        self.flow.flow_id = 'isolated-form-test'
        self.flow.context = {'source': 'user'}
        self.flow._async_in_progress = Mock(return_value=[])
        self.session_patch = patch.object(module, 'async_get_clientsession', return_value=None)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)

    def client(self, *, error=None):
        client = Mock(url=URL)
        client.fetch = AsyncMock(return_value={'location': {'point_id': '504594', 'name': 'Город, аэропорт'}}, side_effect=error)
        return patch.object(module, 'RP5Client', return_value=client)

    async def results(self):
        with patch.object(module, 'search_locations', AsyncMock(return_value=LOCATIONS)):
            return await self.flow.async_step_user({'query': 'Город'})

    async def test_initial_form_and_real_native_select(self):
        result = await self.flow.async_step_user()
        self.assertEqual(result['step_id'], 'user')
        self.assertEqual([str(k) for k in result['data_schema'].schema], ['query'])
        result = await self.results()
        self.assertEqual(result['step_id'], 'select_location')
        select = next(iter(result['data_schema'].schema.values()))
        self.assertEqual(select.config['options'][0], {'value': URL, 'label': LOCATIONS[0]['label']})
        self.assertEqual(result['description_placeholders'], {'query': 'Город'})

    async def test_selection_creates_same_storage_shape_and_unique_id(self):
        await self.results()
        with self.client():
            result = await self.flow.async_step_select_location({'location': URL})
        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['data'], {'url': URL})
        self.assertEqual(self.flow.unique_id, '504594')

    async def test_configured_station_is_rejected_by_native_guard(self):
        await self.results()
        self.hass.config_entries.async_entry_for_domain_unique_id.return_value = types.SimpleNamespace(source='user')
        with self.client(), self.assertRaises(AbortFlow) as error:
            await self.flow.async_step_select_location({'location': URL})
        self.assertEqual(error.exception.reason, 'already_configured')

    async def test_retry_search_returns_to_name_form(self):
        await self.results()
        result = await self.flow.async_step_select_location({'location': module.SEARCH_AGAIN})
        self.assertEqual(result['step_id'], 'user')
        self.assertFalse(self.flow._locations)

    async def test_no_matches_stays_on_search(self):
        with patch.object(module, 'search_locations', AsyncMock(return_value=[])):
            result = await self.flow.async_step_user({'query': 'Несуществующий'})
        self.assertEqual(result['errors'], {'query': 'no_results'})
        self.assertFalse(self.flow._locations)

    async def test_invalid_query_does_not_request(self):
        with patch.object(module, 'search_locations', AsyncMock()) as request:
            result = await self.flow.async_step_user({'query': 'а'})
        self.assertEqual(result['errors'], {'query': 'invalid_query'})
        request.assert_not_called()

    async def test_search_failure_and_timeout_are_recoverable(self):
        for error in [module.ParseError('changed source'), TimeoutError()]:
            with patch.object(module, 'search_locations', AsyncMock(side_effect=error)):
                result = await self.flow.async_step_user({'query': 'Город'})
            self.assertEqual(result['errors'], {'base': 'cannot_search'})

    async def test_unlisted_url_cannot_be_selected(self):
        await self.results()
        with patch.object(module, 'RP5Client') as client:
            result = await self.flow.async_step_select_location({'location': 'https://evil.example/'})
        self.assertEqual(result['errors'], {'location': 'invalid_location'})
        client.assert_not_called()

    async def test_forecast_failure_keeps_selection_for_retry(self):
        await self.results()
        with self.client(error=module.ParseError('empty weather')):
            result = await self.flow.async_step_select_location({'location': URL})
        self.assertEqual(result['step_id'], 'select_location')
        self.assertEqual(result['errors'], {'base': 'cannot_connect'})

    async def test_pasted_url_and_previous_input_shape_still_work(self):
        for user_input in [{'query': URL}, {'url': URL}]:
            with self.client(), patch.object(module, 'search_locations', AsyncMock()) as request:
                result = await self.flow.async_step_user(user_input)
            self.assertEqual(result['type'], 'create_entry')
            self.assertEqual(result['data'], {'url': URL})
            request.assert_not_called()

    async def test_invalid_pasted_url_rejected(self):
        result = await self.flow.async_step_user({'query': 'https://evil.example/'})
        self.assertEqual(result['errors'], {'query': 'invalid_url'})


if __name__ == '__main__':
    unittest.main()
