"""Location lookup through the endpoint used by RP5's mobile search."""
from __future__ import annotations

from html import unescape
import json

from .client import request_text
from .parser import ParseError, normalize_url

SEARCH_ENDPOINT = 'https://m.rp5.ru/mobile/location/jsonsearch.php'
SEARCH_LIMIT = 50


def normalize_query(query):
    if not isinstance(query, str):
        raise ValueError('Enter a location name')
    query = ' '.join(query.split())
    if not 2 <= len(query) <= 100:
        raise ValueError('Enter between 2 and 100 characters')
    return query


def parse_search(text):
    """Keep region/station labels and reject unexpected or unsafe addresses."""
    # The mobile search returns HTTP 200 with an empty body for no matches.
    if not text.strip():
        return []
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as err:
        raise ParseError('RP5 search response is not JSON') from err
    if not isinstance(rows, list):
        raise ParseError('RP5 search response is not a list')
    results = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ParseError('RP5 search result is not an object')
        name, path = row.get('name'), row.get('namealt')
        if not isinstance(name, str) or not name.strip() or not isinstance(path, str):
            raise ParseError('RP5 search result is incomplete')
        try:
            url = normalize_url('https://m.rp5.ru/' + path.lstrip('/'))
        except ValueError as err:
            raise ParseError('RP5 search result has an invalid location address') from err
        if url not in seen:
            seen.add(url)
            results.append({'label': unescape(name).strip(), 'url': url})
    # RP5 ignores `limit`; keep all matches rather than hide the user's station.
    return results


async def search_locations(session, query):
    query = normalize_query(query)
    text = await request_text(session, 'GET', SEARCH_ENDPOINT,
                              referer='https://m.rp5.ru/',
                              params={'q': query.lower(), 'limit': str(SEARCH_LIMIT)})
    return parse_search(text)
