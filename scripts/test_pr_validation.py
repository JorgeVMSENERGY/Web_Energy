"""Regression tests for PR guards; all AWS transports must reject before I/O."""
import copy
from pathlib import Path
import tempfile
import unittest
import os
import subprocess
from unittest.mock import Mock, patch

from audit_s3 import AuditError, AwsCli, request
from audit_s3_boto3 import Boto3Reader
from c2a_observe import ReadOnlyC2
from validate_pr_diff import classify


class PrGuards(unittest.TestCase):
    def test_full_history_detached_merge_checkout_uses_pr_head_diff(self):
        """Model checkout's merge HEAD with base advancing after PR divergence."""
        import json
        import sys
        script = Path(__file__).with_name('validate_pr_diff.py').resolve()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            source.mkdir()
            def git(*args, repo=source):
                return subprocess.check_output(['git', '-C', str(repo), *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            git('config', 'user.email', 'offline@example.invalid')
            git('config', 'user.name', 'Offline fixture')
            (source / 'catalogo-proyectos.html').write_text('historical')
            git('add', '.')
            git('commit', '-m', 'base')
            git('branch', 'base')
            git('checkout', '-b', 'feature')
            (source / 'index.html').write_text('PR change')
            git('add', '.')
            git('commit', '-m', 'PR head')
            head = git('rev-parse', 'HEAD')
            git('checkout', 'base')
            # A base-only change to a protected page must not be attributed to PR.
            (source / 'catalogo-proyectos.html').write_text('upstream change')
            git('add', '.')
            git('commit', '-m', 'base advanced')
            base = git('rev-parse', 'HEAD')
            git('merge', '--no-ff', 'feature', '-m', 'synthetic PR merge')
            merge = git('rev-parse', 'HEAD')
            checkout = Path(temp) / 'checkout'
            subprocess.check_call(['git', 'clone', '--no-local', '--quiet',
                                   str(source), str(checkout)], stderr=subprocess.DEVNULL)
            git('checkout', '--detach', merge, repo=checkout)
            self.assertEqual(git('rev-parse', '--is-shallow-repository', repo=checkout), 'false')
            env = dict(os.environ, BASE_SHA=base, HEAD_SHA=head)
            env.pop('GITHUB_STEP_SUMMARY', None)
            result = subprocess.run([sys.executable, str(script)], cwd=checkout,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['changedPaths'], ['index.html'])
            self.assertEqual(report['stagingExclusiveChanges'], [])

    def test_yaml_contract(self):
        import yaml
        doc = yaml.load((Path(__file__).resolve().parents[1] /
                         '.github/workflows/validate-pr.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(doc['on'], {'pull_request': {'branches': ['main']}})
        self.assertEqual(doc['permissions'], {'contents': 'read'})
        job = doc['jobs']['validate-pr']
        self.assertEqual(job['name'], 'PR offline validation')
        self.assertNotIn('if', job)
        self.assertNotIn('environment', job)
        steps = job['steps']
        self.assertTrue(all('continue-on-error' not in step and 'if' not in step for step in steps))
        checkout = steps[0]
        self.assertEqual(checkout['with']['persist-credentials'], 'false')
        self.assertEqual(checkout['with']['fetch-depth'], '0')
        self.assertIn('--network none', steps[-1]['run'])
        self.assertIn('readonly', steps[-1]['run'])
        self.assertIn('PR_OFFLINE_STRICT=1', steps[-1]['run'])

    def test_real_pr_diff_ignores_historical_pages_and_detects_deletion(self):
        script = Path(__file__).with_name('validate_pr_diff.py').resolve()
        import sys
        with tempfile.TemporaryDirectory() as temp:
            def git(*args):
                return subprocess.check_output(['git', '-C', temp, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            git('config', 'user.email', 'offline@example.invalid')
            git('config', 'user.name', 'Offline fixture')
            page = Path(temp) / 'catalogo-proyectos.html'
            page.write_text('historical')
            git('add', '.')
            git('commit', '-m', 'base')
            base = git('rev-parse', 'HEAD')
            env = dict(os.environ, BASE_SHA=base, HEAD_SHA=base)
            env.pop('GITHUB_STEP_SUMMARY', None)
            def run():
                return subprocess.run([sys.executable, str(script)], cwd=temp, env=env,
                                      capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            page.rename(Path(temp) / 'renamed.html')
            git('add', '-A')
            git('commit', '-m', 'rename protected page')
            env['HEAD_SHA'] = git('rev-parse', 'HEAD')
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('staging-exclusive', result.stdout)
            control = Path(temp) / '.github/workflows/example.yml'
            control.parent.mkdir(parents=True)
            control.write_text('original')
            git('add', '.')
            git('commit', '-m', 'established control')
            env['BASE_SHA'] = git('rev-parse', 'HEAD')
            control.unlink()
            git('add', '-A')
            git('commit', '-m', 'remove control')
            env['HEAD_SHA'] = git('rev-parse', 'HEAD')
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('Established validation controls changed', result.stdout)

    def test_only_changed_pages_block_not_historical_existence(self):
        self.assertEqual(classify(['index.html'])[0], [])
        for page in ('catalogo-proyectos.html', 'proyectos-vms.html'):
            self.assertEqual(classify([page])[0], [page])

    def test_sensitive_paths_and_validation_tests_are_reported(self):
        paths = ['.github/workflows/validate-pr.yml',
                 'scripts/deploy-aws-production.ps1', 'scripts/audit_s3.py',
                 'scripts/c1_plan.py', 'scripts/c2a_observe.py',
                 'scripts/run_pr_offline.py', 'scripts/validate_pr_diff.py',
                 'scripts/audit_s3_boto3.py', 'scripts/audit_performance.py',
                 'scripts/deploy_plan.py', 'scripts/git_batch.py',
                 'scripts/c1_simulator.py', 'infra/forms/lambda/receiver/app.py',
                 'scripts/test_c1_plan.py', 'infra/forms/tests/test_turnstile.py']
        self.assertEqual(classify(paths)[1], sorted(paths))

    def test_writes_rejected_before_cli_or_sdk_transport(self):
        writes = [('s3api', 'put-object'), ('s3api', 'delete-object'),
                  ('s3api', 'delete-objects'), ('s3api', 'copy-object'),
                  ('s3', 'sync'), ('cloudfront', 'create-invalidation'),
                  ('sts', 'assume-role')]
        with tempfile.TemporaryDirectory() as temp:
            readers = [AwsCli(Path(temp), 'unused')]
            # Do not construct sessions or discover credentials.
            for kind in (Boto3Reader, ReadOnlyC2):
                reader = object.__new__(kind)
                reader.clients = {'s3': Mock(), 'sts': Mock()}
                reader.preflight = False
                readers.append(reader)
            with patch('audit_s3.subprocess.run') as transport:
                for reader in readers:
                    for service, operation in writes:
                        with self.subTest(reader=type(reader).__name__, operation=operation):
                            with self.assertRaises(AuditError):
                                reader.call(service, operation, request(Key='fixture'))
                transport.assert_not_called()
            for reader in readers[1:]:
                for client in reader.clients.values():
                    self.assertEqual(client.mock_calls, [])

    def test_c1_c2a_remain_blocked_for_noop_and_change(self):
        from test_c1_plan import setup
        from c1_plan import build_plan
        from deploy_plan import digest
        from test_c2a_observe import fixture, FakeReader, NOW
        from c2a_observe import observe, plan
        desired, baseline, observed = setup()
        for changed in (False, True):
            target = copy.deepcopy(desired)
            if changed:
                target['files']['index.html'] = digest(b'changed')
            result = build_plan(target, baseline, observed)
            self.assertIs(result['applyBlocked'], True)
            self.assertIs(result['authorization']['granted'], False)
        baseline, _, desired, remote = fixture()
        observation = observe(FakeReader(remote), baseline, desired['commit'], now=lambda: NOW)
        for changed in (False, True):
            target = copy.deepcopy(desired)
            if changed:
                target['files']['index.html'] = digest(b'changed')
            self.assertIs(observation['applyBlocked'], True)
            self.assertIs(plan(target, baseline, observation, now=NOW)['applyBlocked'], True)
