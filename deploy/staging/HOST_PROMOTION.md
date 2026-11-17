# Reviewed staging Host promotion

## Localized channel reuse preflight

Run 43218555241 stopped on HTTP 404 while reading the configured Event Manager
text category. The owner diagnostic verified the existing controller, clear
recovery latch, healthy baseline Host, database integrity and backup restore
probe. For a category 404, the diagnostic also compares the bot's guild
inventory by exact ID. The focused Event Manager report distinguishes the
profile's preflight IDs, the Skill's persisted resource policy, and live
parents of recorded Skill channels. A matching category name is a candidate
for review. Use exact IDs and the stored text ownership marker to identify
existing managed resources. An absent inventory entry does not establish
deletion. A diagnostic receipt does not authorize promotion or replace the
live identity and permission checks.

The installed Event Manager package is 0.1.2. The persisted guild lifecycle
record is 0.1.1 and disabled. Preserve that record during this investigation.
Package and Host health evidence does not establish active Skill operation.

### Review the missing Event Manager categories

Use the owner collector's read-only event category recovery review. It queries
the configured voice category independently of the text 404, checks text
channel IDs and unique ownership markers, and records overwrite fingerprints.
It also reads temporary voice IDs from the managed-resource registry and
occurrence records. The review never selects a replacement from a name match.
An empty bot inventory does not exclude categories hidden from that bot.

For this recovery both configured category lookups return 404. The original
text category has a recorded deletion. Verify that neither category has a
hidden replacement with the guild owner's inventory. Require a separate
voice-channel review if any temporary voice binding or child remains. When
the other gates pass, `RECOVERY_PLAN_READY_FOR_OWNER_REVIEW` binds the planned
changes to a `planSha256`. The plan proposes one private EVENT TESTING category
and one private EVENT VOICE category. Both deny `@everyone` View Channel and
allow the verified Staging bot to view. The text category additionally allows
the bot to send and read message history. The voice category allows the bot to
connect. Verify existing effective Manage Channels and Manage Roles from guild
roles. The plan grants neither of those permissions nor Administrator.

The owner-approved change record must bind the exact guild, old category ID,
the two existing channel IDs, their parents and overwrite fingerprints, the
profile and Skill policy values, and a verified backup and restore probe.
Recheck this evidence under the existing operation lock before changes. Bind
the exact approved destination before attempting any moves. For a newly
created category, record its returned ID and verify the category's permissions
first. Journal an attempt before sending each category creation request. Retry
only against recorded, reverified IDs. If creation has an uncertain outcome,
stop and resolve the identity before trying again.

Use the existing Host channel edit contract with `sync_permissions=False`
for each approved channel. Compare the resulting parents and overwrite
fingerprints after each move. On retry, a channel already in the approved
category with identical overwrites is complete. A different parent, ownership
marker or overwrite fingerprint requires a fresh review. An interrupted
change can be rolled back by returning moved channels to their verified
original unparented placement without syncing overwrites. Leave any new
category for owner review.

After the moves are verified, replace only `categoryId` and `voiceCategoryId`
in the protected Staging profile and its existing guild-scoped
`skill_resources` setting, checking the previously recorded values first.
Preserve other policy fields, Skill lifecycle, and all resource and message
bindings. A partially applied reference change requires verification and
review before resuming or reversing. Restore only the two prior field values
if compensation is approved and the current values still match the recorded
new values. A fresh read-only preflight must pass before the separate protected
Host promotion.

The one-time owner apply mode accepts the exact approved plan SHA256 and a
separate confirmation that the owner checked for hidden replacement categories.
It uses the existing Staging worker identity and operation lock, creates and
verifies a fresh private SQLite backup, and records progress in the private
worker journal. It writes only the two category creates, the two single-channel
placement changes, and the category reference fields. `EVENT_CATEGORY_RECOVERY_STOPPED`
means stop and review the reported phase. A journaled create without a returned
ID must be reconciled against Discord before any further create. For a recorded
ID or an interrupted move, the same owner operation can resume only if the
approved source, data, IDs and overwrite fingerprints still match.

`EVENT_CATEGORY_RECOVERY_VERIFIED` confirms both category permissions, both
parents and unchanged overwrite hashes, matching profile and Skill policy,
unchanged Event Manager data, healthy baseline container and the live Discord
lookup preflight. It does not dispatch a Host promotion.

## Reviewing permission and ownership blockers

Bulk structure creation omits explicit Manage Messages grants. Existing
channel and managed-message permission flows retain their own policies.
Review any remaining overwrite bits individually. Do not grant Administrator
or grant guild-wide moderation just to finish creating channels.

The owner-only preview reports roles that satisfy the current Staff policy.
A role name alone is insufficient evidence. If no suitable role exists, review
a dedicated Staff role with access limited to the approved private categories
before changing the policy. Verify the Instant Gaming integration separately.
Private categories must deny @everyone from creation.

For the two Allgemein channels, the report lists saved reference locations
alongside each candidate ID. Check which settings are active owners and which
are legacy aliases or feature references before reconciling the mapping.
Preserve both IDs and use separate approval for any placement change. Open a
fresh preview to review dependent channels after the parent decisions.

## Recovering a partially applied bulk server setup

The Staging deployment of Host `c4985b0df53fffad7b83790b257368ac243c013a`
reported `HOST_READY`, Event Manager `0.1.2`, verified backup and a passed
restore probe in [promotion run 43042142949](https://github.com/Duy-Phan96/GamerHQ/actions/runs/43042142949).
The owner's subsequent setup report recorded 32 created, 15 failed and 19
skipped. Sixteen skips named a parent dependency. Two required verification of
existing Allgemein channels and one intentionally excluded optional gaming-chat.
The old handler discarded exception details, so this report cannot establish
the historical Discord status or exception for the 15 failures. All four failed
categories and all eleven directly failed text channels take an explicit
overwrite path. Compare the current Host role with the permissions named by the
new preflight before assigning any additional permissions.

After protected deployment, use **/server setup → Server Structure → Setup All
Missing** on GamerHQ Staging to open a new owner-only preview. Download
`bulk-setup-preview.txt` from the first response. It contains every decision,
review reason, parent dependency and verified resource ID from the same scan.
The detail pages are optional. Stop at the preview. Compare **Already configured** against the original 32 resources
using the current mappings and Discord inventory. If any are missing or appear
under **Link existing**, verify the actual existing IDs before approval.
Review **Blocked by parent** separately from **Needs review**. A permission
preflight rejection requires a review of the exact bit and policy. Do not
grant Administrator, remove required overwrites or open private resources to
get past a rejection.

For STAFF, SUPPORT TICKETS and AFFILIATE STATS, confirm the expected Staff
roles and the Host access and check that @everyone is denied. Confirm the
selected Instant Gaming bot under Server Management → Integrations before
allowing affiliate-specific access. For server-log, verify Staff read access
and Host posting under the existing log policy. Leave ambiguous roles, bot
identity, or permissions unresolved until reviewed.

For Allgemein, check the ID, type, current parent, absence of conflicting
mappings, and @everyone history access (text) or Connect (voice). Select a
verified offer only to link its ID. Use a separate placement preview for any
move or rename. Use the full new preview for explicit owner approval before
clicking **Confirm Setup**. Keep the Event Manager resources, managed
messages and game catalog outside recovery.

After confirmation, keep the downloadable result. A transport timeout or
mapping-write failure may leave a Discord resource present. Open a fresh
preview and verify its identity, mapping, parent and permissions before retry.
Never use a prior confirmation, delete an existing resource, or create a
replacement to resolve an ambiguous result. Share only the aggregate counts
and sanitized resource-level error codes in GitHub.

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
