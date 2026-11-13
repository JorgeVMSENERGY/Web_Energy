"""Offline suites. CI additionally isolates the entire process tree with Docker."""
import os
from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root / 'scripts'))
    required = ('test_audit_performance', 'test_audit_s3', 'test_audit_s3_boto3',
                'test_aws_cli_encoding', 'test_c1_plan', 'test_c2a_observe',
                'test_deploy_plan', 'test_git_batch', 'test_pr_validation')
    for module in required:
        if not (root / 'scripts' / (module + '.py')).is_file():
            raise RuntimeError('Missing required offline suite: ' + module)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([
        loader.discover(str(root / 'scripts'), pattern='test_*.py'),
        unittest.TestLoader().discover(str(root / 'infra/forms/tests'),
                                      pattern='test_*.py'),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print('Offline tests:', result.testsRun, 'skipped:', len(result.skipped))
    for test, reason in result.skipped:
        print('SKIPPED:', test, reason)
    complete = not result.skipped or os.environ.get('PR_OFFLINE_STRICT') != '1'
    return 0 if result.wasSuccessful() and result.testsRun and complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
