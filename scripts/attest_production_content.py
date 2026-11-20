#!/usr/bin/env python3
"""Read-only production content attestation for VMS Energy.

CI-only, intended for a separately reviewed workflow using a dedicated, S3
read-only OIDC role. Never changes AWS, Git or the deployed website.

Commands:
  preflight: verify GitHub event, repo, branch, SHA, historical manual commit,
             and exact publishable-byte manifest identity.
  identity:  verify AWS STS identity is the dedicated READ-ONLY audit role.
  verify:    download each S3 object temporarily, compare SHA-256 with Git;
             create a local JSON evidence file (outside the repository).
  recheck:   recheck complete bucket inventory and every object's VersionId.

This script is NOT a replacement for protecting the GitHub branch, workflow,
production environment and the AWS role trust policy. See separate review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPOSITORY = 'JorgeVMSENERGY/Web_Energy'
ACCOUNT = '787140332697'
BUCKET = 'vmsenergy-web-prod-787140332697'
REGION = 'us-east-1'
AUDIT_ROLE = 'VMSWebGitHubProductionAudit'
WORKFLOW = 'attest-production-manual.yml'
MANUAL_COMMIT = 'd159be650c858fbc3bf02b7d59748bf923d7c4a4'
WORKFLOW_REF = (REPOSITORY + '/.github/workflows/' + WORKFLOW +
                '@refs/heads/main')
HEX_SHA = re.compile(r'^[0-9a-f]{40}$')
ALLOWED_AWS = {('sts', 'get-caller-identity'),
               ('s3api', 'list-objects-v2'), ('s3api', 'get-object'),
               ('s3api', 'head-object')}


class AttestationError(RuntimeError):
    pass


def run(argv, *, aws_environment=False):
    env = os.environ.copy()
    if aws_environment:
        env.update({'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                    'AWS_PAGER': ''})
    try:
        p = subprocess.run(argv, capture_output=True, check=False, env=env)
    except OSError as exc:
        raise AttestationError('COMMAND_NOT_FOUND: ' + argv[0]) from exc
    if p.returncode:
        raise AttestationError('COMMAND_FAILED: ' + ' '.join(argv[:4]) +
                               ' EXIT=' + str(p.returncode) + ' STDERR=' +
                               p.stderr.decode('utf-8', 'replace')[-500:])
    return p.stdout


def git(repo, *args):
    return run(['git', '-C', str(repo), *args])


def aws_json(*args):
    if tuple(args[:2]) not in ALLOWED_AWS:
        raise AttestationError('AWS_OPERATION_NOT_ALLOWLISTED')
    raw = run(['aws', '--region', REGION, '--no-cli-pager', '--no-paginate',
               '--output', 'json', *args], aws_environment=True)
    if not raw.strip():
        raise AttestationError('EMPTY_AWS_JSON')
    try:
        doc = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError) as exc:
        raise AttestationError('INVALID_AWS_JSON') from exc
    if not isinstance(doc, dict):
        raise AttestationError('UNEXPECTED_AWS_RESPONSE')
    return doc


def check_github():
    required = {
        'GITHUB_ACTIONS': 'true',
        'GITHUB_REPOSITORY': REPOSITORY,
        'GITHUB_REF': 'refs/heads/main',
        'GITHUB_EVENT_NAME': 'workflow_dispatch',
        'GITHUB_WORKFLOW_REF': WORKFLOW_REF,
    }
    for k, value in required.items():
        if os.environ.get(k) != value:
            raise AttestationError('GITHUB_IDENTITY_INVALID: ' + k)
    sha = os.environ.get('GITHUB_SHA', '')
    if not HEX_SHA.fullmatch(sha):
        raise AttestationError('GITHUB_SHA_INVALID')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    if not run_id.isascii() or not run_id.isdigit() or int(run_id) <= 0:
        raise AttestationError('GITHUB_RUN_ID_INVALID')
    return sha


def git_preflight(repo):
    sha = check_github()
    root = Path(git(repo, 'rev-parse', '--show-toplevel').decode('utf-8').strip()).resolve()
    if not root.samefile(repo.resolve()):
        raise AttestationError('NOT_REPOSITORY_ROOT')
    actual_head = git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode('ascii').strip()
    if sha != actual_head:
        raise AttestationError('GITHUB_SHA_NOT_CHECKED_OUT')
    origin = git(repo, 'remote', 'get-url', 'origin').decode('utf-8').strip()
    valid_remotes = {'https://github.com/' + REPOSITORY,
                     'https://github.com/' + REPOSITORY + '.git',
                     'git@github.com:' + REPOSITORY + '.git'}
    if origin not in valid_remotes:
        raise AttestationError('UNEXPECTED_GIT_ORIGIN')
    if git(repo, 'status', '--porcelain').strip():
        raise AttestationError('DIRTY_WORKTREE')
    ancestor = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor',
                               MANUAL_COMMIT, sha], check=False,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if ancestor.returncode != 0:
        raise AttestationError('MANUAL_COMMIT_NOT_ANCESTOR')
    sys.path.insert(0, str(repo / 'scripts'))
    from deploy_plan import inventory
    manual = inventory(str(repo), MANUAL_COMMIT)
    target = inventory(str(repo), sha)
    if manual['commit'] != MANUAL_COMMIT or target['commit'] != sha:
        raise AttestationError('GIT_MANIFEST_COMMIT_MISMATCH')
    if not manual['files'] or manual['files'] != target['files']:
        raise AttestationError('PUBLISHABLE_CONTENT_DIFFERS_FROM_MANUAL_COMMIT')
    return {'sha': sha, 'manual_commit': MANUAL_COMMIT, 'files': target['files']}


def check_aws_identity():
    obj = aws_json('sts', 'get-caller-identity')
    if obj.get('Account') != ACCOUNT:
        raise AttestationError('WRONG_AWS_ACCOUNT')
    arn = obj.get('Arn')
    pattern = (r'^arn:aws:sts::' + ACCOUNT + ':assumed-role/' +
               re.escape(AUDIT_ROLE) + r'/[^/]+$')
    if not isinstance(arn, str) or not re.fullmatch(pattern, arn):
        raise AttestationError('WRONG_AWS_ROLE')
    return obj


def s3_listing():
    objects = {}
    seen_tokens = set()
    token = None
    for page in range(1, 10001):
        argv = ['s3api', 'list-objects-v2', '--bucket', BUCKET,
                '--expected-bucket-owner', ACCOUNT, '--max-keys', '1000']
        if token is not None:
            argv.extend(['--continuation-token', token])
        doc = aws_json(*argv)
        rows = doc.get('Contents') or []
        if not isinstance(rows, list) or doc.get('KeyCount') != len(rows):
            raise AttestationError('INVALID_S3_PAGE')
        for row in rows:
            if not isinstance(row, dict):
                raise AttestationError('INVALID_S3_ROW')
            key, size, etag = row.get('Key'), row.get('Size'), row.get('ETag')
            if (not isinstance(key, str) or not key or key.startswith('/') or
                    '\\' in key or any(p in ('', '.', '..') for p in key.split('/')) or
                    key in objects or type(size) is not int or size < 0 or
                    not isinstance(etag, str) or not etag):
                raise AttestationError('INVALID_S3_OBJECT_ENTRY')
            objects[key] = {'size': size, 'etag': etag,
                            'modified': row.get('LastModified')}
        truncated = doc.get('IsTruncated')
        if type(truncated) is not bool:
            raise AttestationError('S3_PAGINATION_STATUS_MISSING')
        print('S3_PAGE=' + str(page) + ' OBJECTS=' + str(len(rows)) +
              ' TRUNCATED=' + str(truncated), flush=True)
        if not truncated:
            if doc.get('NextContinuationToken'):
                raise AttestationError('UNEXPECTED_FINAL_S3_TOKEN')
            return objects
        new_token = doc.get('NextContinuationToken')
        if (not isinstance(new_token, str) or not new_token or
                new_token in seen_tokens):
            raise AttestationError('INVALID_CONTINUATION_TOKEN')
        seen_tokens.add(new_token)
        token = new_token
    raise AttestationError('S3_PAGINATION_LIMIT')


def sha256_file(path):
    h = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def verify_objects(manifest, *, listing=None):
    expected = manifest['files']
    listing = s3_listing() if listing is None else listing
    if set(listing) != set(expected):
        raise AttestationError('S3_KEY_SET_DIFFERS_FROM_GIT')
    for key, data in expected.items():
        if listing[key]['size'] != data['size']:
            raise AttestationError('S3_SIZE_DIFFERS_FROM_GIT: ' + key)
    rows, checked_bytes = [], 0
    with tempfile.TemporaryDirectory(prefix='vms-manual-attestation-') as td:
        object_path = Path(td) / 's3-object.bin'
        for idx, key in enumerate(sorted(expected), 1):
            try:
                response = aws_json('s3api', 'get-object', '--bucket', BUCKET,
                                    '--expected-bucket-owner', ACCOUNT,
                                    '--key', key, '--if-match', listing[key]['etag'],
                                    str(object_path))
                actual_sha, actual_size = sha256_file(object_path)
            finally:
                object_path.unlink(missing_ok=True)
            version = response.get('VersionId')
            if not isinstance(version, str) or not version or version == 'null':
                raise AttestationError('OBJECT_VERSION_ID_MISSING: ' + key)
            if (response.get('ETag') != listing[key]['etag'] or
                    response.get('ContentLength') != actual_size or
                    expected[key]['size'] != actual_size or
                    expected[key]['sha256'] != actual_sha):
                raise AttestationError('OBJECT_SHA256_OR_METADATA_MISMATCH: ' + key)
            rows.append({'key': key, 'sha256': actual_sha, 'size': actual_size,
                         'etag': response['ETag'], 'version_id': version})
            checked_bytes += actual_size
            if idx == 1 or idx % 25 == 0 or idx == len(expected):
                print('VERIFIED=' + str(idx) + '/' + str(len(expected)), flush=True)
    return {'schema': 1, 'kind': 'manual-production-content-attestation',
            'repo': REPOSITORY, 'account': ACCOUNT, 'bucket': BUCKET,
            'commit': manifest['sha'], 'manual_commit': MANUAL_COMMIT,
            'bytes': checked_bytes, 'objects': rows, 'listing': listing}


def recheck_objects(manifest, evidence):
    if not isinstance(evidence, dict):
        raise AttestationError('INVALID_EVIDENCE_DOCUMENT')
    items = evidence.get('objects')
    if (evidence.get('schema') != 1 or
            evidence.get('kind') != 'manual-production-content-attestation' or
            evidence.get('repo') != REPOSITORY or
            evidence.get('account') != ACCOUNT or
            evidence.get('bucket') != BUCKET or
            evidence.get('commit') != manifest['sha'] or
            evidence.get('manual_commit') != MANUAL_COMMIT or
            not isinstance(items, list) or len(items) != len(manifest['files'])):
        raise AttestationError('ATTESTATION_EVIDENCE_IDENTITY_MISMATCH')
    listed = evidence.get('listing')
    if not isinstance(listed, dict):
        raise AttestationError('MISSING_INITIAL_S3_LISTING')
    after = s3_listing()
    if after != listed:
        raise AttestationError('S3_LISTING_CHANGED_DURING_ATTESTATION')
    verified_keys = set()
    total = 0
    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise AttestationError('INVALID_EVIDENCE_ROW')
        key = item.get('key')
        if key not in manifest['files'] or key in verified_keys:
            raise AttestationError('UNEXPECTED_OR_DUPLICATE_EVIDENCE_KEY')
        verified_keys.add(key)
        expected = manifest['files'][key]
        if item.get('size') != expected['size'] or item.get('sha256') != expected['sha256']:
            raise AttestationError('EVIDENCE_GIT_DIGEST_MISMATCH: ' + key)
        if item.get('etag') != listed[key]['etag']:
            raise AttestationError('EVIDENCE_ETAG_MISMATCH: ' + key)
        version = item.get('version_id')
        if not isinstance(version, str) or not version or version == 'null':
            raise AttestationError('EVIDENCE_VERSION_ID_MISSING: ' + key)
        head = aws_json('s3api', 'head-object', '--bucket', BUCKET,
                        '--expected-bucket-owner', ACCOUNT, '--key', key)
        if (head.get('VersionId') != version or
                head.get('ContentLength') != expected['size'] or
                head.get('ETag') != item['etag']):
            raise AttestationError('S3_VERSION_CHANGED_AFTER_VERIFICATION: ' + key)
        total += expected['size']
        if idx == 1 or idx % 100 == 0 or idx == len(items):
            print('VERSIONS_VERIFIED=' + str(idx) + '/' + str(len(items)), flush=True)
    if total != evidence.get('bytes'):
        raise AttestationError('EVIDENCE_BYTES_MISMATCH')
    return len(items), total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preflight', 'identity', 'verify', 'recheck'])
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    try:
        repo = args.repo.resolve(strict=True)
        manifest = git_preflight(repo)
        print('GIT_MAIN_MANUAL_CONTENT_MATCH=PASS')
        print('GITHUB_ATTESTATION_IDENTITY=PASS')
        if args.command == 'preflight':
            print('RESULT=ATTESTATION_GIT_PREFLIGHT_PASS')
            return 0
        check_aws_identity()
        print('AWS_READ_ONLY_ROLE=PASS')
        if args.command == 'identity':
            print('RESULT=ATTESTATION_AWS_IDENTITY_PASS')
            return 0
        if not args.evidence:
            raise AttestationError('EVIDENCE_PATH_REQUIRED')
        evidence_path = args.evidence.resolve()
        if evidence_path.exists():
            if args.command == 'verify':
                raise AttestationError('WILL_NOT_OVERWRITE_EVIDENCE')
        if args.command == 'verify':
            evidence = verify_objects(manifest)
            with evidence_path.open('x', encoding='utf-8', newline='\n') as f:
                json.dump(evidence, f, ensure_ascii=True, sort_keys=True)
                f.write('\n')
            print('ATTESTATION_OBJECTS_VERIFIED=' + str(len(evidence['objects'])))
            print('RESULT=ATTESTATION_FULL_S3_SHA256_PASS')
            return 0
        if not evidence_path.is_file():
            raise AttestationError('EVIDENCE_NOT_FOUND')
        evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
        count, size = recheck_objects(manifest, evidence)
        print('S3_OBJECT_VERSIONS_UNCHANGED=PASS')
        print('ATTESTATION_OBJECTS_VERIFIED=' + str(count))
        print('ATTESTATION_BYTES_VERIFIED=' + str(size))
        print('RESULT=ATTESTATION_RECHECK_PASS')
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttestationError) as exc:
        print('RESULT=ATTESTATION_BLOCKED')
        print('ERROR=' + str(exc))
        print('AWS_MUTATIONS=0')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
