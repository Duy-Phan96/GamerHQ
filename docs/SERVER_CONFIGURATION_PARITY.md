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

## Games evidence and approval

**Manage Visible Games** lists active rows only. The repository seed and
the proposed expansion leave new rows inactive and non-selectable. The private
preview reports catalog, active and visible counts separately from role and
channel mappings and their Discord resolution. Use **Games → Game Library** to
review showing a verified existing title after approval. That confirmation
uses the existing role and channel setup and recovery paths.

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
