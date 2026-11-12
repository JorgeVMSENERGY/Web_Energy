"""C1: offline-only proposal and persistent simulation. No AWS clients or apply mode."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote

from audit_s3 import ACCOUNT, BUCKET, COMMIT
from deploy_plan import inventory, load_json, publishable, safe_key, validate

DISTRIBUTION = 'E3FWVCRXQ69DI6'
REGION = 'us-east-1'
STAGING = frozenset(('catalogo-proyectos.html', 'proyectos-vms.html'))
METADATA_FIELDS = ('ContentType', 'CacheControl', 'ContentEncoding', 'ContentDisposition',
                   'ContentLanguage', 'Expires', 'Metadata', 'Tags', 'StorageClass',
                   'ServerSideEncryption', 'SSEKMSKeyId', 'WebsiteRedirectLocation',
                   'ObjectLock', 'ACL')


def canonical(document):
    return json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')


def fingerprint(document):
    return hashlib.sha256(canonical(document)).hexdigest()


def content(record):
    if not isinstance(record, dict):
        raise ValueError('Invalid content record')
    size, sha = record.get('size'), record.get('sha256')
    if type(size) is not int or size < 0 or not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Invalid SHA256 or size')
    return {'sha256': sha, 'size': size}


def commit_id(value):
    if not isinstance(value, str) or len(value) not in (40, 64) or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Invalid Git commit')
    return value


def version(value):
    if not isinstance(value, str) or not value or value == 'null':
        raise ValueError('Missing object version')
    return value


def unknown_metadata():
    # The auditor checked SSE-S3 but did not capture other object metadata.
    return {'complete': False, 'values': {'ServerSideEncryption': 'AES256'}}


def checked_metadata(meta):
    if not isinstance(meta, dict) or type(meta.get('complete')) is not bool or not isinstance(meta.get('values'), dict):
        raise ValueError('Invalid metadata evidence')
    values = meta['values']
    if meta['complete']:
        if set(values) != set(METADATA_FIELDS):
            raise ValueError('Complete metadata must explicitly include every supported field')
        if values['ServerSideEncryption'] != 'AES256':
            raise ValueError('Only the established AES256 policy is modelled')
        if not isinstance(values['ContentType'], str) or not values['ContentType']:
            raise ValueError('ContentType is required')
        if not isinstance(values['CacheControl'], str) or not values['CacheControl']:
            raise ValueError('CacheControl is required')
        if not isinstance(values['Metadata'], dict) or not isinstance(values['Tags'], dict):
            raise ValueError('Metadata and Tags must be explicitly captured')
    return copy.deepcopy(meta)


def from_audit(report, kind):
    """Import an existing complete auditor report as historical, never as authorization."""
    if kind not in ('historical-baseline', 'remote-observation') or not isinstance(report, dict):
        raise ValueError('Invalid evidence')
    if (report.get('schema') != 1 or report.get('kind') != 'read-only-s3-audit'
            or report.get('mode') != 'content-audit' or report.get('auditComplete') is not True
            or report.get('applyBlocked') is not True or report.get('status') not in ('matches-git', 'differences-found')
            or report.get('bucket') != BUCKET or report.get('expectedAccount') != ACCOUNT
            or report.get('identity', {}).get('account') != ACCOUNT or report.get('maxObjects') is not None):
        raise ValueError('Incomplete or wrong-account audit evidence')
    if kind == 'historical-baseline' and report.get('status') != 'matches-git':
        raise ValueError('Baseline requires an entirely matching audit')
    commit = commit_id(report.get('commit'))
    if report.get('expectedCommit') != commit or not report.get('finishedAt'):
        raise ValueError('Unpinned or undated audit')
    rows = report.get('objects')
    if not isinstance(rows, dict):
        raise ValueError('Missing object records')
    files, statuses = {}, {'match': 0, 'drift': 0, 'missing-in-s3': 0, 'extra-in-s3': 0}
    for key, row in rows.items():
        safe_key(key)
        if not isinstance(row, dict) or row.get('status') not in statuses:
            raise ValueError('Incomplete object evidence')
        state = row['status']
        statuses[state] += 1
        if state == 'missing-in-s3':
            if row.get('s3Size') is not None:
                raise ValueError('Contradictory missing object')
            continue
        record = content({'size': row.get('s3Size'), 'sha256': row.get('sha256')})
        if row.get('downloadedBytes') != record['size']:
            raise ValueError('Downloaded length not verified')
        ver = version(row.get('versionBefore'))
        if any(row.get(field) != ver for field in ('versionGet', 'versionAfter', 'versionFinal')):
            raise ValueError('Object version changed or missing')
        git_record = row.get('git')
        if state == 'match' and (git_record is None or content(git_record) != record):
            raise ValueError('Contradictory match')
        if state == 'drift' and (git_record is None or content(git_record) == record):
            raise ValueError('Contradictory drift')
        if state == 'extra-in-s3' and git_record is not None:
            raise ValueError('Contradictory extra object')
        files[key] = dict(record, versionId=ver, metadata=unknown_metadata())
    counts = report.get('counts', {})
    expected = {'matches': statuses['match'], 'drift': statuses['drift'],
                'missing': statuses['missing-in-s3'], 'extra': statuses['extra-in-s3'],
                's3Objects': len(files), 'objectsHashed': len(files),
                'gitFiles': len(rows) - statuses['extra-in-s3']}
    if any(type(counts.get(k)) is not int or counts[k] != value for k, value in expected.items()):
        raise ValueError('Inconsistent audit counts')
    if report['status'] == 'matches-git' and (any(statuses[x] for x in ('drift', 'missing-in-s3', 'extra-in-s3'))):
        raise ValueError('Inconsistent matching audit status')
    return {'schemaVersion': 1, 'kind': kind, 'applyBlocked': True, 'account': ACCOUNT, 'bucket': BUCKET,
            'region': REGION, 'distributionId': DISTRIBUTION, 'commit': commit,
            'evidence': {'kind': 'read-only-s3-audit', 'sha256': fingerprint(report),
                         'finishedAt': report['finishedAt'], 'freshness': 'not-established',
                         'writerExclusion': 'not-established', 'trustedProvenance': False},
            'files': dict(sorted(files.items()))}


def checked_snapshot(snapshot, kind):
    if (not isinstance(snapshot, dict) or snapshot.get('schemaVersion') != 1 or snapshot.get('kind') != kind
            or snapshot.get('applyBlocked') is not True
            or snapshot.get('account') != ACCOUNT or snapshot.get('bucket') != BUCKET
            or snapshot.get('region') != REGION or snapshot.get('distributionId') != DISTRIBUTION
            or not isinstance(snapshot.get('files'), dict) or not isinstance(snapshot.get('evidence'), dict)):
        raise ValueError('Invalid snapshot identity or schema')
    commit_id(snapshot.get('commit'))
    evidence = snapshot['evidence']
    evidence_sha = evidence.get('sha256')
    if (not isinstance(evidence_sha, str) or len(evidence_sha) != 64
            or any(c not in '0123456789abcdef' for c in evidence_sha)
            or not isinstance(evidence.get('finishedAt'), str) or not evidence['finishedAt']):
        raise ValueError('Missing evidence provenance')
    for key, row in snapshot['files'].items():
        safe_key(key)
        content(row)
        version(row.get('versionId'))
        checked_metadata(row.get('metadata'))
    return snapshot['files']


def edge_path(key):
    # CloudFront does not accept ~ even encoded; query behavior needs separate review.
    if '~' in key or '*' in key:
        raise ValueError('CloudFront invalidation path requires manual review')
    path = '/' + quote(key, safe='/-._')
    if len(path) > 4000:
        raise ValueError('CloudFront invalidation path too long')
    return path


def build_plan(desired, baseline, observed, create_metadata=None):
    """Three-way comparison. Always returns a non-authorizing offline proposal."""
    target = validate(desired)
    commit_id(desired.get('commit'))
    result = {'schemaVersion': 1, 'kind': 'c1-offline-plan', 'applyBlocked': True,
              'authorization': {'granted': False, 'mechanism': 'not-implemented'},
              'commit': desired.get('commit'), 'account': ACCOUNT, 'bucket': BUCKET,
              'region': REGION, 'distributionId': DISTRIBUTION,
              'freshEvidenceRequiredBeforeAnyFutureApply': True,
              'writerExclusionRequired': True, 'cacheKeyPolicyVerified': False,
              'deletionGate': 'requires dependency review, old-client/cache grace and separate approval before deletion',
              'evidenceUsage': 'historical-offline-replay-not-current-proof',
              'classifications': {}, 'operations': [], 'uploads': [], 'deletes': [],
              'invalidations': [], 'recovery': {'s3': [], 'cloudFront': []}, 'blockers': []}
    if baseline is None or observed is None:
        result['blockers'].append('MISSING_BASELINE_OR_REMOTE_EVIDENCE')
        result['status'] = 'BLOCKED'
        result['planId'] = fingerprint(result)
        return result
    history = checked_snapshot(baseline, 'historical-baseline')
    remote = checked_snapshot(observed, 'remote-observation')
    result['baselineFingerprint'] = fingerprint(baseline)
    result['observationFingerprint'] = fingerprint(observed)
    result['sameSourceEvidence'] = baseline['evidence'].get('sha256') == observed['evidence'].get('sha256')
    create_metadata = create_metadata or {}
    for key in sorted(set(target) | set(history) | set(remote)):
        wanted, previous, current = target.get(key), history.get(key), remote.get(key)
        drift = ((previous is None) != (current is None))
        if previous is not None and current is not None:
            drift = (content(previous) != content(current) or previous['versionId'] != current['versionId'])
            pm, cm = previous['metadata'], current['metadata']
            if pm['complete'] and cm['complete'] and pm != cm:
                drift = True
        if drift:
            state = 'DRIFT'
        elif wanted is None:
            state = 'DELETE'
        elif current is None:
            state = 'CREATE'
        elif content(wanted) != content(current):
            state = 'UPDATE'
        else:
            state = 'UNCHANGED'
        row = {'classification': state, 'protectedStaging': key in STAGING,
               'desired': copy.deepcopy(wanted), 'previous': copy.deepcopy(previous),
               'observed': copy.deepcopy(current)}
        result['classifications'][key] = row
        if state == 'DRIFT':
            result['blockers'].append('EXTERNAL_DRIFT:' + key)
            continue
        if state == 'UNCHANGED':
            continue
        if key in STAGING:
            result['blockers'].append('STAGING_EXCLUSIVE:' + key)
            continue
        if not publishable(key):
            result['blockers'].append('UNMANAGED_KEY:' + key)
            continue
        metadata = current['metadata'] if current is not None else create_metadata.get(key)
        if metadata is None:
            metadata = {'complete': False, 'values': {}}
        else:
            metadata = checked_metadata(metadata)
        if not metadata['complete']:
            result['blockers'].append('METADATA_UNVERIFIED:' + key)
        if previous is not None and not previous['metadata']['complete']:
            result['blockers'].append('METADATA_HISTORY_UNVERIFIED:' + key)
        try:
            path = edge_path(key)
        except ValueError:
            result['blockers'].append('INVALIDATION_PATH_UNSUPPORTED:' + key)
            path = None
        op = {'action': state, 'key': key, 'desired': copy.deepcopy(wanted),
              'expectedCurrent': copy.deepcopy(current), 'metadataToPreserveOrSet': metadata,
              'previousVersionId': current['versionId'] if current else None,
              'phase': ('DEFERRED_DELETE_AFTER_CACHE_GRACE' if state == 'DELETE' else
                        'HTML_AFTER_ASSETS' if key.lower().endswith(('.html', '.htm')) else 'ASSETS_FIRST'),
              'invalidationPaths': ([path] + (['/'] if key == 'index.html' else [])) if path else [],
              'recovery': {'action': 'RESTORE_PREVIOUS_VERSION' if current else 'REMOVE_CREATED_OBJECT',
                           'previousVersionId': current['versionId'] if current else None,
                           'requiresFreshVersionCheck': True}}
        result['operations'].append(op)
    order = {'ASSETS_FIRST': 0, 'HTML_AFTER_ASSETS': 1, 'DEFERRED_DELETE_AFTER_CACHE_GRACE': 2}
    result['operations'].sort(key=lambda item: (order[item['phase']], item['key']))
    result['uploads'] = [op['key'] for op in result['operations'] if op['action'] in ('CREATE', 'UPDATE')]
    result['deletes'] = [op['key'] for op in result['operations'] if op['action'] == 'DELETE']
    result['invalidations'] = sorted({path for op in result['operations'] for path in op['invalidationPaths']})
    result['recovery']['s3'] = [dict(key=op['key'], **op['recovery']) for op in reversed(result['operations'])]
    result['recovery']['cloudFront'] = list(result['invalidations'])
    result['counts'] = {state: sum(row['classification'] == state for row in result['classifications'].values())
                        for state in ('UNCHANGED', 'CREATE', 'UPDATE', 'DELETE', 'DRIFT')}
    result['status'] = ('BLOCKED' if result['blockers'] else 'NO-OP' if not result['operations'] else 'PROPOSED')
    result['planId'] = fingerprint(result)
    return result


def exclusive_json(path, document):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(document, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write('\n')


def persist_journal(path, journal):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.c1-journal-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(journal, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def start_simulation(plan, observed, path):
    if plan.get('kind') != 'c1-offline-plan' or plan.get('applyBlocked') is not True or plan.get('blockers'):
        raise ValueError('Only unblocked offline proposals can be simulated')
    remote = copy.deepcopy(checked_snapshot(observed, 'remote-observation'))
    if fingerprint(observed) != plan.get('observationFingerprint'):
        raise ValueError('Observation does not match plan')
    journal = {'schemaVersion': 1, 'kind': 'c1-local-simulation-journal', 'applyBlocked': True,
               'planId': plan['planId'], 'plan': copy.deepcopy(plan), 'state': 'pending',
               'steps': [{'operation': copy.deepcopy(op), 'state': 'pending', 'simulatedEffectId': None} for op in plan['operations']],
               'simulatedRemote': remote, 'simulatedInvalidations': {},
               'cacheStep': 'pending' if plan['invalidations'] else 'not-required',
               'simulatedCheckpoints': {'deletionGraceAssumed': False},
               'recovery': {'state': 'not-started', 's3': [], 'cloudFront': []}}
    exclusive_json(path, journal)
    return journal


def validate_journal(journal):
    if (not isinstance(journal, dict) or journal.get('kind') != 'c1-local-simulation-journal'
            or journal.get('schemaVersion') != 1 or journal.get('applyBlocked') is not True
            or journal.get('plan', {}).get('planId') != journal.get('planId')):
        raise ValueError('Invalid simulation journal')
    plan = journal['plan']
    expected = dict(plan)
    supplied = expected.pop('planId', None)
    if supplied != fingerprint(expected) or plan.get('applyBlocked') is not True or plan.get('blockers'):
        raise ValueError('Modified or blocked simulation plan')
    if [s.get('operation') for s in journal.get('steps', [])] != plan['operations']:
        raise ValueError('Simulation journal does not match plan')
    return plan


def simulate(path, fail_at=None, assume_deletion_grace=False):
    """Advance fake state only; intent and simulated effect are durably journaled."""
    journal = load_json(path)
    plan = validate_journal(journal)
    if journal['recovery']['state'] != 'not-started':
        raise ValueError('Recovered journal cannot resume forward execution')
    for i, step in enumerate(journal['steps']):
        if step['state'] == 'done':
            continue
        if step['state'] not in ('pending', 'intent'):
            raise ValueError('Invalid step state')
        op = step['operation']
        remote = journal['simulatedRemote']
        previous = op['expectedCurrent']
        result = None if op['action'] == 'DELETE' else dict(
            copy.deepcopy(op['desired']), versionId='sim-' + fingerprint([plan['planId'], i])[:24],
            metadata=copy.deepcopy(op['metadataToPreserveOrSet']))
        effect_id = 'effect-' + fingerprint([plan['planId'], i])[:24]
        if step['state'] == 'intent' and step.get('simulatedEffectId') == effect_id:
            if remote.get(op['key']) != result or (result is None and op['key'] in remote):
                raise ValueError('Simulated effect no longer matches remote state')
            step['state'] = 'done'
            persist_journal(path, journal)
            continue
        if step.get('simulatedEffectId') is not None or remote.get(op['key']) != previous:
            raise ValueError('Simulated remote state changed; cannot retry')
        if op['action'] == 'DELETE' and not journal['simulatedCheckpoints']['deletionGraceAssumed']:
            if not assume_deletion_grace:
                raise ValueError('Simulation requires explicit hypothetical deletion grace')
            journal['simulatedCheckpoints']['deletionGraceAssumed'] = True
        step['state'] = 'intent'
        journal['state'] = 'in-progress'
        persist_journal(path, journal)
        if fail_at == ('before', i):
            raise RuntimeError('Injected simulated failure before effect')
        if result is None:
            remote.pop(op['key'], None)
        else:
            remote[op['key']] = result
        step['simulatedEffectId'] = effect_id
        persist_journal(path, journal)  # fake state and receipt are persisted together
        if fail_at == ('after-effect', i):
            raise RuntimeError('Injected simulated failure after effect')
        step['state'] = 'done'
        persist_journal(path, journal)
    if plan['invalidations'] and journal['cacheStep'] != 'done':
        caller = 'c1-sim-' + plan['planId']
        journal['cacheStep'] = 'intent'
        persist_journal(path, journal)
        if fail_at == ('cache-before', 0):
            raise RuntimeError('Injected simulated cache failure')
        existing = journal['simulatedInvalidations'].get(caller)
        if existing is not None and existing != plan['invalidations']:
            raise ValueError('Simulated CallerReference conflict')
        journal['simulatedInvalidations'][caller] = list(plan['invalidations'])
        persist_journal(path, journal)
        if fail_at == ('cache-after', 0):
            raise RuntimeError('Injected simulated cache acknowledgement failure')
        journal['cacheStep'] = 'done'
    journal['state'] = 'simulated-complete'
    persist_journal(path, journal)
    return journal


def simulate_recovery(path):
    journal = load_json(path)
    plan = validate_journal(journal)
    recovery = journal['recovery']
    if recovery['state'] == 'complete':
        return journal
    recovery['state'] = 'in-progress'
    persist_journal(path, journal)
    for i in reversed(range(len(journal['steps']))):
        step = journal['steps'][i]
        if step['state'] not in ('done', 'intent'):
            continue
        op = step['operation']
        if any(row['index'] == i for row in recovery['s3']):
            continue
        current = journal['simulatedRemote'].get(op['key'])
        previous = op['expectedCurrent']
        expected = None if op['action'] == 'DELETE' else dict(
            copy.deepcopy(op['desired']), versionId='sim-' + fingerprint([plan['planId'], i])[:24],
            metadata=copy.deepcopy(op['metadataToPreserveOrSet']))
        effect_id = 'effect-' + fingerprint([plan['planId'], i])[:24]
        if step.get('simulatedEffectId') is None and current == previous and step['state'] == 'intent':
            continue  # intent persisted before an effect
        if step.get('simulatedEffectId') != effect_id or current != expected:
            raise ValueError('Simulated concurrent change blocks recovery')
        if previous is None:
            journal['simulatedRemote'].pop(op['key'], None)
        else:
            restored = copy.deepcopy(previous)
            restored['versionId'] = 'sim-restore-' + fingerprint([plan['planId'], i])[:16]
            journal['simulatedRemote'][op['key']] = restored
        recovery['s3'].append({'index': i, 'key': op['key'], **op['recovery']})
        persist_journal(path, journal)
    if recovery['s3']:
        # This is a distinct cache recovery action after restoring S3, not evidence of invalidation.
        caller = 'c1-sim-rollback-' + plan['planId']
        paths = sorted({path for item in recovery['s3'] for path in plan['operations'][item['index']]['invalidationPaths']})
        journal['simulatedInvalidations'][caller] = paths
        recovery['cloudFront'] = paths
    recovery['state'] = 'complete'
    journal['state'] = 'simulated-recovered'
    persist_journal(path, journal)
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    historical_input = parser.add_mutually_exclusive_group(required=True)
    historical_input.add_argument('--historical-audit', type=Path)
    historical_input.add_argument('--baseline', type=Path)
    observed_input = parser.add_mutually_exclusive_group(required=True)
    observed_input.add_argument('--observed-audit', type=Path)
    observed_input.add_argument('--observation', type=Path)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-output', type=Path)
    parser.add_argument('--observation-output', type=Path)
    args = parser.parse_args()
    historical = (from_audit(load_json(args.historical_audit), 'historical-baseline')
                  if args.historical_audit else load_json(args.baseline))
    observation = (from_audit(load_json(args.observed_audit), 'remote-observation')
                   if args.observed_audit else load_json(args.observation))
    checked_snapshot(historical, 'historical-baseline')
    checked_snapshot(observation, 'remote-observation')
    desired = inventory(args.repo, args.ref)
    plan = build_plan(desired, historical, observation)
    # Exclusive local output only; no reference to credentials, SDK or AWS endpoint.
    outputs = [p for p in (args.output, args.baseline_output, args.observation_output) if p]
    if len({p.resolve() for p in outputs}) != len(outputs):
        parser.error('Outputs must differ')
    if args.baseline_output:
        exclusive_json(args.baseline_output, historical)
    if args.observation_output:
        exclusive_json(args.observation_output, observation)
    exclusive_json(args.output, plan)
    print(json.dumps({'status': plan['status'], 'applyBlocked': True, 'counts': plan['counts'],
                      'output': str(args.output), 'sameSourceEvidence': plan['sameSourceEvidence']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
