"""Fail closed on public endpoint errors; do not fabricate weather data."""
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

package = types.ModuleType('rp5_source_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'custom_components/rp5_weather')]
sys.modules[package.__name__] = package
client_module = importlib.import_module('rp5_source_tests.client')


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_503_and_504_http_responses_are_retryable(self):
        class Response:
            status = 503
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        class Session:
            def request(self, *args, **kwargs):
                return response
        for status in [503, 504, 403, 404, 429, 500]:
            response = Response()
            response.status = status
            client = client_module.RP5Client(Session(), 'https://rp5.ru/Погода_в_Астрахани')
            with self.subTest(status=status), self.assertRaises(client_module.RP5FetchError) as error:
                await client._text('GET', client.url)
            self.assertEqual(error.exception.retryable, status in {503, 504})
            self.assertIn(str(status), str(error.exception))

    async def test_partial_503_and_504_failures_request_retry(self):
        for status in [503, 504]:
            error = client_module.RP5FetchError(f'RP5 returned HTTP {status}', retryable=True)
            for source in ['forecast_error', 'observation_error']:
                with self.subTest(status=status, source=source):
                    data = await self.fetch(**{source: error})
                    self.assertTrue(data['retry_needed'])

    async def test_total_partial_failure_preserves_retry_classification(self):
        error = client_module.RP5FetchError('RP5 returned HTTP 503', retryable=True)
        with self.assertRaises(client_module.RP5FetchError) as raised:
            await self.fetch(forecast_error=error, observation_error=TimeoutError())
        self.assertTrue(raised.exception.retryable)

    async def test_message_text_does_not_enable_retry(self):
        data = await self.fetch(forecast_error=client_module.ParseError('RP5 returned HTTP 503'))
        self.assertFalse(data['retry_needed'])
        data = await self.fetch(forecast_error=TimeoutError())
        self.assertFalse(data['retry_needed'])

    async def test_success_has_no_retry_flag(self):
        self.assertFalse((await self.fetch())['retry_needed'])

    async def test_location_page_transient_failure_keeps_retry_flag(self):
        client = client_module.RP5Client(None, 'https://rp5.ru/Погода_в_Астрахани')
        client._text = AsyncMock(side_effect=client_module.RP5FetchError('HTTP 504', retryable=True))
        with self.assertRaises(client_module.RP5FetchError) as error:
            await client.fetch(allow_partial=True)
        self.assertTrue(error.exception.retryable)

    async def fetch(self, *, forecast_error=None, observation_error=None, current=True, partial=True):
        client = client_module.RP5Client(None, 'https://rp5.ru/Погода_в_Астрахани')
        client._text = AsyncMock(return_value='location page')
        client._tab = AsyncMock(side_effect=[forecast_error or 'forecast html',
                                             observation_error or 'observation html'])
        with patch.object(client_module, 'metadata', return_value={'point_id': '1', 'offset_seconds': 0}), \
             patch.object(client_module, 'parse_forecast', return_value=[{'datetime': 'future'}]), \
             patch.object(client_module, 'observation', return_value={'native_temperature': 14} if current else None):
            return await client.fetch(allow_partial=partial)

    async def test_forecast_http_failure_keeps_observation(self):
        data = await self.fetch(forecast_error=TimeoutError('forecast timeout'))
        self.assertEqual(data['observation']['native_temperature'], 14)
        self.assertEqual(data['forecast'], [])
        self.assertEqual(data['forecast_error'], 'forecast timeout')

    async def test_forecast_parse_failure_keeps_observation(self):
        client = client_module.RP5Client(None, 'https://rp5.ru/Погода_в_Астрахани')
        client._text = AsyncMock(return_value='location page')
        client._tab = AsyncMock(side_effect=['broken forecast', 'observation html'])
        with patch.object(client_module, 'metadata', return_value={'point_id': '1', 'offset_seconds': 0}), \
             patch.object(client_module, 'parse_forecast', side_effect=client_module.ParseError('bad dates')), \
             patch.object(client_module, 'observation', return_value={'native_temperature': 14}):
            data = await client.fetch(allow_partial=True)
        self.assertEqual(data['observation']['native_temperature'], 14)
        self.assertEqual(data['forecast'], [])
        self.assertEqual(data['forecast_error'], 'bad dates')

    async def test_observation_failure_keeps_forecast(self):
        data = await self.fetch(observation_error=client_module.ParseError('empty observation'))
        self.assertIsNone(data['observation'])
        self.assertTrue(data['forecast'])
        self.assertEqual(data['observation_error'], 'empty observation')

    async def test_both_sources_failed_is_not_success(self):
        with self.assertRaises(client_module.ParseError):
            await self.fetch(forecast_error=TimeoutError(), observation_error=TimeoutError())
        with self.assertRaises(client_module.ParseError):
            await self.fetch(forecast_error=TimeoutError(), current=False)

    async def test_config_flow_still_requires_valid_forecast(self):
        with self.assertRaises(TimeoutError):
            await self.fetch(forecast_error=TimeoutError(), partial=False)

    async def test_missing_observation_not_replaced_by_forecast(self):
        data = await self.fetch(current=False)
        self.assertIsNone(data['observation'])
        self.assertTrue(data['forecast'])

    async def test_empty_non_json_or_wrong_shape_rejected(self):
        client = client_module.RP5Client(None, 'https://rp5.ru/Погода_в_Астрахани')
        for body in ['', '<html>blocked</html>', '{}', 'null', '""']:
            async def response(*args, **kwargs):
                return body
            client._text = response
            with self.assertRaises(client_module.ParseError):
                await client._tab('504594', 2)

    async def test_http_403_is_preserved(self):
        class Response:
            status = 403
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        class Session:
            def request(self, *args, **kwargs):
                return Response()
        client = client_module.RP5Client(Session(), 'https://rp5.ru/Погода_в_Астрахани')
        with self.assertRaisesRegex(client_module.ParseError, 'HTTP 403'):
            await client._text('GET', client.url)


if __name__ == '__main__':
    unittest.main()
