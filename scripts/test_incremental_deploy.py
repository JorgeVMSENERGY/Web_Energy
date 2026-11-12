"""Offline tests: synthetic Git repositories and mocked AWS only."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import incremental_deploy as subject


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args]).decode('utf-8').strip()


class IncrementalDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 'ci@example.invalid')
        git(self.repo, 'config', 'user.name', 'Offline CI')
        self.write('index.html', '<h1>Hello</h1>')
        self.write('assets/site.css', 'body{color:red}')
        self.commit('first')
        self.base = git(self.repo, 'rev-parse', 'HEAD')

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def commit(self, message):
        git(self.repo, 'add', '--all')
        git(self.repo, 'commit', '-qm', message)
        return git(self.repo, 'rev-parse', 'HEAD')

    def plan(self, current=None):
        return subject.make_plan(self.repo, self.base, current or 'HEAD')

    def test_noop_same_commit(self):
        p = self.plan()
        self.assertEqual(p['status'], 'NO-OP')
        self.assertFalse(p['operations'])
        self.assertFalse(p['invalidationPaths'])

    def test_docs_only_noop(self):
        self.write('docs/NOTICE.md', '# documentation')
        self.commit('documentation only')
        self.assertEqual(self.plan()['status'], 'NO-OP')

    def test_update_same_size_sha_sensitive(self):
        self.write('assets/site.css', 'body{color:tan}')  # same length as red
        self.commit('modify css')
        p = self.plan()
        self.assertEqual(p['status'], 'PROPOSED')
        self.assertEqual(p['counts'], {'CREATE': 0, 'UPDATE': 1, 'DELETE': 0})
        self.assertEqual(p['operations'][0]['key'], 'assets/site.css')
        self.assertEqual(p['invalidationPaths'], ['/assets/site.css*'])

    def test_create_asset(self):
        self.write('assets/cv 2026.pdf', 'new-pdf-bytes')
        self.commit('add pdf')
        p = self.plan()
        self.assertEqual(p['counts']['CREATE'], 1)
        self.assertIn('/assets/cv%202026.pdf*', p['invalidationPaths'])

    def test_html_conservative_global_invalidation(self):
        self.write('index.html', '<h1>Next</h1>')
        self.commit('change html')
        self.assertEqual(self.plan()['invalidationPaths'], ['/*'])

    def test_delete_fails_closed(self):
        (self.repo / 'assets/site.css').unlink()
        self.commit('delete css')
        p = self.plan()
        self.assertEqual(p['status'], 'BLOCKED')
        self.assertEqual(p['counts']['DELETE'], 1)
        self.assertIn('DELETIONS_REQUIRE_SEPARATE_APPROVAL', p['blockers'])

    def test_staging_pages_cannot_change(self):
        self.write('catalogo-proyectos.html', 'staging change')
        self.commit('add protected html')
        p = self.plan()
        self.assertEqual(p['status'], 'BLOCKED')
        self.assertIn('PROTECTED_STAGING_PAGE_CHANGED', p['blockers'])

    def test_target_must_be_head(self):
        self.write('assets/site.css', 'body{color:tan}')
        self.commit('new')
        with self.assertRaises(subject.DeployError):
            subject.make_plan(self.repo, self.base, self.base)

    def test_blob_staging_preserves_bytes(self):
        new_content = 'ñ +%? Unicode and binary: '.encode('utf-8') + bytes(range(256))
        path = self.repo / 'assets' / 'bín-+.pdf'
        path.write_bytes(new_content)
        self.commit('add binary')
        op = next(op for op in self.plan()['operations'] if op['key'].endswith('.pdf'))
        with tempfile.TemporaryDirectory() as temp:
            staged = subject.stage_blob(self.repo, git(self.repo, 'rev-parse', 'HEAD'), op, temp)
            self.assertEqual(staged.read_bytes(), new_content)

    def test_checksum_rejects_tampering(self):
        p = self.plan()
        path = Path(self.temp.name) / 'plan.json'
        path.write_text(json.dumps(p), encoding='utf-8')
        self.assertEqual(subject.load_plan(path), p)
        p['counts']['CREATE'] = 7
        path.write_text(json.dumps(p), encoding='utf-8')
        with self.assertRaises(subject.DeployError):
            subject.load_plan(path)

    def test_apply_outside_ci_rejected_before_aws(self):
        self.write('assets/site.css', 'body{color:tan}')
        self.commit('modify css')
        p = self.plan()
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            with patch.object(subject, 'aws_json') as remote:
                with self.assertRaises(subject.DeployError):
                    subject.apply(self.repo, p)
                remote.assert_not_called()

    def test_apply_only_changed_file_with_mock_aws(self):
        self.write('assets/site.css', 'body{color:tan}')
        self.commit('modify css')
        p = self.plan()
        calls = []
        def fake_aws(*args):
            calls.append(args)
            if args[:2] == ('cloudfront', 'create-invalidation'):
                return b'ID-1234\n'
            return b''
        with patch.object(subject, 'verify_workflow_identity') as identity, \
             patch.object(subject, 'verify_s3_content') as verifier, \
             patch.object(subject, 'aws', side_effect=fake_aws):
            subject.apply(self.repo, p)
        identity.assert_called_once()
        verifier.assert_called_once()
        copies = [cmd for cmd in calls if cmd[:2] == ('s3', 'cp')]
        self.assertEqual(len(copies), 1)
        self.assertIn('assets/site.css', copies[0][3])
        self.assertFalse(any('delete' in ' '.join(cmd) for cmd in calls))
        self.assertIn('/assets/site.css*', calls[-2])

    def test_unknown_commit_history_blocked(self):
        # Rewind and create a different lineage.
        git(self.repo, 'reset', '--hard', 'HEAD~0')
        self.write('assets/site.css', 'body{color:blue}')
        self.commit('new current')
        other = Path(self.temp.name) / 'unrelated'
        other.mkdir()
        git(other, 'init', '-q')
        git(other, 'config', 'user.email', 'other@example.invalid')
        git(other, 'config', 'user.name', 'Other')
        (other / 'file.txt').write_text('other', encoding='utf-8')
        git(other, 'add', '.')
        git(other, 'commit', '-qm', 'other')
        alien = git(other, 'rev-parse', 'HEAD')
        with self.assertRaises(subject.DeployError):
            subject.make_plan(self.repo, alien, 'HEAD')


if __name__ == '__main__':
    unittest.main()
