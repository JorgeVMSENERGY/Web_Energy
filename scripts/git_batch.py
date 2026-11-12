"""Strict binary git cat-file --batch reader; one blob streamed at a time."""
from contextlib import nullcontext
import hashlib
import re
import subprocess


CHUNK_SIZE = 1024 * 1024
OID = re.compile(rb'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


def timing(performance, name):
    return performance.measure(name) if performance is not None else nullcontext()


class GitBatch:
    def __init__(self, repo, performance=None):
        self.performance = performance
        with timing(performance, 'git.batch_start'):
            self.process = subprocess.Popen(
                ['git', '-C', str(repo), 'cat-file', '--batch'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0)

    def __enter__(self):
        return self

    def digest(self, oid, expected_size):
        if not OID.fullmatch(oid) or type(expected_size) is not int or expected_size < 0:
            raise ValueError('Invalid Git blob request')
        stream = self.process.stdout
        with timing(self.performance, 'git.batch_blob'):
            try:
                sent = self.process.stdin.write(oid + b'\n')
                if sent != len(oid) + 1:
                    raise ValueError('Incomplete Git batch request')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                raise ValueError('Git batch request failed') from None
            with timing(self.performance, 'git.batch_header'):
                header = stream.readline(256)
                fields = header[:-1].split(b' ') if header.endswith(b'\n') else []
                if (len(fields) != 3 or fields[0] != oid or fields[1] != b'blob'
                        or not re.fullmatch(rb'(?:0|[1-9][0-9]*)', fields[2])):
                    raise ValueError('Invalid Git batch response header')
                length = int(fields[2])
                if length != expected_size:
                    raise ValueError('Git batch length differs from tree')
            sha = hashlib.sha256()
            remaining = length
            while remaining:
                with timing(self.performance, 'git.batch_read'):
                    chunk = stream.read(min(CHUNK_SIZE, remaining))
                if not chunk or len(chunk) > remaining:
                    raise ValueError('Truncated/oversized Git blob')
                remaining -= len(chunk)
                with timing(self.performance, 'git.sha256'):
                    sha.update(chunk)
            if stream.read(1) != b'\n':
                raise ValueError('Invalid Git batch blob separator')
            with timing(self.performance, 'git.sha256'):
                value = sha.hexdigest()
            return {'sha256': value, 'size': length}

    def __exit__(self, exc_type, exc, traceback):
        process = self.process
        try:
            if exc_type is None:
                with timing(self.performance, 'git.batch_finish'):
                    process.stdin.close()
                    try:
                        code = process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        raise ValueError('Git batch did not exit') from None
                    if code != 0:
                        raise ValueError('Git batch exited unsuccessfully')
                    if process.stdout.read(1) != b'':
                        raise ValueError('Unexpected trailing Git batch response')
        finally:
            # Error, malformed protocol and KeyboardInterrupt must all reap Git.
            try:
                if process.poll() is None:
                    process.kill()
                process.wait()
            finally:
                for pipe in (process.stdin, process.stdout):
                    try:
                        pipe.close()
                    except OSError:
                        pass
        return False
