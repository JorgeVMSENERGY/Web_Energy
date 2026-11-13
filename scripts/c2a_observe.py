"""C2A read-only observation and non-authorizing plan; no S3 write methods."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile

from audit_s3 import ACCOUNT, BUCKET, AuditError, decode_listing_key, listing, request
from audit_s3_boto3 import Boto3Reader
from c1_plan import (DISTRIBUTION, REGION, STAGING, build_plan, checked_snapshot,
                     content, exclusive_json, fingerprint, from_audit, version)
from deploy_plan import inventory, load_json, safe_key, validate

HEAD_FIELDS = ('ContentType', 'CacheControl', 'ContentEncoding', 'ContentLanguage',
               'ContentDisposition', 'Expires', 'Metadata', 'ServerSideEncryption',
               'SSEKMSKeyId', 'StorageClass', 'WebsiteRedirectLocation',
               'ObjectLockMode', 'ObjectLockRetainUntilDate', 'ObjectLockLegalHoldStatus',
               'LastModified', 'ChecksumType', 'ChecksumCRC32', 'ChecksumCRC32C',
               'ChecksumCRC64NVME', 'ChecksumSHA1', 'ChecksumSHA256', 'BucketKeyEnabled',
               'TagCount', 'MissingMeta', 'ReplicationStatus', 'Restore')
UNCOLLECTED = ['Tags', 'ACL', 'BucketVersioningStatus', 'BucketPolicy',
               'CloudFrontCachePolicy', 'WriterPermissions', 'ObjectLockBucketConfiguration']


def stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Timezone-aware clock required')
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError('Missing timestamp')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timezone-aware timestamp required')
    return dt.astimezone(timezone.utc)


def normalize_head(document, expected_size):
    if not isinstance(document, dict):
        raise AuditError('Invalid HEAD response')
    ver = version(document.get('VersionId'))
    size = document.get('ContentLength')
    etag = document.get('ETag')
    if type(size) is not int or size != expected_size or not isinstance(etag, str) or not etag:
        raise AuditError('HEAD size or ETag missing/changed')
    if document.get('DeleteMarker') not in (None, False):
        raise AuditError('Unexpected delete marker in live listing')
    fields = {}
    for name in HEAD_FIELDS:
        present = name in document
        value = document.get(name)
        if isinstance(value, datetime):
            value = stamp(value)
        if present:
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError):
                raise AuditError('Unsupported HEAD metadata value') from None
        fields[name] = {'present': present, 'value': value if present else None}
    return {'versionId': ver, 'size': size, 'etag': etag, 'headFields': fields}


def metadata_differences(previous, head):
    values = previous['metadata']['values']
    differences, comparable, unavailable = [], [], []
    for key, old in values.items():
        if key not in head['headFields']:
            unavailable.append(key)
            continue
        new = head['headFields'][key]
        if not new['present']:
            # An omitted HEAD field is not proof of a default or an explicit null.
            unavailable.append(key)
        else:
            comparable.append(key)
            if new['value'] != old:
                differences.append(key)
    return {'changed': sorted(differences), 'compared': sorted(comparable),
            'unavailable': sorted(unavailable),
            'baselineComplete': previous['metadata']['complete']}


class ReadOnlyC2(Boto3Reader):
    def list_versions(self, **fields):
        # Extra, read-only operation deliberately kept outside the audit allowlist.
        allowed = {'Bucket', 'ExpectedBucketOwner', 'Prefix', 'EncodingType',
                   'MaxKeys', 'KeyMarker', 'VersionIdMarker'}
        if (set(fields) - allowed or fields.get('Bucket') != BUCKET
                or fields.get('ExpectedBucketOwner') != ACCOUNT
                or fields.get('EncodingType') != 'url' or not isinstance(fields.get('Prefix'), str)):
            raise AuditError('Invalid read-only version listing request')
        with self.gate:
            if self.stop.is_set():
                raise AuditError('Read cancelled')
            self.calls['list-object-versions'] = self.calls.get('list-object-versions', 0) + 1
        try:
            return self.clients['s3'].list_object_versions(**fields)
        except BaseException:
            self.stop.set()
            raise


def missing_kind(reader, key):
    """Find latest delete marker for an exact missing key, with paginated prefix results."""
    key_marker = version_marker = None
    seen = set()
    while True:
        args = request(Prefix=key, EncodingType='url', MaxKeys=1000)
        if key_marker is not None:
            args['KeyMarker'] = key_marker
        if version_marker is not None:
            args['VersionIdMarker'] = version_marker
        page = reader.list_versions(**args)
        if (not isinstance(page, dict) or page.get('Name') != BUCKET
                or page.get('EncodingType') != 'url' or type(page.get('IsTruncated')) is not bool):
            raise AuditError('Invalid version listing')
        found = []
        for field, label in (('DeleteMarkers', 'DELETE_MARKER'), ('Versions', 'LIVE_VERSION')):
            entries = page.get(field, [])
            if not isinstance(entries, list):
                raise AuditError('Invalid version entries')
            for item in entries:
                if not isinstance(item, dict) or not isinstance(item.get('Key'), str) or type(item.get('IsLatest')) is not bool:
                    raise AuditError('Invalid version entry')
                if decode_listing_key(item['Key']) == key and item['IsLatest']:
                    found.append((label, version(item.get('VersionId'))))
        if len(found) > 1:
            raise AuditError('Multiple latest versions')
        if found:
            if found[0][0] == 'LIVE_VERSION':
                raise AuditError('Live object missing from listing; state changed')
            return {'state': 'DELETE_MARKER', 'versionId': found[0][1]}
        if not page['IsTruncated']:
            return {'state': 'NO_CURRENT_VERSION_FOUND', 'versionId': None}
        next_key, next_version = page.get('NextKeyMarker'), page.get('NextVersionIdMarker')
        if not isinstance(next_key, str) or not next_key or not isinstance(next_version, str) or not next_version:
            raise AuditError('Missing version pagination markers')
        marker = (decode_listing_key(next_key), next_version)
        if marker in seen:
            raise AuditError('Repeated version pagination markers')
        seen.add(marker)
        key_marker, version_marker = marker


def verify_baseline_source(baseline, audit_report):
    """Accept only the bytes/versions from the complete C1 audit; no self-attested baseline."""
    checked_snapshot(baseline, 'historical-baseline')
    original = from_audit(audit_report, 'historical-baseline')
    if baseline != original:
        raise ValueError('Baseline differs from imported complete audit')
    return baseline


def observe(reader, baseline, desired_commit, verify_unknown=False, check_delete_markers=False,
            now=None, ttl_seconds=300):
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 900:
        raise ValueError('TTL must be between 1 and 900 seconds')
    previous = checked_snapshot(baseline, 'historical-baseline')
    clock = now or (lambda: datetime.now(timezone.utc))
    started = clock()
    identity = reader.call('sts', 'get-caller-identity', {})
    arn = identity.get('Arn', '')
    if (identity.get('Account') != ACCOUNT or not isinstance(arn, str)
            or not arn.startswith(('arn:aws:iam::', 'arn:aws:sts::'))
            or len(arn.split(':')) < 6 or arn.split(':')[4] != ACCOUNT):
        raise AuditError('Unexpected AWS account or ARN')
    first, pages = listing(reader)
    rows = {}
    for key, size in first.items():
        before = normalize_head(reader.call('s3api', 'head-object', request(Key=key)), size)
        old = previous.get(key)
        if old and before['versionId'] == old['versionId']:
            if old['size'] != size:
                raise AuditError('Baseline version has contradictory size')
            sha, method = old['sha256'], 'AUDITED_BASELINE_SAME_KEY_VERSION'
        elif verify_unknown:
            downloaded, sha, read_length = reader.hash_object(request(Key=key), size, before['versionId'])
            if read_length != size or downloaded.get('VersionId') != before['versionId']:
                raise AuditError('Changed version during GET')
            method = 'CURRENT_GET_SHA256'
        else:
            sha, method = None, 'UNKNOWN_VERSION_NO_GET'
        comparison = metadata_differences(old, before) if old else None
        rows[key] = dict(before, sha256=sha, sha256Source=method, metadataComparison=comparison)
    missing = {}
    for key in sorted(set(previous) - set(first)):
        missing[key] = (missing_kind(reader, key) if check_delete_markers else
                        {'state': 'MISSING_OR_DELETE_MARKER_UNRESOLVED', 'versionId': None})
    final, final_pages = listing(reader)
    if final != first:
        raise AuditError('S3 listing changed during observation')
    if check_delete_markers:
        for key, marker in missing.items():
            if missing_kind(reader, key) != marker:
                raise AuditError('Delete marker changed during observation')
    for key, size in final.items():
        after = normalize_head(reader.call('s3api', 'head-object', request(Key=key)), size)
        before = {k: rows[key][k] for k in ('versionId', 'size', 'etag', 'headFields')}
        if before != after:
            raise AuditError('Object version or HEAD metadata changed during observation')
    finished = clock()
    if parse_time(stamp(finished)) < parse_time(stamp(started)):
        raise AuditError('Clock moved backwards')
    obs = {'schemaVersion': 1, 'kind': 'c2a-read-only-observation', 'applyBlocked': True,
           'observationComplete': True, 'account': ACCOUNT, 'bucket': BUCKET, 'region': REGION,
           'identity': {'account': ACCOUNT, 'arn': arn},
           'hashCoverage': 'partial' if any(x['sha256'] is None for x in rows.values()) else 'all-current-objects',
           'distributionId': DISTRIBUTION, 'gitCommit': desired_commit,
           'baselineFingerprint': fingerprint(baseline), 'startedAt': stamp(started),
           'observedAt': stamp(finished), 'expiresAt': stamp(finished + timedelta(seconds=ttl_seconds)),
           'freshnessRequiresRevalidation': True, 'writerExclusionEstablished': False,
           'nonHeadAttributesNotCollected': UNCOLLECTED, 'files': dict(sorted(rows.items())),
           'missing': missing, 'listingPagesInitial': pages, 'listingPagesFinal': final_pages,
           'calls': dict(reader.calls)}
    obs['observationId'] = fingerprint(obs)
    return obs


def observe_safe(reader, baseline, desired_commit, **kwargs):
    try:
        return observe(reader, baseline, desired_commit, **kwargs)
    except (Exception, KeyboardInterrupt) as exc:
        return {'schemaVersion': 1, 'kind': 'c2a-read-only-observation', 'applyBlocked': True,
                'observationComplete': False, 'account': ACCOUNT, 'bucket': BUCKET,
                'region': REGION, 'gitCommit': desired_commit, 'errorType': type(exc).__name__,
                'error': str(exc) if isinstance(exc, AuditError) else 'Read failed; no usable observation',
                'calls': dict(reader.calls)}


def verify_observation(obs, baseline, now):
    if (not isinstance(obs, dict) or obs.get('schemaVersion') != 1
            or obs.get('kind') != 'c2a-read-only-observation' or obs.get('observationComplete') is not True
            or obs.get('applyBlocked') is not True or obs.get('account') != ACCOUNT
            or obs.get('bucket') != BUCKET or obs.get('region') != REGION
            or obs.get('identity', {}).get('account') != ACCOUNT
            or not isinstance(obs.get('identity', {}).get('arn'), str)
            or len(obs['identity']['arn'].split(':')) < 6
            or obs['identity']['arn'].split(':')[4] != ACCOUNT
            or not obs['identity']['arn'].startswith(('arn:aws:iam::', 'arn:aws:sts::'))
            or obs.get('baselineFingerprint') != fingerprint(baseline)):
        raise ValueError('Incomplete or unrelated observation')
    copy_obs = dict(obs)
    supplied = copy_obs.pop('observationId', None)
    if supplied != fingerprint(copy_obs):
        raise ValueError('Observation checksum mismatch')
    started, observed, expires = (parse_time(obs[k]) for k in ('startedAt', 'observedAt', 'expiresAt'))
    current = parse_time(stamp(now))
    if started > observed or observed > current or current >= expires or not 0 < (expires - observed).total_seconds() <= 900:
        raise ValueError('Observation expired, future-dated or inconsistent')


def plan(desired, baseline, obs, now=None):
    clock = now or datetime.now(timezone.utc)
    target = validate(desired)
    result = {'schemaVersion': 1, 'kind': 'c2a-plan', 'applyBlocked': True,
              'authorization': {'granted': False}, 'account': ACCOUNT, 'bucket': BUCKET,
              'region': REGION, 'distributionId': DISTRIBUTION, 'commit': desired.get('commit'),
              'createdAt': stamp(clock), 'requiresRevalidationBeforeAnyApply': True,
              'writerExclusionEstablished': False, 'status': 'BLOCKED', 'blockers': [],
              'diagnosis': {}, 'c1Plan': None, 'preconditionsByKey': {},
              'conditionalContracts': {
                  'CREATE': 'PutObject If-None-Match: *; current delete markers need separate review',
                  'UPDATE': 'PutObject If-Match: observed ETag; verify VersionId separately',
                  'DELETE': 'DeleteObject If-Match: observed ETag; verify VersionId separately',
                  'limitations': 'ETag is not SHA256 or a VersionId CAS; ABA and metadata races remain',
                  'implemented': False}}
    try:
        history = checked_snapshot(baseline, 'historical-baseline')
        verify_observation(obs, baseline, clock)
        if obs.get('gitCommit') != desired.get('commit'):
            raise ValueError('Target commit differs from observed commit')
        result.update(observationId=obs['observationId'], observerIdentity=copy.deepcopy(obs['identity']),
                      observedAt=obs['observedAt'],
                      evidenceExpiresAt=obs['expiresAt'], baselineFingerprint=fingerprint(baseline))
        unknown = False
        for key in sorted(set(history) | set(obs['files'])):
            old, row = history.get(key), obs['files'].get(key)
            if row is None:
                state = obs['missing'].get(key, {}).get('state', 'MISSING_UNRESOLVED')
            elif old is None:
                state = 'EXTERNAL_ADDITION'
            elif row['versionId'] != old['versionId']:
                state = ('UNKNOWN_VERSION' if row['sha256'] is None else
                         'EXTERNAL_VERSION_SAME_SHA256' if row['sha256'] == old['sha256'] else 'CONTENT_DRIFT')
            elif row['metadataComparison']['changed']:
                state = 'HEAD_METADATA_DRIFT'
            else:
                state = 'UNCHANGED_VERSION'
            if row is not None and row['sha256'] is None:
                unknown = True
            result['diagnosis'][key] = {'state': state, 'protectedStaging': key in STAGING,
                                        'versionId': row['versionId'] if row else None,
                                        'etagConcurrencyToken': row['etag'] if row else None,
                                        'sha256': row['sha256'] if row else None,
                                        'observedStateFingerprint': fingerprint(row if row is not None else obs['missing'].get(key)),
                                        'metadataComparison': row['metadataComparison'] if row else None}
        result['diagnosisCounts'] = {}
        for row in result['diagnosis'].values():
            state = row['state']
            result['diagnosisCounts'][state] = result['diagnosisCounts'].get(state, 0) + 1
        if not unknown:
            files = {}
            for key, row in obs['files'].items():
                values = {k: v['value'] for k, v in row['headFields'].items() if v['present']}
                files[key] = {'sha256': row['sha256'], 'size': row['size'], 'versionId': row['versionId'],
                              'metadata': {'complete': False, 'values': values}}
            observed = {'schemaVersion': 1, 'kind': 'remote-observation', 'applyBlocked': True,
                        'account': ACCOUNT, 'bucket': BUCKET, 'region': REGION,
                        'distributionId': DISTRIBUTION, 'commit': desired['commit'],
                        'evidence': {'sha256': obs['observationId'], 'finishedAt': obs['observedAt'],
                                     'freshness': 'bounded-but-not-lock', 'trustedProvenance': False},
                        'files': files}
            result['c1Plan'] = build_plan(desired, baseline, observed)
            for op in result['c1Plan']['operations']:
                key, action = op['key'], op['action']
                row = obs['files'].get(key)
                result['preconditionsByKey'][key] = {
                    'action': action, 'ifNoneMatch': '*' if action == 'CREATE' else None,
                    'ifMatch': row['etag'] if row is not None else None,
                    'expectedVersionId': row['versionId'] if row is not None else None,
                    'freshRevalidationRequired': True, 'writerExclusionRequired': True,
                    'implemented': False}
            result['contentComparison'] = result['c1Plan']['status']
        else:
            result['blockers'].append('UNKNOWN_SHA256_REQUIRES_READ_ONLY_GET')
            result['contentComparison'] = 'UNKNOWN'
        if any(row['state'] != 'UNCHANGED_VERSION' for row in result['diagnosis'].values()):
            result['blockers'].append('REMOTE_STATE_DIFFERS_FROM_BASELINE')
        if any(not row['metadata']['complete'] for row in history.values()):
            result['blockers'].append('HISTORICAL_METADATA_INCOMPLETE')
        if any(row['metadataComparison'] and row['metadataComparison']['unavailable']
               for row in obs['files'].values()):
            result['blockers'].append('HEAD_METADATA_COMPARISON_INCOMPLETE')
        result['blockers'] += ['NON_HEAD_METADATA_NOT_COLLECTED', 'WRITER_EXCLUSION_NOT_PROVEN',
                               'PRE_APPLY_ENFORCEMENT_AND_AUTHORIZATION_NOT_IMPLEMENTED']
    except (KeyError, TypeError, ValueError) as exc:
        result['blockers'].append('INVALID_OR_EXPIRED_EVIDENCE')
        result['error'] = str(exc)
    result['planChecksum'] = fingerprint(result)
    return result


def revalidate(old_plan, new_observation, baseline, now=None):
    current = now or datetime.now(timezone.utc)
    copy_plan = dict(old_plan)
    supplied = copy_plan.pop('planChecksum', None)
    if old_plan.get('kind') != 'c2a-plan' or old_plan.get('applyBlocked') is not True or supplied != fingerprint(copy_plan):
        raise ValueError('Invalid plan checksum')
    if old_plan.get('evidenceExpiresAt') is None or current >= parse_time(old_plan['evidenceExpiresAt']):
        raise ValueError('Plan evidence expired; produce a new plan')
    verify_observation(new_observation, baseline, current)
    if old_plan.get('baselineFingerprint') != fingerprint(baseline):
        raise ValueError('Baseline differs from original plan')
    if (new_observation['observationId'] == old_plan.get('observationId')
            or parse_time(new_observation['observedAt']) <= parse_time(old_plan['createdAt'])):
        raise ValueError('Revalidation requires a new observation after plan creation')
    if new_observation['gitCommit'] != old_plan['commit']:
        raise ValueError('Git commit changed')
    if new_observation['identity'] != old_plan.get('observerIdentity'):
        raise ValueError('Observer identity changed; produce a new plan')
    changes = []
    previous = old_plan['diagnosis']
    for key in sorted(set(previous) | set(new_observation['files']) | set(new_observation['missing'])):
        row = new_observation['files'].get(key)
        prev = previous.get(key)
        current_state = row if row is not None else new_observation['missing'].get(key)
        if prev is None or prev.get('observedStateFingerprint') != fingerprint(current_state):
            changes.append(key)
    return {'schemaVersion': 1, 'kind': 'c2a-revalidation', 'applyBlocked': True,
            'planChecksum': supplied, 'observationId': new_observation['observationId'],
            'validatedAt': stamp(current), 'changedKeys': changes,
            'status': 'CHANGED_NEW_PLAN_REQUIRED' if changes else 'NO_CHANGE_STILL_BLOCKED',
            'writerExclusionEstablished': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--read-s3', action='store_true', help='Explicitly enable read-only AWS calls')
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--baseline-audit', type=Path, required=True)
    p.add_argument('--repo', default='.')
    p.add_argument('--profile', default='vms-web-infra')
    p.add_argument('--verify-unknown', action='store_true', help='GET bodies for unknown/new versions')
    p.add_argument('--check-delete-markers', action='store_true', help='List versions for missing baseline keys')
    p.add_argument('--ttl-seconds', type=int, default=300, choices=range(1, 901), metavar='1..900')
    p.add_argument('--observation-output', type=Path, required=True)
    p.add_argument('--plan-output', type=Path, required=True)
    p.add_argument('--revalidate-plan', type=Path)
    args = p.parse_args()
    if not args.read_s3:
        p.error('No AWS calls made: --read-s3 is required')
    if args.observation_output.resolve() == args.plan_output.resolve():
        p.error('Output paths must differ')
    baseline = verify_baseline_source(load_json(args.baseline), load_json(args.baseline_audit))
    desired = inventory(args.repo)
    # Validate any old plan and reserve both outputs before AWS calls.
    old_plan = load_json(args.revalidate_plan) if args.revalidate_plan else None
    if old_plan is not None:
        old_copy = dict(old_plan)
        checksum = old_copy.pop('planChecksum', None)
        if (old_plan.get('kind') != 'c2a-plan' or old_plan.get('applyBlocked') is not True
                or checksum != fingerprint(old_copy) or old_plan.get('commit') != desired['commit']
                or old_plan.get('baselineFingerprint') != fingerprint(baseline)
                or datetime.now(timezone.utc) >= parse_time(old_plan['evidenceExpiresAt'])):
            p.error('Invalid, expired or unrelated plan; no AWS calls made')
    with args.observation_output.open('x', encoding='utf-8') as stream:
        json.dump({'kind': 'c2a-read-only-observation', 'observationComplete': False, 'applyBlocked': True}, stream)
    with args.plan_output.open('x', encoding='utf-8') as stream:
        json.dump({'kind': 'c2a-plan', 'status': 'in-progress', 'applyBlocked': True}, stream)
    try:
        with tempfile.TemporaryDirectory(prefix='c2a-observe-') as temp:
            reader = ReadOnlyC2(Path(temp), args.profile)
            try:
                obs = observe_safe(reader, baseline, desired['commit'], verify_unknown=args.verify_unknown,
                                   check_delete_markers=args.check_delete_markers, ttl_seconds=args.ttl_seconds)
            finally:
                reader.close()
        # Atomic report write via existing audit helper; never save raw exception details.
        from audit_s3 import write_report
        write_report(args.observation_output, obs)
        if old_plan is not None:
            result = revalidate(old_plan, obs, baseline)
        else:
            result = plan(desired, baseline, obs)
        write_report(args.plan_output, result)
        print(json.dumps({'status': result['status'], 'applyBlocked': True,
                          'observationComplete': obs['observationComplete'],
                          'observation': str(args.observation_output), 'plan': str(args.plan_output)}))
        return 0 if obs['observationComplete'] else 1
    except (Exception, KeyboardInterrupt):
        print('Observation or planning failed; apply remains blocked.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
