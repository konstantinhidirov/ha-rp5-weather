"""Read RP5 with the integration client, without running Home Assistant.

Usage: python probe.py 'https://rp5.ru/Погода_в_...' [--output sample.json]
Requires aiohttp; the parser uses only the Python standard library.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import types

# Import the source library without executing the Home Assistant entry point.
package = types.ModuleType('rp5_source')
package.__path__ = [str(Path(__file__).parent / 'custom_components/rp5_weather')]
sys.modules['rp5_source'] = package
from rp5_source.client import RP5Client
import aiohttp


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    async with aiohttp.ClientSession(trust_env=True) as session:
        data = await RP5Client(session, args.url).fetch()
    serialized = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + '\n', encoding='utf-8')
    print(json.dumps({'location': data['location'], 'fetched_at': data['fetched_at'],
                      'observation': data['observation'], 'forecast_count': len(data['forecast']),
                      'first_forecast': data['forecast'][0], 'last_forecast': data['forecast'][-1]},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
