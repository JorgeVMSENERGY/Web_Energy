"""Fail-closed deployed-commit baseline across legacy and incremental workflows.

Read-only: GitHub REST via gh api and git ancestry; no AWS calls.
An incremental run with all jobs skipped is NOT deployment evidence.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = 'JorgeVMSENERGY/Web_Energy'
LEGACY = 'deploy-production.yml'
INCREMENTAL = 'deploy-production-incremental.yml'
WORKFLOWS = (LEGACY, INCREMENTAL)
SHA = re.compile(r'^[0-9a-f]{40}$')
PAGE_SIZE = 100
MAX_PAGES = 20
PLAN_JOB = 'Plan website changes'
DEPLOY_JOB = 'Deploy changed website files'


class BaselineError(RuntimeError):
    pass


def run(args, cwd=None):
    result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise BaselineError('Read command failed: ' + ' '.join(args[:2]) +
                            ', exit=' + str(result.returncode))
    return result.stdout


def parse_json(raw, required):
    try:
        doc = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise BaselineError('Invalid GitHub JSON') from exc
    if not isinstance(doc, dict) or not isinstance(doc.get(required), list):
        raise BaselineError('Incomplete GitHub response: ' + required)
    return doc


def fetch_runs(workflow, page):
    if workflow not in WORKFLOWS or not isinstance(page, int) or not 1 <= page <= MAX_PAGES:
        raise BaselineError('Unsafe GitHub API route')
    if not os.getenv('GH_TOKEN'):
        raise BaselineError('Missing GH_TOKEN')
    route = ('repos/' + REPO + '/actions/workflows/' + workflow + '/runs' +
             '?branch=main&status=success&per_page=100&page=' + str(page))
    return parse_json(run(['gh', 'api', '--method', 'GET', route]), 'workflow_runs')['workflow_runs']


def fetch_jobs(run_id):
    if type(run_id) is not int or run_id <= 0:
        raise BaselineError('Unsafe run ID')
    if not os.getenv('GH_TOKEN'):
        raise BaselineError('Missing GH_TOKEN')
    route = ('repos/' + REPO + '/actions/runs/' + str(run_id) +
             '/jobs?filter=latest&per_page=100')
    doc = parse_json(run(['gh', 'api', '--method', 'GET', route]), 'jobs')
    if (type(doc.get('total_count')) is not int or
            doc['total_count'] != len(doc['jobs']) or len(doc['jobs']) > 100):
        raise BaselineError('Incomplete workflow jobs')
    return doc['jobs']


def is_ancestor(repo, previous, current):
    if not SHA.fullmatch(previous) or not SHA.fullmatch(current):
        raise BaselineError('Invalid ancestry SHA')
    res = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor',
                          previous, current], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, check=False)
    if res.returncode == 0:
        return True
    if res.returncode == 1:
        return False
    raise BaselineError('Git ancestry check failed: ' + str(res.returncode))


def path_matches(path, workflow):
    expected = '.github/workflows/' + workflow
    return path in (expected, expected + '@main', expected + '@refs/heads/main')


def verified_incremental_jobs(jobs):
    """Reject gated/skipped plan: a successful empty run isn't deployment proof."""
    if not isinstance(jobs, list):
        raise BaselineError('Invalid jobs response')
    plan = [job for job in jobs if isinstance(job, dict) and job.get('name') == PLAN_JOB]
    deploy = [job for job in jobs if isinstance(job, dict) and job.get('name') == DEPLOY_JOB]
    if len(plan) != 1 or len(deploy) != 1:
        return False
    p, d = plan[0], deploy[0]
    if p.get('status') != 'completed' or p.get('conclusion') != 'success':
        return False
    steps = p.get('steps')
    if not isinstance(steps, list) or not any(
        isinstance(step, dict) and step.get('name') == 'Plan offline SHA-256 changes'
        and step.get('status') == 'completed' and step.get('conclusion') == 'success'
        for step in steps
    ):
        return False
    return (d.get('status') == 'completed' and
            d.get('conclusion') in ('success', 'skipped'))


def choose_baseline(get_page, get_jobs, ancestor, current_sha, current_run,
                    max_pages=MAX_PAGES):
    if (not isinstance(current_sha, str) or not SHA.fullmatch(current_sha)
            or type(current_run) is not int or current_run <= 0
            or type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES):
        raise BaselineError('Invalid baseline request')
    best = None
    for workflow in WORKFLOWS:
        seen = set()
        found = False
        for page in range(1, max_pages + 1):
            rows = get_page(workflow, page)
            if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
                raise BaselineError('Malformed workflow page')
            for row in rows:
                if not isinstance(row, dict):
                    continue
                run_id = row.get('id')
                if type(run_id) is not int or run_id <= 0:
                    continue
                if run_id in seen:
                    raise BaselineError('Duplicate run ID within workflow history')
                seen.add(run_id)
                if run_id == current_run:
                    continue
                if (row.get('status') != 'completed' or row.get('conclusion') != 'success'
                        or row.get('head_branch') != 'main'
                        or row.get('event') not in ('push', 'workflow_dispatch')
                        or not path_matches(row.get('path'), workflow)):
                    continue
                source = row.get('head_repository')
                if not isinstance(source, dict) or source.get('full_name') != REPO:
                    continue
                commit = row.get('head_sha')
                if not isinstance(commit, str) or not SHA.fullmatch(commit):
                    continue
                if not ancestor(commit, current_sha):
                    continue
                if workflow == INCREMENTAL and not verified_incremental_jobs(get_jobs(run_id)):
                    continue
                candidate = {'sha': commit, 'run_id': run_id, 'workflow': workflow}
                if best is None or candidate['run_id'] > best['run_id']:
                    best = candidate
                found = True
                break
            if found or len(rows) < PAGE_SIZE:
                break
        else:
            raise BaselineError('History scan exhausted: ' + workflow)
    if best is None:
        raise BaselineError('No confirmed deployment ancestor found')
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--current', required=True)
    parser.add_argument('--current-run-id', type=int, required=True)
    args = parser.parse_args()
    try:
        repo = Path(args.repo).resolve()
        current = run(['git', '-C', str(repo), 'rev-parse', '--verify', 'HEAD^{commit}'])
        current = current.decode('ascii').strip()
        workflow_ref = REPO + '/.github/workflows/' + INCREMENTAL + '@refs/heads/main'
        checks = {
            'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': REPO,
            'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': current,
            'GITHUB_RUN_ID': str(args.current_run_id),
            'GITHUB_WORKFLOW_REF': workflow_ref,
        }
        if not SHA.fullmatch(current) or args.current != current:
            raise BaselineError('Current SHA mismatches checkout')
        for key, value in checks.items():
            if os.getenv(key) != value:
                raise BaselineError('Unexpected workflow identity: ' + key)
        if os.getenv('GITHUB_EVENT_NAME') not in ('push', 'workflow_dispatch'):
            raise BaselineError('Unexpected workflow event')
        result = choose_baseline(fetch_runs, fetch_jobs,
                                 lambda a, b: is_ancestor(repo, a, b),
                                 current, args.current_run_id)
        print('BASELINE_SOURCE=' + result['workflow'] + ' BASELINE_RUN=' +
              str(result['run_id']), file=sys.stderr)
        print(result['sha'])
        return 0
    except (BaselineError, OSError, UnicodeError, ValueError) as exc:
        print('CUTOVER_BASELINE_ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
