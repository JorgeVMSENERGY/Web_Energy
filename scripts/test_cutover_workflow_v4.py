"""Static offline checks for safe GitHub Actions workflow coexistence."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / '.github' / 'workflows' / 'deploy-production-incremental.yml'
OLD = ROOT / '.github' / 'workflows' / 'deploy-production.yml'


class CutoverWorkflowTests(unittest.TestCase):
    def test_new_workflow_has_default_off_gate(self):
        doc = NEW.read_text(encoding='utf-8-sig')
        self.assertIn("if: ${{ vars.VMS_INCREMENTAL_DEPLOY_ENABLED == 'true' }}", doc)
        self.assertIn('name: Plan website changes', doc)
        self.assertIn('needs: plan', doc)
        self.assertIn("if: needs.plan.outputs.deploy_required == 'true'", doc)

    def test_legacy_disabled_checked_before_aws_or_baseline(self):
        doc = NEW.read_text(encoding='utf-8-sig')
        gate = doc.index('Require legacy workflow disabled before planning')
        resolve = doc.index('Resolve last successful production deployment')
        oidc = doc.index('Configure AWS credentials with OIDC')
        self.assertLess(gate, resolve)
        self.assertLess(resolve, oidc)
        self.assertIn("$Legacy.state -cne 'disabled_manually'", doc)
        self.assertIn('scripts/incremental_cutover_baseline.py', doc)
        self.assertNotIn('scripts/incremental_baseline.py `', doc)

    def test_original_workflow_is_preserved(self):
        old = OLD.read_text(encoding='utf-8-sig')
        new = NEW.read_text(encoding='utf-8-sig')
        self.assertIn('scripts/deploy-aws-production.ps1 -Apply', old)
        self.assertNotIn('incremental_deploy.py apply', old)
        self.assertIn('group: vms-energy-web-production', old)
        self.assertIn('group: vms-energy-web-production', new)
        self.assertIn('environment:', new)
        self.assertIn('name: production', new)
        self.assertIn('actions: read', new)
        self.assertIn('id-token: write', new)

    def test_new_workflow_can_be_manually_dispatched(self):
        doc = NEW.read_text(encoding='utf-8-sig')
        self.assertIn('workflow_dispatch:', doc)
        self.assertIn('default: verify-noop', doc)
        self.assertIn('BOOTSTRAP_NOOP_REQUIRED', doc)
        self.assertIn('branches:\n      - main', doc)
        self.assertIn('cancel-in-progress: false', doc)


if __name__ == '__main__':
    unittest.main()
