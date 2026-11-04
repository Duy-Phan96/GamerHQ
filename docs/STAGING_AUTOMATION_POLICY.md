# Host-managed staging automation

The owner's 2026-11-03 operating policy separates routine staging from explicit
credential, security, destructive-data and production authorization. It does not
authorize the first Discord Gateway connection.

## GitHub environments

`staging` contains the existing restricted SSH identity and private configuration.
It permits only `main`. Routine health, release verification, offline preparation
and approved staging updates run automatically within the existing Host protocol.
Do not regenerate the key, enable password authentication, forward an agent, add
an arbitrary remote shell, or grant production Docker/root access.

`deployment-security` contains no credentials. It requires the owner reviewer,
disallows admin bypass and permits only `main`. Credential changes, access
revocation, live authorization, first Gateway bootstrap and production operations
must pass this environment before their execution job can receive credentials.
The credential/security and production paths fail closed on rejected, cancelled
or skipped authorization. Production's existing deployment gate remains disabled
until separately authorized; changing workflow policy does not deploy production.

Create and protect `deployment-security`, merge the dependent workflow guards,
and only then remove the routine reviewer requirement from `staging`. Preserve
the encrypted secrets and main-only branch policy throughout the transition.

## Routine operations

`prepare-offline` composes only build-base, exclusive database initialization,
artifact fetch, offline verification and isolated image build/probe. It never
calls live authorization, bootstrap or installation. Resume a partial run at its
failed individual operation; never overwrite a database to rerun initialization.

`diagnose` checks health, runs the existing inspect operation, then reproduces
build-base through the same restricted protocol. Each call has distinct
structured transport evidence. An opaque error must not be described as a Docker
build failure without evidence that the build command was reached.

The staging update workflow can run `auto` explicitly, or after successful
selection of an approved immutable release. Updates remain dormant while their
gate is disabled. Automatic application first requires `HOST_READY` with a live
health check, then fetch/verification and an actual `READY` deployment ID. An
offline/stopped Host never reaches application, and this path never bootstraps a
Gateway. The Host still enforces publisher/source/checksum, runtime/capability and
migration compatibility, deployment freshness/generation, backup, health and
guarded image rollback. Destructive migration modes remain rejected. Failed or
mismatched application is never reported as success; no automatic database
replacement is permitted.

Release selection and its automatic-update job share the originating `main`
workflow. Do not replace this dependency with `workflow_run`: this repository's
default branch is `develop`, so that event's ref cannot enter the main-only
staging environment. A failed release-selection job cannot trigger application.

## Diagnostics and audit

The routine `resume-offline` Actions operation uses the installed version-one SSH
contract. Read-only health, existing storage and writable verification parents
must pass before any build. The fixed sequence never includes initialization,
source replacement, live authorization or deployment. Repeating it reuses the
existing database and immutable release descriptors. This client-side orchestration
needs no VPS code installation. Routine Skill package updates continue through
the Host controller; changes to root-owned control code or its security policy
remain separately reviewed owner operations.

The SSH client captures verbose connection evidence privately and emits only a
fixed error category, phase, operation, exit status and public-key authentication
fact. It never echoes raw stderr, secret stdin, keys or configuration. Host-key,
authentication, connection, entry-point and worker rejection are distinct.

The installed worker is root-owned and immutable. Its existing audit trail must
not be bypassed by granting shell/root access. If its error contract cannot expose
the failing prerequisite, separately reviewed owner diagnostics or a worker
security revision may be necessary; do not invent a cause or widen permissions.
Source/credential/security-policy changes require explicit authorization.

Successful offline probes are not live Discord/Web, production-shaped migration
or production cutover acceptance. Those boundaries remain explicit and separate.
