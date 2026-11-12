"""Offline coverage of safe legacy-to-incremental history cutover."""
import json
import os
import unittest
from unittest.mock import patch

import incremental_cutover_baseline as sut

OLD = 'a' * 40
NEW = 'b' * 40
BAD = 'c' * 40


def record(rid, workflow, sha=OLD, **changes):
    row = {
        'id': rid, 'status': 'completed', 'conclusion': 'success',
        'head_branch': 'main', 'event': 'push',
        'path': '.github/workflows/' + workflow,
        'head_repository': {'full_name': sut.REPO}, 'head_sha': sha
    }
    row.update(changes)
    return row


def jobs(plan='success', deploy='skipped', step='success'):
    return [
        {'name': sut.PLAN_JOB, 'status': 'completed', 'conclusion': plan,
         'steps': [{'name': 'Plan offline SHA-256 changes', 'status': 'completed',
                    'conclusion': step}]},
        {'name': sut.DEPLOY_JOB, 'status': 'completed', 'conclusion': deploy}
    ]


def pages(old=None, new=None):
    def get(workflow, page):
        if page != 1:
            return []
        return old or [] if workflow == sut.LEGACY else new or []
    return get


class CutoverBaselineTests(unittest.TestCase):
    def choose(self, getter, *, verify=None, ancestor=None, current_run=100):
        return sut.choose_baseline(
            getter, verify or (lambda rid: jobs()),
            ancestor or (lambda old, current: old == OLD),
            NEW, current_run)

    def test_bootstrap_uses_old_successful_workflow(self):
        out = self.choose(pages(old=[record(60, sut.LEGACY)]))
        self.assertEqual((out['workflow'], out['run_id']), (sut.LEGACY, 60))

    def test_new_successful_run_wins(self):
        out = self.choose(pages(old=[record(60, sut.LEGACY)],
                                new=[record(88, sut.INCREMENTAL)]),
                          verify=lambda rid: jobs(deploy='success'))
        self.assertEqual((out['workflow'], out['run_id']), (sut.INCREMENTAL, 88))

    def test_new_noop_is_valid_after_plan(self):
        out = self.choose(pages(old=[record(60, sut.LEGACY)],
                                new=[record(80, sut.INCREMENTAL)]))
        self.assertEqual(out['run_id'], 80)

    def test_disabled_gated_runs_cannot_be_baseline(self):
        out = self.choose(pages(old=[record(60, sut.LEGACY)],
                                new=[record(90, sut.INCREMENTAL)]),
                          verify=lambda rid: jobs(plan='skipped'))
        self.assertEqual((out['workflow'], out['run_id']), (sut.LEGACY, 60))

    def test_missing_or_skipped_plan_step_rejected(self):
        for evidence in (jobs(step='skipped'), [], [{'name': sut.PLAN_JOB} ],
                         jobs(deploy='failure')):
            with self.subTest(evidence=evidence):
                self.assertFalse(sut.verified_incremental_jobs(evidence))

    def test_wrong_repository_or_workflow_reference_ignored(self):
        for wrong in (
            record(80, sut.INCREMENTAL, path='.github/workflows/other.yml'),
            record(80, sut.INCREMENTAL, path='.github/workflows/' + sut.INCREMENTAL + '@dev'),
            record(80, sut.INCREMENTAL, head_repository={'full_name':'other/repo'}),
            record(80, sut.INCREMENTAL, event='pull_request'),
        ):
            with self.subTest(wrong=wrong):
                out = self.choose(pages(old=[record(60, sut.LEGACY)], new=[wrong]))
                self.assertEqual(out['run_id'], 60)

    def test_accepted_path_main_refs(self):
        for suffix in ('', '@main', '@refs/heads/main'):
            for workflow in sut.WORKFLOWS:
                self.assertTrue(sut.path_matches('.github/workflows/' + workflow + suffix,
                                                 workflow))

    def test_failed_newer_run_not_used(self):
        new = [record(95, sut.INCREMENTAL, sha=BAD),
               record(90, sut.INCREMENTAL, conclusion='failure'),
               record(85, sut.INCREMENTAL)]
        out = self.choose(pages(old=[record(60, sut.LEGACY)], new=new))
        self.assertEqual(out['run_id'], 85)

    def test_second_page_and_limit(self):
        first = [record(300-i, sut.LEGACY, sha=BAD) for i in range(100)]
        def getter(workflow, page):
            if workflow == sut.INCREMENTAL:
                return []
            if page == 1:
                return first
            return [record(180, sut.LEGACY)]
        out = self.choose(getter)
        self.assertEqual(out['run_id'], 180)
        with self.assertRaisesRegex(sut.BaselineError, 'History scan exhausted'):
            self.choose(lambda w, p: [record(1000 * p + i + 1, sut.LEGACY, sha=BAD)
                                        for i in range(100)] if w == sut.LEGACY else [],
                        current_run=999)

    def test_duplicate_run_fails_closed(self):
        first = [record(i + 1, sut.LEGACY, sha=BAD) for i in range(100)]
        with self.assertRaisesRegex(sut.BaselineError, 'Duplicate'):
            self.choose(lambda w, p: first if w == sut.LEGACY else [])

    def test_no_ancestor_fails_closed(self):
        with self.assertRaisesRegex(sut.BaselineError, 'No confirmed'):
            self.choose(pages(old=[], new=[]))

    def test_read_only_gh_routes(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'offline'}):
            with patch.object(sut, 'run', return_value=b'{"workflow_runs":[]}') as call:
                self.assertEqual(sut.fetch_runs(sut.LEGACY, 1), [])
                args = call.call_args.args[0]
                self.assertEqual(args[:4], ['gh', 'api', '--method', 'GET'])
                self.assertIn('status=success', args[4])
            with patch.object(sut, 'run', return_value=b'{"total_count":2,"jobs":[{},{}]}') as call:
                self.assertEqual(len(sut.fetch_jobs(42)), 2)
                self.assertEqual(call.call_args.args[0][:4],
                                 ['gh', 'api', '--method', 'GET'])
        with self.assertRaisesRegex(sut.BaselineError, 'Unsafe'):
            sut.fetch_runs('../bad', 1)

    def test_missing_jobs_is_hard_failure(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'offline'}):
            with patch.object(sut, 'run', return_value=b'{"total_count":2,"jobs":[{}]}'):
                with self.assertRaisesRegex(sut.BaselineError, 'Incomplete workflow jobs'):
                    sut.fetch_jobs(44)


if __name__ == '__main__':
    unittest.main()
