"""Offline tests for performance attribution and fail-closed diagnostics."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from audit_performance import Performance
from audit_s3 import write_report
from deploy_plan import inventory


class PerformanceTests(unittest.TestCase):
    def test_clock_attribution_failure_and_no_exception_text(self):
        with patch('audit_performance.perf_counter', side_effect=[0, 10, 13, 20, 25]):
            perf = Performance()
            with perf.measure('ok', key='a'):
                pass
            with self.assertRaises(RuntimeError), perf.measure('failure', key='a'):
                raise RuntimeError('credential-secret')
        self.assertEqual(perf.data['phases']['ok']['totalSeconds'], 3)
        self.assertEqual(perf.data['objects']['a']['ok']['totalSeconds'], 3)
        self.assertEqual(perf.data['phases']['failure']['failed'], 1)
        self.assertEqual(perf.data['phases']['failure']['totalSeconds'], 5)
        self.assertNotIn('credential-secret', json.dumps(perf.data))

    def test_thread_safe_counts_and_totals(self):
        perf = Performance()
        def work():
            for _ in range(1000):
                perf.add('parallel', 0.25, key='a')
        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for row in (perf.data['phases']['parallel'], perf.data['objects']['a']['parallel']):
            self.assertEqual(row, {'count': 4000, 'failed': 0, 'totalSeconds': 1000, 'maxSeconds': 0.25})

    def test_inventory_instrumentation_keeps_same_git_commands_and_bytes(self):
        from test_git_batch import Process, response
        oid = b'a' * 40
        outputs = [oid + b'\n', b'100644 blob ' + oid + b' 2\tindex.html\0', oid + b'\n']
        def batch_process(*args, **kwargs):
            return Process(response(b'\x00\xff'))
        with patch('deploy_plan.git', side_effect=outputs) as plain, patch('git_batch.subprocess.Popen', side_effect=batch_process):
            baseline = inventory('.')
            calls = plain.call_args_list
        perf = Performance()
        with patch('deploy_plan.git', side_effect=outputs) as observed, patch('git_batch.subprocess.Popen', side_effect=batch_process):
            timed = inventory('.', performance=perf)
        self.assertEqual(timed, baseline)
        self.assertEqual(observed.call_args_list, calls)
        for phase, count in [('git.rev-parse', 2), ('git.ls-tree', 1),
                             ('git.cat-file', 1), ('git.sha256', 2)]:
            self.assertEqual(perf.data['phases'][phase]['count'], count)

    def test_report_roundtrip_includes_payload_write_and_serialization(self):
        perf = Performance()
        report = {'auditComplete': False, 'applyBlocked': True,
                  'diagnostics': {'performance': perf.data}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'report.json'
            write_report(path, report)
            self.assertEqual(json.loads(path.read_text()), report)
            self.assertEqual(list(Path(temp).glob('.s3-audit-*')), [])
        for phase in ('report.payload_serialization', 'report.payload_write_flush_fsync'):
            self.assertEqual(perf.data['phases'][phase]['count'], 1)

    def test_listing_decode_failure_is_timed(self):
        from audit_s3 import AuditError, BUCKET, listing
        from unittest.mock import Mock
        perf = Performance()
        cli = Mock()
        cli.call.return_value = {'IsTruncated': False, 'Name': BUCKET, 'KeyCount': 1,
                                 'EncodingType': 'url', 'Contents': [{'Key': '%GG', 'Size': 1}]}
        with self.assertRaises(AuditError):
            listing(cli, perf, 'listing.initial')
        self.assertEqual(perf.data['phases']['listing.initial.decode_validate']['failed'], 1)
        self.assertEqual(perf.data['phases']['listing.initial.request']['count'], 1)

    def test_write_failure_leaves_incomplete_report_and_cleans_temp(self):
        perf = Performance()
        report = {'auditComplete': False, 'applyBlocked': True,
                  'diagnostics': {'performance': perf.data}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'report.json'
            path.write_text('{"auditComplete":false,"applyBlocked":true}')
            with patch('audit_s3.os.fsync', side_effect=OSError('secret')), self.assertRaises(OSError):
                write_report(path, report)
            self.assertFalse(json.loads(path.read_text())['auditComplete'])
            self.assertEqual(list(Path(temp).glob('.s3-audit-*')), [])
        self.assertEqual(perf.data['phases']['report.payload_write_flush_fsync']['failed'], 1)
        self.assertNotIn('secret', json.dumps(perf.data))


@unittest.skipUnless(importlib.util.find_spec('boto3'), 'Install scripts/requirements-audit.txt')
class PerformanceIntegrationTests(unittest.TestCase):
    def execute(self, **kwargs):
        from test_audit_s3_boto3 import Boto3Tests
        return Boto3Tests().execute(**kwargs)

    def test_sample_reports_all_executed_phases_without_changing_read_counts(self):
        report, code, _, _ = self.execute(limit=1)
        self.assertEqual(code, 0)
        self.assertFalse(report['auditComplete'])
        self.assertTrue(report['applyBlocked'])
        self.assertEqual(report['readCalls'], {'get-caller-identity': 1, 'list-objects-v2': 2,
                                              'head-object': 3, 'get-object': 1})
        data = report['diagnostics']['performance']
        self.assertGreater(data['elapsedBeforeReportSeconds'], 0)
        for phase in ('git.root', 'git.head', 'git.inventory', 'boto3.initialization',
                      'boto3.imports', 'boto3.session', 'boto3.client.s3', 'boto3.client.sts',
                      'sts.identity', 'listing.initial.request', 'listing.initial.decode_validate',
                      'listing.final.request', 'listing.final.decode_validate', 'inventory.final_compare',
                      'sdk.signing_credentials', 'objects.batch_wall', 'head.final_batch_wall',
                      'report.payload_serialization', 'report.payload_write_flush_fsync'):
            self.assertGreater(data['phases'][phase]['count'], 0, phase)
            self.assertGreaterEqual(data['phases'][phase]['totalSeconds'], 0)
        phases = data['objects']['a']
        for phase in ('head.initial', 'get.total', 'get.response_headers', 'get.first_chunk_upper_bound',
                      'get.streaming', 'get.body_read', 'get.sha256', 'get.size_validation',
                      'head.after', 'head.final', 'get.body_close'):
            self.assertIn(phase, phases)
        self.assertLessEqual(phases['get.response_headers']['totalSeconds'], phases['get.first_chunk_upper_bound']['totalSeconds'])
        self.assertLessEqual(phases['get.first_chunk_upper_bound']['totalSeconds'], phases['get.total']['totalSeconds'])
        self.assertEqual(phases['get.body_read']['count'], 3)  # 1 MiB, remainder, EOF

    def test_failed_get_retains_numeric_diagnostics_and_blocked_state(self):
        report, code, _, _ = self.execute(mode='denied', workers=1)
        self.assertEqual(code, 1)
        self.assertFalse(report['auditComplete'])
        self.assertTrue(report['applyBlocked'])
        phases = report['diagnostics']['performance']['phases']
        self.assertEqual(phases['sdk.get-object']['failed'], 1)
        self.assertEqual(phases['get.total']['failed'], 1)
        self.assertEqual(phases['objects.batch_wall']['failed'], 1)
        self.assertNotIn('listing.final', phases)
        self.assertIn('report.payload_write_flush_fsync', phases)

    def test_preflight_reports_no_body_or_head_timings(self):
        report, code, _, _ = self.execute(preflight=True)
        self.assertEqual(code, 0)
        self.assertFalse(report['auditComplete'])
        phases = report['diagnostics']['performance']['phases']
        self.assertIn('inventory.preflight_compare', phases)
        self.assertNotIn('get.total', phases)
        self.assertNotIn('head.initial', phases)

    def test_initialization_failure_is_saved_without_sensitive_error_text(self):
        from audit_s3 import COMMIT, audit
        from unittest.mock import Mock
        session = Mock()
        session.client.side_effect = RuntimeError('credential-secret')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / 'repo'
            repo.mkdir()
            def git(repo_arg, *args):
                return (str(repo) if args[-1] == '--show-toplevel' else COMMIT).encode()
            target = {'schema': 1, 'commit': COMMIT, 'files': {}}
            with patch('audit_s3.git', side_effect=git), patch('boto3.Session', return_value=session):
                report, code = audit(repo, root / 'report.json', backend='boto3', temp_root=root,
                                     inventory_fn=lambda _: target)
            self.assertEqual(code, 1)
            self.assertFalse(report['auditComplete'])
            self.assertTrue(report['applyBlocked'])
            self.assertNotIn('credential-secret', json.dumps(report))
            phases = report['diagnostics']['performance']['phases']
            self.assertEqual(phases['boto3.initialization']['failed'], 1)
            self.assertEqual(phases['boto3.client.s3']['failed'], 1)
            self.assertEqual(json.loads((root / 'report.json').read_text()), report)

    def test_stream_interruption_records_failure_and_closes_resources(self):
        from audit_s3_boto3 import Boto3Reader
        from audit_s3 import request
        from unittest.mock import Mock
        reader = object.__new__(Boto3Reader)
        reader.performance = Performance()
        reader.stop = threading.Event()
        body = Mock()
        body.read.side_effect = KeyboardInterrupt()
        reader._read = Mock(return_value={'Body': body, 'VersionId': 'v1',
                                         'ContentLength': 1, 'ServerSideEncryption': 'AES256'})
        with self.assertRaises(KeyboardInterrupt):
            reader.hash_object(request(Key='a'), 1, 'v1')
        body.close.assert_called_once()
        self.assertTrue(reader.stop.is_set())
        for phase in ('get.streaming', 'get.body_read'):
            self.assertEqual(reader.performance.data['phases'][phase]['failed'], 1)


if __name__ == '__main__':
    unittest.main()
