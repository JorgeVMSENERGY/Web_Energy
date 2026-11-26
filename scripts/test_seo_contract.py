"""Offline checks of the shared 404, source generation and deployment boundary."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import build_english
import build_seo_redirects
from deploy_plan import publishable

ROOT = Path(__file__).resolve().parents[1]
BASE = 'a6545272976e671cf5824a51a3eed2e80c8d3456'


def git_read(*args):
    # CI mounts the reviewed checkout read-only under a different container UID.
    # Trust only this explicit checkout for reads, never global safe.directory=*.
    return subprocess.check_output(['git', '-c', 'safe.directory=' + str(ROOT), *args], cwd=ROOT)


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class SeoContractTests(unittest.TestCase):
    def test_generated_source_and_quota(self):
        actual = (ROOT / 'infra/site/functions/unified-viewer-request.js').read_bytes()
        self.assertEqual(actual, build_seo_redirects.render().encode('utf-8'))
        self.assertLessEqual(len(actual), 10240)
        self.assertIsNotNone(build_english.esprima, 'Install the existing esprima requirement for static parsing')
        build_english.esprima.parseScript(actual.decode('utf-8'))

    def test_exact_approved_rules_checksum(self):
        rules = json.loads(build_seo_redirects.DATA.read_text(encoding='utf-8'))['redirects']
        self.assertEqual(len(rules), 24)
        canonical = json.dumps(rules, sort_keys=True, separators=(',', ':')).encode('utf-8')
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), '9c64ae7b2bad550dfa6d19a963cdfbb7f2db14a73db9cb517cfebeec7dfa2e71')

    def test_generator_rejects_duplicate_and_unsafe_targets(self):
        with self.assertRaises(ValueError):
            build_seo_redirects.unique_pairs([('a', 1), ('a', 2)])
        data = json.loads(build_seo_redirects.DATA.read_text(encoding='utf-8'))
        data['redirects']['/nosotros'] = 'https://evil.example/'
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'rules.json'
            source.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(build_seo_redirects, 'DATA', source), self.assertRaises(ValueError):
                build_seo_redirects.render()

    def test_sitemap_and_robots_byte_unchanged(self):
        for name in ('sitemap.xml', 'robots.txt', 'infra/site/functions/redirects.js'):
            expected = git_read('show', BASE + ':' + name)
            # Compare repository-normalized newlines as checkout may be CRLF.
            self.assertEqual((ROOT / name).read_bytes().replace(b'\r\n', b'\n'), expected.replace(b'\r\n', b'\n'))
        urls = [node.text for node in ET.fromstring((ROOT / 'sitemap.xml').read_bytes()).iter() if node.tag.endswith('}loc')]
        self.assertEqual(len(urls), 174)
        self.assertEqual(len(set(urls)), 174)
        self.assertEqual(sum('/en/' in url for url in urls), 87)
        for target in ('lp-celdas-media-tension.html', 'nosotros.html'):
            self.assertIn('https://vmsenergy.com/' + target, urls)
        forbidden = ['404.html', 'directorio.html', 'aviso-privacidad.html', 'codigo-conducta.html', 'buzon-sugerencias.html']
        rules = json.loads(build_seo_redirects.DATA.read_text(encoding='utf-8'))['redirects']
        forbidden += [key.lstrip('/') for key in rules]
        for name in forbidden:
            self.assertNotIn('https://vmsenergy.com/' + name, urls)

    def test_404_is_bilingual_unindexed_and_assets_are_root_relative(self):
        text = (ROOT / '404.html').read_text(encoding='utf-8')
        document = Document(text)
        metas = [a for t, a in document.tags if t == 'meta']
        self.assertIn({'name': 'robots', 'content': 'noindex,follow'}, metas)
        self.assertFalse(any(a.get('http-equiv', '').lower() == 'refresh' for a in metas))
        links = [a.get('href') for t, a in document.tags if t == 'a']
        self.assertIn('/', links)
        self.assertIn('/en/', links)
        for tag, attrs in document.tags:
            if tag == 'script':
                self.fail('The shared error page does not need script dependencies')
            for attr in ('href', 'src'):
                if attr in attrs:
                    value = attrs[attr]
                    self.assertTrue(value.startswith('/') or value.startswith('#'), value)
                    if value.startswith('/') and value != '/':
                        self.assertTrue((ROOT / value.lstrip('/')).exists(), value)

    def test_404_is_excluded_from_future_english_generation_even_when_tracked(self):
        with patch.object(build_english, 'TRACKED', build_english.TRACKED | {'404.html'}):
            self.assertNotIn(ROOT / '404.html', build_english.source_pages())

    def test_deployment_boundary(self):
        self.assertTrue(publishable('404.html'))
        self.assertTrue(publishable('tokens.css'))
        for parent in ('infra/site/functions', 'scripts/seo'):
            for file in (ROOT / parent).rglob('*'):
                if file.is_file():
                    self.assertFalse(publishable(file.relative_to(ROOT).as_posix()), file)
        self.assertFalse(publishable('scripts/test_seo_routing.cjs'))
        self.assertFalse(publishable('scripts/preview_seo.cjs'))

    def test_error_mapping_and_activation_are_disabled_by_default(self):
        errors = (ROOT / 'infra/site/seo-errors.tf').read_text(encoding='utf-8')
        self.assertEqual(errors.count('default     = false'), 2)
        routing = (ROOT / 'infra/site/redirects.tf').read_text(encoding='utf-8')
        self.assertIn('default     = false', routing)
        self.assertIn('VMSWebEnglishIndexRewrite', routing)
        self.assertNotIn('resource "aws_cloudfront_function" "redirects"', routing)

    @staticmethod
    def parse_release_switches(source):
        allowed = {
            'seo_unified_routing_enabled',
            'seo_custom_404_enabled',
            'seo_map_s3_403_to_404',
        }

        assignments = {}

        for number, raw in enumerate(source.splitlines(), 1):
            line = raw.strip()

            if not line or line.startswith('#'):
                continue

            match = re.fullmatch(
                r'([A-Za-z_][A-Za-z0-9_]*)[ \\t]*=[ \\t]*(true|false)',
                line,
            )

            if not match:
                raise ValueError(
                    f'Invalid release assignment at line {number}'
                )

            name, value = match.groups()

            if name not in allowed:
                raise ValueError(f'Unknown release switch: {name}')

            if name in assignments:
                raise ValueError(f'Duplicate release switch: {name}')

            assignments[name] = value == 'true'

        if set(assignments) != allowed:
            raise ValueError('Missing required release switches')

        if (
            assignments['seo_map_s3_403_to_404']
            and not assignments['seo_custom_404_enabled']
        ):
            raise ValueError('403 conversion requires custom 404')

        return assignments

    def test_persisted_release_switches_are_valid_and_trackable(self):
        source = (ROOT / 'infra/site/seo.auto.tfvars').read_text(
            encoding='utf-8'
        )
        self.parse_release_switches(source)

        ignore = git_read(
            'check-ignore', '--no-index', '-v',
            'infra/site/seo.auto.tfvars'
        ).decode()

        self.assertIn('!infra/site/seo.auto.tfvars', ignore)
        self.assertFalse(publishable('infra/site/seo.auto.tfvars'))

    def test_release_switches_accept_all_valid_activation_and_rollback_states(self):
        from itertools import product

        names = (
            'seo_unified_routing_enabled',
            'seo_custom_404_enabled',
            'seo_map_s3_403_to_404',
        )

        accepted = 0
        rejected = 0

        for values in product((False, True), repeat=3):
            source = '# Approved release configuration\n' + '\n'.join(
                f'{name} = {str(value).lower()}'
                for name, value in zip(names, values)
            )

            with self.subTest(values=values):
                if values[2] and not values[1]:
                    with self.assertRaises(ValueError):
                        self.parse_release_switches(source)
                    rejected += 1
                else:
                    self.assertEqual(
                        self.parse_release_switches(source),
                        dict(zip(names, values)),
                    )
                    accepted += 1

        self.assertEqual(accepted, 6)
        self.assertEqual(rejected, 2)

    def test_release_switches_reject_invalid_assignments(self):
        baseline = (
            'seo_unified_routing_enabled = false\n'
            'seo_custom_404_enabled = false\n'
            'seo_map_s3_403_to_404 = false\n'
        )

        self.assertEqual(
            self.parse_release_switches(baseline),
            {
                'seo_unified_routing_enabled': False,
                'seo_custom_404_enabled': False,
                'seo_map_s3_403_to_404': False,
            },
        )

        invalid = {
            'unknown-seo-variable':
                baseline + 'seo_extra = true\n',
            'unknown-non-seo-variable':
                baseline + 'aws_profile = true\n',
            'secret-assignment':
                baseline + 'api_token = "example-only"\n',
            'duplicate-same-value':
                baseline + 'seo_unified_routing_enabled = false\n',
            'duplicate-conflicting-value':
                baseline + 'seo_unified_routing_enabled = true\n',
            'missing-switch':
                baseline.replace('seo_custom_404_enabled = false\n', ''),
            'quoted-boolean':
                baseline.replace('false', '"false"', 1),
            'numeric-value':
                baseline.replace('false', '0', 1),
            'uppercase-boolean':
                baseline.replace('false', 'False', 1),
            'null-value':
                baseline.replace('false', 'null', 1),
            'dynamic-expression':
                baseline.replace('false', 'var.other', 1),
            'function-expression':
                baseline.replace('false', 'tobool("false")', 1),
            'collection-value':
                baseline.replace('false', '[false]', 1),
            'additional-block':
                baseline + 'unexpected {}\n',
            'trailing-expression':
                baseline.replace('false', 'false || true', 1),
            'multiple-assignments':
                baseline.replace('\n', ' ', 1),
            'invalid-error-dependency':
                baseline.replace(
                    'seo_map_s3_403_to_404 = false',
                    'seo_map_s3_403_to_404 = true'
                ),
            'empty-source': '',
        }

        for label, source in invalid.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    self.parse_release_switches(source)


if __name__ == '__main__':
    unittest.main()
