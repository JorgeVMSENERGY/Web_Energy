# Incremental AWS website deployment — candidate v1

Status: **development only**. No production workflow changed by this package.

## Scope and safety

- `scripts/incremental_deploy.py plan` inventories the **last successful production commit** and current HEAD using the repository's existing `deploy_plan.inventory`. The comparison uses Git blob SHA-256 and its unchanged publishable-path exclusions.
- A documentation-only commit produces `NO-OP`: no S3 or CloudFront API calls are needed.
- `apply` only runs from GitHub Actions on `refs/heads/main`, with expected repo/SHA and the deployment role verified by STS. The plan is regenerated from Git before writing.
- Only CREATE and UPDATE objects are supported; DELETE, changes to the two staging-exclusive HTML pages, unrelated Git ancestry and tampered plans are blocked.
- Upload uses exact Git blob bytes, `aws s3 cp` with SSE-S3 AES256, Cache-Control from the existing uploader and SHA256 upload checksum. Each changed object's actual S3 bytes are then fetched, hashed and its S3 version rechecked, before CloudFront invalidation.
- Changed HTML triggers conservative `/*` invalidation until CloudFront URI rewrite/alias behavior is fully verified. Non-HTML files use percent-encoded key path plus trailing `*` for query-string cache variants. NO-OP has zero invalidations.
- Failed/partial runs are **not** a successful deployment baseline. Retry must reconcile from the last successful workflow run. A crash between uploads and invalidation requires a rerun; there is no distributed transaction or rollback.
- The strategy assumes no out-of-band S3 writes and does not detect drift in unchanged objects, metadata changes, alternate origin mappings or stale cached HTML. It is NOT a replacement for the conservative C2B design. The preexisting GitHub Actions concurrency group only serializes cooperating runs.

## Operator local validation, PowerShell

From `C:\web-dev\projects\vms-energy-web-incremental`:

```powershell
$Python = Join-Path $env:TEMP 'vms-web-audit-venv\Scripts\python.exe'
& $Python -B -m unittest discover -s scripts -p test_incremental_deploy.py -v
if ($LASTEXITCODE -ne 0) { throw 'TESTS_FAILED' }
$Baseline = '6353e03a40f232c262267ba9027e8865d5fd0f31'
$Report = Join-Path $env:TEMP ('vms-new-incremental-plan-' + [guid]::NewGuid().ToString('N') + '.json')
& $Python -B scripts/incremental_deploy.py plan --repo . --previous $Baseline --current HEAD --output $Report
if ($LASTEXITCODE -ne 0) { throw 'PLAN_FAILED' }
$Plan = Get-Content $Report -Raw | ConvertFrom-Json
if ($Plan.status -ne 'NO-OP' -or @($Plan.operations).Count -ne 0 -or @($Plan.invalidationPaths).Count -ne 0) { throw 'EXPECTED_NOOP' }
Write-Host 'INCREMENTAL_OFFLINE_NOOP=PASS'
```

## Promotion of the workflow (separate, controlled step)

The existing `scripts/validate_pr_diff.py` deliberately rejects PRs changing established `.github/` workflows; **do not disable this gate**. Independently review the candidate workflow below and promote it under an approved control-change process. This package does not modify `.github/workflows/deploy-production.yml`.

The future workflow must use full Git history; `actions:read` for GitHub API; compare against the **latest completed-successful deployment run**, skipping the current run; call the offline planner before OIDC; skip AWS for NO-OP; block deletes/protected paths; and retain the existing production environment, OIDC, concurrency and CloudFront check. Do not treat `github.event.before` alone as the deployment baseline.

The workflow proposal is stored at `scripts/deploy-production.candidate.yml`, not under `.github/`, so GitHub will not execute it. The `scripts/` path is also excluded from the published website payload. **Do not place a `.yml` workflow candidate under `docs/`: `.yml` files outside excluded directories could otherwise be published by the current website deploy.**

## Workflow activation checks (not yet performed)

1. Audit the candidate's GitHub API run lookup, OIDC production environment restriction and first deployment baseline.
2. Verify S3 upload metadata parity and CloudFront URL rewrites/aliases on a disposable environment or non-production objects.
3. Review CODEOWNERS/ruleset requirements for independently promoting the existing production workflow. The new `scripts/test_incremental_deploy.py` is an offline bootstrap test and can be reviewed via PR separately.
4. Only after workflow promotion, check that a docs-only main update yields no AWS calls, a changed PDF updates only that object and its cache path, and any DELETE blocks.

The test package is not a production rollout. No AWS changes have been made by it.
