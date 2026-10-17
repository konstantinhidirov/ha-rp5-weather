"""Validate real search labels, disambiguation and bad server responses."""
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

package = types.ModuleType('rp5_search_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'custom_components/rp5_weather')]
sys.modules[package.__name__] = package
search = importlib.import_module('rp5_search_tests.search')


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def test_real_results_keep_region_and_airport(self):
        source = (Path(__file__).parent / 'fixtures/search-astrakhan.json').read_text(encoding='utf-8')
        results = search.parse_search(source)
        self.assertEqual(len(results), 3)
        self.assertIn('Кировская область', results[1]['label'])
        self.assertIn('Кустодиева (аэропорт)', results[2]['label'])
        self.assertTrue(all(item['url'].startswith('https://m.rp5.ru/') for item in results))

    def test_empty_list_is_valid_no_results(self):
        self.assertEqual(search.parse_search('[]'), [])
        self.assertEqual(search.parse_search(''), [])

    def test_empty_invalid_and_wrong_shape_fail(self):
        for body in ['<html>blocked</html>', '{}', 'null', '[null]', '[{}]']:
            with self.subTest(body=body), self.assertRaises(search.ParseError):
                search.parse_search(body)

    def test_unsafe_location_addresses_fail(self):
        for path in ['https://evil.example/a', '//evil.example/a', 'Погода_в_Городе/../../x', 'Погода_в_Городе?q=1']:
            with self.subTest(path=path), self.assertRaises(search.ParseError):
                search.parse_search(json.dumps([{'name':'Город','namealt':path}]))

    def test_duplicate_urls_are_collapsed(self):
        row = {'name':'Город, регион','namealt':'Погода_в_Городе'}
        self.assertEqual(len(search.parse_search(json.dumps([row, row]))), 1)

    def test_large_result_set_is_not_silently_truncated(self):
        rows = [{'name':f'Город {n}, регион','namealt':f'Погода_в_Городе_{n}'} for n in range(145)]
        self.assertEqual(len(search.parse_search(json.dumps(rows))), 145)

    def test_query_validation(self):
        self.assertEqual(search.normalize_query('  Нижний   Новгород '), 'Нижний Новгород')
        for query in ['', 'a', 'a'*101, None]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                search.normalize_query(query)

    async def test_request_uses_query_params_and_mobile_referer(self):
        with patch.object(search, 'request_text', AsyncMock(return_value='[]')) as request:
            self.assertEqual(await search.search_locations('session', ' Самара '), [])
            request.assert_awaited_once_with('session', 'GET', search.SEARCH_ENDPOINT,
                referer='https://m.rp5.ru/', params={'q':'самара','limit':'50'})

    async def test_invalid_query_never_requests_server(self):
        with patch.object(search, 'request_text', AsyncMock()) as request:
            with self.assertRaises(ValueError):
                await search.search_locations('session', 'a')
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
