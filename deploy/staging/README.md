# Isolated Skill staging on the GamerHQ VPS architecture

This profile reuses the reviewed Host image, UID 10001, read-only container,
SQLite, Docker Compose and SELinux-aware bind layout. It has its own project,
container, network, loopback API port, data, backups, artifacts, worker and secrets.
No production path, database, bot or credential is reused or changed. These are
unconfigured templates; no staging infrastructure or live bot has been created.

| Resource | Dedicated staging location |
| --- | --- |
| Compose project / container | `gamerhq-staging` / `gamerhq-staging-bot` |
| Network / API | `gamerhq-staging-private` / `127.0.0.1:18080` |
| Data / backups | `/opt/gamerhq-staging/data` / `/opt/gamerhq-staging/backups` |
| Reviewed artifacts / temporary builds | `/opt/gamerhq-staging/artifacts` / `/opt/gamerhq-staging/worker` |
| Installed reviewed worker source | `/opt/gamerhq-staging/deployment-worker` |
| Protected configuration | `/etc/gamerhq/staging` |

## Owner-controlled prerequisites

Provisioning requires infrastructure authorization. A trusted administrator must
prepare the dedicated paths, protected reviewed worker installation/venv and a
runner labelled `gamerhq-deployment`. This runner must not accept untrusted PR
jobs. Do not automatically add accounts to the Docker group or change VPS policy.
The bot data/backup directories and new database need effective read/write access
for UID 10001; retain private access and provision appropriate dedicated SELinux
labels/ACLs. Verification checks access from an offline container before any Gateway
can start. It does not relabel production or grant permissions automatically.

Copy these templates into the protected configuration directory only after approval:

- `compose.json` -> `/etc/gamerhq/staging/compose.json`
- `profile.example.json` -> `/etc/gamerhq/staging/profile.json`
- `bot.env.example` -> `/etc/gamerhq/staging/bot.env` (mode 600)
- `event-manager.release.example.json` -> `/etc/gamerhq/staging/event-manager.release.json`
- `sdk.release.example.json` -> `/etc/gamerhq/staging/sdk.release.json`

Supply a new Discord application/token and a dedicated guild/categories. The staging
bot must belong only to that guild; it must not be a member of production. Record
the public production/staging bot and guild IDs for exclusion checks. Keep production
`.env`, private databases, credentials and backups outside this workflow. An approved
anonymized/remapped six-table fixture may be supplied separately; never automatically
copy the live production database into staging or back over production.

Record the approved local Docker daemon ID and an already-present immutable Host
base image ID/digest. Remote Docker contexts are rejected. Set independent staging
Web API credentials in the private bot environment and protected GitHub environment
secret `GAMERHQ_STAGING_WEB_API_SECRET`; the verifier requires them to match.

For the private Skill repository, supply an approved read-only GitHub App installation
token or fine-grained credential restricted to required release contents, as the
`GAMERHQ_SKILL_RELEASE_TOKEN` environment secret. The public Host workflow token is
not assumed to have private access. No credential has been requested, created,
copied, printed or configured by this implementation.

## Release trust and distribution

The existing Skill release workflow publishes versioned wheel/sdist/SHA256SUMS
candidate assets for `v<version>`. The prepared Host SDK workflow uses the independent
`skill-sdk-v<version>` namespace and publishes an SDK wheel/SHA256SUMS prerelease,
without deploying the Host. No tags or releases have been published in this session.

Each protected descriptor must explicitly approve source, exact version/tag,
reviewed commit and independently reviewed SHA-256. SDK version/hash must agree
with the profile. Set `allowPrerelease=true` only when explicitly accepting a
candidate prerelease for staging. The defaults remain unreviewed and blocking.

The Host fetcher reads only approved publisher repositories, resolves annotated tags
to the reviewed commit, requires one matching wheel and checksum entry, verifies
downloaded bytes and wheel metadata, and preserves existing artifacts on conflicts.
API credentials are not forwarded to asset redirects. Wheels remain in the protected
artifact directory; private Skill bytes are never uploaded to public Host CI.

## Prepared workflow, disabled by default

`Isolated Skill staging preparation` invokes the fixed installed worker, with no
repository checkout on the deployment runner. Its gate is
`GAMERHQ_STAGING_PREPARATION_ENABLED`; no such variable currently exists. Protect the
`staging` GitHub environment and supply approved credentials before enabling that
preparation gate. The separate deployment worker gate also remains disabled.

Every operation uses the fixed profile above:

| Operation | Behavior / approval |
| --- | --- |
| `inspect` | Validate isolation layout only; no readiness or credential claim |
| `initialize` | Create an empty staging SQLite database exclusively; requires `INITIALIZE ISOLATED STAGING`; refuses existing data |
| `build-base` | Require `BUILD ISOLATED STAGING BASE`, the approved daemon and exact clean reviewed worker source commit; build only `gamerhq-staging-base:<commit>`, report immutable bytes for review, start no Gateway |
| `fetch` | Download reviewed GitHub Release SDK/Skill artifacts; never execute or install them |
| `verify` | Read-only Docker/context/filesystem, Discord identity/membership/category permissions, credential agreement and release trust checks; write private verification evidence |
| `build` | Require verification; build/probe the initial immutable candidate without credentials, network, production mounts or Gateway |
| `bootstrap` | Require verification and `BOOTSTRAP ISOLATED STAGING`; refuse an existing staging container; start only the dedicated staging service and verify Host/package availability |
| `install` | Require verification and `INSTALL IN ISOLATED STAGING`; use the same public Host installation API as other customers; leave configuration/enablement explicit |

The worker profile starts with `workerEnabled=false`. Read-only verification never
enables it. After reviewing actual verification evidence and authorizing staging,
the owner may enable the protected profile and re-verify it. Evidence is valid for
one hour and is bound to profile, Compose, private bot environment and both release
descriptors. A change invalidates it. Actual daemon identity, layout and artifact
bytes are checked again before worker actions.

If the reviewed Host base is not already available, use `build-base` through the
prepared workflow, review its reported immutable ID, record that ID as `baseImage`
and re-verify. The build uses the installed reviewed source and public locked Host
dependencies, without production volumes, secrets or container replacement. This
prepares the initial Host image without routine manual VPS build commands.

Bootstrap is distinct from updates because no previous staging Host exists yet.
It checks container nonexistence and package availability before normal per-guild
installation. Failed bootstrap stops only that dedicated staging service and retains
its data for review. Subsequent releases use `Approved Host Skill worker` with its
normal staged verification, deployment-ID approval, history and guarded rollback.
Its staging profile/worker paths point to the dedicated locations above.

## Acceptance before production authorization

After explicit staging authorization, use the public Runtime install/configure/enable
flow and the same Discord/Web schema API. Verify persistent area/card identity across
weekdays, editing, participants, privacy, separate occurrences, voice retention,
restarts and retries. Rehearse six-table migration/unchanged rollback on an approved
anonymized, ID-remapped existing-data copy and retain mapping/hash evidence. Adopted
legacy voice channels remain retained. Test a failed update and data-write recovery
refusal. The synthetic offline suite and public-fixture Docker smoke are existing
evidence, not a claim of this live acceptance.

Only then request separate production authorization for the exact reviewed image,
deployment ID, guild/resource policy, backup, migration plan and recovery procedure.
Production provisioning, deployment, destructive migration and channel cleanup stay
outside these staging operations. Current blockers are actual infrastructure access,
dedicated credentials/IDs, approved published artifacts and explicit live authorization.
