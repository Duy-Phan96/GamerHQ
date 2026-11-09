# GamerHQ staging configuration parity

The Host's `SERVER_BLUEPRINT` in [server setup](../services/server_setup_service.py)
defines the shared logical categories, text channels, voice channels and privacy
defaults. [Server operations](../services/server_operations.py) adds the existing
profile and bot role definitions and uses the existing channel permission policies.
[Managed messages](../services/managed_message_service.py),
[member onboarding](MEMBER_ONBOARDING.md) and the [game system](GAME_SYSTEM.md)
retain their definitions and ownership. This is one customer configuration.
Each environment has its own database and Discord IDs.

The blueprint includes shared game and LFG entry points, community discussion,
announcements, support, staff, events and default voice destinations. Existing
production customizations require verification with the guild owner before
becoming shared defaults. Keep production Discord identifiers, custom member
assignments, channel history, credentials and private staff configuration out of
GitHub. Keep PatchBot and optional Skill installation outside this rollout.

## Collect the read-only evidence

1. In the production guild, use the existing owner-only **/server dev → Health**.
   Compare categories, roles and game configuration with the repository defaults
   in the private guild. Use the existing Member Onboarding and Skills views for
   configuration metadata. Record discrepancies using logical keys and aggregate
   counts. Keep the results and detailed names private. Use the default Health
   view without enabling message inspection.
2. On the separately promoted staging Host, open **/server dev → Configuration
   Preview** as the owner of the configured staging guild. The preview uses
   official Discord channel and role inventory reads with a read-only SQLite
   connection. The response is private. A denied inventory aborts the report
   so an unavailable read cannot be interpreted as missing resources.
3. Compare the reports. **Reuse** can mean that an existing resource needs
   linking. **Create** is a candidate for the existing setup flow. **Update**
   describes a difference for separate repair review. **Conflict** requires
   a manual identity, permission or destination decision. **Leave unchanged**
   preserves optional and runtime-owned resources. Message status here is saved
   link metadata. Verify message existence separately in the existing Managed
   Messages tools. Verify native Discord onboarding privately in Discord Server
   Settings. No messages, member listings or membership records are fetched by
   the configuration preview.
4. Submit the staging action list for owner review. First link approved existing
   resources with **Review Structure**. Then open a fresh **Add Missing
   Resources** or **Fix Common Issues** plan and confirm only the approved
   scope. Cancel if the built-in plan includes additional changes. Repeat the
   preview after each confirmed phase. Use Games recovery separately for
   ambiguous game resources and preserve their IDs and overwrites. Review
   Event Manager category bindings through the existing Host integration.
   Keep the installed Skill, its authority and all event resources in place.

## Owner-reported Production baseline, November 9, 2026

The owner ran the default, read-only **/server dev → Health** view in the
Production guild. The result was **103 passed, 20 repairable, 6 mappings need
linking, 21 warnings/review, and 0 critical**. These are diagnostic counts.
Only the findings below were supplied. The remaining entries have no
item-level evidence in this record.

| Logical resource | What the read establishes | Staging parity disposition |
| --- | --- | --- |
| `choose-your-games`, `looking-for-group`, `bot-commands` | Health found an existing resource without the expected managed link. | Prefer verifying and reusing the corresponding staging resource. Review identity, placement and access before linking. |
| `looking-for-group` runtime state | The observed name or parent differs from the expected managed layout. | Check for an intentional placement exception independently of the missing link. Preserve the existing channel and overwrites. |
| `gaming-chat` | Health could not resolve the expected channel. | Hold for review of alternate placement, legacy aliases, saved mapping, and owner intent. Exclude it from automatic creation. |
| `server-log` | The combined mapping, parent, privacy or bot-access check needs review. | Verify the isolated staging mapping and effective privacy. The result alone does not identify an offending overwrite. |
| `partners-benefits` | Managed category permissions differ from the default policy. | Review interactive exceptions and permission-synced children before proposing any staging parent change. |
| `ig-purchases`, `ig-buyer-ranking` | Effective private visibility, staff access or integration access needs review. | Verify each staging resource and expected integration role separately. Keep existing exceptions until their purpose is confirmed. |

Production is an observed baseline. Only owner-approved intended behavior
becomes a shared staging requirement. Keep existing Production resources,
overwrites and messages in place. Use this evidence without running Production
reconcile, repair or setup.

## Protected staging preview candidate

[Candidate PR #232](https://github.com/Duy-Phan96/GamerHQ/pull/232) is based on
the previously verified staging Host `29ecf575f3d865b0f32a9d82adf1b06d7969cdc9`.
Candidate `89709625d9ea0c74c2b731436d1d13c9dcb453c4` carries the reviewed preview and its catalog-reading inputs.
The candidate leaves the staging Web code, Event Manager code and release,
dependency locks, database schema and existing game seed unchanged.

The current installed promotion policy and workflow still bind the previous
Host and do not allow this service delta. Keep the existing protected
`host-promote` operation. The next release step is a separately reviewed,
single-target controller revision and matching `deployment-security` workflow
pin. Verify the exact candidate archive receipt from PR #232. Bind the promotion
to the current staging Host and the already installed Event Manager 0.1.2
artifact, and allow only the reviewed candidate paths. Preserve the existing
catalog-preview policy, SSH restrictions, runtime configuration and staging data.
Retain the existing backup, restore-probe, offline health, and rollback checks.
Owner enrollment of the new pin and approval of the protected deployment remain
separate from repository review. The existing workflow must not be dispatched
against the old pin for this candidate.

After protected promotion and health verification, open the private
Configuration Preview in the staging guild. Compare logical identities and
effective access against the table above. Keep link candidates, policy drift,
unresolved or deliberately omitted resources and intentional exceptions
separate. Review the resulting list before any staging structure confirmation.
The catalog import remains on hold until the live game mappings are verified
and separately approved.

## Games evidence and approval

**Manage Visible Games** lists active rows only. The repository seed and
the proposed expansion leave new rows inactive and non-selectable. The private
preview reports catalog, active and visible counts separately from role and
channel mappings and their Discord resolution. Use **Games → Game Library** to
review showing a verified existing title after approval. That confirmation
uses the existing role and channel setup and recovery paths. The existing
Choose Your Games refresh can use this guild's managed channel link when the
legacy environment override is unset.

The November 5 staging backup yielded 38 matches, 32 additions and one alias
update across the 70-title manifest. The current staging preview reruns that
same planner against the configured guild's database. Approve any catalog
writes separately. GTA V resolves to the existing GTA Online entry under the
reviewed manifest. The preview neither activates titles nor provisions game
channels.

The existing staging catalog workflow only inspects eligible backup copies.
It does not substitute for this live inventory. Production inventory remains
read-only and must use already authorized owner surfaces. The staging Host
promotion and later Discord changes require their separate existing approvals.
