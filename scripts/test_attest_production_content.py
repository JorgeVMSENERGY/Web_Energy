"""Offline fail-closed tests; all Git and AWS interfaces mocked."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import attest_production_content as sut

SHA = 'e' * 40


def manifest():
    return {'sha': SHA, 'files': {'en/index.html': {'sha256': 'x' * 64, 'size': 5}}}


def listings():
    return {'en/index.html': {'size': 5, 'etag': '"abcd"',
                              'modified': '2026-11-19T00:00:00+00:00'}}


def evidence():
    return {'schema': 1, 'kind': 'manual-production-content-attestation',
            'repo': sut.REPOSITORY, 'account': sut.ACCOUNT,
            'bucket': sut.BUCKET, 'commit': SHA,
            'manual_commit': sut.MANUAL_COMMIT, 'bytes': 5,
            'listing': listings(),
            'objects': [{'key': 'en/index.html', 'sha256': 'x'*64, 'size': 5,
                         'etag': '"abcd"', 'version_id': 'version-1'}]}


class AttestationOfflineTests(unittest.TestCase):
    def test_github_identity_accepts_only_manual_main(self):
        env = {'GITHUB_ACTIONS':'true', 'GITHUB_REPOSITORY':sut.REPOSITORY,
               'GITHUB_REF':'refs/heads/main',
               'GITHUB_EVENT_NAME':'workflow_dispatch',
               'GITHUB_WORKFLOW_REF':sut.WORKFLOW_REF,
               'GITHUB_SHA': SHA, 'GITHUB_RUN_ID': '123'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(sut.check_github(), SHA)
            for key in env:
                with self.subTest(missing=key):
                    with patch.dict(os.environ, {key:''}):
                        with self.assertRaises(sut.AttestationError):
                            sut.check_github()
            with patch.dict(os.environ, {'GITHUB_EVENT_NAME':'push'}):
                with self.assertRaises(sut.AttestationError):
                    sut.check_github()

    def test_wrong_identity_role_rejected(self):
        with patch.object(sut, 'aws_json', return_value={
            'Account':sut.ACCOUNT,
            'Arn':'arn:aws:sts::' + sut.ACCOUNT +
                  ':assumed-role/VMSWebGitHubProductionDeploy/session'}):
            with self.assertRaisesRegex(sut.AttestationError, 'WRONG_AWS_ROLE'):
                sut.check_aws_identity()
        with patch.object(sut, 'aws_json', return_value={
            'Account':sut.ACCOUNT,
            'Arn':'arn:aws:sts::' + sut.ACCOUNT +
                  ':assumed-role/' + sut.AUDIT_ROLE + '/session'}):
            sut.check_aws_identity()

    def test_aws_operation_allowlist(self):
        with self.assertRaisesRegex(sut.AttestationError, 'NOT_ALLOWLISTED'):
            sut.aws_json('s3api', 'put-object', '--bucket', sut.BUCKET)
        with self.assertRaisesRegex(sut.AttestationError, 'NOT_ALLOWLISTED'):
            sut.aws_json('cloudfront', 'create-invalidation')

    def test_missing_pagination_token_rejected(self):
        with patch.object(sut, 'aws_json', return_value={
            'KeyCount':0,'IsTruncated':True,'Contents':[]}):
            with self.assertRaisesRegex(sut.AttestationError, 'TOKEN'):
                sut.s3_listing()

    def test_paged_listing_complete(self):
        records = [
            {'KeyCount':1,'IsTruncated':True,'NextContinuationToken':'t1',
             'Contents':[{'Key':'en/index.html','Size':5,'ETag':'"abcd"'}]},
            {'KeyCount':1,'IsTruncated':False,
             'Contents':[{'Key':'home.html','Size':7,'ETag':'"efgh"'}]},
        ]
        with patch.object(sut,'aws_json',side_effect=records) as call:
            obj = sut.s3_listing()
            self.assertEqual(len(obj),2)
            self.assertIn('--continuation-token', call.call_args_list[1].args)

    def test_extra_object_rejected_before_get(self):
        l=listings()
        l['unexpected.html']={'size':5,'etag':'"abcd"'}
        with patch.object(sut, 'aws_json') as call:
            with self.assertRaisesRegex(sut.AttestationError,'S3_KEY_SET'):
                sut.verify_objects(manifest(),listing=l)
            call.assert_not_called()

    def test_sha_mismatch_rejected(self):
        def fake_aws(*args):
            Path(args[-1]).write_bytes(b'abcde')
            return {'ContentLength':5,'ETag':'"abcd"','VersionId':'v1'}
        with patch.object(sut, 'aws_json', side_effect=fake_aws):
            with self.assertRaisesRegex(sut.AttestationError,'OBJECT_SHA256_OR_METADATA_MISMATCH'):
                sut.verify_objects(manifest(),listing=listings())

    def test_missing_version_rejected(self):
        def fake_aws(*args):
            Path(args[-1]).write_bytes(b'abcde')
            return {'ContentLength':5,'ETag':'"abcd"'}
        with patch.object(sut,'aws_json',side_effect=fake_aws):
            with self.assertRaisesRegex(sut.AttestationError,'VERSION_ID_MISSING'):
                sut.verify_objects(manifest(),listing=listings())

    def test_matching_object(self):
        data=b'abcde'
        m=manifest()
        import hashlib
        m['files']['en/index.html']['sha256']=hashlib.sha256(data).hexdigest()
        def fake_aws(*args):
            Path(args[-1]).write_bytes(data)
            return {'ContentLength':5,'ETag':'"abcd"','VersionId':'v1'}
        with patch.object(sut,'aws_json',side_effect=fake_aws):
            out=sut.verify_objects(m,listing=listings())
            self.assertEqual(out['bytes'],5)
            self.assertEqual(out['objects'][0]['version_id'],'v1')

    def test_recheck_version_change_rejected(self):
        with patch.object(sut,'s3_listing',return_value=listings()),\
             patch.object(sut,'aws_json',return_value={
                 'ContentLength':5,'ETag':'"abcd"','VersionId':'version-2'}):
            with self.assertRaisesRegex(sut.AttestationError,'S3_VERSION_CHANGED'):
                sut.recheck_objects(manifest(),evidence())

    def test_recheck_listing_change_rejected(self):
        changed=listings()
        changed['en/index.html']['etag']='"new"'
        with patch.object(sut,'s3_listing',return_value=changed),\
             patch.object(sut,'aws_json') as call:
            with self.assertRaisesRegex(sut.AttestationError,'LISTING_CHANGED'):
                sut.recheck_objects(manifest(),evidence())
            call.assert_not_called()

    def test_recheck_valid(self):
        with patch.object(sut,'s3_listing',return_value=listings()),\
             patch.object(sut,'aws_json',return_value={
                 'ContentLength':5,'ETag':'"abcd"','VersionId':'version-1'}):
            self.assertEqual(sut.recheck_objects(manifest(),evidence()),(1,5))

    def test_recheck_duplicate_evidence_rejected(self):
        e=evidence()
        e['objects'].append(copy.deepcopy(e['objects'][0]))
        with self.assertRaisesRegex(sut.AttestationError,'EVIDENCE_IDENTITY_MISMATCH'):
            sut.recheck_objects(manifest(),e)


if __name__ == '__main__':
    unittest.main()
