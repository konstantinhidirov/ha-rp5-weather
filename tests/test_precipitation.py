# coding: utf-8
"""Contracts reproduced from RP5 rain, snow and mixed-precipitation responses."""
from datetime import datetime, timedelta, timezone
import re
import unittest
from test_parser import p, fixture, NOW


END = datetime(2026, 10, 20, 22, tzinfo=timezone(timedelta(hours=7)))


def cell(text):
    html = '<td><div class="pr_0" onclick="tooltip(this, \'' + text + '\', \'hint\')"><div class="icon"></div></div></td>'
    return p.first(p.Tree(html).root, tag='td')


class PrecipitationTests(unittest.TestCase):
    def test_icon_only_hourly_rain(self):
        data = p.precipitation(cell('Слабый дождь (2.3 мм воды за 1 час с 21:00 до 22:00)'), END, 1)
        self.assertEqual(data['native_precipitation'], 2.3)
        self.assertEqual(data['rp5_snowfall_cm'], 0)
        self.assertEqual(data['rp5_precipitation_type'], 'rain')
        self.assertEqual(data['rp5_precipitation_interval_hours'], 1)

    def test_three_hour_rain_is_accumulated(self):
        data = p.precipitation(cell('Слабый дождь (в среднем 0.27 мм воды за 1 час с 19:00 до 22:00)'), END, 1)
        self.assertEqual(data['native_precipitation'], 0.81)
        self.assertEqual(data['rp5_precipitation_interval_start'], '2026-10-20T12:00:00+00:00')
        self.assertEqual(data['rp5_precipitation_interval_end'], '2026-10-20T15:00:00+00:00')
        self.assertEqual(data['rp5_precipitation_interval_hours'], 3)

    def test_snow_is_not_a_water_equivalent(self):
        data = p.precipitation(cell('Слабый снег (в среднем 0.63 см снега за 1 час с 19:00 до 22:00)'), END, 3)
        self.assertEqual(data['native_precipitation'], 0)
        self.assertEqual(data['rp5_snowfall_cm'], 1.89)
        self.assertEqual(data['rp5_precipitation_type'], 'snow')

    def test_mixed_components_in_either_order(self):
        for text in ['Слабый дождь со снегом (в среднем 0.27 мм воды и 0.17 см снега за 1 час с 19:00 до 22:00)',
                     'Слабый снег с дождём (в среднем 0.17 см снега и 0.27 мм воды за 1 час с 19:00 до 22:00)']:
            data = p.precipitation(cell(text), END, 3)
            self.assertEqual(data['native_precipitation'], 0.81)
            self.assertEqual(data['rp5_snowfall_cm'], 0.51)
            self.assertEqual(data['rp5_precipitation_type'], 'rain_snow')

    def test_midnight_interval_and_decimal_comma(self):
        end = END.replace(day=21, hour=1)
        data = p.precipitation(cell('Снег с дождём (в среднем 0,2 см снега и 0,1 мм воды за 1 час с 22:00 до 01:00)'), end, 3)
        self.assertEqual(data['native_precipitation'], 0.3)
        self.assertEqual(data['rp5_snowfall_cm'], 0.6)
        self.assertEqual(data['rp5_precipitation_interval_start'], '2026-10-20T15:00:00+00:00')

    def test_already_accumulated_amount_not_multiplied_twice(self):
        data = p.precipitation(cell('Дождь (0.9 мм воды за 3 часа с 19:00 до 22:00)'), END, 3)
        self.assertEqual(data['native_precipitation'], 0.9)

    def test_inconsistent_or_unknown_period_is_not_guessed(self):
        for text in ['Дождь (0.9 мм воды)', 'Дождь (0.9 мм воды за 1 час с 19:00 до 23:00)',
                     'Дождь (0.9 мм воды за 1 час с 19:80 до 22:00)',
                     'Дождь (0.9 мм воды за 0 часов с 19:00 до 22:00)']:
            self.assertEqual(p.precipitation(cell(text), END, 3), {})

    def test_feels_like_row_can_be_absent(self):
        html = re.sub(r'<tr class="feel-temp-row">.*?</tr>', '', fixture(), flags=re.S)
        forecast = p.parse_forecast(html, 14400, NOW)
        self.assertEqual(len(forecast), 12)
        self.assertNotIn('native_apparent_temperature', forecast[0])
        self.assertEqual(forecast[0]['native_temperature'], -5)

    def test_present_but_misaligned_optional_row_still_rejected(self):
        html = fixture().replace('<td class="n2"><div class="t_0">-8</div><div class="t_1" style="display:none">999</div></td>', '', 1)
        with self.assertRaises(p.ParseError):
            p.parse_forecast(html, 14400, NOW)


if __name__ == '__main__':
    unittest.main()
