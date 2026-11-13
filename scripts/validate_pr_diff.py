"""Report actual PR changes, including both sides of renames; never contact AWS."""
import json
import os
import subprocess
from pathlib import Path

PROTECTED = {'catalogo-proyectos.html', 'proyectos-vms.html'}
SENSITIVE = {'scripts/deploy-aws-production.ps1', 'scripts/audit_s3.py',
             'scripts/c1_plan.py', 'scripts/c2a_observe.py',
             'scripts/validate_pr_diff.py', 'scripts/run_pr_offline.py',
             'scripts/audit_s3_boto3.py', 'scripts/audit_performance.py',
             'scripts/deploy_plan.py', 'scripts/git_batch.py',
             'scripts/c1_simulator.py', 'infra/forms/lambda/receiver/app.py'}


def classify(paths):
    return (sorted(PROTECTED.intersection(paths)),
            sorted(p for p in paths if p.startswith('.github/') or p in SENSITIVE
                   or '/test' in p or p.startswith('scripts/requirements')))


def main():
    base, head = os.environ['BASE_SHA'], os.environ['HEAD_SHA']
    # Three dots: changes introduced by the PR since its merge base.
    data = subprocess.check_output(['git', 'diff', '--name-only', '-z',
                                    '--no-renames', base + '...' + head, '--'])
    paths = data.decode('utf-8').rstrip('\0').split('\0') if data else []
    protected, sensitive = classify(paths)
    report = {'base': base, 'head': head, 'changedPaths': paths,
              'stagingExclusiveChanges': protected, 'sensitiveChanges': sensitive,
              'independentApprovalRequired': bool(sensitive)}
    text = json.dumps(report, ensure_ascii=True, indent=2)
    print(text)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with Path(summary).open('a', encoding='utf-8') as stream:
            stream.write('### PR diff diagnosis\n```json\n' + text + '\n```\n')
    if sensitive:
        print('::warning::Sensitive controls changed. Independent DevOps review required; see diff diagnosis.')
    # Fail closed for changes/removal of established validation controls. New
    # controls need independent bootstrap review; a PR cannot approve itself.
    controls = [p for p in sensitive if p.startswith('.github/')
                or '/test' in p or p in {'scripts/run_pr_offline.py',
                                         'scripts/validate_pr_diff.py'}]
    established = [p for p in controls if subprocess.run(
        ['git', 'cat-file', '-e', base + ':' + p],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0]
    if established:
        print('::error::Established validation controls changed; independent control promotion required.')
        return 1
    if protected:
        print('::error::PR changes staging-exclusive pages; see diff diagnosis.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
