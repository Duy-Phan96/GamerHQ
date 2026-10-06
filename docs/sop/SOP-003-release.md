# SOP-003: Release

Use for promoting reviewed work toward a deployable release.

Canonical sources:
- ../../RELEASE_WORKFLOW.md
- ../../RELEASE_CHECKLIST.md
- ../development/RELEASES.md

## Procedure

1. Confirm the intended release scope on develop.
2. Confirm required PR/CI gates are green.
3. Review version changes across Host, SDK and external Skills independently.
4. Update CHANGELOG/release notes for user-visible, operational and compatibility changes.
5. Verify external Skill pins are immutable reviewed commits/releases.
6. Review database/config/environment changes and rollback implications.
7. Create/review the release candidate according to RELEASE_WORKFLOW.md.
8. Complete manual Discord acceptance from RELEASE_CHECKLIST.md where required.
9. Promote/tag only the accepted main commit.
10. Deploy separately through the owner-controlled deployment SOP.

Do not treat merge-to-develop, release/tag and production deployment as the same event.