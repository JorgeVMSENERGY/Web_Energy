"""Audit English counterparts, reciprocal language links, and new missing resources."""
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from build_english import ROOT, PAGES, NAMES, english_name, ORIGIN


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.refs = []
        self.lang = None
        self.scripts = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'html':
            self.lang = attrs.get('lang')
        for field in ('href', 'src', 'poster'):
            value = attrs.get(field)
            if value and not (tag == 'link' and attrs.get('rel') in {'canonical', 'alternate'}):
                self.refs.append((tag, field, value))


def local_target(base, value):
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.path:
        return None
    return (ROOT / unquote(parts.path).lstrip('/') if parts.path.startswith('/')
            else base / unquote(parts.path)).resolve()


def main():
    errors, inherited = [], set()
    for source in PAGES:
        native = Links(source.read_text(encoding='utf-8'))
        old_missing = set()
        for _, _, value in native.refs:
            target = local_target(ROOT, value)
            if target and not target.exists():
                old_missing.add(target.name)
        english = ROOT / 'en' / english_name(source.name)
        if not english.exists():
            errors.append((source.name, 'Missing English page'))
            continue
        text = english.read_text(encoding='utf-8')
        parsed = Links(text)
        if parsed.lang != 'en':
            errors.append((english.name, 'Incorrect HTML language'))
        if 'hreflang="es-MX" href="' + ORIGIN + source.name not in text:
            errors.append((english.name, 'Missing reciprocal Spanish alternate'))
        for tag, field, value in parsed.refs:
            target = local_target(english.parent, value)
            if target and not target.exists():
                if target.name in old_missing:
                    inherited.add((source.name, value))
                else:
                    errors.append((english.name, 'New missing resource: ' + value))
            parts = urlsplit(value)
            if tag == 'a' and (not parts.netloc or parts.netloc == 'vmsenergy.com') and parts.path.startswith('/'):
                if parts.path.lstrip('/') in NAMES:
                    errors.append((english.name, 'Link leaves English site: ' + value))
    print(json.dumps({'pages': len(PAGES), 'errors': errors, 'inherited_missing': sorted(inherited)}, indent=2, ensure_ascii=False))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
