# Host-owned staged Skill deployment

`python -m tools.skill_deployment_worker --profile <protected-profile> stage
--release <reviewed-release.json> --wheel <reviewed.whl> --current-version <version>
--manual-review` verifies reviewed bytes and policy, builds an immutable image
with the separately pinned SDK wheel, and executes a credential-free package
probe with no network, live mounts, Docker socket or Gateway. READY means isolated
package verification passed; it does not mean Discord staging acceptance passed.

The release JSON requires skillId, distribution, version, sha256, runtimeApi,
capabilities, sourceReviewed=true and migrationMode (`none` or
`backward-compatible`). Source review is an approval assertion supplied by the
trusted Host release process, never by a Skill or browser request. New capabilities
also require approval in the protected profile. Pins and beta policy remain enforced.

The operator-owned profile supplies database (existing accepted schema), artifactRoot,
baseImage (reviewed immutable digest), sdkWheel, sdkVersion, sdkSha256, composeFile,
service, container, workRoot, backupRoot, healthUrl, healthSecretEnv, environment,
approvedCapabilities (map of Skill ID to capabilities), and optional updatePolicy.
Use dedicated staging paths, database, category and Discord application. Keep the
profile, Compose files, artifact directory and worker installation writable only by
the deployment administrator. Never accept their contents from a portable Skill.
The reviewed base image must already exist in the local Docker daemon. BuildKit
receives an owned local alias, verified against the immutable image ID before and
after build; the output candidate and deployment record use immutable image IDs.

`apply --id <READY-id> --approval "APPROVE <READY-id>"` is a separate operation.
Production additionally requires the protected production approval environment.
Before mutation, the controller refuses READY records after any intervening
application or Host image change. Re-stage against the current image after such
a change. It serializes applications, stops the old Gateway, backs up SQLite,
starts the reviewed image, then checks both container and installed tenant health.
On failure it stops the candidate. Automatic image rollback is allowed only when
the complete application schema/data fingerprint remains unchanged. Otherwise it
records RECOVERY_REQUIRED, retains the backup/new data and leaves recovery for
review. It never restores a database automatically. Interrupted APPLYING records
remain visible and block another writer; inspect real container/database state
before manually resolving them. No claim of crash-atomic Docker/SQLite switching
is made.

GET `/api/v1/servers/{guild}/skills/{skill}/deployments` exposes the latest 100
bounded deployment results to authorized guild managers, without artifact paths,
private evidence, credentials or approval controls. Apply is never a Web or Skill API.

The opt-in `skill-deployment-worker.yml` workflow invokes a fixed, preinstalled
Host worker. Staging uses a GitHub-hosted client with a bounded SSH forced command,
the protected worker at `/opt/gamerhq-staging/deployment-worker`, and profile
`/etc/gamerhq/staging/profile.json`. Follow the
[consolidated owner setup](../../deploy/staging/OWNER_SETUP.md) for the approved
existing VPS, new dedicated identity, release trust and fresh verification gates.
No Docker-group membership or self-hosted deployment runner is required for staging.
`GAMERHQ_STAGING_UPDATES_ENABLED` is separate from preparation and production gates.

The independent production job remains disabled and restricted to main. Before
enabling `GAMERHQ_DEPLOYMENT_WORKER_ENABLED`, the owner must separately authorize
the dedicated `gamerhq-deployment` runner, worker `/opt/gamerhq/deployment-worker`
and `/etc/gamerhq/deployments/production.json`. Protect its environment with an
independent reviewer; never use a runner accepting untrusted PR jobs.
This implementation does not change runner, repository, credential or environment
settings and has not dispatched a deployment.

Current external gates: VPS SSH/protected configuration have not been delegated;
private GitHub Release access, published reviewed bytes and a dedicated staging
Discord identity are not configured. Private Skill CI exercises its actual wheel
against the public Host and Docker without a Gateway. This establishes offline
integration evidence, not the VPS's live readiness or artifact publisher trust.
Do not publish private wheel bytes in a public CI artifact to bypass that boundary.
Live staging must verify installation through normal Runtime lifecycle, six-table
backup/cutover, private permissions, one area/chat over multiple occurrences,
occupied voice retention, restart/retry and failed-update recovery before production.
