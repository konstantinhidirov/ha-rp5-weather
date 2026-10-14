"""Собрать архив для HACS без служебных файлов репозитория."""
import argparse
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', help='Тег релиза: v<версия manifest>')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = root / 'custom_components' / 'rp5_weather'
    manifest = json.loads((source / 'manifest.json').read_text())
    if args.tag and args.tag != 'v' + manifest['version']:
        parser.error('Тег релиза не совпадает с версией manifest.json')
    output = root / 'dist' / 'rp5_weather.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            if path.suffix not in {'.py', '.json', '.png'}:
                continue
            archive.write(path, path.relative_to(source))
    print(f'Архив версии {manifest["version"]}: {output}')


if __name__ == '__main__':
    main()
