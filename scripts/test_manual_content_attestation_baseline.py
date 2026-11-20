"""Offline tests for explicit manual-production content attestation provenance."""
import unittest

import incremental_cutover_baseline as sut

OLD = 'a' * 40
NEW = 'b' * 40
ATTESTED = 'c' * 40


def run_record(run_id, workflow, commit, *, event='workflow_dispatch',
               branch='main', source='JorgeVMSENERGY/Web_Energy', path=None):
    return {
        'id': run_id, 'status': 'completed', 'conclusion': 'success',
        'head_branch': branch, 'head_sha': commit, 'event': event,
        'path': path or '.github/workflows/' + workflow,
        'head_repository': {'full_name': source},
    }


def attestation_jobs(**changes):
    steps = [{'name': name, 'status': 'completed', 'conclusion': 'success'}
             for name in sut.ATTESTATION_STEPS]
    record = {'name': sut.ATTESTATION_JOB, 'status': 'completed',
              'conclusion': 'success', 'steps': steps}
    record.update(changes)
    return [record]


def deployments(workflow, run_id, commit, **kw):
    return run_record(run_id, workflow, commit, event='push', **kw)


class ManualContentAttestationTests(unittest.TestCase):
    def pages(self, attest=None, legacy=None, incremental=None):
        sources = {sut.ATTESTATION: attest or [], sut.LEGACY: legacy or [],
                   sut.INCREMENTAL: incremental or []}
        return lambda workflow, page: sources[workflow] if page == 1 else []

    def choose(self, getter, jobs=None, *, ancestor=None):
        return sut.choose_baseline(getter, jobs or (lambda rid: attestation_jobs()),
                                   ancestor or (lambda older, current: True),
                                   NEW, 999)

    def test_attestation_job_requires_all_four_successful_steps(self):
        self.assertTrue(sut.verified_manual_content_attestation_jobs(attestation_jobs()))
        for required in sut.ATTESTATION_STEPS:
            bad = attestation_jobs()
            bad[0]['steps'] = [x for x in bad[0]['steps'] if x['name'] != required]
            with self.subTest(missing=required):
                self.assertFalse(sut.verified_manual_content_attestation_jobs(bad))
        for conclusion in ('skipped', 'failure', 'cancelled', None):
            bad = attestation_jobs()
            bad[0]['steps'][2]['conclusion'] = conclusion
            with self.subTest(conclusion=conclusion):
                self.assertFalse(sut.verified_manual_content_attestation_jobs(bad))

    def test_invalid_attestation_job_and_duplicate_evidence_rejected(self):
        self.assertFalse(sut.verified_manual_content_attestation_jobs([]))
        self.assertFalse(sut.verified_manual_content_attestation_jobs([{}]))
        self.assertFalse(sut.verified_manual_content_attestation_jobs(
            attestation_jobs(conclusion='skipped')))
        self.assertFalse(sut.verified_manual_content_attestation_jobs(
            attestation_jobs(status='in_progress')))
        self.assertFalse(sut.verified_manual_content_attestation_jobs(
            attestation_jobs() + attestation_jobs()))
        duplicate = attestation_jobs()
        duplicate[0]['steps'].append(dict(duplicate[0]['steps'][0]))
        self.assertFalse(sut.verified_manual_content_attestation_jobs(duplicate))
        with self.assertRaises(sut.BaselineError):
            sut.verified_manual_content_attestation_jobs('not-a-list')

    def test_valid_manual_attestation_beats_older_auto_deploy(self):
        res = self.choose(self.pages(
            legacy=[deployments(sut.LEGACY, 100, OLD)],
            attest=[run_record(200, sut.ATTESTATION, ATTESTED)]))
        self.assertEqual(res['sha'], ATTESTED)
        self.assertEqual(res['run_id'], 200)
        self.assertEqual(res['workflow'], sut.ATTESTATION)

    def test_auto_deploy_newer_than_attestation_still_wins(self):
        res = self.choose(self.pages(
            legacy=[deployments(sut.LEGACY, 100, OLD)],
            attest=[run_record(200, sut.ATTESTATION, ATTESTED)],
            incremental=[deployments(sut.INCREMENTAL, 300, OLD)]),
            jobs=lambda rid: (
                [{'name': sut.PLAN_JOB, 'status': 'completed', 'conclusion': 'success',
                  'steps': [{'name': 'Plan offline SHA-256 changes',
                             'status': 'completed', 'conclusion': 'success'}]},
                 {'name': sut.DEPLOY_JOB, 'status': 'completed', 'conclusion': 'success'}]
                if rid == 300 else attestation_jobs()))
        self.assertEqual((res['workflow'], res['run_id']), (sut.INCREMENTAL, 300))

    def test_reject_attestation_triggered_by_push(self):
        res = self.choose(self.pages(
            legacy=[deployments(sut.LEGACY, 100, OLD)],
            attest=[run_record(200, sut.ATTESTATION, ATTESTED, event='push')]))
        self.assertEqual(res['sha'], OLD)

    def test_reject_wrong_branch_repository_or_workflow_path(self):
        good = run_record(200, sut.ATTESTATION, ATTESTED)
        invalid = [
            dict(good, head_branch='staging'),
            dict(good, head_repository={'full_name': 'other/repo'}),
            dict(good, path='.github/workflows/wrong.yml'),
            dict(good, path='.github/workflows/' + sut.ATTESTATION + '@feature'),
            dict(good, conclusion='failure'),
        ]
        for item in invalid:
            with self.subTest(item=item):
                res = self.choose(self.pages(
                    legacy=[deployments(sut.LEGACY, 100, OLD)], attest=[item]))
                self.assertEqual(res['sha'], OLD)

    def test_reject_attestation_without_git_ancestry(self):
        res = self.choose(self.pages(
            legacy=[deployments(sut.LEGACY, 100, OLD)],
            attest=[run_record(200, sut.ATTESTATION, ATTESTED)]),
            ancestor=lambda sha, current: sha == OLD)
        self.assertEqual(res['sha'], OLD)

    def test_reject_partial_attestation_but_preserve_old_baseline(self):
        broken = attestation_jobs()
        broken[0]['steps'][0]['status'] = 'skipped'
        res = self.choose(self.pages(
            legacy=[deployments(sut.LEGACY, 100, OLD)],
            attest=[run_record(200, sut.ATTESTATION, ATTESTED)]),
            jobs=lambda rid: broken)
        self.assertEqual(res['sha'], OLD)

    def test_only_invalid_attestation_cannot_create_baseline(self):
        with self.assertRaisesRegex(sut.BaselineError, 'No confirmed'):
            self.choose(self.pages(
                attest=[run_record(200, sut.ATTESTATION, ATTESTED, event='push')]))

    def test_accepted_attestation_paths_only_main(self):
        for suffix in ('', '@main', '@refs/heads/main'):
            self.assertTrue(sut.path_matches(
                '.github/workflows/' + sut.ATTESTATION + suffix, sut.ATTESTATION))
        self.assertFalse(sut.path_matches(
            '.github/workflows/' + sut.ATTESTATION + '@dev', sut.ATTESTATION))


if __name__ == '__main__':
    unittest.main()
