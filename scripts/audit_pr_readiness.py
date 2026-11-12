"""Local Git/file audit only. No AWS, commits, index changes or network access."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

from deploy_plan import inventory, publishable

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = 'scripts/pr-readiness-audit.json'
ARTIFACTS = {'scripts/deploy-metadata-report.json'}


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def names(*args):
    return [p.decode('utf-8') for p in git(*args).split(b'\0') if p]


def fingerprint(path):
    sha = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
            size += len(chunk)
    return {'size': size, 'sha256': sha.hexdigest()}


def main():
    refs = {r: git('rev-parse', r).decode().strip()
            for r in ('HEAD', 'main', 'origin/main', 'origin/staging')}
    untracked = names('ls-files', '--others', '--exclude-standard', '-z')
    changed = names('diff', '--name-only', '-z', 'HEAD', '--')
    # Audit outputs are listed but not hashed, avoiding self-reference.
    outputs = {OUTPUT, 'scripts/PR-INCLUDE.txt'}
    rows = []
    for name in sorted(set(untracked + changed)):
        path = ROOT / name
        row = {'path': name, 'gitState': 'untracked' if name in untracked else 'modified',
               'publishable': publishable(name)}
        historical = name in ARTIFACTS or name.startswith(('scripts/c1_simulations/', 'scripts/c2a_simulations/'))
        row['recommendation'] = ('optional-generated-audit' if name == OUTPUT else
                                 'optional-historical-evidence' if historical else 'include')
        if name in outputs:
            row['fingerprintExcluded'] = 'generated audit output'
        elif path.is_file():
            row.update(fingerprint(path))
            if path.suffix == '.py':
                tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=name)
                row['imports'] = sorted({n.module for n in ast.walk(tree)
                                         if isinstance(n, ast.ImportFrom) and n.module}
                                        | {a.name for n in ast.walk(tree)
                                           if isinstance(n, ast.Import) for a in n.names})
            elif path.suffix == '.json':
                data = json.loads(path.read_text(encoding='utf-8-sig'))
                row['jsonValid'] = True
                row['topLevelKeys'] = sorted(data) if isinstance(data, dict) else []
                def flags(value):
                    if isinstance(value, dict):
                        return ([value['applyBlocked']] if 'applyBlocked' in value else []) + [
                            flag for child in value.values() for flag in flags(child)]
                    if isinstance(value, list):
                        return [flag for child in value for flag in flags(child)]
                    return []
                blocked = flags(data)
                row['applyBlockedOccurrences'] = len(blocked)
                row['applyBlockedAllTrue'] = all(flag is True for flag in blocked)
        else:
            row['deleted'] = True
        rows.append(row)
    manifests = {}
    for ref in ('HEAD', 'main', 'origin/main'):
        sha = refs[ref]
        if sha not in manifests:
            manifests[sha] = inventory(ROOT, sha)['files']
    candidate = dict(manifests[refs['HEAD']])
    # Committed payload is Git blob content; only explicit dirty/new paths use
    # current bytes. This avoids attributing checkout CRLF conversion to a PR.
    for name in set(changed + untracked):
        if publishable(name):
            path = ROOT / name
            if path.is_file():
                candidate[name] = fingerprint(path)
            else:
                candidate.pop(name, None)
    comparisons = {}
    for ref in ('main', 'origin/main'):
        base = manifests[refs[ref]]
        differences = [{'path': p, 'base': base.get(p), 'candidate': candidate.get(p)}
                       for p in sorted(set(base) | set(candidate)) if base.get(p) != candidate.get(p)]
        comparisons[ref] = {'baseSha': refs[ref], 'baseCount': len(base),
                            'candidateCount': len(candidate), 'differences': differences}
    protected = {}
    for page in ('catalogo-proyectos.html', 'proyectos-vms.html'):
        protected[page] = {'candidate': candidate.get(page),
                           'main': manifests[refs['main']].get(page),
                           'origin/main': manifests[refs['origin/main']].get(page)}
    report = {'refs': refs, 'trackedChangesFromHead': changed,
              'prCommittedDiffAgainstOriginMain': names('diff', '--name-only', '-z', 'origin/main...HEAD', '--'),
              'files': rows, 'payloadComparisons': comparisons, 'protectedPages': protected,
              'payloadManifestsByCommit': manifests,
              'limitations': ['origin/main freshness must be checked separately with git ls-remote',
                              'candidate includes untracked paths hypothetically; no commit is created',
                              'no proof of current AWS state; Git payload only']}
    (ROOT / OUTPUT).write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    include = sorted({r['path'] for r in rows if r['recommendation'] == 'include'}
                     | {'scripts/PR-INCLUDE.txt'})
    (ROOT / 'scripts/PR-INCLUDE.txt').write_text('\n'.join(include) + '\n', encoding='utf-8')
    print(json.dumps({'auditedFiles': len(rows), 'include': len(include),
                      'trackedChanges': len(changed),
                      'payloadDifferences': {r: len(c['differences']) for r, c in comparisons.items()},
                      'report': OUTPUT}, indent=2))


if __name__ == '__main__':
    main()
