# Reviewed offline worker revision

The installed `2e6a0ea` worker points `DOCKER_CONFIG` at root-owned, read-only
`/etc/gamerhq-staging/docker-client`. This correctly protects trust configuration,
but Buildx defaults its mutable state to that same directory. The real installed
worker's source/daemon preflight passes; disposable rootless CI reproduces the
next command failing with `mkdir .../buildx: read-only file system` and verifies
the same build succeeds with dedicated writable state.

The permanent fix keeps Docker configuration read-only and sets `BUILDX_CONFIG`
to `/opt/gamerhq-staging/worker/buildx`, owned by the staging UID with mode 0700.
Only bootstrap or the reviewed worker revision creates it; health and inspection
remain read-only. No root Docker socket, SSH relaxation, production mount or
additional privilege is introduced.

## One authorized trusted-code installation

The forced-command identity cannot replace its root-owned code. Use the complete
SHA256-verified command generated from the reviewed immutable revision, in the
existing infrastructure-owner root session. This is a security/source revision,
not a routine Skill installation or update. Do not rerun the original bootstrap.

`tools/upgrade_staging_worker.py` supports only the known original, `1c8ec294` and `8390d4ae`
workers, while
offline. It verifies delivery against the exact public Git tree, unchanged
dependency/image definitions and a bounded source-only diff. It locks operations,
sets a maintenance deny marker, checks out the reviewed source, preserves root
ownership, updates the public source binding, requires rebuilding the selected
base against the new source, and verifies
the offline worker inside the existing user sandbox. Failure restores old source
and metadata. A durable root-owned transaction supports interrupted owner-session
recovery and idempotent retry. Existing access revocation is never cleared.

It does not reinstall dependencies, restart a service, change SSH/groups/sudo,
read credential files, initialize/replace a database or operate production.
The original bootstrap receipt and key remain valid historical evidence; the
separate revision receipt records the new trusted worker commit.

After success, Actions resumes `build-base`, then `verify-offline` and `build`.
Existing fetched releases and initialized staging data are preserved. Do not run
`initial`, `initialize` or the whole initial bundle again. Live authorization,
first Gateway activation and production remain separate owner decisions.

## Safe error contract

The CLI emits bounded versioned-protocol evidence: a fixed `stagingFailure` code,
fixed `step`, integer exit code and optional 32-character audit ID. The dispatcher
validates it and correlates STARTED/FAILED journal records with that ID. The SSH
client emits only these allowlisted fields plus operation/authentication facts.
Raw stderr/stdout, arbitrary detail fields, paths, tokens and payloads are never
retained in public error objects. Unknown errors remain generic. Buildx state,
storage, memory, registry, network and timeout failures have distinct safe codes.

CI provisions the original worker, reproduces the Buildx defect, verifies the
isolated state fix, tests source/metadata rollback and retry, checks retained
database/key/configuration bytes, builds through the installed fixed environment,
and verifies error/audit propagation over real forced-command SSH. This does not
replace actual VPS build/probe or live Discord acceptance.

## 2026-11-04 offline verification checkpoint

Actual VPS health, Host base build and fresh release verification passed:
[health](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41802318626),
[base build](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41802386742),
[release verification](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41802570528).
The accepted base image is
`sha256:f8f227d814714d819c27e8ca38357ad6936f5d5240e9551df745fccd6e27f02b`.
The original bootstrap and staging database initialization were not repeated.

[Offline verification](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41802768369)
failed with audit ID `214efaa10f154091879a01d08e7fbe44`. The `1c8ec294` worker
still collapsed this particular path to `PREPARATION_REJECTED`; this is a
confirmed reporting gap, not evidence that SELinux or a file ACL caused the
actual failure.

Verification now distinguishes daemon identity, base-image selection, Compose
validation, each mounted storage path and artifact validation. `inspect` returns
only bounded read-only storage modes/ownership/ACL facts and the observed SELinux
state; it reads no database contents or credential files itself. Its surrounding
dispatcher continues to load the private worker environment normally without
publishing it. No shell or journal-reading operation is added to SSH.

The storage probe uses the same private `:Z` labeling mode as approved Compose
mounts. [Docker's bind-mount guidance](https://docs.docker.com/engine/storage/bind-mounts/)
describes SELinux label handling. Labeling is scoped to staging data/backups;
no global SELinux policy is disabled or changed. This consistency correction
does not by itself establish the VPS failure's cause.

CI exercises source revision/rollback/retry from both known installed commits,
real rootless bind verification, a deliberately masked synthetic database ACL,
the specific error code over real forced-command SSH, and verification after
restoring only that synthetic fixture's ACL mask. Real VPS candidate-image and
offline verification acceptance remain pending the reviewed worker revision.
Staging updates and the Gateway remain disabled; production is unchanged.

## Verification proof persistence

The `8390d4ae` worker passed fresh health, inspection, pinned base rebuild and
release verification, but run
[41808402522](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41808402522)
again returned a generic error after guarded checks. Its configured verification
records still targeted the root-owned staging root. That directory is intentionally
read-only to the staging identity and absent from its writable systemd paths.
The following proof write was outside the earlier phase-specific handlers.

Both live and offline verification records now reside in the existing private
`/opt/gamerhq-staging/worker` state directory. Fresh profiles and reviewed source
upgrades select these exact paths; validation rejects legacy root-level paths.
The root directory, SSH and sandbox permissions remain unchanged. Existing data,
artifacts and historical root-level records are retained. Record creation errors
emit `STAGING_RECORD_PERSISTENCE_FAILED` at step `record` without private output.
Inspection also reports whether proof parents are writable.

The disposable rootless CI matrix reproduces rejected writes to the original
root-level path, then tests the complete bounded SSH sequence: build the pinned
Host base, verify offline and persist the proof, build the candidate image and
run the isolated package probe. It uses only public SDK/example wheels, keeps
the synthetic dataset byte-identical and starts no Gateway. Published private
release verification remains a separate VPS operation. This tests the actual
filesystem and service permissions instead of only unit verification in writable
temporary directories.
