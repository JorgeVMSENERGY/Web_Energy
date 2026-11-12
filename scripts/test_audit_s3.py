"""Offline AWS CLI simulation at subprocess boundary: never invokes AWS."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote, quote_plus

from audit_s3 import (ACCOUNT, BUCKET, COMMIT, READS, AuditError, AwsCli,
                      audit, decode_listing_key)
from deploy_plan import digest, load_json, plan


class SimulatedCli:
    def __init__(self, data, hook=None, page_size=1000):
        self.data = data
        self.hook = hook
        self.page_size = page_size
        self.events = []
        self.counts = {}
        self.download_paths = []

    def run(self, args, **options):
        service, operation = args[1:3]
        assert (service, operation) in READS
        assert not options.get('shell')
        assert '--version-id' not in args
        assert '--no-paginate' in args and '--no-cli-pager' in args
        assert options['env']['AWS_MAX_ATTEMPTS'] == '1'
        if operation == 'get-object':
            assert '--cli-input-json' not in args
            params = {key: args[args.index(flag) + 1] for key, flag in [
                ('Bucket', '--bucket'), ('Key', '--key'), ('ExpectedBucketOwner', '--expected-bucket-owner')]}
        else:
            source = args[args.index('--cli-input-json') + 1]
            params = load_json(Path(source[len('file://'):]))
        assert 'VersionId' not in params and 'Range' not in params
        self.events.append((operation, params))
        self.counts[operation] = self.counts.get(operation, 0) + 1
        if service == 's3api':
            assert params['Bucket'] == BUCKET
            assert params['ExpectedBucketOwner'] == ACCOUNT
            assert args[args.index('--endpoint-url') + 1] == 'https://s3.us-east-1.amazonaws.com'
        if operation == 'get-caller-identity':
            response = {'Account': ACCOUNT, 'Arn': 'arn:aws:iam::' + ACCOUNT + ':user/offline'}
        elif operation == 'list-objects-v2':
            assert params['EncodingType'] == 'url'
            keys = sorted(self.data)
            start = int(params.get('ContinuationToken', '0'))
            selected = keys[start:start + self.page_size]
            response = {'Name': BUCKET, 'Prefix': '', 'IsTruncated': start + len(selected) < len(keys),
                        'KeyCount': len(selected), 'MaxKeys': 1000, 'EncodingType': 'url',
                        'Contents': [{'Key': quote(k, safe='/'), 'Size': len(self.data[k])} for k in selected]}
            if 'ContinuationToken' in params:
                response['ContinuationToken'] = params['ContinuationToken']
            if response['IsTruncated']:
                response['NextContinuationToken'] = str(start + len(selected))
        else:
            data = self.data[params['Key']]
            response = {'VersionId': 'v1-' + params['Key'], 'ContentLength': len(data),
                        'ServerSideEncryption': 'AES256', 'ETag': 'not-a-sha256',
                        'ChecksumCRC64NVME': 'also-not-a-sha256'}
            if operation == 'get-object':
                assert set(params) == {'Bucket', 'Key', 'ExpectedBucketOwner'}
                assert not args[3].startswith('--')
                destination = Path(args[3])
                self.download_paths.append(destination)
                destination.write_bytes(data)
        if self.hook:
            response = self.hook(operation, params, self.counts[operation], response, self)
        if isinstance(response, bytes):
            stdout = response
        else:
            stdout = json.dumps(response, ensure_ascii=False).encode('utf-8')
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=b'')


class AuditTests(unittest.TestCase):
    def execute(self, data=None, desired=None, hook=None, page_size=1000, preflight=False):
        data = {'index.html': b'hello\n'} if data is None else data
        desired = data if desired is None else desired
        model = SimulatedCli(data, hook, page_size)
        target = {'schema': 1, 'commit': COMMIT,
                  'files': {key: digest(value) for key, value in desired.items()}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / 'repo'
            repo.mkdir()
            output = root / 'audit.json'
            def fake_git(repo_arg, *args):
                return (str(repo) if args[-1] == '--show-toplevel' else COMMIT).encode()
            with patch('audit_s3.git', side_effect=fake_git), patch('audit_s3.subprocess.run', side_effect=model.run):
                report, code = audit(repo, output, temp_root=root, inventory_fn=lambda r: target,
                                     preflight=preflight)
            saved = load_json(output)
            self.assertEqual(report, saved)
            self.assertEqual(list(root.glob('vms-s3-audit-*')), [])
            self.assertTrue(all(not p.exists() for p in model.download_paths))
            self.assertTrue(report['applyBlocked'])
            self.assertNotIn('complete', report)
            self.assertNotIn('verification', report)
            return report, code, model

    def test_match_binary_zero_length_unicode_and_full_pagination(self):
        key = 'assets/\u00f1 \U0001f31e+#?%25[1].bin'
        data = {key: bytes(range(256)), 'empty': b''}
        report, code, model = self.execute(data, page_size=1)
        self.assertEqual(code, 0)
        self.assertTrue(report['auditComplete'])
        self.assertEqual(report['counts']['matches'], 2)
        self.assertEqual(report['listingPagesInitial'], 2)
        self.assertEqual(report['listingPagesFinal'], 2)
        self.assertEqual(model.counts, {'get-caller-identity': 1, 'list-objects-v2': 4,
                                      'head-object': 6, 'get-object': 2})
        row = report['objects'][key]
        self.assertEqual(row['sha256'], digest(data[key])['sha256'])
        self.assertEqual(row['versionBefore'], row['versionGet'])
        self.assertEqual(row['versionBefore'], row['versionAfter'])
        self.assertEqual(row['versionBefore'], row['versionFinal'])
        self.assertTrue(any(p.get('Key') == key for op, p in model.events if op == 'get-object'))
        with self.assertRaises(ValueError):
            plan({'schema': 1, 'files': {}}, report)

    def test_drift_same_size_is_not_a_match(self):
        report, code, _ = self.execute({'a': b'xx'}, {'a': b'yy'})
        self.assertEqual(code, 2)
        self.assertTrue(report['auditComplete'])
        self.assertEqual(report['objects']['a']['status'], 'drift')
        self.assertEqual(report['counts']['drift'], 1)

    def test_missing_extra_and_size_difference_reported(self):
        report, code, _ = self.execute({'a': b'xx', 'extra': b'z'}, {'a': b'y', 'missing': b'x'})
        self.assertEqual(code, 2)
        self.assertEqual(report['counts']['missing'], 1)
        self.assertEqual(report['counts']['extra'], 1)
        self.assertEqual(report['objects']['a']['git']['size'], 1)
        self.assertEqual(report['objects']['a']['s3Size'], 2)

    def test_truncated_download_aborts_and_cleans_payload(self):
        def hook(op, params, count, response, model):
            if op == 'get-object':
                model.download_paths[-1].write_bytes(b'he')
            return response
        report, code, model = self.execute(hook=hook)
        self.assertEqual(code, 1)
        self.assertFalse(report['auditComplete'])
        self.assertEqual(model.counts['head-object'], 1)
        self.assertIn('Truncated', report['error'])

    def test_changed_version_get_after_and_final_abort(self):
        for operation, when in [('get-object', 1), ('head-object', 2), ('head-object', 3)]:
            def hook(op, params, count, response, model):
                if op == operation and count == when:
                    response['VersionId'] = 'changed'
                return response
            with self.subTest(operation=operation, when=when):
                report, code, _ = self.execute(hook=hook)
                self.assertEqual(code, 1)
                self.assertFalse(report['auditComplete'])

    def test_identity_rejected_before_any_s3_read(self):
        def hook(op, params, count, response, model):
            if op == 'get-caller-identity':
                response['Account'] = '000000000000'
            return response
        report, code, model = self.execute(hook=hook)
        self.assertEqual(code, 1)
        self.assertEqual(model.counts, {'get-caller-identity': 1})

    def test_permission_failure_at_each_read_aborts(self):
        for operation in ['get-caller-identity', 'list-objects-v2', 'head-object', 'get-object']:
            def hook(op, params, count, response, model):
                if op == operation:
                    raise subprocess.CalledProcessError(254, ['aws'], stderr=b'AccessDenied secret')
                return response
            with self.subTest(operation=operation):
                report, code, _ = self.execute(hook=hook)
                self.assertEqual(code, 1)
                self.assertFalse(report['auditComplete'])
                self.assertNotIn('secret', json.dumps(report))

    def test_nonzero_cli_returncode_is_failure_even_with_valid_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            cli = AwsCli(Path(temp))
            result = subprocess.CompletedProcess(['aws'], 254, stdout=b'{}', stderr=b'AccessDenied secret')
            with patch('audit_s3.subprocess.run', return_value=result), self.assertRaises(AuditError):
                cli.call('sts', 'get-caller-identity', {})

    def test_interruption_during_download_cleans_and_saves_incomplete_report(self):
        def hook(op, params, count, response, model):
            if op == 'get-object':
                raise KeyboardInterrupt()
            return response
        report, code, _ = self.execute(hook=hook)
        self.assertEqual(code, 130)
        self.assertEqual(report['status'], 'interrupted')
        self.assertFalse(report['auditComplete'])

    def test_final_listing_changed_key_or_size_aborts(self):
        for change in ['key', 'size']:
            def hook(op, params, count, response, model):
                if op == 'list-objects-v2' and count == 2:
                    response['Contents'][0]['Key' if change == 'key' else 'Size'] = 'new' if change == 'key' else 42
                return response
            with self.subTest(change=change):
                report, code, _ = self.execute(hook=hook)
                self.assertEqual(code, 1)
                self.assertFalse(report['auditComplete'])

    def test_duplicate_keys_across_pages_and_repeated_token_abort(self):
        for change in ['duplicate', 'token']:
            def hook(op, params, count, response, model):
                if op == 'list-objects-v2' and count == 2:
                    if change == 'duplicate':
                        response['Contents'][0]['Key'] = 'a'
                    else:
                        response['IsTruncated'] = True
                        response['NextContinuationToken'] = '1'
                return response
            with self.subTest(change=change):
                report, code, model = self.execute({'a': b'a', 'b': b'b'}, hook=hook, page_size=1)
                self.assertEqual(code, 1)
                self.assertNotIn('get-object', model.counts)

    def test_invalid_responses_abort_before_download(self):
        changes = [lambda r: r.pop('IsTruncated'), lambda r: r.update(KeyCount=True),
                   lambda r: r.update(Name='wrong-bucket'), lambda r: r.update(EncodingType='unknown'),
                   lambda r: r['Contents'][0].update(Key='%GG'),
                   lambda r: r['Contents'][0].update(Key='%FF'),
                   lambda r: r.update(IsTruncated=True), lambda r: r.update(KeyCount=5)]
        for change in changes:
            def hook(op, params, count, response, model):
                if op == 'list-objects-v2':
                    change(response)
                return response
            with self.subTest(change=change):
                report, code, model = self.execute(hook=hook)
                self.assertEqual(code, 1)
                self.assertFalse(report['auditComplete'])
                self.assertNotIn('get-object', model.counts)

    def test_malformed_json_duplicate_json_members_and_partial_response_abort(self):
        for raw in [b'{', b'{"IsTruncated":false,"IsTruncated":true}', b'null']:
            def hook(op, params, count, response, model):
                return raw if op == 'list-objects-v2' else response
            with self.subTest(raw=raw):
                report, code, _ = self.execute(hook=hook)
                self.assertEqual(code, 1)
                self.assertFalse(report['auditComplete'])

    def test_missing_version_delete_marker_wrong_sse_and_length_abort(self):
        for change in [lambda r: r.pop('VersionId'), lambda r: r.update(VersionId='null'),
                       lambda r: r.update(DeleteMarker=True), lambda r: r.update(ServerSideEncryption='aws:kms'),
                       lambda r: r.update(ContentLength=True), lambda r: r.update(ContentLength=9)]:
            def hook(op, params, count, response, model):
                if op == 'head-object':
                    change(response)
                return response
            with self.subTest(change=change):
                report, code, model = self.execute(hook=hook)
                self.assertEqual(code, 1)
                self.assertNotIn('get-object', model.counts)

    def test_empty_bucket_and_git(self):
        report, code, model = self.execute({})
        self.assertEqual(code, 0)
        self.assertEqual(report['counts']['objectsHashed'], 0)
        self.assertNotIn('get-object', model.counts)

    def test_readonly_allowlist_and_no_historical_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            cli = AwsCli(Path(temp))
            with patch('audit_s3.subprocess.run') as run:
                for op in ['put-object', 'delete-object', 'create-invalidation']:
                    with self.assertRaises(AuditError):
                        cli.call('s3api', op, {})
                with self.assertRaises(AuditError):
                    cli.call('s3api', 'head-object', {'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT, 'VersionId': 'v1'})
                with self.assertRaises(AuditError):
                    cli.call('s3api', 'head-object', {'Bucket': 'wrong', 'ExpectedBucketOwner': ACCOUNT})
                run.assert_not_called()

    def test_get_object_requires_positional_destination_and_exact_api_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            cli = AwsCli(Path(temp))
            params = {'Bucket': BUCKET, 'Key': 'file.bin', 'ExpectedBucketOwner': ACCOUNT}
            with patch('audit_s3.subprocess.run') as run:
                with self.assertRaises(AuditError):
                    cli.call('s3api', 'get-object', params)
                with self.assertRaises(AuditError):
                    cli.call('s3api', 'get-object', dict(params, outfile='wrong.bin'), Path(temp) / 'out.bin')
                run.assert_not_called()

    def test_no_explicit_read_flag_means_no_aws(self):
        import io
        import sys
        from audit_s3 import main
        with patch.object(sys, 'argv', ['audit_s3.py', '--preflight', '--output', 'unused.json']), patch('audit_s3.subprocess.run') as run, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
            run.assert_not_called()

    def test_timeout_aborts_and_cleans(self):
        def hook(op, params, count, response, model):
            if op == 'get-object':
                raise subprocess.TimeoutExpired(['aws'], 900)
            return response
        report, code, _ = self.execute(hook=hook)
        self.assertEqual(code, 1)
        self.assertFalse(report['auditComplete'])

    def test_disk_full_aborts_before_download(self):
        from collections import namedtuple
        usage = namedtuple('usage', 'total used free')(100, 100, 0)
        with patch('audit_s3.shutil.disk_usage', return_value=usage):
            report, code, model = self.execute()
        self.assertEqual(code, 1)
        self.assertNotIn('get-object', model.counts)

    def test_report_and_temp_directory_must_be_outside_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / 'repo'
            repo.mkdir()
            def fake_git(repo_arg, *args):
                return str(repo).encode()
            with patch('audit_s3.git', side_effect=fake_git), patch('audit_s3.subprocess.run') as run:
                with self.assertRaises(AuditError):
                    audit(repo, repo / 'report.json', temp_root=root)
                with self.assertRaises(AuditError):
                    audit(repo, root / 'report.json', temp_root=repo)
                run.assert_not_called()

    def test_existing_report_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / 'repo'
            repo.mkdir()
            output = root / 'report.json'
            output.write_bytes(b'preserve')
            with patch('audit_s3.git', return_value=str(repo).encode()), patch('audit_s3.subprocess.run') as run:
                with self.assertRaises(FileExistsError):
                    audit(repo, output, temp_root=root)
                run.assert_not_called()
            self.assertEqual(output.read_bytes(), b'preserve')

    def test_git_head_changed_before_or_after_audit_invalidates_report(self):
        for change_when in [0, 1]:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo = root / 'repo'
                repo.mkdir()
                output = root / 'report.json'
                model = SimulatedCli({'a': b'a'})
                heads = [0]
                def fake_git(repo_arg, *args):
                    if args[-1] == '--show-toplevel':
                        return str(repo).encode()
                    value = ('bad' if heads[0] == change_when else COMMIT)
                    heads[0] += 1
                    return value.encode()
                desired = {'schema': 1, 'commit': COMMIT, 'files': {'a': digest(b'a')}}
                with patch('audit_s3.git', side_effect=fake_git), patch('audit_s3.subprocess.run', side_effect=model.run):
                    report, code = audit(repo, output, temp_root=root, inventory_fn=lambda r: desired)
                self.assertEqual(code, 1)
                self.assertFalse(report['auditComplete'])
                self.assertEqual(report['status'], 'aborted')
                if change_when == 0:
                    self.assertEqual(model.events, [])

    def test_explicit_wire_decoding_spaces_plus_percent_unicode_once(self):
        cases = [('a+b', 'a b'), ('a%20b', 'a b'), ('a%2Bb', 'a+b'),
                 ('a%252Bb', 'a%2Bb'), ('a%2520b', 'a%20b'), ('a%2525b', 'a%25b'),
                 ('100%25+%C3%B1', '100% \u00f1'), ('a%2B+b', 'a+ b'),
                 ('%252B%2B+%2520%2525', '%2B+ %20%25'),
                 ('CV+VMS+ENERGY+26_comprimido.pdf', 'CV VMS ENERGY 26_comprimido.pdf')]
        for wire, expected in cases:
            with self.subTest(wire=wire):
                self.assertEqual(decode_listing_key(wire), expected)
        for invalid in ['already decoded', '100%', '%GG', '%FF', '\u00f1']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                decode_listing_key(invalid)

    def test_full_audit_uses_decoded_keys_once_without_merging_similar_names(self):
        data = {key: bytes([n]) for n, key in enumerate([
            'a b', 'a+b', 'a%2Bb', 'a%20b', 'a%25b', '100% b', '100%.bin', '\u00f1 + %20.bin'])}
        def hook(op, params, count, response, model):
            if op == 'list-objects-v2':
                # Emulate raw S3 form-style output, including '+' for space.
                response['Contents'] = [{'Key': quote_plus(k, safe='/'), 'Size': len(v)}
                                        for k, v in sorted(data.items())]
            return response
        report, code, model = self.execute(data, hook=hook)
        self.assertEqual(code, 0)
        self.assertEqual(report['counts']['matches'], len(data))
        self.assertEqual(set(report['objects']), set(data))
        self.assertEqual({p['Key'] for op, p in model.events if op == 'get-object'}, set(data))

    def test_preflight_947_paginated_matches_only_sts_and_listing(self):
        data = {'file-%04d' % n: b'x' for n in range(939)}
        data.update({key: b'x' for key in ['a b', 'a+b', 'a%2B', 'a%20', 'a%25', '100%', '\u00f1', 'x +%']})
        def hook(op, params, count, response, model):
            if op == 'list-objects-v2':
                start = int(params.get('ContinuationToken', '0'))
                response['Contents'] = [{'Key': quote_plus(k, safe='/'), 'Size': len(data[k])}
                                        for k in sorted(data)[start:start + 200]]
            return response
        report, code, model = self.execute(data, hook=hook, page_size=200, preflight=True)
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'preflight-matches-git')
        self.assertTrue(report['preflightComplete'])
        self.assertFalse(report['auditComplete'])
        self.assertEqual(report['metadataComparison']['counts']['sameKeyAndSize'], 947)
        self.assertEqual(model.counts, {'get-caller-identity': 1, 'list-objects-v2': 5})
        self.assertEqual(model.download_paths, [])
        self.assertTrue(all(row['status'] == 'metadata-match' for row in report['objects'].values()))

    def test_preflight_differences_and_incorrect_count_abort_without_head_or_get(self):
        desired = {'file-%04d' % n: b'x' for n in range(947)}
        variants = [dict(desired, extra=b'x'), {k: v for k, v in desired.items() if k != 'file-0000'},
                    dict(desired, **{'file-0000': b'xx'}), {'one': b'x'}]
        for data in variants:
            with self.subTest(count=len(data)):
                report, code, model = self.execute(data, desired, preflight=True)
                self.assertEqual(code, 1)
                self.assertEqual(report['status'], 'aborted')
                self.assertFalse(report['preflightComplete'])
                self.assertFalse(report['auditComplete'])
                self.assertNotIn('head-object', model.counts)
                self.assertNotIn('get-object', model.counts)
        # Even equal 1-file inventories cannot satisfy the pinned 947-file contract.
        report, code, _ = self.execute({'one': b'x'}, preflight=True)
        self.assertEqual(code, 1)

    def test_preflight_duplicate_decoded_key_aborts(self):
        def hook(op, params, count, response, model):
            if op == 'list-objects-v2':
                response['Contents'] = [{'Key': 'a+b', 'Size': 1}, {'Key': 'a%20b', 'Size': 1}]
                response['KeyCount'] = 2
            return response
        report, code, model = self.execute(hook=hook, preflight=True)
        self.assertEqual(code, 1)
        self.assertFalse(report['preflightComplete'])
        self.assertNotIn('get-object', model.counts)

    def test_preflight_transport_forbids_head_and_get(self):
        with tempfile.TemporaryDirectory() as temp:
            cli = AwsCli(Path(temp), preflight=True)
            with patch('audit_s3.subprocess.run') as run:
                for op in ['head-object', 'get-object']:
                    with self.assertRaises(AuditError):
                        cli.call('s3api', op, {'Bucket': BUCKET, 'ExpectedBucketOwner': ACCOUNT})
                run.assert_not_called()

    def test_cli_requires_separate_mutually_exclusive_modes(self):
        import io
        import sys
        from audit_s3 import main
        for modes in [[], ['--preflight', '--audit']]:
            with patch.object(sys, 'argv', ['audit_s3.py', '--read-s3', '--output', 'unused.json'] + modes), patch('audit_s3.subprocess.run') as run, patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
