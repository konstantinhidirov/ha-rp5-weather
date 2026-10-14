"""Synthetic parser contract tests; no RP5 HTML is redistributed."""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest

PARSER = Path(__file__).parents[1] / 'custom_components/rp5_weather/parser.py'
spec = importlib.util.spec_from_file_location('rp5_parser', PARSER)
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)

NOW = datetime(2026, 12, 31, 18, 30, tzinfo=timezone.utc)


def fixture():
    rows = ['<tr class="forecastDate"><td colspan="2">31 декабря</td><td colspan="12">1 января</td></tr>']
    def row(cls, cells):
        rows.append(f'<tr class="{cls}"><td class="title">Label</td>' + ''.join(f'<td class="n2">{x}</td>' for x in cells) + '<td class="title">Label</td></tr>')
    row('forecastTime', ['23'] + [f'{h:02}' for h in range(11)])
    for cls, unit, value in [('temperature-row', 't_0', '−5'), ('feel-temp-row', 't_0', '-8'),
                              ('pressure-row', 'p_3', '1024'), ('wind-velocity-row', 'wv_0', '4'),
                              ('wind-gusts-row', 'wv_0', '7')]:
        row(cls, [f'<div class="{unit}">{value}</div><div class="t_1" style="display:none">999</div>'] * 12)
    row('wind-direction-row', ['Ю-В'] * 12)
    row('humidity-row', ['76'] * 12)
    row('cloud-cover-row', ['<div class="cc_0"><div onclick="tooltip(this, \'Ясно\', \'hint\')"></div></div>'] * 12)
    row('precipitation-row', ['<div class="pr_0" onclick="tooltip(this, \'Явления погоды отсутствуют\', \'hint\')"><div class="wp_0"></div></div>'] * 12)
    return '<table>' + ''.join(rows) + '</table>'


class ParserTests(unittest.TestCase):
    def test_new_year_and_units(self):
        f = p.parse_forecast(fixture(), 14400, NOW)
        self.assertEqual(len(f), 12)
        self.assertEqual(f[0]['datetime'], '2026-12-31T19:00:00+00:00')
        self.assertEqual(f[1]['datetime'], '2026-12-31T20:00:00+00:00')
        self.assertEqual(f[-1]['datetime'], '2027-01-01T06:00:00+00:00')
        self.assertEqual(f[0]['native_temperature'], -5)
        self.assertEqual(f[0]['native_pressure'], 1024)
        self.assertEqual(f[0]['native_precipitation'], 0)
        self.assertEqual(f[0]['wind_bearing'], 135)
        self.assertEqual(f[0]['condition'], 'clear-night')

    def test_columns_must_align(self):
        bad = fixture().replace('<td class="n2">76</td>', '', 1)
        with self.assertRaises(p.ParseError):
            p.parse_forecast(bad, 14400, NOW)

    def test_block_and_empty_pages_rejected(self):
        for html in ['', '<html>Access denied</html>']:
            with self.assertRaises(p.ParseError):
                p.parse_forecast(html, 14400, NOW)

    def test_stale_forecast_rejected(self):
        with self.assertRaises(p.ParseError):
            p.parse_forecast(fixture(), 14400, datetime(2026, 7, 1, tzinfo=timezone.utc))

    def test_unreasonable_temperature_rejected(self):
        with self.assertRaises(p.ParseError):
            p.parse_forecast(fixture().replace('−5', '-900'), 14400, NOW)

    def test_missing_zero_not_invented(self):
        html = fixture().replace('Явления погоды отсутствуют', 'Новый неизвестный текст').replace('wp_0', 'wp_unknown')
        f = p.parse_forecast(html, 14400, NOW)
        self.assertNotIn('native_precipitation', f[0])

    def test_observation_not_large_display_value(self):
        html = '<div id="ArchTemp"><span class="t_0">18</span></div><div id="arch-message">30 минут назад было <span class="t_0">+19 °C</span><span style="display:none">+66 °F</span>, без существенной облачности, ветер <span class="wv_0">(4 м/с)</span>, с востоко-юго-востока.</div>'
        sun = '<table><tr class="forecastDate"><td>31 декабря</td></tr><tr class="sun-row"><td class="sunrise">07:00</td><td class="sunset">18:00</td></tr></table>'
        obs = p.observation(html, sun, 14400, NOW)
        self.assertEqual(obs['native_temperature'], 19)
        self.assertEqual(obs['native_wind_speed'], 4)
        self.assertEqual(obs['wind_bearing'], 112.5)
        self.assertEqual(obs['condition'], 'clear-night')
        self.assertNotIn('66', obs['description'])
        self.assertEqual(obs['reported_age_minutes'], 30)
        self.assertIsNone(p.observation(html.replace('30 минут', '8 часов'), sun, 14400, NOW))

    def test_unknown_observation_not_substituted(self):
        self.assertIsNone(p.observation('<div id="ArchTemp">18</div>', '', 14400, NOW))

    def test_rain_snow_clear_mapping(self):
        self.assertEqual(p.condition('Ясно', 'Дождь со снегом', True), 'snowy-rainy')
        self.assertEqual(p.condition('', 'Сильный дождь', True), 'pouring')
        self.assertEqual(p.condition('Без существенной облачности', '', True), 'sunny')
        self.assertIsNone(p.condition('Неизвестное', '', True))

    def test_location_urls_restricted(self):
        self.assertEqual(p.normalize_url('https://rp5.ru/Погода_в_Астрахани'), 'https://m.rp5.ru/%D0%9F%D0%BE%D0%B3%D0%BE%D0%B4%D0%B0_%D0%B2_%D0%90%D1%81%D1%82%D1%80%D0%B0%D1%85%D0%B0%D0%BD%D0%B8')
        for url in ['http://localhost/Погода_в_Астрахани', 'https://rp5.ru.evil.test/Погода_в_Астрахани',
                    'https://rp5.ru/Погода_в_Астрахани?q=a', 'https://rp5.ru/Погода_в_Астрахани/extra',
                    'https://user:pass@rp5.ru/Погода_в_Астрахани']:
            with self.assertRaises(ValueError):
                p.normalize_url(url)


if __name__ == '__main__':
    unittest.main()
