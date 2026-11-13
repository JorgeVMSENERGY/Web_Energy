"""Resolve the last confirmed production deployment from GitHub Actions.

Read-only: invokes `gh api` and `git merge-base` only. Never contacts AWS.
Do not use github.event.before or a PR's latest success as deployment proof.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = 'JorgeVMSENERGY/Web_Energy'
WORKFLOW = 'deploy-production.yml'
WORKFLOW_PATH = '.github/workflows/' + WORKFLOW
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
PAGE_SIZE = 100
MAX_PAGES = 20  # Fail closed if no baseline within 2000 filtered runs.


class BaselineError(RuntimeError):
    pass


def _run(args, *, cwd=None):
    result = subprocess.run(args, cwd=cwd, check=False, capture_output=True)
    if result.returncode != 0:
        # Avoid printing tokens, request URLs, and potentially sensitive stderr.
        raise BaselineError('Command failed: ' + ' '.join(args[:2]) +
                            ', exit=' + str(result.returncode))
    return result.stdout


def _git_sha(repo, ref):
    try:
        value = _run(['git', '-C', str(repo), 'rev-parse', '--verify', ref + '^{commit}']).decode('ascii').strip()
    except UnicodeError as exc:
        raise BaselineError('Invalid Git SHA encoding') from exc
    if not SHA_RE.fullmatch(value):
        raise BaselineError('Invalid Git SHA')
    return value


def is_ancestor(repo, candidate, target):
    if not SHA_RE.fullmatch(candidate) or not SHA_RE.fullmatch(target):
        raise BaselineError('Invalid ancestry input')
    result = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', candidate, target],
                            capture_output=True, check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise BaselineError('Git ancestry verification failed, exit=' + str(result.returncode))


def fetch_page(page):
    if not isinstance(page, int) or page < 1 or page > MAX_PAGES:
        raise BaselineError('Invalid page number')
    if not os.getenv('GH_TOKEN'):
        raise BaselineError('GH_TOKEN is required for GitHub API read access')
    route = ('repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/runs' +
             '?branch=main&status=success&per_page=' + str(PAGE_SIZE) + '&page=' + str(page))
    data = _run(['gh', 'api', '--method', 'GET', route])
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise BaselineError('Invalid GitHub API JSON') from exc
    if not isinstance(doc, dict) or not isinstance(doc.get('workflow_runs'), list):
        raise BaselineError('Incomplete GitHub API response')
    return doc['workflow_runs']


def select_baseline(get_page, ancestor, current_run, current_sha,
                    *, max_pages=MAX_PAGES):
    """Select first qualifying run in API order; API returns newest first.

    Unknown/malformed run records are skipped, but incomplete API pages fail closed.
    A successful workflow at the target SHA (e.g. rerun) is a valid baseline.
    """
    if not isinstance(current_run, int) or current_run <= 0:
        raise BaselineError('Invalid current GitHub run ID')
    if not SHA_RE.fullmatch(current_sha):
        raise BaselineError('Invalid current commit')
    if max_pages < 1 or max_pages > MAX_PAGES:
        raise BaselineError('Unsafe pagination bound')
    seen = set()
    for page in range(1, max_pages + 1):
        rows = get_page(page)
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise BaselineError('Malformed GitHub run page')
        for row in rows:
            if not isinstance(row, dict):
                continue
            run_id = row.get('id')
            if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
                continue
            if run_id in seen:
                raise BaselineError('Duplicate run returned by paginated history')
            seen.add(run_id)
            if run_id == current_run:
                continue
            if (row.get('status') != 'completed' or row.get('conclusion') != 'success'
                    or row.get('head_branch') != 'main'
                    or row.get('event') not in ('push', 'workflow_dispatch')):
                continue
            path = row.get('path')
            valid_paths = (
                WORKFLOW_PATH,
                WORKFLOW_PATH + '@main',
                WORKFLOW_PATH + '@refs/heads/main',
            )
            if path is not None and path not in valid_paths:
                continue
            source = row.get('head_repository')
            if isinstance(source, dict) and source.get('full_name') != REPO:
                continue
            candidate = row.get('head_sha')
            if not isinstance(candidate, str) or not SHA_RE.fullmatch(candidate):
                continue
            if ancestor(candidate, current_sha):
                return {'sha': candidate, 'run_id': run_id, 'page': page}
        if len(rows) < PAGE_SIZE:
            raise BaselineError('No confirmed ancestor production deployment found')
    raise BaselineError('History scan limit reached without confirmed deployment')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--current', required=True)
    parser.add_argument('--current-run-id', required=True, type=int)
    args = parser.parse_args()
    try:
        repo = Path(args.repo).resolve()
        current = _git_sha(repo, 'HEAD')
        if args.current != current:
            raise BaselineError('Current commit is not checkout HEAD')
        if os.getenv('GITHUB_ACTIONS') != 'true':
            raise BaselineError('Baseline resolver must run in GitHub Actions')
        if (os.getenv('GITHUB_REPOSITORY') != REPO or
                os.getenv('GITHUB_REF') != 'refs/heads/main' or
                os.getenv('GITHUB_SHA') != current or
                os.getenv('GITHUB_RUN_ID') != str(args.current_run_id)):
            raise BaselineError('Unexpected production workflow identity')
        result = select_baseline(fetch_page, lambda a, b: is_ancestor(repo, a, b),
                                 args.current_run_id, current)
        print('BASELINE_RUN_ID=' + str(result['run_id']) +
              ' BASELINE_PAGE=' + str(result['page']), file=sys.stderr)
        print(result['sha'])
        return 0
    except (BaselineError, OSError, ValueError) as exc:
        print('BASELINE_ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
