"""Installed AWS CLI against a loopback XML fixture. No AWS endpoint/credentials."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from audit_s3 import (ACCOUNT, BUCKET, COMMIT, PROFILE, AwsCli, audit, decode_listing_key,
                      hash_file, object_metadata, request)


@unittest.skipUnless(shutil.which('aws'), 'AWS CLI not installed; loopback compatibility test skipped')
class InstalledCliEncodingTests(unittest.TestCase):
    def test_get_object_collector_argv_outfile_and_unsupported_json_regression(self):
        """Exercise the exact collector subprocess argv with only transport isolated."""
        key = 'VMS_PR_generacion-disponibilidad_v2.zip'
        body = bytes(range(256)) + b'\x00\xff\r\n'
        seen, calls = [], []
        class Handler(BaseHTTPRequestHandler):
            def respond(self, send_body):
                seen.append((self.command, self.path, self.headers.get('x-amz-expected-bucket-owner')))
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('x-amz-version-id', 'fixture-current-v1')
                self.send_header('x-amz-server-side-encryption', 'AES256')
                self.end_headers()
                if send_body:
                    self.wfile.write(body)

            def do_HEAD(self):
                self.respond(False)

            def do_GET(self):
                self.respond(True)

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        real_run = subprocess.run
        version = real_run([shutil.which('aws'), '--version'], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=20, check=True)
        self.assertIn(b'aws-cli/2.36.42 ', version.stdout + version.stderr,
                      'This regression requires the incident CLI version 2.36.42')
        try:
            with tempfile.TemporaryDirectory(prefix='get-object test ') as temp:
                root = Path(temp)
                config, credentials = root / 'config', root / 'credentials'
                config.write_text('[profile ' + PROFILE + ']\nregion=us-east-1\n', encoding='ascii')
                credentials.write_text('', encoding='ascii')
                isolated = {k: v for k, v in os.environ.items() if not k.upper().startswith('AWS_')}
                isolated.update(AWS_CONFIG_FILE=str(config), AWS_SHARED_CREDENTIALS_FILE=str(credentials),
                                AWS_EC2_METADATA_DISABLED='true', AWS_MAX_ATTEMPTS='1', AWS_PAGER='',
                                NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost',
                                PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
                def run_local(args, **options):
                    # Do not reconstruct the command under test. Preserve collector
                    # order and JSON; change only executable/endpoint/auth/environment.
                    argv = list(args)
                    argv[0] = shutil.which('aws')
                    argv[argv.index('--endpoint-url') + 1] = 'http://127.0.0.1:%s' % server.server_port
                    argv.insert(3, '--no-sign-request')
                    if args[2] == 'get-object':
                        self.assertNotIn('--cli-input-json', args)
                        params = {name: args[args.index(flag) + 1] for name, flag in [
                            ('Bucket', '--bucket'), ('Key', '--key'), ('ExpectedBucketOwner', '--expected-bucket-owner')]}
                    else:
                        source = Path(args[args.index('--cli-input-json') + 1][len('file://'):])
                        params = json.loads(source.read_text(encoding='utf-8'))
                    self.assertEqual(params, {'Bucket': BUCKET, 'Key': key, 'ExpectedBucketOwner': ACCOUNT})
                    self.assertNotIn('outfile', params)
                    self.assertNotIn('VersionId', params)
                    self.assertNotIn('--version-id', args)
                    result = real_run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=isolated, timeout=20, check=False)
                    calls.append((list(args), argv, result))
                    self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
                    return result
                cli = AwsCli(root)
                destination = root / 'cuerpo \u00f1 + %20.bin'
                # Collector creates an empty secure file before passing it to AWS.
                destination.write_bytes(b'')
                with patch('audit_s3.subprocess.run', side_effect=run_local):
                    before = cli.call('s3api', 'head-object', request(Key=key))
                    downloaded = cli.call('s3api', 'get-object', request(Key=key), destination)
                    after = cli.call('s3api', 'head-object', request(Key=key))
                self.assertEqual(destination.read_bytes(), body)
                import hashlib
                self.assertEqual(hash_file(destination), (hashlib.sha256(body).hexdigest(), len(body)))
                for metadata in (before, downloaded, after):
                    self.assertEqual(object_metadata(metadata, len(body)), 'fixture-current-v1')
                self.assertEqual([event[0] for event in seen], ['HEAD', 'GET', 'HEAD'])
                self.assertTrue(all(owner == ACCOUNT for method, path, owner in seen))
                self.assertTrue(all(urlsplit(path).path.endswith('/' + key) for method, path, owner in seen))
                self.assertTrue(all('versionId' not in parse_qs(urlsplit(path).query)
                                    for method, path, owner in seen))
                get_args = calls[1][0]
                self.assertEqual(get_args.count(str(destination)), 1)
                self.assertEqual(get_args[3], str(destination))
                # Reproduce the original argv: JSON-only API args + trailing outfile.
                local_args = calls[1][1]
                without_outfile = [value for value in local_args if value != str(destination)]
                without_api_flags = list(without_outfile)
                for flag in ['--bucket', '--key', '--expected-bucket-owner']:
                    index = without_api_flags.index(flag)
                    del without_api_flags[index:index + 2]
                source = root / 'request.json'
                old_args = without_api_flags + ['--cli-input-json', 'file://' + str(source), str(destination)]
                legacy = real_run(old_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=isolated, timeout=20, check=False)
                self.assertEqual(legacy.returncode, 252)
                self.assertIn(b'--bucket, --key', legacy.stderr)
                self.assertEqual(len(seen), 3, 'Syntax failure must not reach the HTTP server')
                # Even with required flags present, GetObject rejects this option.
                unsupported = real_run(local_args + ['--cli-input-json', 'file://' + str(source)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=isolated, timeout=20, check=False)
                self.assertEqual(unsupported.returncode, 252)
                self.assertIn(b'Unknown options', unsupported.stderr)
                self.assertIn(b'--cli-input-json', unsupported.stderr)
                self.assertEqual(len(seen), 3)
                # outfile cannot be omitted or supplied as a member of the API JSON.
                omitted = real_run(without_outfile, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=isolated, timeout=20, check=False)
                self.assertEqual(omitted.returncode, 252)
                bad_input = root / 'outfile-is-not-an-api-field.json'
                bad_input.write_text(json.dumps(dict(request(Key=key), outfile=str(destination))), encoding='utf-8')
                bad_args = without_api_flags + ['--cli-input-json', 'file://' + str(bad_input)]
                embedded = real_run(bad_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=isolated, timeout=20, check=False)
                self.assertNotEqual(embedded.returncode, 0)
                self.assertEqual(len(seen), 3)
                # GetObject now passes Key through argv; verify Unicode/reserved
                # characters with the installed CLI rather than only a mock.
                key = 'assets/\u00f1 + %20 #?.bin'
                with patch('audit_s3.subprocess.run', side_effect=run_local):
                    special = cli.call('s3api', 'get-object', request(Key=key), destination)
                self.assertEqual(destination.read_bytes(), body)
                self.assertEqual(object_metadata(special, len(body)), 'fixture-current-v1')
                self.assertEqual(seen[-1][0], 'GET')
                self.assertTrue(unquote(urlsplit(seen[-1][1]).path).endswith('/' + key))
                self.assertEqual(seen[-1][2], ACCOUNT)
                self.assertEqual(len(seen), 4)
                # --output/--query format stdout metadata, never the binary outfile.
                queried = real_run(calls[1][1] + ['--query', '{VersionId:VersionId,ContentLength:ContentLength}'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=isolated, timeout=20, check=False)
                self.assertEqual(queried.returncode, 0, queried.stderr)
                self.assertEqual(json.loads(queried.stdout),
                                 {'VersionId': 'fixture-current-v1', 'ContentLength': len(body)})
                self.assertEqual(destination.read_bytes(), body)
                # Run the real collector lifecycle as well: only identity/listing
                # are fixtures; HEAD and GET still use the installed CLI above.
                from deploy_plan import digest
                repo = root / 'repo'
                repo.mkdir()
                target = {'schema': 1, 'commit': COMMIT, 'files': {key: digest(body)}}
                for mode in ('success', 'length-mismatch', 'exit-252'):
                    downloads = []
                    def transport(args, **options):
                        operation = args[2]
                        if operation == 'get-caller-identity':
                            result = {'Account': ACCOUNT, 'Arn': 'arn:aws:iam::' + ACCOUNT + ':user/offline'}
                        elif operation == 'list-objects-v2':
                            from urllib.parse import quote
                            result = {'Name': BUCKET, 'EncodingType': 'url', 'IsTruncated': False,
                                      'KeyCount': 1, 'Contents': [{'Key': quote(key, safe='/'), 'Size': len(body)}]}
                        else:
                            if operation == 'get-object':
                                downloads.append(Path(args[3]))
                                if mode == 'exit-252':
                                    return legacy
                            response = run_local(args, **options)
                            if operation != 'get-object' or mode != 'length-mismatch':
                                return response
                            result = json.loads(response.stdout)
                            result['ContentLength'] += 1
                        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(result).encode(), stderr=b'')
                    def fake_git(repo_arg, *args):
                        return (str(repo) if args[-1] == '--show-toplevel' else COMMIT).encode()
                    with self.subTest(collector=mode), patch('audit_s3.git', side_effect=fake_git), patch('audit_s3.subprocess.run', side_effect=transport):
                        report, code = audit(repo, root / (mode + '.json'), temp_root=root,
                                             inventory_fn=lambda unused: target)
                        self.assertEqual(code, 0 if mode == 'success' else 1)
                        self.assertEqual(report['auditComplete'], mode == 'success')
                        self.assertTrue(report['applyBlocked'])
                        self.assertEqual(len(downloads), 1)
                        self.assertTrue(all(not path.exists() for path in downloads))
                        self.assertEqual(list(root.glob('vms-s3-audit-*')), [])
                        if mode == 'success':
                            row = report['objects'][key]
                            self.assertEqual(row['sha256'], digest(body)['sha256'])
                            self.assertEqual(row['downloadedBytes'], len(body))
                            self.assertEqual(row['versionGet'], 'fixture-current-v1')
                        else:
                            self.assertEqual(report['status'], 'aborted')
                            self.assertIn('252' if mode == 'exit-252' else 'ContentLength', report['error'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_explicit_url_is_raw_but_implicit_url_is_sdk_decoded(self):
        # Hardcoded wire fixture, not generated using the production decoder.
        wire = ['CV+VMS+ENERGY+26_comprimido.pdf', 'literal%2B.pdf',
                'literal%252B.pdf', 'a%20b.pdf', 'a%2520b.pdf',
                '100%25.pdf', '100%2525.pdf', '%C3%B1.pdf']
        expected_implicit = ['CV VMS ENERGY 26_comprimido.pdf', 'literal+.pdf',
                             'literal%2B.pdf', 'a b.pdf', 'a%20b.pdf',
                             '100%.pdf', '100%25.pdf', '\u00f1.pdf']
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(parse_qs(urlsplit(self.path).query))
                body = ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                        '<Name>offline-fixture</Name><Prefix></Prefix><EncodingType>url</EncodingType>'
                        '<MaxKeys>1000</MaxKeys><KeyCount>8</KeyCount><IsTruncated>false</IsTruncated>' +
                        ''.join('<Contents><Key>' + k + '</Key><Size>1</Size></Contents>' for k in wire) +
                        '</ListBucketResult>').encode('ascii')
                self.send_response(200)
                self.send_header('Content-Type', 'application/xml')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                config, credentials = Path(temp) / 'config', Path(temp) / 'credentials'
                config.write_text('[default]\nregion=us-east-1\n', encoding='ascii')
                credentials.write_text('', encoding='ascii')
                env = {k: v for k, v in os.environ.items() if not k.upper().startswith('AWS_')}
                env.update(AWS_CONFIG_FILE=str(config), AWS_SHARED_CREDENTIALS_FILE=str(credentials),
                           AWS_EC2_METADATA_DISABLED='true', AWS_MAX_ATTEMPTS='1',
                           AWS_PAGER='', NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost',
                           PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
                base = [shutil.which('aws'), 's3api', 'list-objects-v2', '--bucket', 'offline-fixture',
                        '--endpoint-url', 'http://127.0.0.1:%s' % server.server_port,
                        '--region', 'us-east-1', '--no-sign-request', '--no-paginate',
                        '--no-cli-pager', '--no-cli-auto-prompt', '--output', 'json',
                        '--cli-connect-timeout', '2', '--cli-read-timeout', '2']
                source = Path(temp) / 'request.json'
                source.write_text('{"EncodingType":"url"}', encoding='ascii')
                for mode in ['flag', 'json', 'implicit']:
                    with self.subTest(mode=mode):
                        explicit = mode != 'implicit'
                        extra = (['--encoding-type', 'url'] if mode == 'flag' else
                                 ['--cli-input-json', 'file://' + str(source)] if mode == 'json' else [])
                        args = base + extra
                        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                env=env, timeout=20, check=True)
                        document = json.loads(result.stdout.decode('utf-8'))
                        keys = [row['Key'] for row in document['Contents']]
                        self.assertEqual(keys, wire if explicit else expected_implicit)
                        if explicit:
                            self.assertEqual(decode_listing_key(keys[0]), 'CV VMS ENERGY 26_comprimido.pdf')
                            self.assertEqual(decode_listing_key(keys[1]), 'literal+.pdf')
                            self.assertEqual(decode_listing_key(keys[2]), 'literal%2B.pdf')
                self.assertEqual(len(seen), 3)
                self.assertTrue(all(q['encoding-type'] == ['url'] for q in seen))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
