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

`apply --id <READY-id> --approval "APPROVE <READY-id>"` is a separate operation.
Production additionally requires the protected production approval environment.
The controller serializes applications, stops the old Gateway, backs up SQLite,
and refuses READY records after any intervening application or Host image change.
Re-stage against the current image after such a change. It then
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
Host worker, with no repository checkout on the deployment runner. Before enabling
`GAMERHQ_DEPLOYMENT_WORKER_ENABLED`, the owner must provision a dedicated runner
labelled `gamerhq-deployment`, install the reviewed worker at
`/opt/gamerhq/deployment-worker`, install protected profiles under
`/etc/gamerhq/deployments/{staging,production}.json`, and protect both GitHub
environments (production requires an independent reviewer). Production dispatch
is restricted to main. Do not enable this on a runner accepting untrusted PR jobs.
This implementation does not change runner, repository, credential or environment
settings and has not dispatched a deployment.

Current external gates: this workstation has no Docker executable; private Event
Manager artifact delivery to the public Host build is not yet approved/configured;
no staging Discord identity/profile or protected deployment runner was supplied.
Do not publish private wheel bytes in a public CI artifact to bypass that boundary.
Live staging must verify installation through normal Runtime lifecycle, six-table
backup/cutover, private permissions, one area/chat over multiple occurrences,
occupied voice retention, restart/retry and failed-update recovery before production.
