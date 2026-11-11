"""Offline deployment planner. No AWS client and no apply mode."""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from urllib.parse import quote

from git_batch import GitBatch, OID, timing


TOP = {'.github', '.claude', 'infra', 'scripts', '_dist', '_nucleo-wordpress',
       'assets - copia', 'vms_web_backup'}
EXACT = {'.gitignore', 'assets/img/solar.zip', 'especialidades', 'stand-rim-2026 3.html'}


def publishable(key):
    p = PurePosixPath(key)
    return (p.parts[0].lower() not in TOP and key.lower() not in EXACT
            and p.suffix.lower() not in {'.md', '.csv'}
            and not fnmatch.fnmatchcase(p.name.lower(), '*.bak*'))


def safe_key(key):
    if (not isinstance(key, str) or not key or key.startswith('/')
            or '\\' in key or any(x in ('', '.', '..') for x in key.split('/'))
            or any(ord(x) < 32 for x in key)):
        raise ValueError('Unsafe object key: %r' % key)
    return key


def digest(data):
    return {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}


def load_json(path):
    """Reject duplicate JSON members rather than silently retaining the last one."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON member: ' + key)
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError('Invalid JSON constant: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                      object_pairs_hook=unique, parse_constant=invalid_constant)


def s3_metadata(document):
    """Validate one root listing or CLI aggregate; never manufacture SHA evidence."""
    if not isinstance(document, dict):
        raise ValueError('S3 snapshot must be a JSON object')
    if document.get('Prefix', '') != '':
        raise ValueError('Partial Prefix is not a root inventory')
    for field in ('Delimiter', 'StartAfter', 'ContinuationToken',
                  'NextContinuationToken', 'NextToken', 'EncodingType'):
        if document.get(field) not in (None, ''):
            raise ValueError('Partial, encoded or paginated snapshot: ' + field)
    if document.get('CommonPrefixes') not in (None, []):
        raise ValueError('Partial or malformed CommonPrefixes')
    if 'IsTruncated' in document and type(document['IsTruncated']) is not bool:
        raise ValueError('IsTruncated must be boolean')
    if document.get('IsTruncated') is True:
        raise ValueError('Truncated snapshot')
    contents = document.get('Contents')
    if 'Contents' not in document and document.get('KeyCount') == 0 and document.get('IsTruncated') is False:
        contents = []
    if not isinstance(contents, list):
        raise ValueError('Contents must be an array')
    for field in ('KeyCount', 'MaxKeys'):
        if field in document and (type(document[field]) is not int or document[field] < 0):
            raise ValueError('Invalid ' + field)
    if 'KeyCount' in document and document['KeyCount'] != len(contents):
        raise ValueError('KeyCount disagrees with Contents')
    if 'MaxKeys' in document and len(contents) > document['MaxKeys']:
        raise ValueError('Contents exceeds MaxKeys')
    objects = {}
    for entry in contents:
        if not isinstance(entry, dict):
            raise ValueError('Invalid S3 entry')
        key = safe_key(entry.get('Key'))
        size = entry.get('Size')
        if type(size) is not int or size < 0:
            raise ValueError('Invalid Size for ' + key)
        if key in objects:
            raise ValueError('Duplicate S3 key: ' + key)
        objects[key] = size
    return objects, document.get('IsTruncated') is False


def compare_metadata(desired, document):
    target = validate(desired)
    remote, declared_untruncated = s3_metadata(document)
    common = set(target) & set(remote)
    equal = sorted(k for k in common if target[k]['size'] == remote[k])
    different = sorted(common - set(equal))
    missing = sorted(set(target) - set(remote))
    extra = sorted(set(remote) - set(target))
    def row(key):
        return {'key': key, 'gitSize': target[key]['size'] if key in target else None,
                's3Size': remote.get(key)}
    return {
        'schema': 1, 'comparison': 'snapshot-key-size-only', 'commit': desired.get('commit'),
        'applyBlocked': True, 'byteEquality': 'not-verified',
        'coverage': ('declared-untruncated-root-listing' if declared_untruncated
                     else 'unproven-missing-IsTruncated'),
        'reason': 'Metadata cannot establish SHA256 equality or authorize apply',
        'counts': {'gitFiles': len(target), 'snapshotObjects': len(remote),
                   'sameKeyAndSize': len(equal), 'missingInSnapshot': len(missing),
                   'extraInSnapshot': len(extra), 'sizeDifferences': len(different)},
        'sameKeyAndSize': [row(k) for k in equal],
        'missingInSnapshot': [row(k) for k in missing],
        'extraInSnapshot': [row(k) for k in extra],
        'sizeDifferences': [row(k) for k in different]}


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def inventory(repo, ref='HEAD', performance=None):
    def run(*args):
        if performance is None:
            return git(repo, *args)
        return performance.call('git.' + args[0], git, repo, *args)

    def resolve(reference):
        value = run('rev-parse', '--verify', reference + '^{commit}').strip()
        if not OID.fullmatch(value):
            raise ValueError('Invalid Git commit OID')
        return value.decode('ascii')

    head = resolve('HEAD')
    commit = head if ref == 'HEAD' else resolve(ref)
    # Pin the tree once. Paths never enter the batch request protocol: only OIDs.
    tree = run('ls-tree', '-rzl', '--full-tree', commit)
    if tree and not tree.endswith(b'\0'):
        raise ValueError('Truncated Git tree listing')
    entries, seen = [], set()
    for row in tree.split(b'\0'):
        if not row:
            continue
        meta, name = row.split(b'\t', 1)
        mode, kind, oid, size = meta.split()
        key = safe_key(name.decode('utf-8'))
        if not publishable(key):
            continue
        if mode not in (b'100644', b'100755') or kind != b'blob':
            raise ValueError('Unsupported Git entry: ' + key)
        if (not OID.fullmatch(oid) or not size.isdigit()
                or (len(size) > 1 and size.startswith(b'0')) or key in seen):
            raise ValueError('Invalid or duplicate Git tree entry')
        seen.add(key)
        entries.append((key, oid, int(size)))
    files = {}
    with timing(performance, 'git.cat-file'), GitBatch(repo, performance) as batch:
        for key, oid, size in entries:
            files[key] = batch.digest(oid, size)
    if resolve('HEAD') != head:
        raise ValueError('HEAD changed during Git inventory')
    return {'schema': 1, 'commit': commit, 'files': dict(sorted(files.items()))}


def validate(document):
    if document.get('schema') != 1 or not isinstance(document.get('files'), dict):
        raise ValueError('Invalid manifest schema')
    for key, entry in document['files'].items():
        safe_key(key)
        sha = entry.get('sha256', '')
        if (len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)
                or type(entry.get('size')) is not int or entry['size'] < 0):
            raise ValueError('Invalid content record: ' + key)
    return document['files']


def plan(desired, observed=None, confirmed=None, force=False):
    target = validate(desired)
    previous = validate(confirmed) if confirmed is not None else None
    if observed is None:
        return {'schema': 1, 'mode': 'simulation', 'blocked': True,
                'reason': 'Complete remote byte verification required',
                'inventoryCount': len(target)}
    remote = validate(observed)
    if observed.get('complete') is not True or observed.get('verification') != 'download-sha256':
        raise ValueError('Remote evidence must cover complete listing and downloaded bytes')
    uploads = sorted(k for k in target if remote.get(k) != target[k])
    deletes = sorted(k for k in remote if k not in target and publishable(k))
    unmanaged = sorted(k for k in remote if k not in target and not publishable(k))
    drift = sorted(k for k in set(remote) | set(previous or {})
                   if previous is not None and remote.get(k) != previous.get(k))
    paths = {'/' + quote(k, safe='/~-._') for k in uploads + deletes}
    if 'index.html' in uploads + deletes:
        paths.add('/')
    return {'schema': 1, 'mode': 'simulation', 'blocked': False,
            'commit': desired.get('commit'), 'uploads': uploads, 'deletes': deletes,
            'unchanged': sorted(set(target) - set(uploads)), 'drift': drift,
            'unmanagedRemote': unmanaged, 'invalidations': sorted(paths),
            'forceReconcile': force, 'manifestRecovery': previous is None,
            'requiresFreshVerificationBeforeApply': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--observed', type=Path)
    parser.add_argument('--confirmed', type=Path)
    parser.add_argument('--force-reconcile', action='store_true')
    parser.add_argument('--s3-metadata', type=Path,
                        help='Offline ListObjectsV2 JSON; never SHA256 evidence')
    parser.add_argument('--output', type=Path, help='Write JSON locally (UTF-8)')
    args = parser.parse_args()
    # Validate inputs before the potentially expensive Git blob inventory.
    observed = load_json(args.observed) if args.observed else None
    confirmed = load_json(args.confirmed) if args.confirmed else None
    metadata = load_json(args.s3_metadata) if args.s3_metadata else None
    if args.s3_metadata:
        s3_metadata(metadata)
    desired = inventory(args.repo, args.ref)
    result = {'inventory': desired, 'plan': plan(desired, observed,
              confirmed, args.force_reconcile)}
    if args.s3_metadata:
        result['metadataComparison'] = compare_metadata(desired, metadata)
    output = json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + '\n'
    if args.output:
        protected = [args.observed, args.confirmed, args.s3_metadata]
        if any(p and p.resolve() == args.output.resolve() for p in protected):
            parser.error('--output must not overwrite an input snapshot/manifest')
        # Exclusive creation prevents accidental overwrites of repository files.
        with args.output.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(output)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
