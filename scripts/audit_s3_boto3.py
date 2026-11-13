"""Optional persistent, read-only Boto3 transport. No SDK import for CLI users."""
import hashlib
import threading

from audit_performance import Performance, perf_counter
from audit_s3 import AuditError, object_metadata, validate_read


class Boto3Reader:
    def __init__(self, work, profile, preflight=False, workers=1, session=None, performance=None):
        self.performance = performance or Performance()
        with self.performance.measure('boto3.imports'):
            import boto3
            from botocore.config import Config
        self.work, self.preflight = work, preflight
        self.calls = {}
        self.stop = threading.Event()
        self.gate = threading.Lock()
        # Session construction/client creation happen on the main thread. Share
        # only low-level clients; retain the refreshable profile/SSO provider.
        with self.performance.measure('boto3.session'):
            session = session or boto3.Session(profile_name=profile, region_name='us-east-1')
        config = Config(region_name='us-east-1', max_pool_connections=workers,
                        connect_timeout=15, read_timeout=60,
                        retries={'total_max_attempts': 1, 'mode': 'standard'},
                        ignore_configured_endpoint_urls=True,
                        s3={'addressing_style': 'path'})
        self.clients = {}
        self.request_timing = threading.local()
        try:
            for service in ('s3', 'sts'):
                with self.performance.measure('boto3.client.' + service):
                    self.clients[service] = session.client(
                        service, region_name='us-east-1',
                        endpoint_url='https://%s.us-east-1.amazonaws.com' % service,
                        config=config)
                events = self.clients[service].meta.events
                events.register('before-sign.*.*', self._before_sign)
                events.register('before-send.*.*', self._before_send)
        except BaseException:
            self.close()
            raise

    def _before_sign(self, **kwargs):
        # Observe the existing signer path; never resolve/freeze credentials early.
        self.request_timing.sign_started = perf_counter()

    def _before_send(self, **kwargs):
        start = getattr(self.request_timing, 'sign_started', None)
        if start is not None:
            self.performance.add('sdk.signing_credentials', perf_counter() - start)
            self.request_timing.sign_started = None

    def _read(self, service, operation, fields, destination=None):
        validate_read(service, operation, fields, destination, self.preflight)
        # Admission is serialized, HTTP is concurrent. Already admitted calls
        # may finish after cancellation; no new calls may be admitted.
        with self.gate:
            if self.stop.is_set():
                raise AuditError('Read cancelled after worker failure')
            self.calls[operation] = self.calls.get(operation, 0) + 1
        client = self.clients['s3' if service == 's3api' else 'sts']
        performance = getattr(self, 'performance', None) or Performance()
        with performance.measure('sdk.' + operation, key=fields.get('Key')):
            try:
                return getattr(client, operation.replace('-', '_'))(**fields)
            except BaseException:
                self.stop.set()
                raise
            finally:
                # If signing/SSO resolution failed before before-send, preserve
                # the partial duration, without error messages or credential data.
                timing = getattr(self, 'request_timing', None)
                start = getattr(timing, 'sign_started', None)
                if start is not None:
                    performance.add('sdk.signing_credentials', perf_counter() - start, failed=True)
                    timing.sign_started = None

    def call(self, service, operation, request, destination=None):
        if operation == 'get-object':
            raise AuditError('Boto3 GET requires validated streaming hash_object')
        return self._read(service, operation, request, destination)

    def hash_object(self, fields, size, version):
        performance = getattr(self, 'performance', None) or Performance()
        key = fields['Key']
        started = perf_counter()
        response = self._read('s3api', 'get-object', fields, destination=True)
        performance.add('get.response_headers', perf_counter() - started, key=key)
        body = response['Body']
        try:
            actual_version = performance.call('get.metadata_validation', object_metadata,
                                              response, size, key=key)
            if actual_version != version:
                raise AuditError('VersionId changed during GetObject')
            sha, length, first = hashlib.sha256(), 0, True
            with performance.measure('get.streaming', key=key):
                while True:
                    if self.stop.is_set():
                        raise AuditError('Streaming read cancelled after worker failure')
                    chunk = performance.call('get.body_read', body.read, 1024 * 1024, key=key)
                    if not chunk:
                        if first:
                            performance.add('get.empty_body_eof', perf_counter() - started, key=key)
                        break
                    if first:
                        # Application-visible first chunk, not exact socket TTFB.
                        performance.add('get.first_chunk_upper_bound', perf_counter() - started, key=key)
                        first = False
                    with performance.measure('get.size_validation', key=key):
                        length += len(chunk)
                        if length > size:
                            raise AuditError('Truncated/oversized download')
                    performance.call('get.sha256', sha.update, chunk, key=key)
                with performance.measure('get.size_validation', key=key):
                    if length != size:
                        raise AuditError('Truncated/oversized download')
                digest = performance.call('get.sha256', sha.hexdigest, key=key)
            return {k: v for k, v in response.items() if k != 'Body'}, digest, length
        except BaseException:
            self.stop.set()
            raise
        finally:
            performance.call('get.body_close', body.close, key=key)

    def close(self):
        for client in self.clients.values():
            client.close()
