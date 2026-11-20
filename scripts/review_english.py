"""Report translation-memory anomalies for editorial review; never uses the network."""
import json
import os
from pathlib import Path
import re

from build_english import ROOT, human, NUMBER, preserve_numbers


def main():
    cache = json.loads((ROOT / 'scripts/english-translations.json').read_text(encoding='utf-8'))
    cache.update(json.loads((ROOT / 'scripts/english-overrides.json').read_text(encoding='utf-8')))
    digits = NUMBER
    differences, inflated = [], []
    for source, target in cache.items():
        if not human(source):
            continue
        target = preserve_numbers(source, target)
        canonical = lambda text: [re.sub(r'[ ,\u00a0\u202f]', '', part) for part in digits.findall(text)]
        before, after = canonical(source), canonical(target)
        if before != after:
            differences.append({'source': source, 'target': target, 'numbers': [before, after]})
        if len(target) > max(80, 3*len(source)):
            inflated.append({'source': source, 'target': target})
    report = {'numeric_differences': differences, 'unexpected_expansion': inflated}
    path = Path(os.environ.get('TEMP', '/tmp')) / 'vms-english-editorial.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'numeric_differences': len(differences), 'unexpected_expansion': len(inflated), 'report': str(path)}))
    print(json.dumps(differences[:12], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
