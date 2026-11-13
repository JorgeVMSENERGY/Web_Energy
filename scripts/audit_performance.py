"""Numeric-only, thread-safe perf_counter diagnostics; never stores SDK payloads."""
from contextlib import contextmanager
import threading
from time import perf_counter


class Performance:
    def __init__(self):
        self.started = perf_counter()
        self.lock = threading.Lock()
        self.data = {'clock': 'time.perf_counter', 'units': 'seconds',
                     'aggregation': 'Overlapping inclusive durations; do not sum phases or workers.',
                     'firstByte': 'SDK headers and first nonempty application chunk are upper bounds, not wire TTFB. Reads remain 1 MiB.',
                     'reportWrite': 'Payload serialization/write/flush/fsync measured; diagnostics footer and final fsync/replace excluded.',
                     'phases': {}, 'objects': {}}

    def add(self, name, seconds, key=None, failed=False):
        with self.lock:
            targets = [self.data['phases']]
            if key is not None:
                targets.append(self.data['objects'].setdefault(key, {}))
            for target in targets:
                row = target.setdefault(name, {'count': 0, 'failed': 0, 'totalSeconds': 0.0, 'maxSeconds': 0.0})
                row['count'] += 1
                row['failed'] += int(failed)
                row['totalSeconds'] += seconds
                row['maxSeconds'] = max(row['maxSeconds'], seconds)

    @contextmanager
    def measure(self, name, key=None):
        start, failed = perf_counter(), False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = perf_counter() - start
            self.add(name, elapsed, key=key, failed=failed)

    def call(self, name, function, *args, key=None, **kwargs):
        with self.measure(name, key=key):
            return function(*args, **kwargs)
