"""Parse the public RP5 mobile pages without executing JavaScript."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import quote, unquote, urlsplit


class ParseError(ValueError):
    """The source is unavailable, unexpected or incomplete."""


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)

    def find(self, *, tag=None, cls=None, id=None):
        for child in self.children:
            if isinstance(child, Node):
                if ((tag is None or child.tag == tag)
                    and (cls is None or cls in child.attrs.get('class', '').split())
                    and (id is None or child.attrs.get('id') == id)):
                    yield child
                yield from child.find(tag=tag, cls=cls, id=id)

    def text(self):
        return ''.join(c.text() if isinstance(c, Node) else c for c in self.children)


class Tree(HTMLParser):
    """Small tolerant HTML tree; no scripts or event handlers are executed."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or '' for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: v or '' for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def first(node, **kwargs):
    return next(node.find(**kwargs), None)


def number(text):
    text = re.sub(r'\s+', '', text).replace('−', '-').replace(',', '.')
    match = re.search(r'[+-]?\d+(?:\.\d+)?', text)
    return float(match.group()) if match else None


def cell_number(cell, unit_class=None):
    if cell is None:
        return None
    node = first(cell, cls=unit_class) if unit_class else cell
    return number(node.text()) if node else None


def tooltip(node):
    if node is None:
        return ''
    for item in [node, *node.find()]:
        value = item.attrs.get('onclick', '')
        match = re.search(r"tooltip\(this,\s*'((?:\\.|[^'])*)'", value)
        if match:
            return unescape(match.group(1))
    return ''


def normalize_url(url):
    """Accept a city page on RP5; use one canonical HTTPS mobile host."""
    p = urlsplit(url.strip())
    if (p.scheme not in {'http', 'https'} or p.hostname not in {'rp5.ru', 'm.rp5.ru', 'www.rp5.ru'}
        or p.username or p.password or p.port not in {None, 80, 443}
        or not p.path or p.path == '/' or p.query or p.fragment):
        raise ValueError('Use the full RP5 city page URL, without query parameters')
    path = unquote(p.path)
    if not path.startswith('/Погода_') or '/' in path[1:]:
        raise ValueError('Use the Russian-language forecast page URL (/Погода_...)')
    return 'https://m.rp5.ru' + quote(path, safe='/,_().-')


def metadata(html):
    id_match = re.search(r'page_values\.id\s*=\s*["\'](\d+)', html)
    offset = re.search(r'var\s+gmtAdd\s*=\s*(-?\d+)', html)
    title = first(Tree(html).root, tag='title')
    if not id_match or not offset or not title:
        raise ParseError('RP5 location metadata is missing')
    seconds = int(offset.group(1))
    if not -43200 <= seconds <= 50400:
        raise ParseError('Invalid RP5 UTC offset')
    return {'point_id': id_match.group(1), 'name': title.text().removesuffix(' - РП5'),
            'offset_seconds': seconds}


MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
DIRECTIONS = {'С': 0, 'С-С-В': 22.5, 'С-В': 45, 'В-С-В': 67.5, 'В': 90, 'В-Ю-В': 112.5,
              'Ю-В': 135, 'Ю-Ю-В': 157.5, 'Ю': 180, 'Ю-Ю-З': 202.5, 'Ю-З': 225,
              'З-Ю-З': 247.5, 'З': 270, 'З-С-З': 292.5, 'С-З': 315, 'С-С-З': 337.5}


def condition(cloud_text, weather_text, is_day):
    text = (cloud_text + ' ' + weather_text).lower()
    if 'гроз' in text:
        return 'lightning-rainy' if any(w in text for w in ['дожд', 'лив']) else 'lightning'
    if 'град' in text:
        return 'hail'
    if 'снег' in text and any(w in text for w in ['дожд', 'мокр']):
        return 'snowy-rainy'
    if any(w in text for w in ['снег', 'снеж']):
        return 'snowy'
    if any(w in text for w in ['дожд', 'лив', 'морось']):
        return 'pouring' if 'сильн' in text else 'rainy'
    if any(w in text for w in ['туман', 'дымка']):
        return 'fog'
    if any(w in text for w in ['малооблач', 'небольш', 'перемен', 'разорван']):
        return 'partlycloudy'
    if any(w in text for w in ['ясно', 'без существенной облачности', 'облаков нет']):
        return 'sunny' if is_day else 'clear-night'
    if any(w in text for w in ['пасмур', 'значительн', 'сплошн', 'облачно']):
        return 'cloudy'
    return None


def precipitation(cell, end, fallback_hours):
    """Return rain in mm and snowfall in cm accumulated over the source interval.

    RP5 displays hourly rates inside icon tooltips, even for 3-hour slots.
    The column timestamp is the END of the accumulation interval.
    Snow depth is never converted to a water equivalent.
    """
    metric = first(cell, cls='pr_0')
    if metric is None:
        return {}
    text = tooltip(metric)
    dry = first(metric, cls='wp_0') is not None or 'явления погоды отсутствуют' in text.lower()
    rain = re.search(r'(\d+(?:[.,]\d+)?)\s*мм\s+воды', text)
    snow = re.search(r'(\d+(?:[.,]\d+)?)\s*см\s+снега', text)
    if not dry and not rain and not snow:
        return {}

    duration = fallback_hours
    reference_hours = 1.0
    if not dry:
        clocks = re.search(r'с\s+(\d{1,2}):(\d{2})\s+до\s+(\d{1,2}):(\d{2})', text)
        reference = re.search(r'за\s+(\d+(?:[.,]\d+)?)\s+час', text)
        if not clocks or not reference:
            return {}  # Unknown period must not turn into a guessed amount.
        sh, sm, eh, em = map(int, clocks.groups())
        if sh > 23 or eh > 23 or sm > 59 or em > 59 or (eh, em) != (end.hour, end.minute):
            return {}
        duration = ((eh * 60 + em) - (sh * 60 + sm)) % 1440 / 60
        reference_hours = float(reference.group(1).replace(',', '.'))
    if not 0 < duration <= 24 or not 0 < reference_hours <= 24:
        return {}

    rain_amount = float(rain.group(1).replace(',', '.')) if rain else 0.0
    snow_amount = float(snow.group(1).replace(',', '.')) if snow else 0.0
    kind = 'none' if dry else 'rain_snow' if rain and snow else 'rain' if rain else 'snow'
    return {
        'native_precipitation': round(rain_amount * duration / reference_hours, 6),
        'rp5_snowfall_cm': round(snow_amount * duration / reference_hours, 6),
        'rp5_precipitation_type': kind,
        'rp5_precipitation_interval_hours': duration,
        'rp5_precipitation_interval_start': (end - timedelta(hours=duration)).astimezone(timezone.utc).isoformat(),
        'rp5_precipitation_interval_end': end.astimezone(timezone.utc).isoformat(),
    }


def parse_forecast(html, offset_seconds, fetched_at):
    root = Tree(html).root
    rows = {}
    for row in root.find(tag='tr'):
        classes = row.attrs.get('class', '').split()
        for cls in classes:
            if cls not in rows:
                rows[cls] = row
    def cells(cls):
        if cls not in rows:
            raise ParseError(f'Missing forecast row: {cls}')
        return [c for c in rows[cls].children if isinstance(c, Node) and c.tag == 'td'
                and 'title' not in c.attrs.get('class', '').split()]
    hours = cells('forecastTime')
    # The last "Местное время" column is also class=title and excluded.
    n = len(hours)
    fields = {key: cells(cls) for key, cls in {
        'temperature': 'temperature-row', 'pressure': 'pressure-row',
        'wind': 'wind-velocity-row', 'gust': 'wind-gusts-row', 'bearing': 'wind-direction-row',
        'humidity': 'humidity-row', 'cloud': 'cloud-cover-row', 'precipitation': 'precipitation-row',
    }.items()}
    # RP5 omits this entire row when it has nothing to display.
    fields['apparent'] = cells('feel-temp-row') if 'feel-temp-row' in rows else [None] * n
    if n < 12 or any(len(v) != n for v in fields.values()):
        raise ParseError('Forecast columns no longer align')
    tz = timezone(timedelta(seconds=offset_seconds))
    local_now = fetched_at.astimezone(tz)
    dates = []
    previous = None
    date_cells = cells('forecastDate')
    for i, cell in enumerate(date_cells):
        match = re.search(r'(\d{1,2})\s+(' + '|'.join(MONTHS) + r')', cell.text())
        if not match:
            raise ParseError('Forecast date is missing')
        day, month = int(match.group(1)), MONTHS.index(match.group(2)) + 1
        candidates = [datetime(y, month, day, tzinfo=tz) for y in [local_now.year - 1, local_now.year, local_now.year + 1]]
        anchor = previous or local_now
        date = min(candidates, key=lambda x: abs((x - anchor).total_seconds()))
        if previous and date <= previous:
            raise ParseError('Forecast dates are not increasing')
        previous = date
        count = int(cell.attrs.get('colspan', '1')) - (1 if i == 0 else 0) - (1 if i == len(date_cells) - 1 else 0)
        dates.extend([date] * count)
    if len(dates) != n:
        raise ParseError('Forecast date spans do not match hours')
    result = []
    for i, hour in enumerate(hours):
        h = number(hour.text())
        if h is None or int(h) != h or not 0 <= h <= 23:
            raise ParseError('Invalid forecast hour')
        dt = dates[i].replace(hour=int(h))
        if result and dt <= datetime.fromisoformat(result[-1]['datetime']):
            raise ParseError('Forecast timestamps are not increasing')
        temp = cell_number(fields['temperature'][i], 't_0')
        if temp is None or not -90 <= temp <= 65:
            raise ParseError('Invalid forecast temperature')
        apparent = cell_number(fields['apparent'][i], 't_0')
        if apparent is None and 'равно значению температуры' in tooltip(fields['apparent'][i]):
            apparent = temp
        cloud = first(fields['cloud'][i], cls='cc_0')
        rain = first(fields['precipitation'][i], cls='pr_0')
        cloud_text, rain_text = tooltip(cloud) if cloud else '', tooltip(rain) if rain else ''
        if i:
            duration = (dt - datetime.fromisoformat(result[-1]['datetime'])).total_seconds() / 3600
        else:
            next_hour = number(hours[1].text())
            if next_hour is None or int(next_hour) != next_hour or not 0 <= next_hour <= 23:
                raise ParseError('Invalid forecast hour')
            duration = (dates[1].replace(hour=int(next_hour)) - dt).total_seconds() / 3600
        item = {'datetime': dt.astimezone(timezone.utc).isoformat(), 'native_temperature': temp,
                'native_apparent_temperature': apparent, 'native_pressure': cell_number(fields['pressure'][i], 'p_3'),
                'native_wind_speed': cell_number(fields['wind'][i], 'wv_0'),
                'native_wind_gust_speed': cell_number(fields['gust'][i], 'wv_0'),
                'wind_bearing': DIRECTIONS.get(fields['bearing'][i].text().strip()),
                'humidity': cell_number(fields['humidity'][i]),
                'condition': condition(cloud_text, rain_text, 'd' in hour.attrs.get('class', ''))}
        item.update(precipitation(fields['precipitation'][i], dt, duration))
        result.append({k: v for k, v in item.items() if v is not None})
    if abs((datetime.fromisoformat(result[0]['datetime']) - fetched_at).total_seconds()) > 86400:
        raise ParseError('Forecast start is stale or unexpected')
    return result


def daylight_from_forecast(html, local_time):
    """Use RP5 sunrise/sunset for the first forecast day, not HA's home location."""
    root = Tree(html).root
    date_row = first(root, tag='tr', cls='forecastDate')
    if not date_row:
        return None
    match = re.search(r'(\d{1,2})\s+(' + '|'.join(MONTHS) + r')', date_row.text())
    if not match or (int(match.group(1)), MONTHS.index(match.group(2)) + 1) != (local_time.day, local_time.month):
        return None
    row = first(root, tag='tr', cls='sun-row')
    if not row:
        return None
    times = []
    for cls in ['sunrise', 'sunset']:
        node = first(row, cls=cls)
        match = re.fullmatch(r'(\d{2}):(\d{2})', node.text().strip()) if node else None
        if not match:
            return None
        h, m = map(int, match.groups())
        if h > 23 or m > 59:
            return None
        times.append(h * 60 + m)
    return times[0] <= local_time.hour * 60 + local_time.minute < times[1]


def visible_text(node):
    parts = []
    for child in node.children:
        if isinstance(child, Node):
            if re.search(r'display\s*:\s*none', child.attrs.get('style', '')):
                continue
            parts.append(visible_text(child))
        else:
            parts.append(child)
    return ''.join(parts)


def observation(html, forecast_html, offset_seconds, fetched_at):
    """Parse observations, retaining RP5's age instead of claiming they are live."""
    node = first(Tree(html).root, id='arch-message')
    if not node:
        return None
    temp = cell_number(node, 't_0')
    if temp is None or not -90 <= temp <= 65:
        return None
    text = ' '.join(visible_text(node).split())
    age = re.search(r'(\d+)\s*(минут|час|дн|сут)', text)
    if not age or 'назад' not in text:
        return None
    minutes = int(age.group(1)) * (1 if age.group(2) == 'минут' else 60 if age.group(2) == 'час' else 1440)
    # Old observations must not masquerade as current conditions.
    if minutes > 360:
        return None
    observed_at = fetched_at - timedelta(minutes=minutes)
    day = daylight_from_forecast(forecast_html, observed_at.astimezone(timezone(timedelta(seconds=offset_seconds))))
    sky = condition(text, '', bool(day))
    if day is None and sky in {'sunny', 'clear-night'}:
        sky = None
    phrases = ['с севера', 'с северо-северо-востока', 'с северо-востока', 'с востоко-северо-востока',
               'с востока', 'с востоко-юго-востока', 'с юго-востока', 'с юго-юго-востока',
               'с юга', 'с юго-юго-запада', 'с юго-запада', 'с западо-юго-запада',
               'с запада', 'с западо-северо-запада', 'с северо-запада', 'с северо-северо-запада']
    bearing = next((i * 22.5 for i, phrase in enumerate(phrases) if phrase in text.lower()), None)
    return {k: v for k, v in {
        'native_temperature': temp, 'native_wind_speed': cell_number(node, 'wv_0'),
        'wind_bearing': bearing, 'condition': sky, 'description': text,
        'reported_age_minutes': minutes, 'observed_at_approx': observed_at.isoformat(),
    }.items() if v is not None}
