"""Fail closed on public endpoint errors; do not fabricate weather data."""
import importlib
from pathlib import Path
import sys
import types
import unittest

package = types.ModuleType('rp5_source_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'custom_components/rp5_weather')]
sys.modules[package.__name__] = package
client_module = importlib.import_module('rp5_source_tests.client')


class ClientTests(unittest.IsolatedAsyncioTestCase):
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
