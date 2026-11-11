"""Real Boto3 HTTP transport to loopback only; no AWS credential discovery."""
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
from urllib.parse import quote, unquote, urlsplit

from audit_s3 import ACCOUNT, BUCKET, COMMIT, AuditError, audit, request
from audit_s3_boto3 import Boto3Reader
from deploy_plan import digest


@unittest.skipUnless(importlib.util.find_spec('boto3'), 'Install scripts/requirements-audit.txt')
class Boto3Tests(unittest.TestCase):
    def execute(self, mode='success', workers=4, limit=None, preflight=False):
        import boto3
        data = {'a': bytes(range(256)) * 5000, 'empty': b'',
                'assets/ñ +%20#?.bin': b'\x00\xff\r\n'}
        if mode == 'parallel-denied':
            data = {'file-%03d' % i: b'x' for i in range(20)}
        if preflight:
            data = {'file-%04d' % i: b'x' for i in range(947)}
        seen, bodies, readers = [], [], []
        counts = Counter()
        lock = threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def respond(self, head=False):
                parts = urlsplit(self.path)
                key = unquote(parts.path)[len('/' + BUCKET + '/'):]
                listing = 'list-type=2' in parts.query
                with lock:
                    seen.append((self.command, self.path, self.client_address[1]))
                    counts[(self.command, key)] += 1
                    count = counts[(self.command, key)]
                if listing:
                    body = ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                            '<Name>' + BUCKET + '</Name><EncodingType>url</EncodingType>'
                            '<KeyCount>' + str(len(data)) + '</KeyCount><IsTruncated>false</IsTruncated>' +
                            ''.join('<Contents><Key>' + quote(k, safe='/') + '</Key><Size>' + str(len(v)) + '</Size></Contents>' for k,v in data.items()) +
                            '</ListBucketResult>').encode()
                else:
                    body = data[key]
                    if mode == 'drift' and not head:
                        body = bytes((b ^ 1) for b in body)
                if mode == 'listing-change' and listing and count == 2:
                    body = body.replace(b'<Size>0</Size>', b'<Size>1</Size>')
                if mode in ('denied', 'parallel-denied') and not listing and not head:
                    self.send_response(403)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                if not listing:
                    version = 'changed' if ((mode == 'get-version' and not head) or
                              (mode == 'after-version' and head and count == 2) or
                              (mode == 'final-version' and head and count == 3)) else 'v1'
                    self.send_header('x-amz-version-id', version)
                    self.send_header('x-amz-server-side-encryption', 'AES256')
                self.end_headers()
                if not head:
                    self.wfile.write(body)
            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    pass  # Expected when a cancelled streaming read closes early.
            def do_GET(self):
                self.respond()
            def do_HEAD(self):
                self.respond(True)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        session = boto3.Session(aws_access_key_id='fixture', aws_secret_access_key='fixture', region_name='us-east-1')
        sts = Mock()
        sts.get_caller_identity.return_value = {'Account': ACCOUNT, 'Arn': 'arn:aws:iam::' + ACCOUNT + ':user/offline'}
        def client(service, **kwargs):
            self.assertEqual(kwargs['region_name'], 'us-east-1')
            self.assertEqual(kwargs['endpoint_url'], 'https://%s.us-east-1.amazonaws.com' % service)
            self.assertEqual(kwargs['config'].max_pool_connections, workers)
            if service == 'sts':
                return sts
            kwargs['endpoint_url'] = 'http://127.0.0.1:%d' % server.server_port
            c = session.client(service, **kwargs)
            # Capture streaming resources without replacing SDK reads/parsing.
            original = c.get_object
            def get(**fields):
                self.assertEqual(fields['ExpectedBucketOwner'], ACCOUNT)
                self.assertNotIn('VersionId', fields)
                response = original(**fields)
                bodies.append(response['Body'])
                return response
            c.get_object = get
            return c
        factory_session = Mock()
        factory_session.client.side_effect = client
        def factory(work, profile, **kwargs):
            reader = Boto3Reader(work, profile, session=factory_session, **kwargs)
            readers.append(reader)
            return reader
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo = root / 'repo'
                repo.mkdir()
                def git(repo_arg, *args):
                    return (str(repo) if args[-1] == '--show-toplevel' else COMMIT).encode()
                desired = {'schema': 1, 'commit': COMMIT, 'files': {k: digest(v) for k,v in data.items()}}
                with patch('audit_s3.git', side_effect=git), patch('audit_s3_boto3.Boto3Reader', side_effect=factory):
                    report, code = audit(repo, root / 'report.json', backend='boto3', workers=workers,
                                         max_objects=limit, preflight=preflight, temp_root=root,
                                         inventory_fn=lambda _: desired)
                self.assertEqual(json.loads((root / 'report.json').read_text()), report)
                self.assertEqual(list(root.glob('vms-s3-audit-*')), [])
                self.assertTrue(report['applyBlocked'])
                self.assertTrue(all(body._raw_stream.closed for body in bodies))
                sts.close.assert_called_once()
                return report, code, seen, readers
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_parallel_binary_unicode_empty_and_final_verification(self):
        report, code, seen, _ = self.execute()
        self.assertEqual(code, 0, report.get('error'))
        self.assertTrue(report['auditComplete'])
        self.assertEqual(report['counts']['matches'], 3)
        self.assertEqual(report['readCalls'], {'get-caller-identity': 1, 'list-objects-v2': 2, 'head-object': 9, 'get-object': 3})
        self.assertEqual(report['objects']['a']['sha256'], hashlib.sha256(bytes(range(256))*5000).hexdigest())
        # Persistent HTTP/1.1 connections are reused for more than one operation.
        self.assertLess(len({port for _,_,port in seen}), len(seen))

    def test_version_changes_before_after_and_final_fail_closed(self):
        for mode in ('get-version', 'after-version', 'final-version'):
            with self.subTest(mode=mode):
                report, code, _, readers = self.execute(mode)
                self.assertEqual(code, 1)
                self.assertFalse(report['auditComplete'])

    def test_failure_stops_admission_and_does_not_refill(self):
        report, code, seen, readers = self.execute('denied', workers=1)
        self.assertEqual(code, 1)
        self.assertFalse(report['auditComplete'])
        self.assertEqual(report['readCalls']['get-object'], 1)
        reader = readers[0]
        count = dict(reader.calls)
        with self.assertRaises(AuditError):
            reader.call('s3api', 'head-object', request(Key='a'))
        self.assertEqual(reader.calls, count)

    def test_parallel_failure_never_admits_more_than_initial_window(self):
        report, code, seen, _ = self.execute('parallel-denied', workers=4)
        self.assertEqual(code, 1)
        self.assertFalse(report['auditComplete'])
        self.assertLessEqual(report['readCalls']['head-object'], 4)
        self.assertLessEqual(report['readCalls'].get('get-object', 0), 4)
        self.assertEqual(report['readCalls']['list-objects-v2'], 1)

    def test_final_listing_change_aborts(self):
        report, code, _, _ = self.execute('listing-change')
        self.assertEqual(code, 1)
        self.assertFalse(report['auditComplete'])
        self.assertIn('keys/sizes changed', report['error'])

    def test_hash_drift_is_reported_in_full_and_sample_modes(self):
        for limit in (None, 1):
            report, code, _, _ = self.execute('drift', limit=limit)
            self.assertEqual(code, 2)
            self.assertGreater(report['counts']['drift'], 0)
            self.assertEqual(report['auditComplete'], limit is None)
            self.assertEqual(report['status'], 'differences-found' if limit is None else 'sample-differences')

    def test_sample_never_complete_even_if_limit_exceeds_inventory(self):
        for limit in (1, 100):
            report, code, _, _ = self.execute(limit=limit)
            self.assertEqual(code, 0)
            self.assertEqual(report['status'], 'sample-verified')
            self.assertFalse(report['auditComplete'])
            self.assertEqual(report['readCalls']['get-object'], min(limit, 3))

    def test_preflight_947_does_not_head_or_get(self):
        report, code, _, _ = self.execute(preflight=True)
        self.assertEqual(code, 0, report.get('error'))
        self.assertTrue(report['preflightComplete'])
        self.assertFalse(report['auditComplete'])
        self.assertNotIn('get-object', report['readCalls'])
        self.assertNotIn('head-object', report['readCalls'])

    def test_validation_rejects_invalid_workers_and_sample(self):
        for options in ({'workers': 0}, {'workers': 5}, {'workers': 2},
                        {'max_objects': 0}, {'max_objects': 1, 'preflight': True}):
            with self.subTest(options=options), patch('audit_s3.git') as git:
                with self.assertRaises(AuditError):
                    audit('.', 'unused', **options)
                git.assert_not_called()

    def test_sdk_allowlist_owner_version_and_preflight_guards(self):
        reader = object.__new__(Boto3Reader)
        reader.preflight = False
        reader.stop = threading.Event()
        reader.gate = threading.Lock()
        reader.calls = {}
        reader.clients = {'s3': Mock(), 'sts': Mock()}
        for service, op, fields in [('s3api', 'put-object', request(Key='a')),
                                     ('s3api', 'head-object', dict(request(Key='a'), Bucket='wrong')),
                                     ('s3api', 'head-object', dict(request(Key='a'), ExpectedBucketOwner='wrong')),
                                     ('s3api', 'head-object', request(Key='a', VersionId='old'))]:
            with self.assertRaises(AuditError):
                reader.call(service, op, fields)
        reader.preflight = True
        with self.assertRaises(AuditError):
            reader.call('s3api', 'head-object', request(Key='a'))
        with self.assertRaises(AuditError):
            reader.hash_object(request(Key='a'), 1, 'v1')
        self.assertEqual(reader.calls, {})
        reader.clients['s3'].assert_not_called()

    def test_stream_failure_closes_body_and_stops(self):
        for mode in ('short', 'long', 'exception', 'interrupt', 'metadata'):
            reader = object.__new__(Boto3Reader)
            reader.stop = threading.Event()
            body = Mock()
            body.read.side_effect = ([b'x', b''] if mode == 'short' else
                                     [b'xxx'] if mode == 'long' else
                                     OSError('secret') if mode == 'exception' else
                                     KeyboardInterrupt() if mode == 'interrupt' else [b'xx', b''])
            response = {'Body': body, 'VersionId': 'wrong' if mode == 'metadata' else 'v1',
                        'ContentLength': 2, 'ServerSideEncryption': 'AES256'}
            reader._read = Mock(return_value=response)
            with self.subTest(mode=mode), self.assertRaises(BaseException):
                reader.hash_object(request(Key='a'), 2, 'v1')
            body.close.assert_called_once()
            self.assertTrue(reader.stop.is_set())


if __name__ == '__main__':
    unittest.main()
