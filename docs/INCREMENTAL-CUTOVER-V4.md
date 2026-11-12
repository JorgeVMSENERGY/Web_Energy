# VMS Energy website: independent incremental workflow rollout

Status: **inactive until approved cutover**. The original `.github/workflows/deploy-production.yml` remains untouched and active.

The new workflow `.github/workflows/deploy-production-incremental.yml` has the **same concurrency group** as legacy; its `plan` job is disabled by default unless repository Actions variable `VMS_INCREMENTAL_DEPLOY_ENABLED` is the literal string `true`. Before the planned switch, do not create this variable or leave its value `false`. The new workflow uses the current `scripts/incremental_deploy.py` (V3 MIME parity), with the existing protected production OIDC role, website, region and distribution.

The new resolver `scripts/incremental_cutover_baseline.py` reads completed-successful production runs of **both** workflow files. Runs of the new workflow with skipped plans do not count: it checks that the plan job and its offline SHA-256 step succeeded and that deploy succeeded or skipped (NO-OP). GitHub history checks require main, expected repo/workflow, valid Git ancestry, and newest trusted run. No AWS access during planning.

## Before merge

1. Review independently with the DevOps/security owner; a PR creating a new workflow must not approve itself. Do not change `scripts/validate_pr_diff.py`, established workflows, or IAM to bypass this.
2. Check the PR's offline CI and the exact 3 or 4 changed paths. Confirm repository variable is **not** `true`.
3. Merge while old workflow remains active. The new workflow may appear as a skipped run; this is expected and not deployment evidence. Verify the old workflow's new production deployment concludes success.

## Planned cutover, separately authorized

1. Verify current main and all existing production workflow runs are completed (including queued and in-progress); stop publishing changes during the switch. `gh workflow list -a -R JorgeVMSENERGY/Web_Energy`.
2. Ensure `VMS_INCREMENTAL_DEPLOY_ENABLED` is `false` or unset. Use `gh variable list -R JorgeVMSENERGY/Web_Energy` to read.
3. `gh workflow disable deploy-production.yml -R JorgeVMSENERGY/Web_Energy` — **does not cancel running jobs**.
4. Check old workflow reports `disabled_manually` via GitHub API; ensure no old runs queued/in-progress.
5. Set `gh variable set VMS_INCREMENTAL_DEPLOY_ENABLED -b true -R JorgeVMSENERGY/Web_Energy` and enable `gh workflow enable deploy-production-incremental.yml -R JorgeVMSENERGY/Web_Energy` if needed.
6. Verify old workflow disabled, new active; dispatch new `gh workflow run deploy-production-incremental.yml -R JorgeVMSENERGY/Web_Energy --ref main -f mode=verify-noop`. First run should be NO-OP based on last successful legacy deployment. In this safety mode any PROPOSED deployment is **blocked before OIDC/S3**; investigate rather than switching to `mode=deploy`.
7. Verify successful no-op and zero S3 uploads/CloudFront invalidations in logs; then authorize a small controlled asset change in a separate PR to validate exactly one changed object.

## Rollback

During freeze, set variable `false` first to gate new jobs, then disable the incremental workflow, verify its in-progress runs have finished, enable legacy, verify legacy `active`, and run legacy workflow_dispatch if a recovery deployment is required. Do not allow concurrent writers. This is workflow rollback, **not** automatic rollback of already written S3 objects.

## Limitations

No distributed writer lock, no protection from out-of-band S3 modification, and no automatic deletion. GitHub Actions concurrency only serializes runs of workflows in this repository using the same concurrency group. `NO-OP` does not prove external S3 drift is absent. Stage-only HTML pages remain blocked.
