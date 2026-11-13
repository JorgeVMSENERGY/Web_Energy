"""Offline C2A reader tests. Fake S3 only; no real AWS."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

from audit_s3 import ACCOUNT, BUCKET, COMMIT, AuditError
from c1_plan import fingerprint, from_audit
from c2a_observe import (missing_kind, observe, observe_safe, plan, revalidate,
                         verify_baseline_source, verify_observation)
from deploy_plan import digest
from test_c1_plan import audit_fixture

NOW = datetime(2026, 11, 10, 22, 30, tzinfo=timezone.utc)


class FakeReader:
    def __init__(self, data, page_size=1000):
        self.data = copy.deepcopy(data)
        self.calls = {}
        self.page_size = page_size
        self.gets = []
        self.fail = None
        self.change_head = None
        self.change_field = None
        self.head_count = {}
        self.delete_markers = {}
        self.version_pages = []
    def call(self, service, operation, fields):
        self.calls[operation] = self.calls.get(operation, 0) + 1
        if self.fail == operation:
            raise PermissionError('secret should not appear')
        if operation == 'get-caller-identity':
            return {'Account': ACCOUNT, 'Arn': 'arn:aws:iam::' + ACCOUNT + ':user/fixture'}
        if operation == 'list-objects-v2':
            self.assert_request(fields)
            keys = sorted(self.data)
            start = int(fields.get('ContinuationToken', '0'))
            selected = keys[start:start+self.page_size]
            truncated = start + len(selected) < len(keys)
            result = {'Name': BUCKET, 'EncodingType': 'url', 'IsTruncated': truncated,
                      'KeyCount': len(selected), 'Contents': [{'Key': quote(k, safe='/'), 'Size': len(self.data[k]['body'])} for k in selected]}
            if truncated:
                result['NextContinuationToken'] = str(start + len(selected))
            return result
        if operation == 'head-object':
            self.assert_request(fields)
            key = fields['Key']
            obj = self.data[key]
            self.head_count[key] = self.head_count.get(key, 0) + 1
            version = obj['version']
            if self.change_head == key and self.head_count[key] > 1:
                version = 'changed-during-observation'
            result = {'VersionId': version, 'ETag': obj.get('etag', '"fixture-etag"'),
                      'ContentLength': len(obj['body']), 'ServerSideEncryption': obj.get('sse', 'AES256'),
                      'ContentType': obj.get('contentType', 'text/plain'),
                      'CacheControl': obj.get('cacheControl', 'max-age=300'), 'Metadata': {'x': '1'}}
            result.update(obj.get('extraHead', {}))
            if self.change_field == key and self.head_count[key] > 1:
                result['ContentType'] = 'changed-with-same-version'
            return result
        raise AssertionError(operation)
    def assert_request(self, fields):
        assert fields['Bucket'] == BUCKET and fields['ExpectedBucketOwner'] == ACCOUNT
        assert 'VersionId' not in fields
    def hash_object(self, fields, size, version):
        self.assert_request(fields)
        self.gets.append(fields['Key'])
        self.calls['get-object'] = self.calls.get('get-object', 0) + 1
        if self.fail == 'get-object':
            raise PermissionError('secret should not appear')
        obj = self.data[fields['Key']]
        if obj.get('getVersion', obj['version']) != version:
            raise AuditError('Changed version during GET')
        return {'VersionId': version}, digest(obj['body'])['sha256'], len(obj['body'])
    def list_versions(self, **fields):
        self.calls['list-object-versions'] = self.calls.get('list-object-versions', 0) + 1
        if self.fail == 'list-object-versions':
            raise PermissionError('secret')
        if self.version_pages:
            return self.version_pages.pop(0)
        key = fields['Prefix']
        markers = [] if key not in self.delete_markers else [{'Key': quote(key, safe='/'), 'IsLatest': True, 'VersionId': self.delete_markers[key]}]
        return {'Name': BUCKET, 'EncodingType': 'url', 'IsTruncated': False, 'DeleteMarkers': markers}


def fixture(files=None):
    files = files if files is not None else {'a.css': b'abc', 'index.html': b'old'}
    audit = audit_fixture(files)
    baseline = from_audit(audit, 'historical-baseline')
    desired = {'schema': 1, 'commit': COMMIT, 'files': {key: digest(body) for key, body in files.items()}}
    remote = {key: {'body': body, 'version': 'v-' + key} for key, body in files.items()}
    return baseline, audit, desired, remote


class ObservationTests(unittest.TestCase):
    def collect(self, baseline, remote, **kw):
        reader = FakeReader(remote, page_size=2)
        return observe(reader, baseline, COMMIT, now=lambda: NOW, **kw), reader

    def test_947_same_versions_reuse_audited_sha_without_get(self):
        files = {'assets/%04d ñ +%%.js' % i: b'x' for i in range(947)}
        baseline, audit, desired, remote = fixture(files)
        obs, reader = self.collect(baseline, remote)
        self.assertEqual(len(obs['files']), 947)
        self.assertEqual(reader.gets, [])
        first_row = next(iter(obs['files'].values()))
        self.assertFalse(first_row['headFields']['ChecksumSHA256']['present'])
        self.assertIsNone(first_row['headFields']['ChecksumSHA256']['value'])
        self.assertEqual(reader.calls['head-object'], 1894)
        self.assertEqual(obs['listingPagesInitial'], 474)
        self.assertTrue(all(x['sha256Source'] == 'AUDITED_BASELINE_SAME_KEY_VERSION' for x in obs['files'].values()))
        result = plan(desired, baseline, obs, now=NOW)
        self.assertEqual(result['c1Plan']['counts']['UNCHANGED'], 947)
        self.assertEqual(result['contentComparison'], 'NO-OP')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(result['applyBlocked'])
        self.assertIn('HISTORICAL_METADATA_INCOMPLETE', result['blockers'])
        self.assertNotIn('secret', str(result))

    def test_changed_version_unknown_then_verified_same_sha(self):
        baseline, audit, desired, remote = fixture()
        remote['a.css']['version'] = 'changed'
        obs, reader = self.collect(baseline, remote)
        self.assertIsNone(obs['files']['a.css']['sha256'])
        self.assertEqual(reader.gets, [])
        result = plan(desired, baseline, obs, now=NOW)
        self.assertEqual(result['diagnosis']['a.css']['state'], 'UNKNOWN_VERSION')
        self.assertIn('UNKNOWN_SHA256_REQUIRES_READ_ONLY_GET', result['blockers'])
        self.assertIsNone(result['c1Plan'])
        verified, reader = self.collect(baseline, remote, verify_unknown=True)
        self.assertEqual(reader.gets, ['a.css'])
        result = plan(desired, baseline, verified, now=NOW)
        self.assertEqual(result['diagnosis']['a.css']['state'], 'EXTERNAL_VERSION_SAME_SHA256')
        self.assertEqual(result['c1Plan']['classifications']['a.css']['classification'], 'DRIFT')
        self.assertEqual(baseline['files']['a.css']['versionId'], 'v-a.css')

    def test_same_size_external_content_change_and_metadata_changes(self):
        baseline, audit, desired, remote = fixture()
        remote['a.css'].update(version='new', body=b'bad')
        obs, _ = self.collect(baseline, remote, verify_unknown=True)
        self.assertEqual(plan(desired, baseline, obs, now=NOW)['diagnosis']['a.css']['state'], 'CONTENT_DRIFT')
        for field in ('ContentType', 'CacheControl'):
            local = copy.deepcopy(baseline)
            local['files']['a.css']['metadata']['values'][field] = 'OLD'
            obs, _ = self.collect(local, fixture()[3])
            result = plan(desired, local, obs, now=NOW)
            with self.subTest(field=field):
                self.assertIn(field, result['diagnosis']['a.css']['metadataComparison']['changed'])
                self.assertEqual(result['diagnosis']['a.css']['state'], 'HEAD_METADATA_DRIFT')
                self.assertIn('REMOTE_STATE_DIFFERS_FROM_BASELINE', result['blockers'])

    def test_new_missing_and_delete_marker_are_distinguished(self):
        baseline, audit, desired, remote = fixture()
        del remote['a.css']
        remote['new.bin'] = {'body': b'new', 'version': 'new-v'}
        obs, reader = self.collect(baseline, remote)
        result = plan(desired, baseline, obs, now=NOW)
        self.assertEqual(result['diagnosis']['a.css']['state'], 'MISSING_OR_DELETE_MARKER_UNRESOLVED')
        self.assertEqual(result['diagnosis']['new.bin']['state'], 'EXTERNAL_ADDITION')
        reader = FakeReader(remote)
        reader.delete_markers['a.css'] = 'marker-v'
        obs = observe(reader, baseline, COMMIT, now=lambda: NOW, check_delete_markers=True, verify_unknown=True)
        self.assertEqual(obs['missing']['a.css'], {'state': 'DELETE_MARKER', 'versionId': 'marker-v'})
        self.assertEqual(plan(desired, baseline, obs, now=NOW)['diagnosis']['a.css']['state'], 'DELETE_MARKER')

    def test_delete_marker_pagination_preserves_opaque_version_marker(self):
        reader = FakeReader({})
        reader.version_pages = [
            {'Name': BUCKET, 'EncodingType': 'url', 'IsTruncated': True,
             'NextKeyMarker': 'a%20b', 'NextVersionIdMarker': 'opaque+%2F', 'Versions': []},
            {'Name': BUCKET, 'EncodingType': 'url', 'IsTruncated': False,
             'DeleteMarkers': [{'Key': 'a%20b', 'IsLatest': True, 'VersionId': 'm1'}]}]
        self.assertEqual(missing_kind(reader, 'a b'), {'state': 'DELETE_MARKER', 'versionId': 'm1'})
        self.assertEqual(reader.calls['list-object-versions'], 2)

    def test_delete_marker_change_during_observation_aborts(self):
        baseline, audit, desired, remote = fixture()
        del remote['a.css']
        reader = FakeReader(remote)
        reader.version_pages = [
            {'Name': BUCKET, 'EncodingType': 'url', 'IsTruncated': False,
             'DeleteMarkers': [{'Key': 'a.css', 'IsLatest': True, 'VersionId': 'first'}]},
            {'Name': BUCKET, 'EncodingType': 'url', 'IsTruncated': False,
             'DeleteMarkers': [{'Key': 'a.css', 'IsLatest': True, 'VersionId': 'second'}]}]
        obs = observe_safe(reader, baseline, COMMIT, now=lambda: NOW, check_delete_markers=True)
        self.assertFalse(obs['observationComplete'])
        self.assertEqual(obs['error'], 'Delete marker changed during observation')
        self.assertTrue(obs['applyBlocked'])

    def test_expiry_tampering_and_revalidation_changes(self):
        baseline, audit, desired, remote = fixture()
        obs, _ = self.collect(baseline, remote)
        expired = plan(desired, baseline, obs, now=NOW + timedelta(seconds=300))
        self.assertIn('INVALID_OR_EXPIRED_EVIDENCE', expired['blockers'])
        original = plan(desired, baseline, obs, now=NOW)
        with self.assertRaises(ValueError):
            revalidate(original, obs, baseline, now=NOW)
        fresh = observe(FakeReader(remote), baseline, COMMIT, now=lambda: NOW + timedelta(seconds=1))
        self.assertEqual(revalidate(original, fresh, baseline, now=NOW + timedelta(seconds=2))['status'], 'NO_CHANGE_STILL_BLOCKED')
        different = copy.deepcopy(fresh)
        different['identity']['arn'] = 'arn:aws:iam::' + ACCOUNT + ':user/other'
        different.pop('observationId')
        different['observationId'] = fingerprint(different)
        with self.assertRaises(ValueError):
            revalidate(original, different, baseline, now=NOW + timedelta(seconds=2))
        remote['a.css']['contentType'] = 'text/css'
        changed = observe(FakeReader(remote), baseline, COMMIT, now=lambda: NOW + timedelta(seconds=1))
        check = revalidate(original, changed, baseline, now=NOW + timedelta(seconds=2))
        self.assertEqual(check['status'], 'CHANGED_NEW_PLAN_REQUIRED')
        self.assertEqual(check['changedKeys'], ['a.css'])
        with self.assertRaises(ValueError):
            revalidate(original, fresh, baseline, now=NOW + timedelta(seconds=301))
        forged = copy.deepcopy(fresh)
        forged['files']['a.css']['etag'] = 'changed'
        with self.assertRaises(ValueError):
            verify_observation(forged, baseline, NOW)

    def test_incomplete_permissions_and_version_races_fail_closed(self):
        baseline, audit, desired, remote = fixture()
        for operation in ('get-caller-identity', 'list-objects-v2', 'head-object'):
            reader = FakeReader(remote)
            reader.fail = operation
            result = observe_safe(reader, baseline, COMMIT, now=lambda: NOW)
            with self.subTest(operation=operation):
                self.assertFalse(result['observationComplete'])
                self.assertTrue(result['applyBlocked'])
                self.assertNotIn('secret', str(result))
                self.assertIn('INVALID_OR_EXPIRED_EVIDENCE', plan(desired, baseline, result, now=NOW)['blockers'])
        reader = FakeReader(remote)
        reader.change_head = 'a.css'
        self.assertFalse(observe_safe(reader, baseline, COMMIT, now=lambda: NOW)['observationComplete'])
        reader = FakeReader(remote)
        reader.change_field = 'a.css'
        self.assertFalse(observe_safe(reader, baseline, COMMIT, now=lambda: NOW)['observationComplete'])
        remote['a.css']['version'] = 'new'
        reader = FakeReader(remote)
        reader.fail = 'get-object'
        self.assertFalse(observe_safe(reader, baseline, COMMIT, now=lambda: NOW, verify_unknown=True)['observationComplete'])
        remote['a.css'].update(version='new', getVersion='changed')
        self.assertFalse(observe_safe(FakeReader(remote), baseline, COMMIT, now=lambda: NOW, verify_unknown=True)['observationComplete'])
        del remote['a.css']
        reader = FakeReader(remote)
        reader.fail = 'list-object-versions'
        self.assertFalse(observe_safe(reader, baseline, COMMIT, now=lambda: NOW, check_delete_markers=True)['observationComplete'])
        # An independent retry starts from a new read and remains non-authorizing.
        success, _ = self.collect(baseline, fixture()[3])
        self.assertTrue(success['observationComplete'])
        self.assertTrue(plan(desired, baseline, success, now=NOW)['applyBlocked'])

    def test_conditional_contracts_capture_opaque_etag_and_do_not_authorize(self):
        baseline, audit, desired, remote = fixture()
        desired['files']['a.css'] = digest(b'new bytes')
        obs, _ = self.collect(baseline, remote)
        result = plan(desired, baseline, obs, now=NOW)
        condition = result['preconditionsByKey']['a.css']
        self.assertEqual(condition['action'], 'UPDATE')
        self.assertEqual(condition['ifMatch'], '"fixture-etag"')
        self.assertEqual(condition['expectedVersionId'], 'v-a.css')
        self.assertTrue(condition['freshRevalidationRequired'])
        self.assertFalse(condition['implemented'])
        self.assertTrue(result['applyBlocked'])
        self.assertIn('If-None-Match', result['conditionalContracts']['CREATE'])
        self.assertIn('If-Match', result['conditionalContracts']['DELETE'])

    def test_staging_and_baseline_source_protected(self):
        key = 'catalogo-proyectos.html'
        baseline, audit, desired, remote = fixture({key: b'old'})
        self.assertEqual(verify_baseline_source(baseline, audit), baseline)
        tampered = copy.deepcopy(baseline)
        tampered['files'][key]['sha256'] = '0'*64
        with self.assertRaises(ValueError):
            verify_baseline_source(tampered, audit)
        desired['files'][key] = digest(b'new')
        obs, _ = self.collect(baseline, remote)
        result = plan(desired, baseline, obs, now=NOW)
        self.assertTrue(result['diagnosis'][key]['protectedStaging'])
        self.assertIn('STAGING_EXCLUSIVE:' + key, result['c1Plan']['blockers'])
        self.assertEqual(result['c1Plan']['operations'], [])

    def test_cli_requires_explicit_read_flag_without_constructing_reader(self):
        from c2a_observe import main
        args = ['c2a_observe.py', '--baseline', 'unused', '--baseline-audit', 'unused',
                '--observation-output', 'x', '--plan-output', 'y']
        with patch.object(sys, 'argv', args), patch('c2a_observe.ReadOnlyC2') as reader:
            with self.assertRaises(SystemExit):
                main()
            reader.assert_not_called()


class RealSdkLoopbackTests(unittest.TestCase):
    def test_actual_boto3_observe_and_get_only_loopback(self):
        import importlib.util
        if importlib.util.find_spec('boto3') is None:
            self.skipTest('Install optional Boto3 dependency')
        import boto3
        import os
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import urlsplit
        from c2a_observe import ReadOnlyC2
        from audit_s3 import request
        body = bytes(range(256)) * 20
        key = 'assets/test.bin'
        baseline, audit, desired, _ = fixture({key: body})
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def respond(self, is_head):
                parts = urlsplit(self.path)
                seen.append((self.command, parts.path, parts.query, self.headers.get('x-amz-expected-bucket-owner')))
                if 'list-type=2' in parts.query:
                    content = ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                               '<Name>' + BUCKET + '</Name><EncodingType>url</EncodingType>'
                               '<KeyCount>1</KeyCount><IsTruncated>false</IsTruncated>'
                               '<Contents><Key>' + key + '</Key><Size>' + str(len(body)) + '</Size></Contents>'
                               '</ListBucketResult>').encode()
                elif 'versions' in parts.query:
                    content = ('<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                               '<Name>' + BUCKET + '</Name><EncodingType>url</EncodingType>'
                               '<IsTruncated>false</IsTruncated>'
                               '<DeleteMarker><Key>missing%20%C3%B1%20%2B%25</Key>'
                               '<VersionId>marker-v</VersionId><IsLatest>true</IsLatest></DeleteMarker>'
                               '</ListVersionsResult>').encode()
                else:
                    content = body
                self.send_response(200)
                self.send_header('Content-Length', str(len(content)))
                if 'list-type=2' not in parts.query and 'versions' not in parts.query:
                    self.send_header('x-amz-version-id', 'new-version')
                    self.send_header('x-amz-server-side-encryption', 'AES256')
                    self.send_header('ETag', '"opaque-etag"')
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Cache-Control', 'max-age=300')
                    self.send_header('x-amz-meta-fixture', 'local')
                self.end_headers()
                if not is_head:
                    self.wfile.write(content)
            def do_HEAD(self): self.respond(True)
            def do_GET(self): self.respond(False)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fake = boto3.Session(aws_access_key_id='fixture', aws_secret_access_key='fixture', region_name='us-east-1')
        sts = Mock()
        sts.get_caller_identity.return_value = {'Account': ACCOUNT, 'Arn': 'arn:aws:iam::' + ACCOUNT + ':user/fixture'}
        class LocalSession:
            def client(self, service, **kwargs):
                self_test.assertEqual(kwargs['endpoint_url'], 'https://%s.us-east-1.amazonaws.com' % service)
                if service == 'sts':
                    return sts
                kwargs['endpoint_url'] = 'http://127.0.0.1:%d' % server.server_port
                return fake.client(service, **kwargs)
        self_test = self
        try:
            with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost',
                                        'AWS_EC2_METADATA_DISABLED': 'true'}):
                reader = ReadOnlyC2(Path('.'), 'fixture', session=LocalSession())
                try:
                    obs = observe(reader, baseline, COMMIT, verify_unknown=True, now=lambda: NOW)
                    marker = missing_kind(reader, 'missing \u00f1 +%')
                finally:
                    reader.close()
            self.assertEqual(obs['files'][key]['sha256'], digest(body)['sha256'])
            self.assertEqual(obs['files'][key]['etag'], '"opaque-etag"')
            self.assertEqual(obs['files'][key]['headFields']['ContentType']['value'], 'application/octet-stream')
            self.assertEqual(obs['files'][key]['headFields']['Metadata']['value'], {'fixture': 'local'})
            self.assertEqual(marker, {'state': 'DELETE_MARKER', 'versionId': 'marker-v'})
            self.assertEqual([event[0] for event in seen].count('GET'), 4)  # 2 LIST, body, versions
            self.assertEqual([event[0] for event in seen].count('HEAD'), 2)
            self.assertTrue(all(event[3] == ACCOUNT for event in seen))
            self.assertTrue(all(event[0] in ('GET', 'HEAD') for event in seen))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
