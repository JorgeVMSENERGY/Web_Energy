"""C1 offline planning, safeguards and recovery simulations."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from audit_s3 import ACCOUNT, BUCKET, COMMIT
from c1_plan import (METADATA_FIELDS, STAGING, build_plan, from_audit,
                     simulate, simulate_recovery, start_simulation)
from deploy_plan import digest


def metadata():
    values = {key: None for key in METADATA_FIELDS}
    values.update(ContentType='application/octet-stream', CacheControl='public,max-age=300,must-revalidate',
                  Metadata={}, Tags={}, StorageClass='STANDARD', ServerSideEncryption='AES256',
                  ACL={'mode': 'bucket-owner-enforced'})
    return {'complete': True, 'values': values}


def audit_fixture(files):
    rows = {}
    for key, body in files.items():
        row = digest(body)
        rows[key] = {'status': 'match', 'git': copy.deepcopy(row), 's3Size': row['size'],
                     'sha256': row['sha256'], 'downloadedBytes': row['size'],
                     'versionBefore': 'v-' + key, 'versionGet': 'v-' + key,
                     'versionAfter': 'v-' + key, 'versionFinal': 'v-' + key}
    count = len(rows)
    return {'schema': 1, 'kind': 'read-only-s3-audit', 'mode': 'content-audit',
            'auditComplete': True, 'applyBlocked': True, 'status': 'matches-git',
            'bucket': BUCKET, 'expectedAccount': ACCOUNT, 'identity': {'account': ACCOUNT},
            'commit': COMMIT, 'expectedCommit': COMMIT, 'finishedAt': '2026-11-10T22:19:33Z',
            'counts': {'matches': count, 'drift': 0, 'missing': 0, 'extra': 0,
                       's3Objects': count, 'objectsHashed': count, 'gitFiles': count}, 'objects': rows}


def setup(files=None):
    files = {'assets/app.css': b'abc', 'doc.pdf': b'old', 'index.html': b'old'} if files is None else files
    audit = audit_fixture(files)
    baseline = from_audit(audit, 'historical-baseline')
    observed = from_audit(audit, 'remote-observation')
    for snapshot in (baseline, observed):
        for row in snapshot['files'].values():
            row['metadata'] = metadata()
    desired = {'schema': 1, 'commit': COMMIT, 'files': {k: digest(v) for k, v in files.items()}}
    return desired, baseline, observed


class PlannerTests(unittest.TestCase):
    def test_noop_and_separate_historical_evidence(self):
        desired, baseline, observed = setup()
        plan = build_plan(desired, baseline, observed)
        self.assertEqual(plan['status'], 'NO-OP')
        self.assertEqual(plan['counts']['UNCHANGED'], 3)
        self.assertEqual(plan['uploads'] + plan['deletes'] + plan['invalidations'], [])
        self.assertTrue(plan['applyBlocked'])
        self.assertFalse(plan['authorization']['granted'])
        self.assertTrue(plan['sameSourceEvidence'])
        self.assertTrue(plan['freshEvidenceRequiredBeforeAnyFutureApply'])
        self.assertFalse(plan['cacheKeyPolicyVerified'])

    def test_css_pdf_html_update_and_precise_invalidation(self):
        for key in ('assets/app.css', 'doc.pdf', 'index.html'):
            desired, baseline, observed = setup()
            desired['files'][key] = digest(b'NEW')  # same byte length as existing file
            plan = build_plan(desired, baseline, observed)
            with self.subTest(key=key):
                self.assertEqual(plan['status'], 'PROPOSED')
                self.assertEqual(plan['uploads'], [key])
                self.assertEqual(plan['counts']['UPDATE'], 1)
                self.assertEqual(plan['counts']['UNCHANGED'], 2)
                self.assertEqual(set(plan['invalidations']), {'/' + key, '/'} if key == 'index.html' else {'/' + key})
                op = plan['operations'][0]
                self.assertEqual(op['previousVersionId'], 'v-' + key)
                self.assertEqual(op['metadataToPreserveOrSet'], observed['files'][key]['metadata'])
                self.assertEqual(plan['recovery']['s3'][0]['previousVersionId'], 'v-' + key)

    def test_create_delete_and_asset_before_html_and_delete(self):
        desired, baseline, observed = setup()
        del desired['files']['doc.pdf']
        desired['files']['assets/new +%.js'] = digest(b'new')
        desired['files']['index.html'] = digest(b'new page')
        plan = build_plan(desired, baseline, observed, {'assets/new +%.js': metadata()})
        self.assertEqual([x['action'] for x in plan['operations']], ['CREATE', 'UPDATE', 'DELETE'])
        self.assertEqual(plan['uploads'], ['assets/new +%.js', 'index.html'])
        self.assertEqual(plan['deletes'], ['doc.pdf'])
        self.assertIn('/assets/new%20%2B%25.js', plan['invalidations'])
        self.assertEqual(plan['operations'][0]['recovery']['action'], 'REMOVE_CREATED_OBJECT')
        self.assertEqual(plan['operations'][2]['recovery']['previousVersionId'], 'v-doc.pdf')
        self.assertEqual(plan['recovery']['cloudFront'], plan['invalidations'])

    def test_same_size_external_drift_and_version_only_change_block(self):
        for change in ('bytes', 'version', 'metadata'):
            desired, baseline, observed = setup()
            row = observed['files']['doc.pdf']
            if change == 'bytes':
                row.update(digest(b'bad'))
            elif change == 'version':
                row['versionId'] = 'external-same-bytes'
            else:
                row['metadata']['values']['CacheControl'] = 'changed'
            plan = build_plan(desired, baseline, observed)
            with self.subTest(change=change):
                self.assertEqual(plan['status'], 'BLOCKED')
                self.assertEqual(plan['classifications']['doc.pdf']['classification'], 'DRIFT')
                self.assertIn('EXTERNAL_DRIFT:doc.pdf', plan['blockers'])
                self.assertNotIn('doc.pdf', plan['uploads'] + plan['deletes'])

    def test_external_addition_and_external_deletion_are_drift(self):
        desired, baseline, observed = setup()
        observed['files']['external.bin'] = copy.deepcopy(observed['files']['doc.pdf'])
        del observed['files']['index.html']
        plan = build_plan(desired, baseline, observed)
        self.assertEqual(plan['counts']['DRIFT'], 2)
        self.assertEqual(plan['classifications']['external.bin']['classification'], 'DRIFT')
        self.assertEqual(plan['classifications']['index.html']['classification'], 'DRIFT')
        self.assertEqual(plan['operations'], [])

    def test_missing_incomplete_or_inconsistent_evidence_is_rejected(self):
        desired, baseline, observed = setup()
        for h, o in ((None, observed), (baseline, None), (None, None)):
            plan = build_plan(desired, h, o)
            self.assertEqual(plan['status'], 'BLOCKED')
            self.assertTrue(plan['applyBlocked'])
            self.assertEqual(plan['operations'], [])
        for mutate in (lambda r: r.update(auditComplete=False),
                       lambda r: r.update(bucket='wrong'),
                       lambda r: r['counts'].update(objectsHashed=0),
                       lambda r: r['objects']['doc.pdf'].update(versionFinal='changed'),
                       lambda r: r['objects']['doc.pdf'].update(downloadedBytes=1),
                       lambda r: r['objects']['doc.pdf'].update(sha256='0'*64)):
            report = audit_fixture({'doc.pdf': b'old'})
            mutate(report)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                from_audit(report, 'historical-baseline')

    def test_staging_existing_noop_but_changes_never_proposed(self):
        for key in STAGING:
            desired, baseline, observed = setup({key: b'old'})
            self.assertEqual(build_plan(desired, baseline, observed)['status'], 'NO-OP')
            for mode in ('update', 'delete', 'create'):
                d, b, o = copy.deepcopy(desired), copy.deepcopy(baseline), copy.deepcopy(observed)
                if mode == 'update':
                    d['files'][key] = digest(b'new')
                elif mode == 'delete':
                    del d['files'][key]
                else:
                    del b['files'][key]
                    del o['files'][key]
                plan = build_plan(d, b, o)
                with self.subTest(key=key, mode=mode):
                    self.assertEqual(plan['status'], 'BLOCKED')
                    self.assertIn('STAGING_EXCLUSIVE:' + key, plan['blockers'])
                    self.assertEqual(plan['operations'], [])

    def test_unknown_metadata_blocks_changes_but_not_historical_noop(self):
        report = audit_fixture({'doc.pdf': b'old'})
        baseline = from_audit(report, 'historical-baseline')
        observed = from_audit(report, 'remote-observation')
        desired = {'schema': 1, 'commit': COMMIT, 'files': {'doc.pdf': digest(b'old')}}
        self.assertFalse(baseline['files']['doc.pdf']['metadata']['complete'])
        self.assertEqual(build_plan(desired, baseline, observed)['status'], 'NO-OP')
        desired['files']['doc.pdf'] = digest(b'new')
        plan = build_plan(desired, baseline, observed)
        self.assertIn('METADATA_UNVERIFIED:doc.pdf', plan['blockers'])
        self.assertEqual(plan['status'], 'BLOCKED')
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            start_simulation(plan, observed, Path(temp) / 'j.json')

    def test_versioned_baseline_and_observation_are_usable_by_cli(self):
        import sys
        from unittest.mock import patch
        from c1_plan import main
        desired, baseline, observed = setup()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'baseline.json').write_text(json.dumps(baseline))
            (root / 'observed.json').write_text(json.dumps(observed))
            output = root / 'plan.json'
            args = ['c1_plan.py', '--baseline', str(root / 'baseline.json'),
                    '--observation', str(root / 'observed.json'), '--output', str(output)]
            with patch.object(sys, 'argv', args), patch('c1_plan.inventory', return_value=desired):
                self.assertEqual(main(), 0)
            plan = json.loads(output.read_text())
            self.assertEqual(plan['status'], 'NO-OP')
            self.assertTrue(plan['applyBlocked'])

    def test_snapshot_with_missing_commit_or_provenance_is_rejected(self):
        desired, baseline, observed = setup()
        for field in ('commit', 'evidence', 'applyBlocked'):
            invalid = copy.deepcopy(baseline)
            invalid.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_plan(desired, invalid, observed)

    def test_invalidation_encoding_and_unsupported_tilde(self):
        key = 'assets/ñ +%?#.pdf'
        desired, baseline, observed = setup({key: b'old'})
        desired['files'][key] = digest(b'new')
        plan = build_plan(desired, baseline, observed)
        self.assertEqual(plan['invalidations'], ['/assets/%C3%B1%20%2B%25%3F%23.pdf'])
        d, b, o = setup({'~.css': b'old'})
        d['files']['~.css'] = digest(b'new')
        self.assertIn('INVALIDATION_PATH_UNSUPPORTED:~.css', build_plan(d, b, o)['blockers'])


class SimulationTests(unittest.TestCase):
    def test_partial_failure_and_idempotent_resume_after_ambiguous_effect(self):
        desired, baseline, observed = setup()
        desired['files']['assets/app.css'] = digest(b'NEW')
        desired['files']['doc.pdf'] = digest(b'NEW')
        plan = build_plan(desired, baseline, observed)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'journal.json'
            start_simulation(plan, observed, path)
            with self.assertRaises(RuntimeError):
                simulate(path, fail_at=('after-effect', 0))
            interrupted = json.loads(path.read_text())
            self.assertEqual(interrupted['steps'][0]['state'], 'intent')
            self.assertEqual(interrupted['simulatedRemote']['assets/app.css']['sha256'], digest(b'NEW')['sha256'])
            done = simulate(path)
            self.assertEqual(done['state'], 'simulated-complete')
            self.assertTrue(done['applyBlocked'])
            self.assertEqual([step['state'] for step in done['steps']], ['done', 'done'])
            self.assertEqual(simulate(path), done)
            self.assertEqual(len(done['simulatedInvalidations']), 1)

    def test_cache_failure_resume_preserves_stable_caller_reference(self):
        desired, baseline, observed = setup()
        desired['files']['index.html'] = digest(b'new')
        plan = build_plan(desired, baseline, observed)
        for mode in ('cache-before', 'cache-after'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'journal.json'
                start_simulation(plan, observed, path)
                with self.assertRaises(RuntimeError):
                    simulate(path, fail_at=(mode, 0))
                done = simulate(path)
                self.assertEqual(done['cacheStep'], 'done')
                self.assertEqual(len(done['simulatedInvalidations']), 1)
                self.assertEqual(simulate(path), done)

    def test_failure_recovery_records_s3_versions_and_separate_cache(self):
        desired, baseline, observed = setup()
        desired['files']['assets/app.css'] = digest(b'NEW')
        desired['files']['doc.pdf'] = digest(b'NEW')
        plan = build_plan(desired, baseline, observed)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'journal.json'
            start_simulation(plan, observed, path)
            with self.assertRaises(RuntimeError):
                simulate(path, fail_at=('after-effect', 0))
            recovered = simulate_recovery(path)
            self.assertEqual(recovered['state'], 'simulated-recovered')
            self.assertEqual(recovered['recovery']['s3'][0]['previousVersionId'], 'v-assets/app.css')
            self.assertEqual(recovered['simulatedRemote']['assets/app.css']['sha256'], digest(b'abc')['sha256'])
            self.assertNotEqual(recovered['simulatedRemote']['assets/app.css']['versionId'], 'v-assets/app.css')
            self.assertEqual(recovered['recovery']['cloudFront'], ['/assets/app.css'])
            self.assertEqual(simulate_recovery(path), recovered)
            with self.assertRaises(ValueError):
                simulate(path)

    def test_unexpected_external_change_and_modified_plan_fail_closed(self):
        desired, baseline, observed = setup()
        desired['files']['doc.pdf'] = digest(b'new')
        plan = build_plan(desired, baseline, observed)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'journal.json'
            start_simulation(plan, observed, path)
            doc = json.loads(path.read_text())
            doc['simulatedRemote']['doc.pdf']['versionId'] = 'outside-writer'
            path.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                simulate(path)
            doc['plan']['uploads'] = ['evil']
            path.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                simulate(path)

    def test_delete_requires_explicit_simulated_grace_and_recovers_version(self):
        desired, baseline, observed = setup()
        del desired['files']['doc.pdf']
        plan = build_plan(desired, baseline, observed)
        self.assertEqual(plan['operations'][0]['phase'], 'DEFERRED_DELETE_AFTER_CACHE_GRACE')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'journal.json'
            start_simulation(plan, observed, path)
            with self.assertRaises(ValueError):
                simulate(path)
            self.assertEqual(json.loads(path.read_text())['steps'][0]['state'], 'pending')
            deleted = simulate(path, assume_deletion_grace=True)
            self.assertNotIn('doc.pdf', deleted['simulatedRemote'])
            self.assertTrue(deleted['simulatedCheckpoints']['deletionGraceAssumed'])
            recovered = simulate_recovery(path)
            self.assertEqual(recovered['simulatedRemote']['doc.pdf']['sha256'], digest(b'old')['sha256'])
            self.assertEqual(recovered['recovery']['s3'][0]['previousVersionId'], 'v-doc.pdf')
            self.assertEqual(recovered['recovery']['cloudFront'], ['/doc.pdf'])

    def test_external_delete_cannot_masquerade_as_our_unacknowledged_effect(self):
        desired, baseline, observed = setup()
        del desired['files']['doc.pdf']
        plan = build_plan(desired, baseline, observed)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'journal.json'
            start_simulation(plan, observed, path)
            with self.assertRaises(RuntimeError):
                simulate(path, fail_at=('before', 0), assume_deletion_grace=True)
            journal = json.loads(path.read_text())
            journal['simulatedRemote'].pop('doc.pdf')
            path.write_text(json.dumps(journal))
            with self.assertRaises(ValueError):
                simulate(path)

    def test_noop_simulation_has_no_writes_or_invalidations(self):
        desired, baseline, observed = setup()
        plan = build_plan(desired, baseline, observed)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'journal.json'
            start_simulation(plan, observed, path)
            journal = simulate(path)
            self.assertEqual(journal['steps'], [])
            self.assertEqual(journal['simulatedInvalidations'], {})
            self.assertEqual(journal['simulatedRemote'], observed['files'])


if __name__ == '__main__':
    unittest.main()
