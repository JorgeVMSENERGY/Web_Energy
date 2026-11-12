from pathlib import Path
import subprocess
import tempfile
import unittest

from deploy_plan import (compare_metadata, digest, inventory, load_json, plan,
                         publishable, s3_metadata)


def manifest(values, remote=False):
    doc = {'schema': 1, 'files': {k: digest(v) for k, v in values.items()}}
    if remote:
        doc.update(complete=True, verification='download-sha256')
    return doc


class PlannerTests(unittest.TestCase):
    def test_same_size_new_deleted_unchanged_and_special_paths(self):
        old = {'index.html': b'aa', 'old.js': b'x', 'same': b'z'}
        new = {'index.html': b'bb', 'same': b'z', 'a #?%*ñ.js': b'n'}
        result = plan(manifest(new), manifest(old, True), manifest(old))
        self.assertEqual(result['uploads'], ['a #?%*ñ.js', 'index.html'])
        self.assertEqual(result['deletes'], ['old.js'])
        self.assertEqual(result['unchanged'], ['same'])
        self.assertEqual(result['invalidations'], ['/', '/a%20%23%3F%25%2A%C3%B1.js', '/index.html', '/old.js'])

    def test_drift_missing_and_interrupted_retry(self):
        target = manifest({'a': b'a', 'b': b'b'})
        previous = manifest({'a': b'a', 'b': b'b', 'gone': b'g'})
        actual = manifest({'a': b'x', 'gone': b'g'}, True)
        first = plan(target, actual, previous)
        self.assertEqual(first['uploads'], ['a', 'b'])
        self.assertEqual(first['drift'], ['a', 'b'])
        partial = manifest({'a': b'a', 'gone': b'g'}, True)
        retry = plan(target, partial, previous)
        self.assertEqual(retry['uploads'], ['b'])
        self.assertEqual(retry['deletes'], ['gone'])
        done = plan(target, manifest({'a': b'a', 'b': b'b'}, True), previous)
        self.assertEqual(done['uploads'] + done['deletes'], [])

    def test_missing_corrupt_stale_manifest_and_evidence(self):
        target = manifest({'a': b'a'})
        self.assertTrue(plan(target)['blocked'])
        actual = manifest({'a': b'a'}, True)
        self.assertTrue(plan(target, actual)['manifestRecovery'])
        self.assertEqual(plan(target, actual, manifest({'a': b'x'}))['uploads'], [])
        with self.assertRaises(ValueError):
            plan(target, actual, {'schema': 9})
        actual['complete'] = False
        with self.assertRaises(ValueError):
            plan(target, actual)

    def test_exclusions_unmanaged_and_force(self):
        for key in ['infra/a.tf', 'scripts/x.py', 'README.MD', 'x.csv', 'x.bak2', 'assets/img/solar.zip']:
            self.assertFalse(publishable(key))
        result = plan(manifest({}), manifest({'infra/x': b'x'}, True), force=True)
        self.assertEqual(result['deletes'], [])
        self.assertEqual(result['unmanagedRemote'], ['infra/x'])
        self.assertTrue(result['forceReconcile'])

    def test_unsafe_and_invalid_hash(self):
        for key in ['../x', '/x', 'a//b', 'a\\b', 'a\nx']:
            with self.assertRaises(ValueError):
                plan(manifest({key: b'x'}))
        bad = manifest({'x': b'x'})
        bad['files']['x']['sha256'] = 'etag'
        with self.assertRaises(ValueError):
            plan(bad)

    def test_git_bytes_ignore_checkout_crlf_and_archive_attributes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def run(*args):
                return subprocess.check_output(['git', '-C', temp, *args], stderr=subprocess.STDOUT)
            run('init')
            run('config', 'user.email', 'offline@example.invalid')
            run('config', 'user.name', 'Offline test')
            run('config', 'core.autocrlf', 'false')
            (root / 'index.html').write_bytes(b'a\nb\n')
            (root / 'binary.bin').write_bytes(bytes(range(256)))
            (root / '.gitattributes').write_bytes(b'index.html export-ignore\n')
            run('add', '.')
            run('commit', '-m', 'fixture')
            first = inventory(temp)
            (root / 'index.html').write_bytes(b'a\r\nb\r\n')
            self.assertEqual(first, inventory(temp))
            self.assertEqual(first['files']['index.html'], digest(b'a\nb\n'))
            self.assertEqual(first['files']['binary.bin'], digest(bytes(range(256))))
            (root / 'README.md').write_bytes(b'docs')
            run('add', '.')
            # Restore the tracked HTML before committing documentation only.
            (root / 'index.html').write_bytes(b'a\nb\n')
            run('add', 'index.html')
            run('commit', '-m', 'docs')
            self.assertEqual(first['files'], inventory(temp)['files'])


class MetadataTests(unittest.TestCase):
    def snapshot(self, values, **fields):
        return dict({'Contents': [{'Key': k, 'Size': size} for k, size in values],
                     'IsTruncated': False, 'Prefix': '', 'KeyCount': len(values)}, **fields)

    def test_same_size_different_bytes_never_authorizes_apply(self):
        desired = manifest({'index.html': b'aa'})
        actual = manifest({'index.html': b'bb'}, True)
        report = compare_metadata(desired, self.snapshot([('index.html', 2)]))
        self.assertEqual(report['counts']['sameKeyAndSize'], 1)
        self.assertTrue(report['applyBlocked'])
        self.assertEqual(report['byteEquality'], 'not-verified')
        self.assertNotIn('complete', report)
        self.assertNotIn('verification', report)
        self.assertTrue(plan(desired)['blocked'])
        with self.assertRaises(ValueError):
            plan(desired, report)
        self.assertEqual(plan(desired, actual)['uploads'], ['index.html'])

    def test_categories_sizes_drift_and_special_characters(self):
        key = 'assets/a #?%*\u00f1\U0001f31e.js'
        desired = manifest({key: b'ab', 'same': b'a', 'missing': b'b'})
        report = compare_metadata(desired, self.snapshot([(key, 3), ('same', 1), ('extra', 7)]))
        self.assertEqual(report['counts'], {'gitFiles': 3, 'snapshotObjects': 3,
                         'sameKeyAndSize': 1, 'missingInSnapshot': 1,
                         'extraInSnapshot': 1, 'sizeDifferences': 1})
        self.assertEqual(report['sizeDifferences'], [{'key': key, 'gitSize': 2, 's3Size': 3}])
        self.assertEqual(report['missingInSnapshot'][0]['key'], 'missing')
        self.assertEqual(report['extraInSnapshot'][0]['key'], 'extra')
        self.assertEqual(report, compare_metadata(desired, self.snapshot([('extra', 7), ('same', 1), (key, 3)])))

    def test_incomplete_and_inconsistent_snapshots_fail_closed(self):
        base = self.snapshot([('a', 1)])
        invalid = [dict(base, IsTruncated=True), dict(base, IsTruncated='false'),
                   dict(base, KeyCount=2), dict(base, KeyCount=True),
                   dict(base, MaxKeys=0), dict(base, MaxKeys=-1),
                   dict(base, Prefix='assets/'), dict(base, Delimiter='/'),
                   dict(base, CommonPrefixes=[{'Prefix': 'assets/'}]),
                   dict(base, NextContinuationToken='next'),
                   dict(base, NextContinuationToken=[]), dict(base, CommonPrefixes=''),
                   dict(base, ContinuationToken='previous'), dict(base, NextToken='cli'),
                   dict(base, StartAfter='a'), dict(base, EncodingType='url'),
                   {}, [], None, dict(base, Contents=None),
                   {'Contents': None, 'KeyCount': 0, 'IsTruncated': False}]
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(ValueError):
                s3_metadata(document)

    def test_aggregate_missing_pagination_flag_is_unproven_and_blocked(self):
        report = compare_metadata(manifest({'a': b'a'}), {'Contents': [{'Key': 'a', 'Size': 1}], 'Prefix': ''})
        self.assertEqual(report['coverage'], 'unproven-missing-IsTruncated')
        self.assertTrue(report['applyBlocked'])
        self.assertTrue(plan(manifest({'a': b'a'}))['blocked'])

    def test_malformed_entries_and_duplicate_keys(self):
        entries = [None, [], 'a', {}, {'Key': 'a'}, {'Key': 3, 'Size': 0},
                   {'Key': '', 'Size': 0}, {'Key': '../a', 'Size': 0},
                   {'Key': 'a', 'Size': -1}, {'Key': 'a', 'Size': True},
                   {'Key': 'a', 'Size': 1.0}, {'Key': 'a', 'Size': '1'}]
        for entry in entries:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                s3_metadata({'Contents': [entry], 'IsTruncated': False})
        with self.assertRaises(ValueError):
            s3_metadata(self.snapshot([('a', 1), ('a', 1)]))

    def test_json_invalid_duplicates_nonfinite_and_bom(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'snapshot.json'
            for content in ['{', '{} {}', '{"Contents":[],"Contents":[]}',
                            '{"Contents":[{"Key":"a","Size":1,"Size":2}]}',
                            '{"Size":NaN}', '{"Size":Infinity}']:
                path.write_text(content, encoding='utf-8')
                with self.subTest(content=content), self.assertRaises(ValueError):
                    load_json(path)
            path.write_text('{"Contents": [], "IsTruncated": false}', encoding='utf-8-sig')
            self.assertEqual(s3_metadata(load_json(path)), ({}, True))

    def test_case_sensitive_keys_and_empty_listing(self):
        report = compare_metadata(manifest({'A': b'a'}), self.snapshot([('a', 1)]))
        self.assertEqual(report['counts']['sameKeyAndSize'], 0)
        self.assertEqual(report['counts']['missingInSnapshot'], 1)
        self.assertEqual(report['counts']['extraInSnapshot'], 1)
        self.assertEqual(s3_metadata({'IsTruncated': False, 'KeyCount': 0}), ({}, True))

    def test_unicode_key_differences_are_not_repaired_or_normalized(self):
        desired = manifest({'Dise\u00f1o.png': b'x', 'condici\u00f3n.png': b'x', '\u00e9.png': b'x'})
        snapshot = self.snapshot([('Dise\u00b1o.png', 1), ('condici\u00ben.png', 1), ('e\u0301.png', 1)])
        report = compare_metadata(desired, snapshot)
        self.assertEqual(report['counts']['sameKeyAndSize'], 0)
        self.assertEqual(report['counts']['missingInSnapshot'], 3)
        self.assertEqual(report['counts']['extraInSnapshot'], 3)
        self.assertEqual(report['counts']['sizeDifferences'], 0)

    def test_cli_rejects_invalid_snapshot_without_creating_report(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'snapshot.json'
            output = Path(temp) / 'report.json'
            source.write_text('{"Contents":[],"IsTruncated":true}', encoding='utf-8')
            import sys
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('deploy_plan.py')),
                                     '--s3-metadata', str(source), '--output', str(output)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
