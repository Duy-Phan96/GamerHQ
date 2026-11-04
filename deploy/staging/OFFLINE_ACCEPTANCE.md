# Actual VPS offline acceptance — 2026-11-04

The owner installed reviewed worker source
`2cb9adc79d42b95bf07ebd81600550be358cef11` using the checksum-verified
source-revision installer. The receipt reports no changes to production, SSH
policy, credentials, dependencies, database or isolation policy. The worker
remains disabled. No bootstrap, configuration or database initialization was
repeated for this acceptance.

Actual AlmaLinux 9.8 rootless VPS operations, through restricted GitHub Actions SSH:

| Operation | Successful run |
| --- | --- |
| Read-only health (`READY_OFFLINE`) | [41812282540](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41812282540) |
| Inspection: writable private proof parents, mapped database ACLs | [41812403722](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41812403722) |
| Pinned Host base build | [41812543856](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41812543856) |
| Published SDK and private Skill release verification | [41812644861](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41812644861) |
| Offline storage/compatibility verification and persisted proof | [41812832339](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41812832339) |
| Candidate image build and isolated offline package probe | [41812921696](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41812921696) |

Base image digest:
`sha256:c455f5ac17877f17afbc4c9d6c146a5871b9b660546a2d4420b4342ac48a28f4`.
Build admission verifies the clean installed source against the policy-pinned
commit. Release admission verifies GitHub provenance, immutable source, wheel
SHA256, SDK/API capabilities and migration compatibility. The image probe has no
network, credentials or Discord Gateway. SELinux observation was `Unavailable`;
this is not evidence that SELinux was disabled or changed.

Verified releases:

| Package | Source commit | Wheel SHA256 |
| --- | --- | --- |
| SDK 0.3.0 | `2e6a0ea40c40447fd3bae1a3b14fb845d6675b77` | `1a2136365f7a75449871423d4e57c91a421c4684dafbe067e900d11b60354399` |
| Event Manager 0.1.0 | `fad8fe3093411b71e3597334a0da88acf34f236d` | `7618bc72706c58b81e868365bb8f80790cee67f90b403ef64bceb3b28fb00dd3` |

This accepts offline preparation only. It does not accept live Discord permissions,
Gateway startup, Web management against the live Host, production-data migration,
or authority cutover. The staging update gate remains false. Next, separately
authorize the protected `authorize-live` operation to check dedicated staging bot,
guild/category permissions and release access through read-only REST. That step
enables the staging worker but starts no Gateway and does not enable update gates.
First Gateway startup and production remain separate explicit boundaries.

## Live-verification continuation

The owner approved run [41813865296](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41813865296).
Its SSH connection authenticated, but `authorize-live` failed with
`REMOTE_WORKER_REJECTED`; the installed immutable worker suppresses the private
exception. The independent bounded `verify` run
[41838993630](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41838993630)
also failed with `PREPARATION_REJECTED`, audit
`126a7c3efdbb4a2c9c08fe2f26e73bc0`. These results do not establish a Discord
credential, permission or network cause, and do not accept live staging.

The main-only **Read-only staging live preflight** workflow diagnoses the existing
protected configuration on an ephemeral GitHub runner using Host descriptor,
HTTP, category-permission and artifact adapters from the exact reviewed worker
commit above. It reports fixed phases and HTTP status or missing permission names;
it never publishes response bodies, tokens, private artifacts or configuration.
It performs only Discord/GitHub reads, with private wheels in a temporary runner
directory. It needs no VPS source replacement or root access.

A runner preflight cannot attest VPS networking, persisted credential equality,
Docker isolation, worker enablement or its live authorization proof. Successful
runner checks must still be followed by successful protected `authorize-live`
on the actual VPS before asking for first Gateway startup. Both deployment gates
remain disabled throughout diagnosis.

### Discord HTTP transport repair

Runner diagnosis [41840684139](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41840684139)
reproduced HTTP 403 at `bot-identity` before guild or permission checks. The
installed adapter used Python's default user agent. An unauthenticated public
`/gateway` comparison returned 403 with that default and 200 with Discord's bot
user-agent format. Discord's [official reference](https://github.com/discord/discord-api-docs/blob/main/developers/reference.mdx#user-agent)
requires an identifying HTTP user agent and documents possible blocking without it.

The repair sets that header on existing GET-only, redirect-rejecting requests.
It preserves TLS/authentication policy. Worker failures now expose fixed Discord
HTTP categories and identity/category/release phases, correlated to the existing
audit journal; no body, token, URL or arbitrary exception is published. A live
authorization persists its candidate proof before enabling the worker, so a
proof-write failure cannot leave the worker enabled without proof.

The source-only installer accepts the currently installed proof repair and the
previous three reviewed revisions. Disposable CI verifies rollback, retries,
retained data/key/configuration, restricted SSH evidence and the complete offline
build/proof/candidate sequence. The installed root-owned worker cannot replace
its own trusted code. Applying this control-plane repair remains an explicit
owner security operation; it grants no new SSH, Docker, root or sandbox privileges.
Routine Skill package installation/updates and runner diagnostics need no root SSH.
