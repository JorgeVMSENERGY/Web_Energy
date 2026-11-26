# SEO legacy redirects — local candidate, 25 November 2026

Base: `main@a6545272976e671cf5824a51a3eed2e80c8d3456`. No deployment is
authorized by this document. No AWS resources, workflow gates or state were changed.

## Sources and contract

`infra/site/functions/legacy-redirects.json` is the technical dataset for the
24 user-supplied rules verified by the audit matrix. It does **not** replace
Marketing's unavailable `auditoria-seo/redirecciones-301.xlsx`. The claimed
`_export_redirects.py` and `_build_rules.py` were not found. Preserve the exact
destinations; additions or editorial changes require approval.

Generate/check deterministically, without AWS or translation services:

The existing Python test dependency is `esprima==4.0.1` from
`scripts/requirements-english.txt`. If a shared TEMP installation is incomplete,
install that same requirement in an isolated TEMP directory and set `PYTHONPATH`
to it. Do not work around missing parser methods by weakening or skipping tests.

```powershell
python -B scripts/build_seo_redirects.py --write
python -B scripts/build_seo_redirects.py --check
node scripts/test_seo_routing.cjs --report "$env:TEMP\seo-routing-results.json"
python -B -m unittest discover -s scripts -p 'test*.py'
```

The template lives in `scripts/seo/unified-viewer-request.js.template`; Terraform
uses only the generated `infra/site/functions/unified-viewer-request.js`.
`functions/redirects.js` remains byte-equivalent to the base and is not associated
or published. There are **eight** additional exact ES entries in that historical
map, rather than seven: the approved author URL was previously covered by the
generic `WP_ARCHIVE` pattern instead of an exact map entry. Those eight entries
and all 19 EN aliases remain inactive. No generic WordPress archive rule is added.

Audited LIVE reference: `scripts/seo/fixtures/english-live-20261125.js`, exported
from `VMSWebEnglishIndexRewrite`, runtime `cloudfront-js-2.0`, distribution
`E3FWVCRXQ69DI6`. Raw bytes are pinned by `.gitattributes` and SHA-256:
`3fbd770c83a207606e516c0f4667a9a7f3893b088d0e4046005d1489f92e2d1c`.
The source fixture does not imply Terraform ownership of that function.

The candidate preserves `/en` and `/en/` as internal `/en/index.html` rewrites,
without 301 on apex. No English fallback or alias is activated. All other normal
requests retain their object, headers, cookies, query data and case. Legacy matches
are case-sensitive and may discard any number of trailing `/` **for lookup only**.
Unknown paths are not normalized. GET/HEAD implement redirects. OPTIONS and other
methods retain LIVE behavior, including the English rewrite; this avoids changing
preflight handling for the distribution's permitted OPTIONS method.

Only `www.vmsenergy.com` gets host canonicalization (case-insensitive, accepting
a terminal DNS dot); unrelated hostnames and the CloudFront hostname retain their
normal behavior. Approved legacy paths still redirect on any hostname, as specified.
HTTPS www plus legacy resolves directly to the final canonical destination.
HTTP may first get CloudFront's existing protocol upgrade and is not promised one hop.

All redirect authorities are literal `https://vmsenergy.com`. Paths and query
components cannot add an authority or raw header control characters. Property
lookups/iteration ignore inherited keys. Targets cannot come from viewer parameters.

## Query strings

CloudFront events group duplicate keys in `multiValue`, including the first value;
iterate that array once instead of also appending `entry.value`. Percent-encoded
octets from the event are preserved, not decoded or double-encoded. Raw Unicode is
UTF-8 percent-encoded when forming Location; raw delimiters/control characters are
escaped. Existing `+` retains its query-string interpretation; `%2B` stays `%2B`.
UTM, GCLID, FBCLID, MSCLKID, empty values, encoded keys, repeated values and encoded
`?`, `=`, `#`, `%20`, `%26`, `%2B`, `%25` are covered.

The event cannot reconstruct the exact order of interleaved duplicate keys or
distinguish all original representations such as `?flag` versus `?flag=`. Empty
values serialize as `flag=`. No promise of byte-identical reconstruction or signed
query-string compatibility is made. The future-fragment helper puts query before
`#fragment`; the current 24 targets have no fragments. Non-redirected requests keep
the original query object unchanged.

Official contracts:

- [AWS event structure and multiValue](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/functions-event-structure.html)
- [AWS encoding and one association per event](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/edge-function-restrictions-all.html)
- [AWS function quotas](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cloudfront-limits.html)

The generated function is checked against 10,240 bytes and statically parsed;
Node simulation is not AWS TestFunction and cannot establish compute utilization.
Runtime validation in AWS remains part of a separately approved deployment.

## 404 and SEO boundaries

`404.html` is a shared responsive bilingual error document with existing brand
assets/tokens, root-relative paths, no new external dependencies, no automatic
redirect, and `noindex,follow`. The English authoring generator excludes this shared
document even after it becomes tracked; no English build was run.

Creating/uploading this file alone does not change production's current S3 403s.
Direct `/404.html` can be 200; a missing URL must retain a true error status when
served its content. No sitemap or robots change is included. The 174 entries
(87 ES, 87 EN), priority pages and noindex omissions remain intact. Eight audited
hreflang duplicate groups, including conflicting EN alternatives on `nosotros.html`,
are known and out of scope. No Search Console indexing was requested.

Both custom error switches default to false:

- `seo_custom_404_enabled`: future 404→404 with `/404.html`, TTL 10.
- `seo_map_s3_403_to_404`: future global 403→404, requires the first switch and
  independent approval. It can hide S3/OAC permission failures or WAF blocks;
  WAF custom responses take precedence. Do not solve this by changing S3/OAC/WAF.

## Terraform reconciliation proposal

Observed: HCL declared `vmsenergy-redirects-301`, which does not exist in AWS; remote
state serial 14 had no function or association, while AWS associated the English
function. Candidate HCL removes the unused function declaration, retains its code
as history, and declares the **new** `VMSWebUnifiedViewerRequest` resource. The
existing English function is not imported, overwritten, managed, or removed.

`seo_unified_routing_enabled=false` (default) means the new function is DEVELOPMENT
only on its first creation (`publish=false`), and the single viewer-request association points to the
existing English ARN. `true` permits a future apply to publish the candidate and
switch the single association. It is never enabled automatically. This staged
configuration allows AWS tests of DEVELOPMENT before the reviewed activation plan.

Production values previously came only from defaults in `variables.tf` and the
SSO provider profile. No site tfvars, TF_VAR-based release wrapper, or infrastructure
workflow was found in the checked trees (including the original site's tfvars
filenames). Backend state does not persist input values for later invocations.
`infra/site/seo.auto.tfvars` is now the explicit non-secret persistence location,
with an exact `.gitignore` exception; it contains **only three false switches**.
It is excluded from S3 by the `infra/` deployment boundary.

After separate deployment approval, commit the approved routing value in that
file as true, keep both error flags false unless separately approved, and review
the real plan from that exact checkout. Ordinary `terraform -chdir=infra/site`
operations automatically load it, preventing a later default=false from silently
reverting the association. Do not activate using a one-off CLI `-var` or `TF_VAR`
setting alone. Check for other auto.tfvars files and explicit CLI overrides; later
auto files or CLI inputs can supersede these values. Every infrastructure release
must review the planned association and all three effective values.

For a managed rollback, persist false for routing in the same file, keep the new
function resource declared, and review the saved plan that restores the old ARN.
Do not assume `publish=false` unpublishes or deletes any existing LIVE code. The
locked AWS provider 6.64.0 update path only invokes PublishFunction when true;
there is no inverse unpublish operation in that path. Safety comes from the
association switch and preserving both functions, not from removing the candidate
LIVE stage. If an emergency authorized association update bypasses Terraform,
follow with the persisted false configuration and a separately reviewed refresh/
plan reconciliation before any ordinary apply; no state rm/mv is needed.

References: [Terraform variable-file loading and precedence](https://developer.hashicorp.com/terraform/language/values/variables),
[locked provider 6.64.0 function implementation](https://github.com/hashicorp/terraform-provider-aws/blob/v6.64.0/internal/service/cloudfront/function.go).

Static validation may use a TEMP copy of the module **without backend.tf**, with
`terraform init -backend=false -lockfile=readonly` and `terraform validate`. No
init/plan/apply/import/state manipulation against the production backend is part
of local phase 2. No global plan is executed. Review a real, saved infrastructure
plan only in the next independently authorized phase.

## Local preview

```powershell
node scripts/preview_seo.cjs --port 8767
```

The server binds **127.0.0.1 only**; port 0 selects a free port and prints its URL.
It runs the actual candidate handler, uses the checkout as an origin and serves
the custom document with HTTP 404 for missing paths. This simulates the future
error response, not current S3's 403. It does not emulate WAF, OAC, caching, TLS,
POP propagation or all CloudFront validations. OPTIONS gets local 204 after handler
simulation. Preview CSP blocks external analytics, fonts, API calls and forms.

- `/`, `/en`, `/en/`: Spanish and English home pages.
- `/nosotros.html`, `/lp-celdas-media-tension.html`: priority pages.
- `/404.html`: direct document preview (200).
- `/missing/nested/example.html`: simulated missing URL (404).
- `/nosotros?utm_source=preview`: real candidate legacy decision (301).
- `/__seo_www__/en/`: simulated www→apex then English rewrite.

To keep navigation local, the server rewrites Location to its loopback address;
`X-Preview-CloudFront-Location` records the actual candidate HTTPS Location. It
does not redirect the browser to production. `X-Preview-Origin-URI` shows rewrites.
Infrastructure/test/dotfile paths are not served. Stop the preview by its recorded
process ID when review is complete; it is not a production service.

## Future deployment and rollback — instructions only

1. Obtain independent deployment approval. Revalidate STS/account/profile, remote
   SHAs, distribution ETag and English LIVE SHA-256. If LIVE changed since audit,
   compare contracts again before proceeding. Back up distribution, LIVE and
   DEVELOPMENT code/config/ETags and content manifests outside the repo.
2. Check current workflow states and `VMS_INCREMENTAL_DEPLOY_ENABLED`. Local phase
   2 preserves false. Do not enable gates, push or trigger workflows implicitly.
   Content deployment and edge infrastructure remain separate operations.
3. Review a real Terraform plan with default switches. Refresh must reconcile the
   state-only missing association to actual English routing. Allow only the new
   DEVELOPMENT function and expected association reconciliation; stop for destroys,
   old-function changes, S3/OAC/WAF/IAM changes or unrelated drift. Do not import or
   rename the English function into the candidate resource.
4. After an approved DEVELOPMENT-only apply, use AWS TestFunction against real
   events and inspect validation/compute utilization. Re-run the 192-rule matrix
   and negative/method/query/English cases. Local Node parsing is not sufficient.
5. Review a second plan with `seo_unified_routing_enabled=true`; it must publish
   and associate only the candidate. Preserve English LIVE as rollback. Apply only
   with approval, wait for Deployed, verify HTTP statuses/Location/campaigns and
   English/assets after propagation. A single apply publishes and associates;
   require a separate approved release procedure if an intermediate published,
   unassociated LIVE inspection is required.
6. Deploy `404.html` separately through an approved content flow. Verify the object
   and root-relative assets before any custom error switch. Consider 404 mapping
   separately; evaluate 403→404 independently with observability for real permission
   failures. Avoid a blanket 200 fallback. No S3 permissions or WAF changes.
7. Roll back routing by restoring association to the saved English ARN. Preferred
   managed rollback sets `seo_unified_routing_enabled=false` and reviews/applies
   only the association change; retain the candidate resource. If an emergency
   approved distribution update bypasses Terraform, immediately reflect the false
   switch in configuration and review refresh/plan to reconcile state, avoiding
   accidental reactivation. Do not use state rm/mv or delete the old function.
8. Restore prior error settings (none) and content versions through separately
   approved procedures if those releases fail. Check /en, /en/, the three English
   priority pages, assets and ordinary pages after each rollback. Browser-cached
   301s can outlive rollback and cannot be erased by CloudFront invalidation.
9. Only after production passes HTTP and SEO checks should a human use Search
   Console for inspection/indexing. No indexation request is part of phase 2.

## Phase 3 review results and CI proposal (not applied)

The incoming phase-2 inventory is 16 paths: 14 content/configuration/script files
plus two `.gitattributes` files. Phase 3 adds `.gitignore` to the modified-path
inventory and the new `infra/site/seo.auto.tfvars`, bringing the complete feature
to 18 paths. All are SEO-scoped; no workflow, requirements, existing ES/EN content,
sitemap or robots file changed.

Additional Node tests cover 7,500-byte query values, 10/100/500 repeated values,
special/numeric/empty names, encoded Unicode/invalid percent octets, semantic
parsing, encoded path segments, slash/backslash cases, foreign/CloudFront hosts
and GET/HEAD/OPTIONS. `safePath(safePath(uri)) === safePath(uri)` holds across the
tested corpus. The first www call distinguishes raw viewer `#` from a future
trusted target fragment; the centralized second call preserves already encoded
octets. No reproducible handler defect was found, so its template/generated bytes
remain unchanged. Node checks syntax/behavior locally, not AWS runtime compute.

404 keyboard review found the original blue focus outline had only 1.565:1
contrast against the lightest hero stop. Hero links now use a white outline
(13.857:1 against that stop); other links retain the blue outline on light
backgrounds. Desktop/mobile captures and keyboard checks accompany the report.

The two repository-read assertions in the SEO Python suite now use an explicit
`git -c safe.directory=<reviewed-checkout>` just as the existing English builder
does. A process-only foreign-owner simulation reproduced exit 128 without that
scope and success with it; no ownership, global Git trust or test assertions were
changed. The final SEO contract rerun also exercises this read-only-container case.

Current `.github/workflows/validate-pr.yml` runs on PRs targeting main, with no
path filters. Its Docker image installs Boto3/PyYAML, not esprima or Node.
`run_pr_offline.py` discovers both scripts/test_*.py and forms tests, so the new
Python SEO tests would run automatically, but the parser assertion would fail
without esprima. The Node matrix, generation CLI, authoring checker and English
audit currently have no dedicated steps. Do not interpret the current workflow
as complete SEO coverage.

Minimal future CI proposal, requiring independent authorization:

1. In the existing isolated test image, add `nodejs` to the existing apt tool list
   and install the existing `esprima==4.0.1` alongside the pinned Boto3/PyYAML.
   Verify the supplied Node supports `Object.hasOwn` and `URLSearchParams.size`
   (Node 18.16+ API surface), and pin/record the selected distribution image/tool
   versions in the independently reviewed control update.
2. Inside the same Docker `--network none`, read-only checkout, add these commands
   before the existing Python runner (no AWS credentials or OIDC):

   ```sh
   python -B scripts/build_seo_redirects.py --check
   node scripts/test_seo_routing.cjs
   python -B scripts/check_english_authoring.py
   python -B scripts/audit_english.py
   exec python scripts/run_pr_offline.py
   ```

   The Node suite covers matrix/LIVE differential, methods, hostnames, campaigns,
   dormant rules and size. The Python SEO suite covers deterministic generation,
   ES5 parser compatibility, exact 24-rule checksum, persisted disabled switches,
   174-entry sitemap, robots, 404 and deployment boundaries. Existing English and
   incremental/form suites remain intact and skip-free enforcement remains enabled.
3. Do not change `scripts/run_pr_offline.py` or weaken skip enforcement merely to
   add SEO commands. No workflow changes or deployment gate changes were made in
   local phase 3. The current workflow needs this separately reviewed promotion
   before treating SEO CI as a release check.

The base-trusted `validate_pr_diff.py` marks new test files as sensitive and asks
for independent bootstrap review, but does not block them as established controls
because they are absent from the base. Editing the existing workflow in the same
ordinary feature PR would fail the established-control guard. Therefore promote
the CI enhancement through the repository's independent control-review process,
not by weakening the base validator in this feature.

## Proposed GitHub integration (not executed)

Recommended commit: `feat(seo): add approved legacy redirects and custom 404 candidate`

Recommended PR title: `Preserve English routing while preparing 24 legacy SEO redirects`

Proposed PR body:

```text
Historical WordPress paths currently return 403 and www serves duplicate URLs.
Add a reproducible 24-rule CloudFront candidate that preserves campaign parameters
and the LIVE /en rewrite, with a shared responsive 404 and disabled Terraform
activation/error mappings. Preserve dormant redirects and existing SEO content.

Release switches persist in a non-secret site auto.tfvars file, initially false.
The English function remains available for rollback. No AWS or content deployment
is included. CI additions are a separate independent-control proposal.

Validation: attach phase-3 test summary, 192-case redirect matrix, 206 additional
edge cases, full Python/Boto3/form results, loopback HTTP and accessibility evidence,
deterministic generation and isolated Terraform fmt/validate. Node does not certify
CloudFront runtime; AWS TestFunction and a real plan require deployment approval.
```

Do not publish this description with stale counts: the external phase-3 report
records the actual final suite results and exact git-add inventory.

Integration sequence:

1. Revalidate branch/base, remote SHAs, clean original worktree, scope and disabled
   gate. If main advances, review an isolated update of this feature before making
   the PR; do not reset/rebase another worktree or mix exclusive Hugo commits.
2. After commit/push/PR authorization, add only the explicit inventory in the
   phase-3 report. Open feature→main; its diff must contain only SEO files. New
   controls require independent review; do not claim its current CI runs Node.
3. Keep incremental gate=false and legacy workflow disabled. Merging code does not
   authorize infrastructure apply or gate activation. Reconfirm workflow/gate state
   immediately before merge; changes by another operator alter this assumption.
4. After the main merge, synchronize staging in a separate approved operation.
   Staging is currently an ancestor of main, so prefer a fast-forward if protection
   allows it, otherwise a main→staging promotion PR with a merge strategy preserving
   ancestry (no squash for synchronization). This intentionally includes the main
   fixes absent from staging; review that promotion separately from the SEO PR.
   Current validation triggers only for main, so staging promotion needs the same
   local checks or separately approved CI coverage.
5. Do not merge/rewrite Hugo during this feature. Its exclusive changes remain on
   Hugo. Any later main→Hugo reconciliation must be coordinated in another branch/
   PR preserving those commits and resolving content conflicts explicitly.
6. Before and after each merge, verify protected staging pages, English routes,
   sitemap/robots, disabled switches, manifests and ancestry. Risks include stale
   staging promotion, independently advancing Hugo/main, CI tool gaps, accidental
   tfvars overrides and later deployment activation. No git add/commit/push/PR,
   synchronization or remote mutation is performed by this document.
