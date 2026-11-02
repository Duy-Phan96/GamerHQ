# Verified one-time staging bootstrap

The operator command is generated from an exact public Git commit, the SHA-256
of that commit's installer bytes, and the actual public half of the dedicated
Actions key. It downloads into an automatically created private temporary
directory, rejects redirects/oversized responses/checksum mismatch, then executes.
It requires no pre-copied script, public-key file or guessed remote path. Do not
use the obsolete `/APPROVED-TEMP` command from the earlier handoff.

Use the **complete generated operator command from the accepted handoff** in the
existing root SSH session. Do not replace its commit/checksum/key with examples.
`tools/bootstrap_command.py` produces the command and CI executes that same
verified-download path twice to test both initial setup and retry. No moving
branch, curl pipe, production credential or GitHub access token is involved.

## What the one-time root operation does

- Checks Linux/systemd/cgroup-v2, Python 3.12+, rootless Docker helpers and SSH
  environment policy. AlmaLinux/RHEL 9 and Debian/Ubuntu use the same capability
  checks: an actual temporary venv with pip/SSL/SQLite, a user D-Bus socket with
  dbus-broker or dbus-daemon, user namespaces and existing Docker/helper binaries.
  System Python is unchanged; a Python 3.9 launcher re-executes the verified
  installer with the separately installed working Python 3.12+ interpreter.
  On Debian/Ubuntu only, with the generated command, missing prerequisites may be
  installed only from already configured signed APT repositories after a dry run
  proves there are no upgrades/removals or rootful Docker/SSH replacements. It
  does not add package repositories, relax AppArmor/kernel/SSH policy or disable
  production Docker. Unsupported hosts stop with the exact prerequisite blocker.
  RHEL packages are never automatically installed or upgraded. Missing capabilities
  report RHEL hints (shadow-utils, dbus-broker, working Python venv); Debian-only
  package names are not required on AlmaLinux.
- Clones the public Host at the exact commit, verifies its installer matches the
  downloaded bytes, and creates a root-owned, read-only deployment worker/venv.
  Public pinned dependencies use a clean environment, without production Git/pip
  credentials. The bootstrap's root-private state records resumable phases; the
  same command retries known partial setup, while unknown/legacy paths are refused.
- Creates only the new `gamerhq-staging-bridge` account, with an impossible password
  hash and no supplementary admin/Docker groups or sudo rule. Its root-owned SSH
  key file and forced-command entry point accept only bounded Host operations.
- Adds and validates a **per-account** SSH restriction: public key only, no shell,
  user rc, PTY, forwarding or tunnels. The existing SSH service is reloaded to apply
  that restriction, preserving current sessions. Global/root login rules stay intact.
- Enables only the new account's user manager/lingering and a separate rootless
  Docker service. Production Docker is never stopped, disabled or reconfigured.
  A UID-specific systemd sandbox hides `/opt/gamerhq`, `/etc/gamerhq`, `/root`,
  `/var/lib/docker` and the production Docker socket from deployment jobs/daemon.
  Before starting its daemon, setup verifies the new user's real D-Bus socket,
  systemd user session and an actual RootlessKit user namespace. It verifies the
  daemon's cgroup-v2 delegation, storage driver and staging-only storage location.
  SELinux state is checked before and after startup and recorded in the root-private
  capability receipt. No SELinux policy, booleans, permissive domains, global labels,
  sysctls or system-default Python are changed. If enforcing policy blocks startup,
  setup fails; an owner-reviewed staging-only policy adjustment would be required.
- Creates staging-only data/backup/artifact/work directories and private settings.
  ACLs share only the new staging SQLite/data paths between the Host worker and
  mapped container UID 10001; no production data or secrets are read/copied.
- Generates a new private staging API credential, leaves the Discord token blank
  and worker disabled, and enables a five-minute **read-only** watchdog. It creates
  no staging database, Event Manager installation or Discord Gateway connection.

CI exercises real AlmaLinux 9 system Python 3.9, separate Python 3.12 venv/pip and
D-Bus provider detection in a disposable container; Ubuntu CI additionally exercises
the full rootless Docker/systemd/restricted SSH lifecycle. A container CI job cannot
prove AlmaLinux user-systemd or enforcing SELinux behavior on the VPS. Only successful
execution there establishes bootstrap acceptance. The previously failed prerequisite
check ran before creating bootstrap state; rerun the new verified command. Existing
state belonging to a different commit/key is refused, never replaced or key-rotated.

| Actual installed resource | Location / identity |
| --- | --- |
| Reviewed immutable worker | `/opt/gamerhq-staging/deployment-worker` |
| Root-only resumable bootstrap state | `/var/lib/gamerhq-staging-bootstrap` |
| Private staging configuration | `/etc/gamerhq-staging/settings` |
| Immutable identity/isolation policy | `/etc/gamerhq-staging/policy.json` |
| Staging data/backups/artifacts/work | `/opt/gamerhq-staging/{data,backups,artifacts,worker}` |
| Dedicated home/rootless image storage | `/var/lib/gamerhq-staging-bridge` |
| Rootless socket | `/run/user/<allocated deployment UID>/docker.sock`, derived and recorded by setup |
| Compose container/network | `gamerhq-staging-bot` / `gamerhq-staging-private`, only in that daemon |
| Host API listener | `127.0.0.1:18080`, authenticated |

The socket UID is discovered, not an operator placeholder. The generated command
contains no remote file path that must exist beforehand. Container/network creation
waits for an approved immutable candidate; it cannot safely fabricate an image,
release, Discord application or guild.

## One-time inputs that cannot be invented

The repository's protected `staging` environment must require owner review, deny
admin bypass and allow only `main`. `GAMERHQ_STAGING_PREPARATION_ENABLED` and
`GAMERHQ_STAGING_UPDATES_ENABLED` are **repository variables**, initially `false`.
The dedicated private key belongs only in environment secret
`GAMERHQ_STAGING_SSH_KEY`; never send it to chat or put it in source/on the VPS.
The actual handoff records which of these settings were configured through the
available GitHub administration permission.

1. Run the verified operator command once. Its public receipt reports the observed
   SSH address/port and actual public host key. Confirm public reachability (NAT or
   an SSH proxy can change the externally reachable address/port) and host-key
   authenticity through the existing trusted root session. Supply that **non-secret**
   receipt to the agent, or set environment variables `GAMERHQ_STAGING_SSH_HOST`,
   `GAMERHQ_STAGING_SSH_PORT`, `GAMERHQ_STAGING_SSH_KNOWN_HOSTS` in GitHub. No host-key
   auto-trust, `ssh-keyscan` or `accept-new` is used.
2. Add environment secret `GAMERHQ_STAGING_CONFIGURATION`, following
   [the configuration schema](configuration.schema.json). It needs a **new** staging
   Discord application/token, dedicated guild/categories, public production IDs for
   exclusion checks, read-only access to approved private GitHub Releases, and exact
   reviewed SDK/Skill release commit/version/checksum descriptors. Never paste the
   packet in chat, reuse the production token or copy production `.env`/SQLite.
   The staging API secret is already generated server-side; it is not a manual input.
3. In **Protected staging configuration and access**, choose `initial`, approve the
   environment review, and enter `CONFIGURE ISOLATED STAGING`. Credentials travel
   on encrypted SSH **stdin**, never remote-command arguments or audit output.
   Initial configuration refuses an existing staging database/container.

These are one-time owner identity/trust/credential actions, not routine VPS edits.
If a required publisher release does not exist, publish/review it in its owning
repository first; do not substitute a moving branch or unverified local wheel.

## Host-owned operations after enrollment

After restricted reachability/health is verified, enable the preparation repository
variable. All following operations are reviewed GitHub workflows using the installed
Host worker; no manual VPS command is needed:

1. Preparation `build-base`, approval `BUILD ISOLATED STAGING BASE`: build exact
   installed Host source inside the rootless daemon and record its immutable image
   automatically. This never changes the production image or starts a bot.
2. `initialize`, approval `INITIALIZE ISOLATED STAGING`: create new staging SQLite
   exclusively. Then `fetch`, `verify-offline`, `build` verify exact GitHub release
   provenance/checksums/SDK compatibility and probe the isolated candidate image.
3. Configuration `authorize-live`, approval `AUTHORIZE ISOLATED STAGING`: freshly
   verify the actual new bot identity, exclusive staging guild membership, category
   permissions, private release access and approved bytes, then enable the staging
   worker and record a fresh live proof. Offline proof cannot authorize live use.
4. Preparation `bootstrap`, approval `BOOTSTRAP ISOLATED STAGING`: **first staging
   Gateway connection**, only after explicit approval; refuses an existing container
   and waits for its Gateway heartbeat and Host/package health. `install`, approval
   `INSTALL IN ISOLATED STAGING`, uses the public Skill lifecycle. Configure/enable
   the Skill and perform live Discord/Web and migration/rollback acceptance.
5. Enable the separate staging-updates repository variable only after acceptance.

For each future Skill release, use configuration `release` with the exact approved
`{"skillRelease": ...}` descriptor and `APPROVE STAGING RELEASE`; then preparation
`fetch`, `verify`, followed by the existing worker `stage`, inspect READY/history,
then explicitly `apply` that deployment ID. Version is read from the running Host;
paths/publishers/capabilities cannot be supplied as arbitrary SSH arguments. Every
release still needs source/trust review; this is not unattended production deployment.
New descriptors invalidate old verification evidence. Renew private release access by updating the
GitHub configuration secret and choosing `credentials`, approval
`ROTATE STAGING ARTIFACT ACCESS`; it updates only artifact access and never the
bot token, API secret or dataset. No SSH credential-file edit is needed. Health validation, backups,
deployment history and guarded image rollback use the existing Host controller.

## Health, failure handling and revocation

The watchdog checks rootless engine identity/storage, enforced cgroup-v2 limits,
production path masking and (after live enablement) Gateway heartbeat plus authenticated
Host/Skill health. It never repairs, creates resources or connects a second bot.
Before live authorization it reports `READY_OFFLINE`. During initial authorized
bootstrap, health may remain failed until the first Gateway/Host is ready.

Actions record safe operation results. Audit records in the staging user/system
journal carry only operation, status, random audit ID and UID, never payloads,
tokens or subprocess stderr. Root-owned worker sources and systemd sandbox policy
remain separate from writable staging state. Worker locks serialize all remote operations.
Interrupted private record writes use unique atomic temporary files; existing data
is retained. Unchanged-data image rollback and `RECOVERY_REQUIRED` refusal after
application writes remain authoritative; there is no automatic DB replacement.

Configuration `revoke`, approval `REVOKE STAGING ACCESS`, writes a durable deny marker
before attempting to stop the named staging container and **only its user Docker
service**. It works even when Docker RPC is unhealthy. Every subsequent forced
command is denied. Disable both repository gates and delete the GitHub SSH secret
as well. If service shutdown itself fails, the deny marker remains and an owner
must perform emergency recovery; no production service is targeted. Re-enrollment
or removal of the root-owned SSH key is an explicit owner security operation,
not an automatic recovery or normal Skill-update step.

## Separate production boundary

No production key, token, database, container, package pin or deployment gate is
provisioned or enabled here. Production requires its own exact image/deployment-ID,
backup, authority cutover and recovery authorization after successful isolated
acceptance. This bootstrap does not grant that permission.
