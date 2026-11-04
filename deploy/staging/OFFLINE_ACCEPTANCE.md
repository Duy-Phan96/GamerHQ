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
