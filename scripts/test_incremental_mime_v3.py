"""Offline regression for explicit VMS website MIME parity (no AWS network)."""
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import incremental_deploy as publisher


EXPECTED = {
    '.css': 'text/css',
    '.html': 'text/html',
    '.ico': 'image/vnd.microsoft.icon',
    '.ini': 'binary/octet-stream',
    '.jpg': 'image/jpeg',
    '.js': 'text/javascript',
    '.json': 'application/json',
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
    '.txt': 'text/plain',
    '.webp': 'image/webp',
    '.xml': 'application/xml',
    '.zip': 'application/zip',
}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args],
                                   stderr=subprocess.DEVNULL).decode('utf-8').strip()


class IncrementalMimeV3Tests(unittest.TestCase):
    def create_repo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name)
        git(repo, 'init', '-q')
        git(repo, 'config', 'user.email', 'offline@example.invalid')
        git(repo, 'config', 'user.name', 'Offline')
        (repo / 'index.html').write_text('before', encoding='utf-8')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', 'before')
        previous = git(repo, 'rev-parse', 'HEAD')
        return repo, previous

    def test_all_fourteen_production_content_types(self):
        self.assertEqual(publisher.CONTENT_TYPES, EXPECTED)
        for suffix, mime in EXPECTED.items():
            self.assertEqual(publisher.content_type_for('assets/file' + suffix), mime)
        self.assertEqual(publisher.content_type_for('ASSETS/PHOTO.PDF'), 'application/pdf')

    def test_unknown_extension_fails_closed(self):
        for key in ('assets/unknown.newtype', 'assets/extensionless'):
            with self.subTest(key=key):
                with self.assertRaisesRegex(publisher.DeployError, 'Unapproved Content-Type'):
                    publisher.content_type_for(key)

    def test_plan_blocks_unapproved_extension(self):
        repo, previous = self.create_repo()
        file = repo / 'assets' / 'unknown.newtype'
        file.parent.mkdir()
        file.write_text('payload', encoding='utf-8')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', 'new unknown type')
        plan = publisher.make_plan(repo, previous, 'HEAD')
        self.assertEqual(plan['status'], 'BLOCKED')
        self.assertIn('UNSUPPORTED_CONTENT_TYPE', plan['blockers'])
        with patch.object(publisher, 'aws') as aws:
            with self.assertRaises(publisher.DeployError):
                publisher.apply(repo, plan)
            aws.assert_not_called()

    def test_mime_mismatch_rejected_before_download(self):
        expected = {'sha256': '0'*64, 'size': 7}
        metadata = {
            'ContentLength': 7,
            'ServerSideEncryption': 'AES256',
            'CacheControl': publisher.CACHE_CONTROL,
            'ContentType': 'application/octet-stream',
            'VersionId': 'v1',
        }
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(publisher, 'aws_json', return_value=metadata) as getter:
                with self.assertRaisesRegex(publisher.DeployError, 'metadata'):
                    publisher.verify_s3_content('assets/site.css', expected, temp)
                getter.assert_called_once()

    def test_upload_has_explicit_content_type(self):
        repo, previous = self.create_repo()
        path = repo / 'assets' / 'site.js'
        path.parent.mkdir()
        path.write_text('alert(1)', encoding='utf-8')
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', 'new javascript')
        plan = publisher.make_plan(repo, previous, 'HEAD')
        self.assertEqual(plan['status'], 'PROPOSED')
        calls = []
        def fake_aws(*args):
            calls.append(args)
            if args[:2] == ('cloudfront', 'create-invalidation'):
                return b'ID-mime-v3\n'
            return b''
        with patch.object(publisher, 'verify_workflow_identity') as identity, \
             patch.object(publisher, 'verify_s3_content') as verify, \
             patch.object(publisher, 'aws', side_effect=fake_aws):
            publisher.apply(repo, plan)
            identity.assert_called_once()
            verify.assert_called_once()
        copies = [c for c in calls if c[:2] == ('s3', 'cp')]
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0][copies[0].index('--content-type') + 1], 'text/javascript')
        self.assertNotIn('--delete', copies[0])


if __name__ == '__main__':
    unittest.main()
