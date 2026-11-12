"""Offline regression tests for paginated successful deployment selection."""
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import incremental_baseline as candidate

SHA_OLD = 'a' * 40
SHA_NEW = 'b' * 40
SHA_OTHER = 'c' * 40


def run_record(run_id, sha=SHA_OLD, **overrides):
    row = {'id': run_id, 'status': 'completed', 'conclusion': 'success',
           'head_branch': 'main', 'event': 'push', 'path': candidate.WORKFLOW_PATH,
           'head_repository': {'full_name': candidate.REPO}, 'head_sha': sha}
    row.update(overrides)
    return row


class IncrementalBaselineTests(unittest.TestCase):
    def test_first_successful_ancestor(self):
        rows = [run_record(18, SHA_OTHER), run_record(17, SHA_OLD)]
        result = candidate.select_baseline(lambda page: rows,
                                           lambda old, new: old == SHA_OLD,
                                           19, SHA_NEW)
        self.assertEqual(result, {'sha': SHA_OLD, 'run_id': 17, 'page': 1})

    def test_self_failed_non_main_and_fork_ignored(self):
        rows = [run_record(99), run_record(98, conclusion='failure'),
                run_record(97, head_branch='dev'), run_record(96, event='pull_request'),
                run_record(95, head_repository={'full_name': 'other/repo'}),
                run_record(94, path='.github/workflows/other.yml'),
                run_record(93)]
        result = candidate.select_baseline(lambda page: rows,
                                           lambda old, new: True, 99, SHA_NEW)
        self.assertEqual(result['run_id'], 93)

    def test_second_page_after_one_hundred_unusable_runs(self):
        page1 = [run_record(1000 - i, SHA_OTHER) for i in range(100)]
        page2 = [run_record(100, SHA_OLD)]
        called = []
        def page(number):
            called.append(number)
            return page1 if number == 1 else page2
        result = candidate.select_baseline(page, lambda old, new: old == SHA_OLD,
                                           9999, SHA_NEW)
        self.assertEqual(result['page'], 2)
        self.assertEqual(called, [1, 2])

    def test_earlier_exhaustion_fails_closed(self):
        with self.assertRaisesRegex(candidate.BaselineError, 'No confirmed'):
            candidate.select_baseline(lambda page: [], lambda old, new: True,
                                      20, SHA_NEW)

    def test_pagination_limit_fails_closed(self):
        page = [run_record(i + 1, SHA_OTHER) for i in range(100)]
        with self.assertRaisesRegex(candidate.BaselineError, 'limit'):
            candidate.select_baseline(lambda n: page if n == 1 else
                                      [dict(row, id=row['id'] + 100) for row in page],
                                      lambda old, new: False, 999, SHA_NEW,
                                      max_pages=2)

    def test_duplicate_runs_fail_closed(self):
        page1 = [run_record(i + 1, SHA_OTHER) for i in range(100)]
        with self.assertRaisesRegex(candidate.BaselineError, 'Duplicate run'):
            candidate.select_baseline(lambda n: page1, lambda old, new: False,
                                      999, SHA_NEW, max_pages=2)

    def test_malformed_page_and_api_errors_fail_closed(self):
        for rows in (None, {}, [run_record(i) for i in range(101)]):
            with self.subTest(rows=type(rows).__name__):
                with self.assertRaises(candidate.BaselineError):
                    candidate.select_baseline(lambda n: rows, lambda old, new: True,
                                              99, SHA_NEW)
        with self.assertRaisesRegex(candidate.BaselineError, 'API'):
            with patch.object(candidate, '_run', return_value=b'{bad'):
                with patch.dict(candidate.os.environ, {'GH_TOKEN': 'test'}):
                    candidate.fetch_page(1)

    def test_read_only_api_route_and_token_required(self):
        with patch.object(candidate, '_run', return_value=b'{"workflow_runs": []}') as runner:
            with patch.dict(candidate.os.environ, {'GH_TOKEN': 'fake'}):
                self.assertEqual(candidate.fetch_page(1), [])
        argv = runner.call_args.args[0]
        self.assertEqual(argv[:4], ['gh', 'api', '--method', 'GET'])
        self.assertIn('status=success', argv[4])
        self.assertIn('page=1', argv[4])
        with patch.dict(candidate.os.environ, {}, clear=True):
            with self.assertRaisesRegex(candidate.BaselineError, 'GH_TOKEN'):
                candidate.fetch_page(1)

    def test_real_git_ancestry_and_error(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-q')
            git('config', 'user.email', 'test@example.invalid')
            git('config', 'user.name', 'Test')
            (repo / 'readme.txt').write_text('a')
            git('add', '.')
            git('commit', '-qm', 'first')
            old = git('rev-parse', 'HEAD')
            (repo / 'readme.txt').write_text('b')
            git('add', '.')
            git('commit', '-qm', 'second')
            new = git('rev-parse', 'HEAD')
            self.assertTrue(candidate.is_ancestor(repo, old, new))
            self.assertFalse(candidate.is_ancestor(repo, new, old))
            with self.assertRaises(candidate.BaselineError):
                candidate.is_ancestor(repo, 'f' * 40, new)


if __name__ == '__main__':
    unittest.main()
