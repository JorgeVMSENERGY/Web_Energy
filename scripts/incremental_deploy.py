"""Git-to-S3 incremental website publisher.

Plan is offline and contains no AWS calls. Apply requires GitHub Actions on the
protected main/production route and OIDC identity. Deletes are intentionally
unsupported. This is a practical publisher, NOT a distributed writer lock.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote

from deploy_plan import inventory, safe_key

ACCOUNT = '787140332697'
BUCKET = 'vmsenergy-web-prod-787140332697'
DISTRIBUTION = 'E3FWVCRXQ69DI6'
REPOSITORY = 'JorgeVMSENERGY/Web_Energy'
ROLE = 'VMSWebGitHubProductionDeploy'
REGION = 'us-east-1'
CACHE_CONTROL = 'public,max-age=300,must-revalidate'
# Explicitly preserve the 14 S3 Content-Type values sampled in production on
# 2026-11-12; fail closed for an extension not yet reviewed.
CONTENT_TYPES = {
    '.css': 'text/css',
    '.html': 'text/html',
    '.ico': 'image/vnd.microsoft.icon',
    '.ini': 'binary/octet-stream',
    '.jpg': 'image/jpeg',
    '.js': 'text/javascript',
    '.json': 'application/json',
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
    '.txt': 'text/plain',
    '.webp': 'image/webp',
    '.xml': 'application/xml',
    '.zip': 'application/zip',
}
PROTECTED = frozenset(('catalogo-proyectos.html', 'proyectos-vms.html'))
OID = re.compile(r'[0-9a-f]{40}')


class DeployError(RuntimeError):
    pass


def run(arguments, *, stdout=None):
    result = subprocess.run(arguments, stdout=stdout or subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        tail = result.stderr.decode('utf-8', 'replace')[-900:]
        raise DeployError(f'Command failed ({result.returncode}): {arguments[:3]!r}; {tail}')
    return result.stdout if stdout is None else b''


def git(repo, *args):
    return run(['git', '-C', str(repo), *args])


def resolve(repo, ref):
    value = git(repo, 'rev-parse', '--verify', ref + '^{commit}').decode('ascii').strip()
    if not OID.fullmatch(value):
        raise DeployError('Invalid Git commit SHA')
    return value


def fingerprint(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True).encode('ascii')).hexdigest()


def content_type_for(key):
    suffix = Path(key).suffix.lower()
    value = CONTENT_TYPES.get(suffix)
    if value is None:
        raise DeployError('Unapproved Content-Type for object: ' + key)
    return value


def invalidation_paths(operations):
    if not operations:
        return []
    # Until distribution URI rewrites/aliases are reviewed, HTML changes require
    # a conservative broad invalidation. Non-HTML assets use exact-key prefixes
    # to cover possible cache-key query-string variants.
    if any(item['key'].lower().endswith(('.html', '.htm')) for item in operations):
        return ['/*']
    paths = set()
    for item in operations:
        key = item['key']
        if '~' in key or '*' in key:
            return ['/*']
        path = '/' + quote(key, safe='/-._') + '*'
        if len(path) > 4000:
            return ['/*']
        paths.add(path)
    if len(paths) > 1000:
        return ['/*']
    return sorted(paths)


def make_plan(repo, previous, current):
    repo = Path(repo).resolve()
    prev = resolve(repo, previous)
    target = resolve(repo, current)
    head = resolve(repo, 'HEAD')
    if target != head:
        raise DeployError('Target must equal checked-out HEAD')
    ancestor = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', prev, target],
                              check=False, capture_output=True)
    if ancestor.returncode:
        raise DeployError('Previous successful deployment is not an ancestor of HEAD')
    old_inventory = inventory(str(repo), prev)
    new_inventory = inventory(str(repo), target)
    old = old_inventory['files']
    new = new_inventory['files']
    operations = []
    for key in sorted(set(old) | set(new)):
        before = old.get(key)
        after = new.get(key)
        if before == after:
            continue
        safe_key(key)
        action = 'CREATE' if before is None else 'DELETE' if after is None else 'UPDATE'
        operations.append({'action': action, 'key': key, 'previous': before, 'desired': after})
    blockers = []
    if any(item['action'] in ('CREATE', 'UPDATE') and
           Path(item['key']).suffix.lower() not in CONTENT_TYPES
           for item in operations):
        blockers.append('UNSUPPORTED_CONTENT_TYPE')
    if any(item['action'] == 'DELETE' for item in operations):
        blockers.append('DELETIONS_REQUIRE_SEPARATE_APPROVAL')
    if any(item['key'] in PROTECTED for item in operations):
        blockers.append('PROTECTED_STAGING_PAGE_CHANGED')
    counts = {kind: sum(op['action'] == kind for op in operations)
              for kind in ('CREATE', 'UPDATE', 'DELETE')}
    result = {
        'schema': 1, 'kind': 'vms-incremental-deploy-plan',
        'repository': REPOSITORY, 'account': ACCOUNT, 'bucket': BUCKET,
        'distribution': DISTRIBUTION, 'region': REGION,
        'previousSuccessfulCommit': prev, 'targetCommit': target,
        'sourceFiles': len(new), 'previousFiles': len(old),
        'counts': counts, 'operations': operations,
        'invalidationPaths': invalidation_paths(operations),
        'status': 'BLOCKED' if blockers else 'NO-OP' if not operations else 'PROPOSED',
        'blockers': blockers, 'applyBlocked': True,
        'authorization': {'granted': False, 'scope': 'offline-plan-only'},
        'limitations': ['no-distributed-writer-lock', 'external-S3-drift-not-covered',
                        'no-automatic-delete', 'HTML-invalidation-conservative']
    }
    result['planChecksum'] = fingerprint(result)
    return result


def load_plan(path):
    with Path(path).open('r', encoding='utf-8') as stream:
        doc = json.load(stream)
    if not isinstance(doc, dict):
        raise DeployError('Invalid plan format')
    original = dict(doc)
    checksum = original.pop('planChecksum', None)
    if checksum != fingerprint(original):
        raise DeployError('Plan checksum mismatch')
    if (doc.get('schema') != 1 or doc.get('kind') != 'vms-incremental-deploy-plan'
            or doc.get('repository') != REPOSITORY or doc.get('account') != ACCOUNT
            or doc.get('bucket') != BUCKET or doc.get('distribution') != DISTRIBUTION):
        raise DeployError('Plan identity mismatch')
    return doc


def aws(*args):
    return run(['aws', *args, '--region', REGION, '--no-cli-pager'])


def aws_json(*args):
    return json.loads(aws(*args).decode('utf-8'))


def verify_workflow_identity(commit):
    required = {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_SHA': commit}
    for key, value in required.items():
        if os.environ.get(key) != value:
            raise DeployError(f'Apply not allowed outside approved GitHub main: {key}')
    identity = aws_json('sts', 'get-caller-identity')
    arn = identity.get('Arn', '')
    if (identity.get('Account') != ACCOUNT or
            not re.fullmatch(r'arn:aws:sts::' + ACCOUNT + r':assumed-role/' + ROLE + r'/[^/]+', arn)):
        raise DeployError('Unexpected AWS caller identity')


def blob_id(repo, commit, key):
    # Git handles Unicode and punctuation without passing content through a shell.
    safe_key(key)
    value = git(repo, 'rev-parse', '--verify', commit + ':' + key).decode('ascii').strip()
    if not OID.fullmatch(value):
        raise DeployError('Invalid Git blob ID')
    typ = git(repo, 'cat-file', '-t', value).decode('ascii').strip()
    if typ != 'blob':
        raise DeployError('Non-blob Git item cannot be deployed')
    return value


def stage_blob(repo, commit, op, tempdir):
    key = safe_key(op['key'])
    expected = op['desired']
    if op['action'] not in ('CREATE', 'UPDATE') or not isinstance(expected, dict):
        raise DeployError('Only additions and modifications may be published')
    destination = Path(tempdir) / key
    destination.parent.mkdir(parents=True, exist_ok=True)
    oid = blob_id(repo, commit, key)
    proc = subprocess.Popen(['git', '-C', str(repo), 'cat-file', 'blob', oid],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sha = hashlib.sha256()
    count = 0
    try:
        with destination.open('xb') as output:
            while True:
                chunk = proc.stdout.read(1024 * 1024)
                if not chunk:
                    break
                sha.update(chunk)
                count += len(chunk)
                output.write(chunk)
        stderr = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
        if proc.wait() != 0:
            raise DeployError('Git blob extraction failed: ' + stderr.decode('utf-8', 'replace')[-500:])
        if count != expected.get('size') or sha.hexdigest() != expected.get('sha256'):
            raise DeployError('Staged Git blob does not match planned SHA-256')
        return destination
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def verify_s3_content(key, desired, tempdir):
    head_args = ('s3api', 'head-object', '--bucket', BUCKET, '--key', key,
                 '--expected-bucket-owner', ACCOUNT)
    before = aws_json(*head_args)
    if (before.get('ContentLength') != desired['size']
            or before.get('ServerSideEncryption') != 'AES256'
            or before.get('CacheControl') != CACHE_CONTROL
            or before.get('ContentType') != content_type_for(key)):
        raise DeployError('Uploaded object metadata is not as expected: ' + key)
    version = before.get('VersionId')
    if not version or version == 'null':
        raise DeployError('Versioning evidence missing for: ' + key)
    download = Path(tempdir) / '_check' / key
    download.parent.mkdir(parents=True, exist_ok=True)
    response = aws_json('s3api', 'get-object', '--bucket', BUCKET, '--key', key,
                        '--expected-bucket-owner', ACCOUNT, str(download))
    if response.get('VersionId') != version:
        raise DeployError('Object version changed during GET: ' + key)
    sha = hashlib.sha256()
    size = 0
    with download.open('rb') as stream:
        while (chunk := stream.read(1024 * 1024)):
            sha.update(chunk)
            size += len(chunk)
    download.unlink()
    if size != desired['size'] or sha.hexdigest() != desired['sha256']:
        raise DeployError('Post-upload S3 SHA-256 mismatch: ' + key)
    after = aws_json(*head_args)
    if after.get('VersionId') != version or after.get('ContentLength') != size:
        raise DeployError('Object changed after verification: ' + key)


def apply(repo, plan):
    repo = Path(repo).resolve()
    if plan.get('blockers') or plan.get('status') == 'BLOCKED':
        raise DeployError('Deploy blocked: ' + ', '.join(plan.get('blockers', [])))
    current = resolve(repo, 'HEAD')
    if current != plan['targetCommit']:
        raise DeployError('HEAD does not match plan')
    if plan.get('status') == 'NO-OP' and not plan['operations']:
        print(json.dumps({'status': 'NO-OP', 'writes': 0, 'invalidations': 0}))
        return
    # Verify both the plan content and its Git source have not changed.
    if plan != make_plan(repo, plan['previousSuccessfulCommit'], current):
        raise DeployError('Plan differs from fresh Git inventory')
    # Verify every planned content type before any S3 object is written.
    for operation in plan['operations']:
        if operation['action'] in ('CREATE', 'UPDATE'):
            content_type_for(operation['key'])
    verify_workflow_identity(current)
    with tempfile.TemporaryDirectory(prefix='vms-incremental-') as tempdir:
        for operation in plan['operations']:
            if operation['action'] not in ('CREATE', 'UPDATE'):
                raise DeployError('Automatic deletes are disabled')
            key = operation['key']
            content_type = content_type_for(key)
            staged = stage_blob(repo, current, operation, tempdir)
            aws('s3', 'cp', str(staged), 's3://' + BUCKET + '/' + key,
                '--cache-control', CACHE_CONTROL, '--content-type', content_type,
                '--sse', 'AES256',
                '--checksum-algorithm', 'SHA256', '--only-show-errors', '--no-progress')
            verify_s3_content(key, operation['desired'], tempdir)
            print('Verified SHA-256: ' + key, flush=True)
    paths = plan['invalidationPaths']
    if not paths:
        raise DeployError('Changes without invalidation paths; no cache action possible')
    invalidation_id = aws('cloudfront', 'create-invalidation', '--distribution-id',
                          DISTRIBUTION, '--paths', *paths, '--query', 'Invalidation.Id',
                          '--output', 'text').decode('utf-8').strip()
    if not invalidation_id or invalidation_id == 'None':
        raise DeployError('CloudFront did not return an invalidation ID')
    aws('cloudfront', 'wait', 'invalidation-completed', '--distribution-id',
        DISTRIBUTION, '--id', invalidation_id)
    print(json.dumps({'status': 'DEPLOYED', 'writes': len(plan['operations']),
                      'invalidations': len(paths), 'invalidationId': invalidation_id}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('plan', help='Offline plan; no AWS calls')
    p.add_argument('--repo', default='.')
    p.add_argument('--previous', required=True)
    p.add_argument('--current', default='HEAD')
    p.add_argument('--output', type=Path, required=True)
    a = subs.add_parser('apply', help='GitHub production role only; writes changed keys')
    a.add_argument('--repo', default='.')
    a.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'plan':
            result = make_plan(args.repo, args.previous, args.current)
            # Never overwrite an existing plan accidentally.
            with args.output.open('x', encoding='utf-8', newline='\n') as stream:
                json.dump(result, stream, sort_keys=True, indent=2, ensure_ascii=True)
                stream.write('\n')
            print(json.dumps({'status': result['status'], 'counts': result['counts'],
                              'invalidations': len(result['invalidationPaths']),
                              'blockers': result['blockers'], 'output': str(args.output)}))
        else:
            apply(args.repo, load_plan(args.plan))
        return 0
    except (ValueError, KeyError, DeployError, OSError, json.JSONDecodeError) as e:
        print('INCREMENTAL_DEPLOY_ERROR: ' + str(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
