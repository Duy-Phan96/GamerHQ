# Reviewed staging Host promotion

## Bulk setup permission candidate

[Candidate PR #260](https://github.com/Duy-Phan96/GamerHQ/pull/260) overlays
[Host PR #259](https://github.com/Duy-Phan96/GamerHQ/pull/259) on the verified
Staging Host `1539eb13cb242a765ea89130c28b0438ce8dcb48`. The exact candidate is `71e1fcbb0cf3904365d1ed020a866b50755f58e4` and
the archive SHA256 is `8636420eacd2f106b6b7a2b4ded497a02bfa29ac50af41122711f351b0d7aa0b`. [Candidate CI](https://github.com/Duy-Phan96/GamerHQ/actions/runs/43089158534).

After scoped CI, use the checksum-verified owner installer with
`--bulk-permission-fix --approval 'ENROLL STAGING BULK PERMISSION FIX'`. It requires the current
controller `8eea84e96a706b82e1ffb66a70b6e3c6fd01bd64`, the approved Staging image, and a clear recovery latch.
Register the matching workflow after enrollment and use the existing protected
owner review for promotion.

After promotion, collect the owner-only Bulk Setup attachment. Review the existing
Staff role evidence and the saved references for both Allgemein channel IDs.
Keep the preview unconfirmed pending the reviewed setup plan.

## Bulk setup preview report candidate

[Candidate PR #256](https://github.com/Duy-Phan96/GamerHQ/pull/256) overlays
[Host PR #255](https://github.com/Duy-Phan96/GamerHQ/pull/255) on the
verified Staging Host `96855865b746c214f4197b9f2808cbae1c21b7ff`.
The exact candidate is `1539eb13cb242a765ea89130c28b0438ce8dcb48`, archive SHA256 `1faa932bc6c4878256e09444b0f2a7c8ac327a4f57bfe580d4a24baaba0b501a`.
[Candidate CI](https://github.com/Duy-Phan96/GamerHQ/actions/runs/43081548397).

Use the checksum-verified owner installer with `--bulk-preview-report --approval 'ENROLL STAGING BULK PREVIEW REPORT'`.
It requires the current controller `b7753c60461bd4281758b66b0cb2b6d1687b22be`, the approved Staging image,
and a clear recovery latch. Keep workflow registration and protected promotion
separate from enrollment.

After promotion, the owner opens **/server setup → Server Structure → Setup All
Missing** in Staging and downloads the complete preview from the first
response. Compare the configured resource IDs, reason for each review item, and
parent blockers. Stop before **Confirm Setup**.

## Bulk setup recovery candidate

[Candidate PR #252](https://github.com/Duy-Phan96/GamerHQ/pull/252) overlays
the fix from [Host PR #251](https://github.com/Duy-Phan96/GamerHQ/pull/251)
on the currently deployed Host `c4985b0df53fffad7b83790b257368ac243c013a`. The exact candidate is
`96855865b746c214f4197b9f2808cbae1c21b7ff`, archive SHA256 `4b6eb69666f4268ac715cd94797b35139c917dd5a074bb5a8a5d082386ade37f`.
[Candidate CI](https://github.com/Duy-Phan96/GamerHQ/actions/runs/43057102226).

The owner-only pin installer requires the active bulk setup controller
`890cdfe91607bf3c47c015a65e2061f18ff1d865`, the currently approved staging image and a clear recovery latch.
Use `--bulk-setup-recovery --approval 'ENROLL STAGING BULK SETUP RECOVERY'` after verifying the
immutable installer checksum in scoped CI. The installer leaves the catalog
authorization, SSH restrictions and live resources in place. Register the
matching workflow after enrollment and use the existing deployment-security
review for promotion.

After promotion, verify the backup, restore probe, Host health, Event Manager
0.1.2 and authority preservation. Open the owner-only bulk setup preview and
compare the existing resource IDs against the Staging inventory. Stop before
confirmation. Use the recovery runbook in the Host fix to review private
policies and both Allgemein channels.

## Bulk structure setup candidate

[Candidate PR #248](https://github.com/Duy-Phan96/GamerHQ/pull/248) overlays the reviewed
bulk setup on the installed staging Host `de6447a25d36dc002070b2f0e94b4df0dcc9ef0b`.
The exact candidate is `c4985b0df53fffad7b83790b257368ac243c013a` and its uncompressed archive SHA256 is `ad7db7f7c85f0e5ea94439148d8c63e5651dfdbc0080a677036f4afc6e2a73b8`.
[Candidate Linux CI](https://github.com/Duy-Phan96/GamerHQ/actions/runs/43027658058)
verifies the archive and application.

Enrollment requires the existing structure controller `aefa82516ee227c4281bb65e08a16a083f1f7460`, the
installed approved staging image and a clear recovery latch. Use the
checksum-verified installer with `--bulk-structure-setup --approval 'ENROLL STAGING BULK STRUCTURE SETUP'`.
Register the matching workflow on main after verified enrollment, then queue the
protected promotion for the owner. Deployment preserves the installed Event
Manager 0.1.2 package and its existing lifecycle configuration.

After deployment, open `/server setup → Server Structure → Setup All Missing`
in GamerHQ Staging. Review the counts, private permission summaries and eligible
Allgemein channels. Keep confirmation pending until the owner explicitly approves
the displayed plan.

## Structure phases candidate

[Candidate PR #244](https://github.com/Duy-Phan96/GamerHQ/pull/244) overlays the
ten application and test paths from merged Host PR #243 on the existing
`6734b756685d90de8e56ddc299560ec13625300e` staging Host. The exact candidate is
`de6447a25d36dc002070b2f0e94b4df0dcc9ef0b`, with uncompressed Git archive
SHA256 `260c8f886d7485bd6fafa7701ac9021d07d4cf1d8cff81c8387034d00731faf6`
and [candidate CI](https://github.com/Duy-Phan96/GamerHQ/actions/runs/42876302300).

Before enrolling, confirm the active staging controller is
`ff519f698f973b056eeae0b9d026a71dbf8fbaae` with the current Host pin and a
clear recovery latch. The owner uses the checksum-verified installer from the
reviewed controller head with `--structure-phases --approval 'ENROLL STAGING STRUCTURE PHASES'`.
Enrollment only updates the exact Host pin and immutable controller selection.
The protected workflow registration and `deployment-security` review remain
separate steps. The Event Manager 0.1.2 artifact and lifecycle are retained.

After promotion, health, backup and restore verification, the first owner
acceptance is a read-only review of the two existing `Allgemein` channels.
Record each ID, type, parent, ownership and permission result. Confirm text
history access and voice connection access. Do not select or confirm a link
or move until that evidence has been reviewed.

## Managed-message Configuration Preview candidate

Draft staging source [PR #240](https://github.com/Duy-Phan96/GamerHQ/pull/240)
is based on Host `89709625d9ea0c74c2b731436d1d13c9dcb453c4`. Its exact head is
`6734b756685d90de8e56ddc299560ec13625300e`, with uncompressed Git archive
SHA256 `5f22a55a8d13d8a48fcb040d308bba240ba8d805939d9b51017a5f26efb368eb`.
The seven changed files match [Host PR #239](https://github.com/Duy-Phan96/GamerHQ/pull/239).
[Candidate CI](https://github.com/Duy-Phan96/GamerHQ/actions/runs/42834032907)
runs the Linux matrix. The scoped controller rehearsal independently verifies
and prints the exact archive hash on Linux.

The previous protected [promotion receipt](https://github.com/Duy-Phan96/GamerHQ/actions/runs/42718803421)
reports Host `89709625`, Event Manager package `0.1.2`, a verified backup,
a passing restore probe and retained Event Manager authority. A subsequent
[smoke receipt](https://github.com/Duy-Phan96/GamerHQ/actions/runs/42777421404)
confirms the same Host and installed package. Before enrollment, obtain a
fresh **Policy-bound staging automation → Run workflow → main → status**
receipt. It must report `ACTIVE`, `bootstrapVerified: true`,
`controllerCommit: 4b15a48574f7c6ff3cc20f441afbc75160fd0bfb`,
`hostPromotionCommit: 89709625d9ea0c74c2b731436d1d13c9dcb453c4` and
`recoveryRequired: false`. A mismatch stops this handoff. The status command
records normal controller access bookkeeping without changing the application.

After controller review, run the checksum-verified installer as the owner in
the staging VPS root session. Use the exact controller commit and installer
SHA256 from the controller PR's scoped automation CI receipt.

```sh
set -eu
umask 077
controller='<reviewed controller commit>'
installer_sha256='<CI-verified installer SHA256>'
work=$(mktemp -d)
installer="$work/extend_staging_host_promotion.py"
curl --fail --silent --show-error --location \
  "https://raw.githubusercontent.com/Duy-Phan96/GamerHQ/$controller/tools/extend_staging_host_promotion.py" \
  --output "$installer"
printf '%s  %s\n' "$installer_sha256" "$installer" | sha256sum --check --strict
python3 "$installer" --commit "$controller" --managed-message-preview \
  --approval 'ENROLL STAGING MANAGED MESSAGE PREVIEW'
```

The installer accepts only the prior reviewed controller, workflow and catalog
pins. It requires an active staging baseline and a clear recovery latch under
the existing operation lock. The enrollment receipt records the new Host pin
and leaves the profile, database, containers, catalog authorization and SSH
policy intact. The existing `host-promote` operation still requires the
registered `main` workflow and separate `deployment-security` owner approval.
The offline image rehearsal checks imports and controls without Gateway login.
Review live startup's existing command sync, operational logging and temporary
voice cleanup before authorizing any restart.

After separately approved promotion and health verification, run
**/server dev → Configuration Preview** in the staging guild and classify the
24 unresolved conflict rows from live evidence. Reconcile, setup, repair and
catalog import have separate review and approval.

## Configuration Preview promotion

The exact reviewed Host candidate is `89709625d9ea0c74c2b731436d1d13c9dcb453c4`
from [candidate PR #232](https://github.com/Duy-Phan96/GamerHQ/pull/232), validated
by [run 42646395695](https://github.com/Duy-Phan96/GamerHQ/actions/runs/42646395695).
The uncompressed Git archive SHA256 is
`b4155457b84179aad5263da8d9dda63fd83751e9de29a0c3c34aafbecf964a2c`.
The promotion accepts only the reviewed path delta, checks the exact archive
and requires the installed staging Host `29ecf575` and the verified Event Manager
0.1.2 artifact. It leaves the approved schema, dependency locks, Host adapters,
Skill code and existing catalog seed unchanged.

Use the checksum-verified installer from the reviewed controller commit with
`--configuration-preview` and approval text `ENROLL STAGING CONFIGURATION PREVIEW`.
The installer verifies the existing catalog controller `11e4062b`, its Host pin,
protected workflow digest and catalog preview modules. Only the root-owned
controller selection and exact Host pin change. The catalog workflow digest,
SSH restrictions, runtime profile, deployment state, database and containers
remain in place. The scoped CI rehearsal enrolls this revision from that exact
prior controller on a disposable runner and records the installer SHA256.

After enrollment, register the exact workflow in
[draft PR #234](https://github.com/Duy-Phan96/GamerHQ/pull/234), dispatch the
existing `host-promote` workflow on `main`, and obtain the actual
`deployment-security` owner review. The protected apply verifies the backup,
restore probe, retained Event state, candidate health and rollback readiness.
After successful staging health verification, use the private Configuration
Preview and compare it with the [Production baseline](../../docs/SERVER_CONFIGURATION_PARITY.md).
Staging structure changes and the catalog import have separate approvals.

## Configuration Preview recovery hold

Run `42664893488` rejected the installed guild lifecycle row before the Host
promotion could apply a replacement. The package manifest and approved Skill
artifact identify 0.1.2. The unchanged guild row records 0.1.1 and disabled.
Host health alone does not establish enabled Event Manager or Discord event
integration health. Retain the guild row exactly and verify the package and
artifact independently.

The recovery-only enrollment is for controller `0931e16e` with its existing
exact Host pin, protected workflow, catalog authorization and an active recovery
latch. It switches only to the reviewed controller code and leaves the latch
set. It records the prior Skill-update backup's file mode without opening it.
Routine updates had imported the legacy worker's controller, which let SQLite
create backup files with default permissions. The corrected controller reuses
the reviewed deployment module that creates backups at mode 0600. Keep the
existing unsafe backup out of inspection and restore selection. The earlier
verified Host backup establishes restore feasibility for that historical copy.

After enrollment, use the checksum-verified owner-only
`tools/staging_host_promotion_diagnostics.py` collector for the corrected
controller. A `RECOVERY_HOLD_VERIFIED` receipt binds the installed controller
files to the reviewed source, checks the current image, wheel and mounted
database, and verifies that the earlier Host backup still passes the
in-memory restore probe. The receipt confirms that the unsafe Skill-update
backup remains excluded. The read-only operation never releases the latch.
Routine health, smoke and history are read-only, but do not replace this
combined verification. The routine `recover` operation remains blocked.

After owner review of the held receipt, the same checksum-verified collector
has a separate root-only `--release-verified-recovery` mode with approval
`RELEASE VERIFIED STAGING HOST RECOVERY HOLD`. Under the existing operation
lock it rechecks the controller, image, artifact, mounted database, guild
lifecycle, deployment history and historical backup. Only the known
Skill-update backup may be corrected from 0640 to 0600. It opens the inode for
metadata only before chmod. It then verifies the recorded checksum, SQLite
integrity and an in-memory restore against the staging guild. A failed
verification leaves recovery held and keeps any already tightened permissions.
The action logs the approval attempt, permission change, verification and
release to the existing staging security journal. It changes only the backup
mode and, after all checks pass, the recovery latch.

A `RECOVERY_HOLD_RELEASED` receipt is the boundary before separately
reviewing Host promotion. This action does not restore a database, change
Skill lifecycle, restart the Gateway, or dispatch the protected workflow.

## Event Manager V2.1 pin replacement

Host #219 is pinned at `29ecf575f3d865b0f32a9d82adf1b06d7969cdc9`, with CI run
`42060126446` and Git archive SHA256
`8d4286c5596963fe10b8f36da163bbe3de12d6b2512673f5b54535145ad424bd`.
The installer requires the installed controller `9c1a6f4` and its prior
reviewed pin. The promotion requires the current Host `17858f4` and the exact
published Skill 0.1.1 bytes. It keeps that Skill version while verifying the
backup, restore probe, retained Event state and channel/card mappings.

After the protected Host promotion and smoke pass, prepare Skill 0.1.2 through
routine staging automation. The release resolves to
`41a7d968852453d815a52f2f03e4ccac7e93eff9` and its wheel SHA256 is
`84121ae4e48c151ec9060faec70e1d262b3c961629c8cdaa535afcba96788465`.
Apply only after the offline probe succeeds, then verify restart, health,
deployment history and smoke. Manual Discord UX acceptance is separate.
Recovery after consent or attendance writes requires review before returning
to older invitation behavior.

## Event Manager V2 pin replacement

The next reviewed Host target is `17858f47480e914956dd241b9f37a328b4c6ac2b`
from #216, verified by Actions run `42013244636`. Its application archive
SHA256 is `77504247b70dee58c6f7d982eb4d65e50218a7fccb3581cd7e5723799fe888d9`.
The live baseline must report Host `6a2c74a234d693abe70f5f69dfbb41dbce38bc75`
and installed Skill `0.1.0`. Dependency locks, Dockerfile, database, Runtime,
portable Skills and services must match the baseline. The root-owned controller
permits only the exact reviewed Host adapter file differences.

The pin installer verifies the existing immutable extension
`135bbf604926d6b1794503d17821ad00f1c09555`, its policy and entry, and selects
the new revision under the existing operation lock. Active authorization,
SSH routing, the worker binding, configuration, credentials, database and
containers are retained. After checking the new pin via the routine status
operation, use the protected Host workflow in `deployment-security`.
Its workflow and signed proof remain specific to the new target.

1. Promote the Host while the published `0.1.0` Skill is installed. Check the
   backup and restore probe, existing database records, channel and card mappings,
   Health and Smoke receipts.
2. Only after that passes, use the routine staging workflow to prepare the
   reviewed `0.1.1` GitHub prerelease from Skill #7 and #8. Its image must pass
   the offline probe on the new Host base. Apply it through the existing
   approved staging update operation.
3. Check the installed pair and deployment history, then run a bounded restart
   and smoke test. Preserve the backups. Failed health permits automatic image
   rollback only while the application fingerprint matches the backup.
   Any recovery after writes or use of new scheduling semantics needs review.
   Manual Discord acceptance is a separate owner test.

## Previous promotion

Owner authorization covers only application commit
`6a2c74a234d693abe70f5f69dfbb41dbce38bc75`. It does not authorize production,
new credentials, broader SSH/Docker privileges or a destructive migration.

The existing scoped controller gains one `host-promote` operation. A separate
`staging-host-promotion.yml` job runs in `deployment-security`; GitHub must record
an actual owner approval. The VPS verifies signed short-lived OIDC identity,
repository/owner IDs, main ref, exact workflow bytes, nonce/replay protection and
the review. Routine workflow identities cannot invoke this operation. Its source
is fixed in root-owned policy; there is no user-supplied URL, commit, image or path.
It shares existing locks, audit logs, request/build/restart limits and revocation.

## Preparation and safety

The current image must report baseline Host source
`92db47042d58055cb0debdfd83f11f8508e19809` and be the approved staging container.
The immutable application archive SHA256 is
`ca3c9acbf7087d59881f130c903bf13b2c47fed1f2157933f6ca08e07df2fc97`.
Schema, dependency locks, Dockerfile, Runtime contracts and portable/Host service
trees must match the baseline. Public Host CI must still report success for the
exact target. Private-wheel compatibility CI is recorded in Host PR #209.

An application-only overlay uses the existing image's dependencies, then the
existing Docker staging adapter verifies/reinstalls the same published SDK and
Event Manager 0.1.0 bytes and probes with no credentials, mounts, network or Gateway.
Build source never replaces root-owned controller/worker code. BUILD_COMMIT and
the image revision identify the exact approved application commit.

Application uses the canonical `SkillUpdateController`, with an explicit reviewed
same-Skill-version Host promotion. It stops the old Gateway before replacement,
takes an exclusive 0600 consistent SQLite backup, verifies integrity and restores
into a disposable clone, checks the clone fingerprint, then applies the staged
immutable image. Skill installation/enablement, authoritative ownership and all
existing Event configuration/membership/history/resource identities are checked
against the backup after health. Scheduler status may naturally advance; retained
Event data must not disappear or change. No live database restore is automatic.

Failed startup can restore the old image only if application data is unchanged.
Otherwise the controller stops and records RECOVERY_REQUIRED. A successful apply
records the source and updates the staging profile's base image, so subsequent
approved Skill patches retain the restored UI. The trusted legacy worker binding
is unchanged. An interrupted application requires reviewed recovery; it is not
silently retried. Historical deployment/backup evidence stays in existing storage.

## Installing the capability

The existing SSH identity intentionally cannot replace trusted root-owned code.
`tools/extend_staging_host_promotion.py` installs a versioned controller bundle and
atomically selects it under the existing operation lock, preserving enabled state.
It verifies original source, immutable delivery and the existing workflow hashes.
The bundle includes exact canonical deployment-controller source, not a forked
implementation. The installer is Python 3.9 compatible, restores 0750/0640 modes
under umask 077, preserves existing SELinux labels and restores new-path contexts.
Retries install only the same immutable revision. SSH routing, keys, sandbox,
worker, database, configuration secrets and containers remain unchanged.

The owner must install that reviewed capability once because this Codex environment
has no root VPS access. The published operator command must use a real immutable
commit and SHA256; do not run the legacy source-only worker repair for a UI change.
After the public installation receipt, verify controller status and dispatch the
protected Host promotion workflow. The owner reviews that run in GitHub. Do not
claim installation or deployment before its actual receipt/result is verified.

## Acceptance

Record actual deployed image/source, Skill 0.1.0 installation, backup/restore probe,
authority/data retention and HOST_READY after application, then run scoped health
and smoke. The owner will manually test Discord and provide screenshots. Automated
component snapshots and offline image probes do not establish live UX acceptance.
Both legacy deployment gates and production remain unchanged.
