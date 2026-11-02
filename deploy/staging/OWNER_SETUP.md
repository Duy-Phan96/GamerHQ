# One-time owner setup for isolated staging

Nothing in this procedure deploys production. It requires an owner-controlled VPS
administrator session; SSH access and credentials have not been delegated to Codex.
Do not execute it until the exact Host commit and installer have been reviewed.

## Consolidated infrastructure preparation

Use a new dedicated Ed25519 SSH key pair, kept outside the repositories. Retain the
private half in a protected GitHub `staging` environment secret, never send it to
Codex or put it on the VPS. Transfer only its public half and a reviewed copy of
`tools/prepare_staging_access.py` to an administrator-controlled temporary directory.
The approved Linux VPS needs Python 3.12+, venv/pip, Git, Docker Compose v2, OpenSSH
and sudo/visudo already installed. The installer deliberately does not install
system packages, alter SSH daemon policy, grant Docker group access or restart any
service. Its public dependency download needs outbound PyPI/GitHub access.

Run the same command first without `--apply` for a read-only preflight, then once
with the exact approval after reviewing that plan:

```sh
sudo python3 /APPROVED-TEMP/prepare_staging_access.py \
  --commit REVIEWED_40_CHARACTER_HOST_COMMIT \
  --public-key-file /APPROVED-TEMP/dedicated-staging.pub \
  --apply --approval 'PREPARE ISOLATED STAGING'
```

This is the single infrastructure setup command. It creates only the new staging
root/configuration, a root-owned worker/venv from the exact public Host commit and
the locked-password `gamerhq-staging-bridge` account. The account has no Docker
membership; a root-owned `authorized_keys` forced command accepts only bounded
staging operations. The sole sudo rule permits the fixed root dispatcher with no
arguments. No general shell, client-selected paths, environment or source refs are
accepted. The dispatcher clears inherited environment and executes existing Host
tools. It starts no container and creates no database. It never reads production
`.env` or copies production data. Existing paths/accounts cause refusal before
provisioning; after a partial failure, review the new staging paths locally rather
than deleting or overwriting them automatically.

Independently verify the VPS SSH host-key fingerprint through the existing trusted
administrator channel. Keep strict host-key checking; do not use automatic
`ssh-keyscan` trust or `accept-new`. If local SSH/PAM policy refuses the dedicated
account, have the administrator review the narrow account policy; do not weaken
global SSH settings or unlock password login. CI exercises this path on a fresh
Ubuntu runner; that does not establish the VPS's actual policy or reachability.

## Protected configuration packet

Complete this packet in the same owner-controlled setup session, using a protected
editor or secret manager. Do not paste values into chat, repository files or logs.

| Location | Required configuration |
| --- | --- |
| `/etc/gamerhq/staging/profile.json` | Reviewed immutable Host image, exact SDK hash, dedicated staging/production public IDs for exclusion, staging categories; keep `workerEnabled=false` |
| `/etc/gamerhq/staging/*release.json` | Approved publisher, exact tag/version/commit and independently reviewed wheel SHA-256; `sourceReviewed=true` only after review |
| `/etc/gamerhq/staging/worker.env` (0600) | Read-only private Skill release credential and a newly generated staging Web API secret |
| `/etc/gamerhq/staging/bot.env` (0600) | New staging Discord token, dedicated guild, the same new staging API secret; no production values |
| GitHub protected `staging` environment | Required reviewer, restrict allowed workflow branches to reviewed release refs/main; secret `GAMERHQ_STAGING_SSH_KEY` only |
| GitHub `staging` variables | `GAMERHQ_STAGING_SSH_HOST`, optional `GAMERHQ_STAGING_SSH_PORT`, independently pinned `GAMERHQ_STAGING_SSH_KNOWN_HOSTS` |

The installer records the local daemon ID and source commit, but leaves image,
artifact approvals and identities unconfigured. Protected descriptors are owner
policy; SSH callers cannot replace them. Candidate releases must first be published
from reviewed tags in their owning repositories. Private artifact access is not
assumed. Keep credentials limited to release contents in the required repository.

## Verification and explicit live boundary

After the packet and restricted SSH reachability are verified, authorize the
preparation workflow by setting `GAMERHQ_STAGING_PREPARATION_ENABLED=true`. Use
`inspect`, `build-base` (explicit build approval), review and record the resulting
immutable image, `initialize` (dedicated IDs and explicit empty-DB approval),
`fetch`, `verify-offline`, then `build`. These operations start no Gateway. Local
artifact verification attests reviewed bytes, not GitHub publication provenance;
`fetch` and live verification separately verify exact published tags/commits.

Do not enable the worker simply to build an image. Only after verifying the new
Discord identity belongs exclusively to the isolated guild and live verification
passes, obtain owner authorization to enable `workerEnabled`, re-run `verify`,
and explicitly approve `bootstrap`. This is the first operation that can connect
the staging bot. `install` then uses the public Host lifecycle, followed by explicit
configuration/enablement. Unconfigured templates and offline proof cannot cross
this boundary. Keep `GAMERHQ_STAGING_UPDATES_ENABLED` unset until this initial
acceptance is complete; subsequent stage/apply/history reuse the Host controller
and require a reviewed READY deployment ID before application.

Do not set `GAMERHQ_DEPLOYMENT_WORKER_ENABLED`, transfer production credentials,
restore production data, migrate production or clean up Discord channels during
this procedure. Separate production approval must identify the exact image,
deployment ID, backup, authority cutover and recovery plan after staging acceptance.
