"""Read-only S3 SHA256 audit. Explicit --read-s3 required; never authorizes writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote_plus

from audit_performance import Performance, perf_counter

from deploy_plan import compare_metadata, git, inventory, load_json, s3_metadata, validate

ACCOUNT = '787140332697'
BUCKET = 'vmsenergy-web-prod-787140332697'
COMMIT = '72ad37efaf16c796649c458c273e70984061fdb5'
PROFILE = 'vms-web-infra'
EXPECTED_FILES = 947
READS = {('sts', 'get-caller-identity'), ('s3api', 'list-objects-v2'),
         ('s3api', 'head-object'), ('s3api', 'get-object')}


def now():
    return datetime.now(timezone.utc).isoformat()


class AuditError(ValueError):
    pass


def validate_read(service, operation, request, destination, preflight=False):
    if (service, operation) not in READS:
        raise AuditError('Operation not on read-only allowlist')
    if preflight and operation not in ('get-caller-identity', 'list-objects-v2'):
        raise AuditError('Preflight forbids HEAD and body GET')
    if 'VersionId' in request:
        raise AuditError('Historical version requests are forbidden')
    if service == 's3api' and (request.get('Bucket') != BUCKET
                              or request.get('ExpectedBucketOwner') != ACCOUNT):
        raise AuditError('Unexpected bucket or owner')
    if (operation == 'get-object') != (destination is not None):
        raise AuditError('Invalid download destination')
    if destination is not None and set(request) != {'Bucket', 'Key', 'ExpectedBucketOwner'}:
        raise AuditError('Unexpected GetObject API fields; outfile must be positional')


class AwsCli:
    """JSON input except streaming GetObject; raw UTF-8 output, fixed endpoints."""
    def __init__(self, work, profile=PROFILE, command=None, preflight=False):
        self.work = Path(work)
        self.profile = profile
        self.command = command or ['aws']
        self.calls = {}
        self.preflight = preflight

    def call(self, service, operation, request, destination=None):
        validate_read(service, operation, request, destination, self.preflight)
        source = self.work / 'request.json'
        source.write_text(json.dumps(request, ensure_ascii=True), encoding='utf-8')
        endpoint = ('https://s3.us-east-1.amazonaws.com' if service == 's3api'
                    else 'https://sts.us-east-1.amazonaws.com')
        args = self.command + [service, operation]
        if destination is not None:
            # CLI 2.36.42 streaming GetObject does not support --cli-input-json.
            # Pass API fields explicitly and outfile as a CLI-only positional.
            # --output formats stdout metadata; keep it unfiltered (no --query)
            # so VersionId, ContentLength and encryption can all be validated.
            args.append(str(destination))
            args += ['--bucket', request['Bucket'], '--key', request['Key'],
                     '--expected-bucket-owner', request['ExpectedBucketOwner']]
        args += ['--profile', self.profile,
                '--region', 'us-east-1', '--endpoint-url', endpoint,
                '--output', 'json', '--no-paginate', '--no-cli-pager',
                '--no-cli-auto-prompt', '--cli-connect-timeout', '15',
                '--cli-read-timeout', '60']
        if destination is None:
            args += ['--cli-input-json', 'file://' + str(source)]
        env = os.environ.copy()
        env.update(AWS_PAGER='', AWS_CLI_AUTO_PROMPT='off', PYTHONUTF8='1',
                   PYTHONIOENCODING='utf-8', AWS_CLI_FILE_ENCODING='UTF-8',
                   AWS_MAX_ATTEMPTS='1', AWS_RETRY_MODE='standard')
        self.calls[operation] = self.calls.get(operation, 0) + 1
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, timeout=900, check=False)
        if result.returncode:
            # Do not embed stderr, credentials or an arbitrary CLI response in a report.
            raise AuditError('%s failed (AWS CLI exit %s)' % (operation, result.returncode))
        response = self.work / 'response.json'
        response.write_bytes(result.stdout)
        parsed = load_json(response)
        if not isinstance(parsed, dict):
            raise AuditError('AWS response must be a JSON object')
        return parsed


def request(**fields):
    return dict(Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT, **fields)


def decode_listing_key(encoded):
    """Decode only explicit EncodingType=url wire keys, exactly once.

    S3 may represent spaces as '+' or '%20'; a literal '+' is '%2B'.
    This must NOT be applied to keys auto-decoded by an SDK or raw Git keys.
    """
    if not isinstance(encoded, str):
        raise AuditError('Invalid encoded key')
    encoded.encode('ascii', errors='strict')
    if any(ord(char) <= 32 or ord(char) == 127 for char in encoded):
        raise AuditError('Raw whitespace/control in encoded key; possible prior decoding')
    if re.search(r'%(?![0-9a-fA-F]{2})', encoded):
        raise AuditError('Invalid percent-encoded key')
    return unquote_plus(encoded, encoding='utf-8', errors='strict')


def listing(cli, performance=None, phase='listing'):
    performance = performance or Performance()
    objects, seen_tokens, token, pages = {}, set(), None, 0
    while True:
        # Explicit EncodingType prevents botocore's implicit auto-decoding.
        # Only the raw encoded result is decoded by decode_listing_key.
        args = request(MaxKeys=1000, EncodingType='url')
        if token is not None:
            args['ContinuationToken'] = token
        page = performance.call(phase + '.request', cli.call, 's3api', 'list-objects-v2', args)
        with performance.measure(phase + '.decode_validate'):
            pages += 1
            truncated = page.get('IsTruncated')
            if type(truncated) is not bool:
                raise AuditError('Missing or invalid IsTruncated')
            next_token = page.get('NextContinuationToken')
            if truncated and (not isinstance(next_token, str) or not next_token or next_token in seen_tokens):
                raise AuditError('Missing/repeated continuation token')
            if not truncated and next_token is not None:
                raise AuditError('Unexpected final continuation token')
            if page.get('ContinuationToken') not in (None, token):
                raise AuditError('Continuation token does not match request')
            if type(page.get('KeyCount')) is not int:
                raise AuditError('Missing or invalid KeyCount')
            if page.get('Name') != BUCKET or page.get('EncodingType') != 'url':
                raise AuditError('Unexpected bucket name or listing encoding')
            normalized = dict(page, IsTruncated=False)
            normalized.pop('NextContinuationToken', None)
            normalized.pop('ContinuationToken', None)
            normalized.pop('EncodingType', None)
            if 'Contents' in page:
                if not isinstance(page['Contents'], list):
                    raise AuditError('Invalid Contents')
                normalized['Contents'] = []
                for entry in page['Contents']:
                    if not isinstance(entry, dict) or not isinstance(entry.get('Key'), str):
                        raise AuditError('Invalid encoded key')
                    key = decode_listing_key(entry['Key'])
                    normalized['Contents'].append(dict(entry, Key=key))
            entries, _ = s3_metadata(normalized)
            if len(entries) > 1000 or (truncated and not entries):
                raise AuditError('Invalid page length')
            for key, size in entries.items():
                if key in objects:
                    raise AuditError('Duplicate key across listing pages: ' + key)
                objects[key] = size
            if not truncated:
                return dict(sorted(objects.items())), pages
            seen_tokens.add(next_token)
            token = next_token


def object_metadata(document, size):
    version = document.get('VersionId')
    if not isinstance(version, str) or not version or version == 'null':
        raise AuditError('Missing/null VersionId; versioned current objects required')
    if document.get('DeleteMarker') not in (None, False):
        raise AuditError('Delete marker encountered')
    if type(document.get('ContentLength')) is not int or document['ContentLength'] != size:
        raise AuditError('ContentLength changed or invalid')
    if document.get('ServerSideEncryption') != 'AES256':
        raise AuditError('Expected SSE-S3 (AES256)')
    return version


def hash_file(path):
    sha, length = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            length += len(chunk)
            sha.update(chunk)
    return sha.hexdigest(), length


def collect(desired, cli, report, verify_git=lambda: None, preflight=False, workers=1, max_objects=None, performance=None):
    """Populate a diagnostic report; success still cannot be used as an apply manifest."""
    performance = performance or Performance()
    target = performance.call('inventory.validate', validate, desired)
    identity = performance.call('sts.identity', cli.call, 'sts', 'get-caller-identity', {})
    arn = identity.get('Arn', '')
    if (identity.get('Account') != ACCOUNT or not isinstance(arn, str)
            or len(arn.split(':')) < 6 or arn.split(':')[4] != ACCOUNT
            or not arn.startswith(('arn:aws:iam::', 'arn:aws:sts::'))):
        raise AuditError('Unexpected AWS account/ARN')
    report['identity'] = {'account': ACCOUNT, 'arn': arn}
    initial, pages = performance.call('listing.initial', listing, cli, performance, 'listing.initial')
    comparison_started = perf_counter()
    report['listingPagesInitial'] = pages
    report['counts'].update(gitFiles=len(target), s3Objects=len(initial),
                            remoteBytes=sum(initial.values()),
                            maxObjectBytes=max(initial.values(), default=0))
    records = report['objects']
    for key in sorted(set(target) | set(initial)):
        records[key] = {'git': target.get(key), 's3Size': initial.get(key),
                        'status': 'pending' if key in initial else 'missing-in-s3'}
    performance.add('inventory.records', perf_counter() - comparison_started)
    if preflight:
        comparison = performance.call('inventory.preflight_compare', compare_metadata, desired, {
            'IsTruncated': False, 'KeyCount': len(initial),
            'Contents': [{'Key': key, 'Size': size} for key, size in initial.items()]})
        report['metadataComparison'] = comparison
        counts = comparison['counts']
        for key, row in records.items():
            row['status'] = ('missing-in-s3' if key not in initial else
                             'extra-in-s3' if key not in target else
                             'metadata-match' if target[key]['size'] == initial[key]
                             else 'size-difference')
        if (len(target) != EXPECTED_FILES or len(initial) != EXPECTED_FILES
                or counts['sameKeyAndSize'] != EXPECTED_FILES
                or any(counts[name] for name in ('missingInSnapshot', 'extraInSnapshot', 'sizeDifferences'))):
            raise AuditError('Preflight requires exactly 947 Git/S3 key-size matches; see metadataComparison')
        verify_git()
        report.update(status='preflight-matches-git', preflightComplete=True)
        # auditComplete deliberately remains false: no remote bytes were read.
        return
    selected = dict(list(initial.items())[:max_objects]) if max_objects is not None else initial
    def read_object(key, size):
        try:
            read_one(key, size)
        except BaseException:
            if hasattr(cli, 'stop'):
                cli.stop.set()
            raise

    def read_one(key, size):
        row = records[key]
        before = performance.call('head.initial', cli.call, 's3api', 'head-object', request(Key=key), key=key)
        version = performance.call('head.initial_validation', object_metadata, before, size, key=key)
        row['versionBefore'] = version
        if hasattr(cli, 'hash_object'):
            downloaded, sha, length = performance.call('get.total', cli.hash_object, request(Key=key), size, version, key=key)
            row['versionGet'] = downloaded.get('VersionId')
            row.update(sha256=sha, downloadedBytes=length)
        else:
            if shutil.disk_usage(cli.work).free < size + 16 * 1024 * 1024:
                raise AuditError('Insufficient temporary disk space')
            fd, name = tempfile.mkstemp(prefix='payload-', dir=str(cli.work))
            os.close(fd)
            payload = Path(name)
            try:
                downloaded = cli.call('s3api', 'get-object', request(Key=key), payload)
                row['versionGet'] = downloaded.get('VersionId')
                if object_metadata(downloaded, size) != version:
                    raise AuditError('VersionId changed during GetObject')
                sha, length = hash_file(payload)
                row.update(sha256=sha, downloadedBytes=length)
                if length != size:
                    raise AuditError('Truncated/oversized download')
            finally:
                payload.unlink()
        after = performance.call('head.after', cli.call, 's3api', 'head-object', request(Key=key), key=key)
        row['versionAfter'] = after.get('VersionId')
        if performance.call('head.after_validation', object_metadata, after, size, key=key) != version:
            raise AuditError('VersionId changed after GetObject')
        comparison_started = perf_counter()
        row['status'] = ('extra-in-s3' if key not in target else
                         'match' if target[key] == {'size': size, 'sha256': sha} else 'drift')
        performance.add('inventory.object_compare', perf_counter() - comparison_started, key=key)

    with performance.measure('objects.batch_wall'):
        if workers == 1:
            for key, size in selected.items():
                report['currentKey'] = key
                read_object(key, size)
                report['counts']['objectsHashed'] += 1
        else:
            # Bounded rolling window: never queue the entire inventory.
            pool = ThreadPoolExecutor(max_workers=workers)
            pending = set()
            items = iter(selected.items())
            try:
                for _ in range(workers):
                    item = next(items, None)
                    if item is not None:
                        pending.add(pool.submit(read_object, *item))
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
                        report['counts']['objectsHashed'] += 1
                    for _ in done:
                        if cli.stop.is_set():
                            raise AuditError('Worker failed; audit cancelled')
                        item = next(items, None)
                        if item is not None:
                            pending.add(pool.submit(read_object, *item))
            except BaseException:
                cli.stop.set()
                for future in pending:
                    future.cancel()
                raise
            finally:
                pool.shutdown(wait=True)
    final, pages = performance.call('listing.final', listing, cli, performance, 'listing.final')
    compare_started = perf_counter()
    report['listingPagesFinal'] = pages
    if final != initial:
        raise AuditError('S3 keys/sizes changed during audit')
    performance.add('inventory.final_compare', perf_counter() - compare_started)
    with performance.measure('head.final_batch_wall'):
        for key, size in selected.items():
            report['currentKey'] = key
            row = records[key]
            latest = performance.call('head.final', cli.call, 's3api', 'head-object', request(Key=key), key=key)
            row['versionFinal'] = latest.get('VersionId')
            if performance.call('head.final_validation', object_metadata, latest, size, key=key) != row['versionBefore']:
                raise AuditError('Current VersionId changed at final verification')
    verify_git()
    report.pop('currentKey', None)
    report['counts'].update(matches=sum(r['status'] == 'match' for r in records.values()),
                            drift=sum(r['status'] == 'drift' for r in records.values()),
                            missing=sum(r['status'] == 'missing-in-s3' for r in records.values()),
                            extra=sum(r['status'] == 'extra-in-s3' for r in records.values()))
    if max_objects is not None:
        differences = any(report['counts'][name] for name in ('drift', 'missing', 'extra'))
        report.update(status='sample-differences' if differences else 'sample-verified',
                      auditComplete=False)
        return
    report['auditComplete'] = True
    report['status'] = ('matches-git' if report['counts']['matches'] == len(records)
                        else 'differences-found')


def outside_repo(path, repo):
    path, repo = Path(path).resolve(), Path(repo).resolve()
    if path == repo or repo in path.parents:
        raise AuditError('Temporary files and report must be outside the repository')
    return path


def write_report(path, document):
    data = document.get('diagnostics', {}).get('performance')
    performance = Performance()
    if data is not None:
        performance.data = data
    payload = {k: v for k, v in document.items() if k != 'diagnostics'}
    encoded = performance.call('report.payload_serialization', json.dumps, payload,
                               ensure_ascii=True, sort_keys=True, indent=2)
    fd, name = tempfile.mkstemp(prefix='.s3-audit-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            with performance.measure('report.payload_write_flush_fsync'):
                stream.write(encoded[:-1])
                stream.flush()
                os.fsync(stream.fileno())
            if 'diagnostics' in document:
                stream.write((', ' if payload else '') + '"diagnostics": ')
                json.dump(document['diagnostics'], stream, ensure_ascii=True, sort_keys=True)
            stream.write('}\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, str(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def audit(repo, output, profile=PROFILE, expected_commit=COMMIT, temp_root=None,
          cli_factory=AwsCli, inventory_fn=inventory, preflight=False,
          backend='cli', workers=1, max_objects=None):
    if backend not in ('cli', 'boto3') or type(workers) is not int or not 1 <= workers <= 4:
        raise AuditError('Invalid backend/workers (allowed 1..4)')
    if backend == 'cli' and workers != 1:
        raise AuditError('Concurrency requires boto3 backend')
    if max_objects is not None and (type(max_objects) is not int or max_objects < 1 or preflight):
        raise AuditError('Positive max_objects requires content audit')
    performance = Performance()
    repo = Path(performance.call('git.root', git, repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    output = outside_repo(output, repo)
    root = outside_repo(temp_root or tempfile.gettempdir(), repo)
    # Refuse existing reports, symlinks and accidental overwrites, even before AWS.
    with output.open('x', encoding='utf-8') as stream:
        report = {'schema': 1, 'kind': 'read-only-s3-audit', 'status': 'in-progress',
                  'auditComplete': False, 'applyBlocked': True, 'bucket': BUCKET,
                  'mode': 'metadata-preflight' if preflight else 'content-audit',
                  'preflightComplete': False, 'keyDecoding': 'explicit-url-form-once',
                  'expectedAccount': ACCOUNT, 'expectedCommit': expected_commit,
                  'backend': backend, 'workers': workers, 'maxObjects': max_objects,
                  'profile': profile, 'startedAt': now(), 'objects': {},
                  'diagnostics': {'performance': performance.data},
                  'counts': {'objectsHashed': 0},
                  'globalTransaction': False, 'otherWritersMustBeControlled': True}
        with performance.measure('report.initial_serialization_write'):
            json.dump(report, stream)
    code, cli = 1, None
    try:
        def verify_git():
            if performance.call('git.head', git, repo, 'rev-parse', 'HEAD').decode().strip() != expected_commit:
                raise AuditError('HEAD differs from expected audited commit')
        verify_git()
        with performance.measure('git.inventory'):
            desired = (inventory_fn(repo, performance=performance) if inventory_fn is inventory
                       else inventory_fn(repo))
        if desired.get('commit') != expected_commit:
            raise AuditError('Inventory commit does not match expected HEAD')
        report['commit'] = desired['commit']
        with tempfile.TemporaryDirectory(prefix='vms-s3-audit-', dir=str(root)) as work:
            if backend == 'boto3':
                from audit_s3_boto3 import Boto3Reader
                cli = performance.call('boto3.initialization', Boto3Reader, Path(work), profile,
                                       preflight=preflight, workers=workers, performance=performance)
            else:
                cli = cli_factory(Path(work), profile, preflight=preflight)
            try:
                collect(desired, cli, report, verify_git, preflight=preflight,
                        workers=workers, max_objects=max_objects, performance=performance)
            finally:
                if hasattr(cli, 'close'):
                    performance.call('backend.close', cli.close)
        code = 0 if report['status'] in ('matches-git', 'preflight-matches-git', 'sample-verified') else 2
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'aborted',
                      auditComplete=False, preflightComplete=False, errorType=type(exc).__name__)
        # Only our validation messages are safe to include; arbitrary process errors
        # may carry credentials or sensitive output in their string representation.
        report['error'] = str(exc) if isinstance(exc, AuditError) else 'Audit failed; no usable evidence'
        code = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        if cli is not None:
            report['readCalls'] = cli.calls
            if backend == 'cli':
                report['awsCliCalls'] = cli.calls
        report['finishedAt'] = now()
        performance.data['elapsedBeforeReportSeconds'] = perf_counter() - performance.started
        write_report(output, report)
    return report, code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-s3', action='store_true', help='Explicitly enable production READS')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight', action='store_true', help='STS + full listing only; require 947 key-size matches')
    modes.add_argument('--audit', action='store_true', help='Explicit full SHA256 body audit')
    parser.add_argument('--repo', default='.')
    parser.add_argument('--profile', default=PROFILE)
    parser.add_argument('--expected-head', default=COMMIT, choices=[COMMIT])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--temp-root', type=Path)
    parser.add_argument('--backend', choices=['cli', 'boto3'], default='cli')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--max-objects', type=int, help='Limited diagnostic sample; never complete evidence')
    args = parser.parse_args()
    if not args.read_s3:
        parser.error('No AWS calls made: --read-s3 is required')
    try:
        report, code = audit(args.repo, args.output, args.profile, args.expected_head,
                             args.temp_root, preflight=args.preflight, backend=args.backend,
                             workers=args.workers, max_objects=args.max_objects)
    except (Exception, KeyboardInterrupt):
        print('Audit could not start/save report; no usable evidence.', file=sys.stderr)
        return 1
    print(json.dumps({'status': report['status'], 'applyBlocked': True,
                      'counts': report['counts'], 'report': str(args.output)}, ensure_ascii=True))
    return code


if __name__ == '__main__':
    # The optional backend imports shared validation from this module.
    sys.modules['audit_s3'] = sys.modules[__name__]
    sys.exit(main())
