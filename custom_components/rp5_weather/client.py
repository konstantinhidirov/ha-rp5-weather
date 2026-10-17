"""Read the same public endpoints used by the RP5 mobile page."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

import aiohttp

from .parser import ParseError, metadata, normalize_url, observation, parse_forecast

ENDPOINT = 'https://m.rp5.ru/mobile/location/html_forecast.php'
MAX_BYTES = 2_000_000
HEADERS = {'User-Agent': 'HomeAssistant-RP5-Weather/0.1', 'Accept-Language': 'ru'}


async def request_text(session, method, url, *, referer, **kwargs):
    """Bound and validate responses from the public mobile endpoints."""
    async with session.request(method, url, headers={**HEADERS, 'Referer': referer},
                               timeout=aiohttp.ClientTimeout(total=25),
                               allow_redirects=False, **kwargs) as response:
        if response.status != 200:
            raise ParseError(f'RP5 returned HTTP {response.status}')
        data = bytearray()
        async for part in response.content.iter_chunked(65536):
            data.extend(part)
            if len(data) > MAX_BYTES:
                raise ParseError('RP5 response is too large')
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError as err:
            raise ParseError('RP5 response is not UTF-8') from err


class RP5Client:
    def __init__(self, session, url):
        self.session = session
        self.url = normalize_url(url)

    async def _text(self, method, url, **kwargs):
        return await request_text(self.session, method, url, referer=self.url, **kwargs)

    async def _tab(self, point_id, tab):
        text = await self._text('POST', ENDPOINT, data={'id': point_id, 'ftab': str(tab)})
        try:
            html = json.loads(text)
        except json.JSONDecodeError as err:
            raise ParseError('RP5 response is not JSON') from err
        if not isinstance(html, str) or not html.strip():
            raise ParseError('RP5 returned empty or unexpected content')
        return html

    async def fetch(self, *, allow_partial=False):
        # Refresh metadata too: current local UTC offset can change with DST.
        page = await self._text('GET', self.url)
        meta = await asyncio.to_thread(metadata, page)
        errors = {}
        tabs = {}
        for name, tab in [('forecast', 2), ('observation', 0)]:
            try:
                tabs[name] = await self._tab(meta['point_id'], tab)
            except (aiohttp.ClientError, TimeoutError, ParseError) as err:
                if not allow_partial:
                    raise
                errors[name] = str(err) or type(err).__name__
        fetched_at = datetime.now(timezone.utc)
        forecast = []
        current = None
        if 'forecast' in tabs:
            try:
                forecast = await asyncio.to_thread(parse_forecast, tabs['forecast'], meta['offset_seconds'], fetched_at)
            except ParseError as err:
                if not allow_partial:
                    raise
                errors['forecast'] = str(err)
        if 'observation' in tabs:
            current = await asyncio.to_thread(observation, tabs['observation'], tabs.get('forecast', ''),
                                              meta['offset_seconds'], fetched_at)
        if not forecast and not current:
            raise ParseError('RP5 has no usable forecast or observation: ' + '; '.join(errors.values()))
        return {'location': meta, 'forecast': forecast, 'observation': current,
                'fetched_at': fetched_at.isoformat(), 'source_url': self.url,
                'forecast_error': errors.get('forecast'), 'observation_error': errors.get('observation')}
