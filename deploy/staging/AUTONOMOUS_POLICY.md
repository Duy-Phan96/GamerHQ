# Autonomous staging policy

This extends the installed Host controller without replacing the installed
worker, its pinned application base, database, bot credentials or sandbox. It
does not change or approve pending first-Gateway run **41850674216**.

## Authority boundaries

| Identity | Environment | Authority |
| --- | --- | --- |
| Dedicated automation Ed25519 key + GitHub-signed short-lived OIDC | `staging-automation` | Fixed staging-only health, prepare, install, update, restart, recover, smoke and history operations |
| Same limited transport + owner-reviewed OIDC job | `deployment-security` | Activate/deactivate the enrolled policy; cannot expand its immutable limits |
| Existing legacy key and configuration packet | `staging`, owner-protected after initial bootstrap | Existing security/credential operations; never copied into automation |
| Separately owner-reviewed deployment | `production` | Production remains disabled and needs its own credentials/approval |

The SSH server selects a separate root-owned forced command for the new key.
Legacy protocol, first bootstrap, shell commands, arbitrary Docker, file paths,
guilds, credentials and production operations are absent from that protocol.
Root-owned policy/controller files run as the existing restricted UID through
the existing `systemd --user` sandbox. The routine key has no sudo, Docker group,
root daemon, forwarding or production mount access.

Every private operation verifies GitHub's RSA signature against fixed-issuer
HTTPS JWKS, nonce-bound audience, expiry, unique token ID, immutable repository/
owner IDs, main branch, GitHub-hosted runner, environment and workflow reference.
The actual workflow bytes at the signed source commit must match the enrolled
SHA256. Both legacy and immutable GitHub subject formats require matching IDs.
Activation additionally verifies the actual owner review receipt and current
review protection. Caller-supplied `sourceReviewed` or a workflow input cannot
activate the policy or enlarge its privileges.

## Activation and the first startup

Enrollment is inactive. Activation requires the original first-Gateway run to
have succeeded with the approved source and an owner review receipt. The Host
must retain its existing approved candidate container/image and staging limits.
An absent container cannot be bootstrapped by routine automation. After the first
successful startup, the legacy `staging` environment must be owner-protected;
activation and routine admission fail if that protection is absent.

The initial pending run uses immutable older workflow code and its existing
environment. Do not add a second review to that run or silently approve/replace
it. Configure legacy environment protection **after it completes**, before new
policy activation. The separate activation workflow first checks enrollment and
bootstrap completion, then waits for `deployment-security` owner review.

Exact one-time GitHub activation action, once these prerequisites pass:
**Actions → Owner activation of staging automation → Run workflow → main →
activate → Review deployments → deployment-security → Approve and deploy**.
Codex can dispatch the activation run and provide its exact URL when ready.
Future routine runs use `Policy-bound staging automation`, with no owner reviews.

There is a real infrastructure prerequisite: the existing restricted key cannot
install a new root-owned forced command or enroll another public key. An owner
must run the checksum-verified `enroll_staging_automation.py` command once in the
existing trusted VPS root session. This additive installer registers only the new
limited identity/controller; it does not touch the old entry, worker, containers,
configuration, credentials, dependency installation or production. A GitHub
review alone cannot supply missing root permissions. The exact immutable command,
public key and checksum are generated with release evidence; no placeholders or
private credentials are needed in chat.

## Reviewed limits and recovery

- One Host operation at a time, shared with the existing worker lock.
- At most 12 mutations/hour and 3 prepare/update builds/hour.
- At most 2 restarts/recoveries/hour, 120-second cooldown; failed restart opens a
  one-hour circuit. Failures consume the allowance. Clock regression fails closed.
- Restarts use only the existing recorded approved image/container. They never
  create a missing container, change image, restore a database or run bootstrap.
- Runtime must retain user 10001, read-only root, dropped capabilities, isolated
  staging network/mounts, no auto-restart, at most 512 MiB, 1 CPU and 128 PIDs.
  Drift requires owner review.
- Published patches stay in Event Manager 0.1.x, use the approved repository,
  SDK/base and API, and cannot add capabilities. GitHub tag source, publisher
  checksum and wheel bytes are verified independently by the existing adapter.
  New series, publishers, capabilities and destructive migration modes are denied.
- Installation uses the public Host lifecycle and a consistent staging SQLite
  backup. Updates reuse `SkillUpdateController`: fresh isolated probe, READY ID,
  freshness/generation checks, backup, stop-before-start and health gates. Its
  existing rollback permits image rollback only when application data is unchanged;
  `RECOVERY_REQUIRED` freezes further automated mutations. No automatic DB restore
  or backup/data/channel deletion is introduced.
- Audit IDs, operation outcomes and deployment history are retained. JWTs, private
  exceptions, response bodies, credentials and database records are never logged.

The default branch is `develop`. The schedule therefore dispatches the fixed
`main` workflow with only `reconcile`; it has no environment, SSH key or OIDC
permission. Routine jobs carry only the new scoped key and short-lived identity.
Existing deployment gates remain disabled during preparation; the new enrollment
flag remains false until the actual VPS returns a matching scoped status receipt.

## Revocation and remaining acceptance

Owner-reviewed `deactivate` stops automatic mutations while leaving the existing
bot intact. Set the enrollment flag false and remove the new encrypted key to stop
workflow access. The existing protected `revoke` operation remains the emergency
deny marker and staging-only shutdown route, independent of automation rate limits.
Removing an SSH identity or changing the sandbox is an explicit owner operation.

CI uses generated signing keys and a disposable rootless VPS fixture; it never
starts a real Discord Gateway. Passing those tests is not actual VPS enrollment,
initial startup, automation activation or live Event Manager/Web acceptance.
