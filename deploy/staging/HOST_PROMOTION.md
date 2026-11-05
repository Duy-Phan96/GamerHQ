# Reviewed staging Host promotion

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
