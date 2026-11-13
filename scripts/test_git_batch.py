"""Strict batch protocol regressions; no network or project Git mutations."""
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from git_batch import CHUNK_SIZE, GitBatch
from deploy_plan import digest, inventory


OID = b'a' * 40


class Process:
    def __init__(self, output=b'', code=0):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(output)
        self.code = code
        self.returncode = None
        self.killed = False
    def poll(self):
        return self.returncode
    def wait(self, **kwargs):
        self.returncode = self.code if self.returncode is None else self.returncode
        return self.returncode
    def kill(self):
        self.killed = True
        self.returncode = -9


def response(body, oid=OID):
    return oid + b' blob ' + str(len(body)).encode() + b'\n' + body + b'\n'


class BatchProtocolTests(unittest.TestCase):
    def test_binary_empty_duplicate_blobs_and_single_process(self):
        binary = bytes(range(256)) + b'\n\x00\xff'
        process = Process(response(binary) + response(b'') + response(binary))
        with patch('git_batch.subprocess.Popen', return_value=process) as popen:
            with GitBatch('fixture') as batch:
                self.assertEqual(batch.digest(OID, len(binary)), digest(binary))
                self.assertEqual(batch.digest(OID, 0), digest(b''))
                self.assertEqual(batch.digest(OID, len(binary)), digest(binary))
                self.assertEqual(process.stdin.getvalue(), (OID + b'\n') * 3)
        popen.assert_called_once()
        self.assertEqual(popen.call_args[0][0], ['git', '-C', 'fixture', 'cat-file', '--batch'])
        self.assertFalse(popen.call_args[1].get('text', False))
        self.assertTrue(process.stdin.closed and process.stdout.closed)
        self.assertFalse(process.killed)

    def test_invalid_headers_lengths_and_separator_fail_closed(self):
        cases = [b'', OID + b' missing\n', b'b' * 40 + b' blob 1\nx\n',
                 OID + b' tree 1\nx\n', OID + b' blob -1\n',
                 OID + b' blob +1\n', OID + b' blob 01\nx\n',
                 OID + b' blob nope\n', OID + b' blob 2\nxx\n',
                 OID + b' blob 1\nx!', OID + b' blob 1\nx',
                 OID + b' blob 1\n', OID + b' blob 1\r\nx\n', b'x' * 300 + b'\n']
        for output in cases:
            process = Process(output)
            with self.subTest(output=output[:60]), patch('git_batch.subprocess.Popen', return_value=process):
                with self.assertRaises(ValueError), GitBatch('.') as batch:
                    batch.digest(OID, 1)
                self.assertTrue(process.killed)
                self.assertTrue(process.stdin.closed and process.stdout.closed)

    def test_invalid_request_is_never_written(self):
        for oid, size in [(b'HEAD', 1), (OID + b'\n', 1), (OID, -1), (OID, True)]:
            process = Process()
            with patch('git_batch.subprocess.Popen', return_value=process):
                with self.assertRaises(ValueError), GitBatch('.') as batch:
                    batch.digest(oid, size)
                self.assertTrue(process.killed)

    def test_short_reads_and_bounded_memory(self):
        body = bytes(range(256)) * 9000
        class Fragmented(io.BytesIO):
            def read(self, size=-1):
                self_sizes.append(size)
                return super().read(min(size, 8191))
        self_sizes = []
        process = Process()
        process.stdout = Fragmented(response(body))
        with patch('git_batch.subprocess.Popen', return_value=process), GitBatch('.') as batch:
            self.assertEqual(batch.digest(OID, len(body)), digest(body))
        self.assertTrue(self_sizes)
        self.assertTrue(all(0 < size <= CHUNK_SIZE for size in self_sizes))

    def test_git_exit_failure_and_trailing_bytes_reject_inventory(self):
        for output, code in [(response(b'x'), 128), (response(b'x') + b'extra', 0)]:
            process = Process(output, code)
            with patch('git_batch.subprocess.Popen', return_value=process), self.assertRaises(ValueError):
                with GitBatch('.') as batch:
                    batch.digest(OID, 1)
            self.assertTrue(process.stdin.closed and process.stdout.closed)

    def test_keyboard_interrupt_closes_and_reaps_process(self):
        process = Process()
        class Interrupted(io.BytesIO):
            def readline(self, *args):
                raise KeyboardInterrupt()
        process.stdout = Interrupted()
        with patch('git_batch.subprocess.Popen', return_value=process), self.assertRaises(KeyboardInterrupt):
            with GitBatch('.') as batch:
                batch.digest(OID, 1)
        self.assertTrue(process.killed)
        self.assertTrue(process.stdin.closed and process.stdout.closed)

    def test_broken_stdin_and_exit_timeout_fail_closed(self):
        for mode in ('pipe', 'timeout'):
            process = Process(response(b'x'))
            if mode == 'pipe':
                class Broken(io.BytesIO):
                    def write(self, *args):
                        raise BrokenPipeError()
                process.stdin = Broken()
            else:
                original_wait = process.wait
                def wait(**kwargs):
                    if kwargs:
                        raise subprocess.TimeoutExpired('git', 30)
                    return original_wait()
                process.wait = wait
            with self.subTest(mode=mode), patch('git_batch.subprocess.Popen', return_value=process), self.assertRaises(ValueError):
                with GitBatch('.') as batch:
                    batch.digest(OID, 1)
            self.assertTrue(process.killed)
            self.assertTrue(process.stdin.closed and process.stdout.closed)


class InventoryBatchTests(unittest.TestCase):
    def test_head_change_rejects_completed_blobs(self):
        tree = b'100644 blob ' + OID + b' 1\tx.bin\0'
        process = Process(response(b'x'))
        with patch('deploy_plan.git', side_effect=[OID + b'\n', tree, b'b' * 40 + b'\n']), patch('git_batch.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(ValueError, 'HEAD changed'):
                inventory('.')
        self.assertTrue(process.stdout.closed)

    def test_tree_invalid_modes_sizes_oids_and_duplicates_fail_closed(self):
        entries = [b'120000 blob ' + OID + b' 1\tx\0',
                   b'100644 blob BAD 1\tx\0',
                   b'100644 blob ' + OID + b' -\tx\0',
                   b'100644 blob ' + OID + b' 1\t../x\0',
                   (b'100644 blob ' + OID + b' 1\tx\0') * 2]
        for tree in entries:
            with self.subTest(tree=tree), patch('deploy_plan.git', side_effect=[OID + b'\n', tree]), patch('git_batch.subprocess.Popen') as popen:
                with self.assertRaises(ValueError):
                    inventory('.')
                popen.assert_not_called()

    def test_inventory_failure_prevents_aws_and_saves_blocked_audit(self):
        from audit_s3 import COMMIT, audit
        from unittest.mock import Mock
        for mode in ('head-change', 'truncated-blob'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo = root / 'repo'
                repo.mkdir()
                output = root / 'report.json'
                def fake_audit_git(repo_arg, *args):
                    return (str(repo) if args[-1] == '--show-toplevel' else COMMIT).encode()
                tree = b'100644 blob ' + OID + b' 1\tx.bin\0'
                process = Process(response(b'x') if mode == 'head-change' else OID + b' blob 1\n')
                values = [COMMIT.encode(), tree, b'b' * 40]
                factory = Mock()
                with patch('audit_s3.git', side_effect=fake_audit_git), patch('deploy_plan.git', side_effect=values), patch('git_batch.subprocess.Popen', return_value=process), patch('audit_s3.subprocess.run') as aws:
                    report, code = audit(repo, output, temp_root=root, inventory_fn=inventory, cli_factory=factory)
                self.assertEqual(code, 1)
                self.assertEqual(report['status'], 'aborted')
                self.assertFalse(report['auditComplete'])
                self.assertTrue(report['applyBlocked'])
                factory.assert_not_called()
                aws.assert_not_called()
                self.assertTrue(process.stdout.closed and process.stdin.closed)

    def test_real_git_uses_fixed_commit_and_four_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def run(*args):
                return subprocess.check_output(['git', '-C', temp, *args], stderr=subprocess.STDOUT)
            run('init')
            run('config', 'user.email', 'offline@example.invalid')
            run('config', 'user.name', 'Offline fixture')
            run('config', 'core.autocrlf', 'false')
            values = {'index.html': b'a\nb\n', 'empty.bin': b'',
                      'assets/ñ + %20 # [x].bin': bytes(range(256)) * 9000}
            for key, body in values.items():
                path = root / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            run('add', '.')
            run('commit', '-m', 'offline fixture')
            commit = run('rev-parse', 'HEAD').decode().strip()
            for key in values:
                (root / key).write_bytes(b'working tree modified')
            calls = []
            original_popen = subprocess.Popen
            def counted(*args, **kwargs):
                calls.append(args[0])
                return original_popen(*args, **kwargs)
            with patch('subprocess.Popen', side_effect=counted):
                result = inventory(temp)
            self.assertEqual(result, {'schema': 1, 'commit': commit, 'files': {k: digest(v) for k,v in sorted(values.items())}})
            self.assertEqual(len(calls), 4)
            self.assertEqual(sum('cat-file' in c for c in calls), 1)
            self.assertIn(['git', '-C', temp, 'ls-tree', '-rzl', '--full-tree', commit], calls)
            self.assertTrue(all('--filters' not in c and '--textconv' not in c for c in calls))
            # A named historical ref still pins its own tree and checks current HEAD.
            self.assertEqual(inventory(temp, ref=commit), result)


if __name__ == '__main__':
    unittest.main()
