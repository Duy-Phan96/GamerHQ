# Host-owned Skill release proposals

The scheduled `skill-release-proposals.yml` workflow detects new stable releases
only in repositories already referenced by the reviewed Host lock. It reads the
current version from the immutable source commit (or wheel pin), requires a
matching universal wheel plus one unambiguous SHA256SUMS entry, and prepares a
checksum-pinned requirement and machine-readable review record.

Discovery never downloads or executes release code. It opens a **draft Host PR**;
every proposal defaults to manual review. It neither merges nor deploys. Skills
receive no Host/VPS credential. The workflow uses its own bounded GitHub token
only for repository metadata, a new branch and a PR; it contains no production
secret, SSH, Docker or database command.

Offline tests use injected metadata and do not contact GitHub. Locally, a read-only
scan can run as:

```text
python -m tools.skill_release_updates --output /protected/skill-proposals.json
```

`--prepare-lock` edits only the current checkout's requirement lines for a review
PR. It does not install packages. Missing/inaccessible metadata, absent checksums,
ambiguous assets, unsupported versions or publisher mismatches are skipped with
bounded diagnostics. No fallback to moving source or arbitrary URLs is allowed.
Exact version pins without a locked source repository cannot be discovered this
way. Beta/pinned-policy update execution remains future orchestration work; the
existing pure policy evaluator can preflight those choices independently.

Before accepting a proposal, review publisher/source provenance, dependencies,
capabilities and migrations. Run non-executing artifact preflight, normal Host CI,
staged image/migration/restart/health checks and a verified rollback rehearsal.
Then follow the existing Host release procedure and obtain explicit approval for
production. A hash from a publisher proves the selected bytes, not code safety.

GitHub repository settings must permit Actions to create PRs before this workflow
can operate. This implementation does not change those settings. Under
[GitHub's documented token behavior](https://docs.github.com/en/actions/concepts/security/github_token),
PR events created using GITHUB_TOKEN can require approval before their CI runs;
approve those runs or run the existing Offline tests workflow for the proposed
branch before review. Failed PR creation remains a
visible workflow failure, with the immutable branch preserved for review/recovery.

The first reviewed Host contract PR is #173. This follow-up is a stacked PR into
its feature branch; release it only after the contract foundation is accepted.
No production deployment history or health-driven rollback is implemented here.
