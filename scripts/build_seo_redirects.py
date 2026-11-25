"""Offline generation/validation of the approved technical redirect dataset.

python -B scripts/build_seo_redirects.py --check
python -B scripts/build_seo_redirects.py --write
This technical source does not replace Marketing's unavailable editorial Excel.
"""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'infra/site/functions/legacy-redirects.json'
OUTPUT = ROOT / 'infra/site/functions/unified-viewer-request.js'
TEMPLATE = ROOT / 'scripts/seo/unified-viewer-request.js.template'
MAX_SIZE = 10240


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key: ' + key)
        result[key] = value
    return result


def render():
    data = json.loads(DATA.read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)
    rules = data['redirects']
    if len(rules) != 24:
        raise ValueError('Exactly 24 approved redirects are required')
    for source, target in rules.items():
        if not re.fullmatch(r'/[a-z0-9_/-]+', source) or source.endswith('/'):
            raise ValueError('Invalid source: ' + source)
        if not re.fullmatch(r'/[a-z0-9_-]+\.html', target):
            raise ValueError('Only approved internal HTML targets are supported: ' + target)
        if not (ROOT / target.lstrip('/')).is_file():
            raise ValueError('Missing destination: ' + target)
    template = TEMPLATE.read_text(encoding='utf-8')
    if template.count('__REDIRECTS_JSON__') != 1:
        raise ValueError('Expected exactly one rules marker')
    code = template.replace('__REDIRECTS_JSON__', json.dumps(rules, indent=4, ensure_ascii=True))
    if len(code.encode('utf-8')) > MAX_SIZE:
        raise ValueError('CloudFront Function exceeds 10 KB')
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--write', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = render().encode('utf-8')
    if args.write:
        OUTPUT.write_bytes(expected)
    elif not OUTPUT.exists() or OUTPUT.read_bytes() != expected:
        parser.exit(1, 'Generated JavaScript is stale; run --write\n')
    print('SEO_REDIRECTS=24; FUNCTION_SIZE_BYTES=' + str(len(expected)) + '; GENERATED_SOURCE_MATCH=YES')


if __name__ == '__main__':
    main()
