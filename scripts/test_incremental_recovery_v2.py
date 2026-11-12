"""Offline failure/retry contract for the preexisting incremental publisher.

Uses actual Git blobs and mocked AWS; no credentials or network access.
"""
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import incremental_deploy as publisher


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args],
                                   stderr=subprocess.DEVNULL).decode().strip()


class IncrementalRecoveryV2Tests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.repo = Path(work.name)
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'offline@example.invalid')
        git(self.repo, 'config', 'user.name', 'Offline')
        self.write('assets/a.css', 'body{color:red}')
        self.write('assets/b.css', 'body{color:red}')
        self.commit('before')
        self.previous = git(self.repo, 'rev-parse', 'HEAD')
        self.write('assets/a.css', 'body{color:tan}')
        self.write('assets/b.css', 'body{color:tan}')
        self.commit('after')
        self.plan = publisher.make_plan(self.repo, self.previous, 'HEAD')
        self.assertEqual(self.plan['status'], 'PROPOSED')
        self.assertEqual(self.plan['counts']['UPDATE'], 2)

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(content, encoding='utf-8')

    def commit(self, message):
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', message)

    def _run(self, cloud):
        with patch.object(publisher, 'verify_workflow_identity') as identity, \
             patch.object(publisher, 'verify_s3_content') as verify, \
             patch.object(publisher, 'aws', side_effect=cloud):
            publisher.apply(self.repo, self.plan)
            identity.assert_called_once()
            self.assertEqual(verify.call_count, 2)

    def test_mid_upload_failure_does_not_invalidate_and_retry_replays(self):
        first_calls = []
        def first(*args):
            first_calls.append(args)
            if args[:2] == ('s3', 'cp') and sum(
                    call[:2] == ('s3', 'cp') for call in first_calls) == 2:
                raise publisher.DeployError('simulated upload interruption')
            return b''
        with self.assertRaisesRegex(publisher.DeployError, 'interruption'):
            self._run(first)
        self.assertEqual(sum(call[:2] == ('s3', 'cp') for call in first_calls), 2)
        self.assertFalse(any(call[:2] == ('cloudfront', 'create-invalidation')
                             for call in first_calls))
        retry_calls = []
        def retry(*args):
            retry_calls.append(args)
            if args[:2] == ('cloudfront', 'create-invalidation'):
                return b'ID-retry\n'
            return b''
        self._run(retry)
        self.assertEqual(sum(call[:2] == ('s3', 'cp') for call in retry_calls), 2)
        self.assertEqual(sum(call[:2] == ('cloudfront', 'create-invalidation')
                             for call in retry_calls), 1)

    def test_failed_invalidation_does_not_claim_deployment_success(self):
        first_calls = []
        def first(*args):
            first_calls.append(args)
            if args[:2] == ('cloudfront', 'create-invalidation'):
                raise publisher.DeployError('simulated cache failure')
            return b''
        with self.assertRaisesRegex(publisher.DeployError, 'cache failure'):
            self._run(first)
        self.assertEqual(sum(call[:2] == ('s3', 'cp') for call in first_calls), 2)
        self.assertEqual(sum(call[:2] == ('cloudfront', 'create-invalidation')
                             for call in first_calls), 1)
        retry_calls = []
        def retry(*args):
            retry_calls.append(args)
            if args[:2] == ('cloudfront', 'create-invalidation'):
                return b'ID-retry\n'
            return b''
        self._run(retry)
        self.assertEqual(sum(call[:2] == ('s3', 'cp') for call in retry_calls), 2)
        self.assertEqual(sum(call[:2] == ('cloudfront', 'create-invalidation')
                             for call in retry_calls), 1)

    def test_failed_wait_retries_and_reverifies_all_objects(self):
        calls = []
        def first(*args):
            calls.append(args)
            if args[:2] == ('cloudfront', 'create-invalidation'):
                return b'ID-first\n'
            if args[:2] == ('cloudfront', 'wait'):
                raise publisher.DeployError('invalidation timeout')
            return b''
        with self.assertRaisesRegex(publisher.DeployError, 'timeout'):
            self._run(first)
        self.assertEqual(sum(call[:2] == ('s3', 'cp') for call in calls), 2)
        self.assertEqual(sum(call[:2] == ('cloudfront', 'wait') for call in calls), 1)
        self.assertEqual(publisher.make_plan(self.repo, self.previous, 'HEAD'), self.plan)


if __name__ == '__main__':
    unittest.main()
