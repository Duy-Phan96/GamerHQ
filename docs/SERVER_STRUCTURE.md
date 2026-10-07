# Server structure and resource identity

Read with [permissions](PERMISSIONS.md) for structural changes. The [README layout](../README.md#discord-server-structure) is the canonical human-readable layout; do not copy live Discord IDs into documentation.

## Permanent conventions

[server_setup_service.py](../services/server_setup_service.py) defines SERVER_BLUEPRINT. [server_operations.py](../services/server_operations.py) separates confirmed creation, ID reconciliation and known-resource repair using existing policies and mapping keys. The older combined repair helper remains internal compatibility code; slash commands use the [production operations workflow](PRODUCTION_OPERATIONS.md).

- START HERE: public onboarding, selectors and LFG/guide/support entries.
- COMMUNITY: general conversation, newbies, introductions, suggestions and bot commands. The optional `💎・booster-lounge` is created only through explicit Server Boosters management and uses Discord's native Server Booster role as its access source of truth.
- MARKETPLACE: public read-only boards in managed order: Gaming News, Gaming Deals, Free Games, Amazon, AI Tools, Electricity. The support-gamerhq overview links to useful Marketplace offers; the former direct-support channel is retained for separate owner review.
- STAFF: private staff conversation, suggestion inbox, ticket/bot/mod logs, `📜・server-log` operational notices and commands. Server Log uses the existing managed-channel key; only reviewed setup creates it.
- AFFILIATE STATS: private purchases/buyer-ranking using existing IG channel IDs and explicit Instant Gaming access.
- SUPPORT TICKETS: private ticket channels created on demand.
- EVENTS: 🎉・community-events → 🏆・tournaments → 🎁・giveaways. Reconciliation links existing Community Events; setup creates it only if genuinely missing; repair fixes known permissions/pins. Existing specialized layout sync retains its ordering policy. Full tournament/giveaway engines are not implemented.
- VOICE CHANNELS: common voice rooms and a generator. STREAMERS has its separate feature lifecycle.
- 🎮 GAMING: one shared category, optional role-gated text channels sorted alphabetically. No new per-game categories/LFG/create-voice channels.
- STAFF also contains private 🎮・games-log for threshold candidates and game administration.

Text channels commonly use emoji + ・ + kebab-case; category labels commonly use emoji + uppercase words. Voice names vary by feature. Reuse each service's normalizer/aliases instead of imposing a new global naming rule. Existing STAFF aliases are in cogs/suggestions.py.

## Configurable and dynamic resources

| Resource | Identity / owner |
| --- | --- |
| Guild and legacy selector/intro channels | config.py and environment bootstrap; see .env.example |
| External bot identities | bot_member:<guild>:<name> settings via Setup/Manage; legacy/environment fallback |
| Core channels/categories | settings keys managed_channel:<guild>:<name> and managed_category:<guild>:<name>, plus existing onboarding/legacy keys |
| Base profile roles | services/role_service.py; managed_roles stores kind/key/group and role ID |
| Server Booster lounge | services/server_booster_service.py; Discord native premium subscriber role owns membership, GamerHQ stores only the managed lounge channel ID |
| Game roles and optional channels | games.role_id / games.channel_id; legacy fields are migration hints; [Game system](GAME_SYSTEM.md) |
| Temporary game/common voice | temp_voice_channels; cogs/voice.py and temp_voice_service.py |
| LFG private text/voice/cards | lfg_events and related tables; [LFG](LFG_EVENTS.md) |
| Streamer Hub (hidden beta), retained legacy areas | managed channel/role settings; twitch_connections/twitch_live_deliveries; legacy streamer_profiles/streamer_channels retained. [Beta policy](STREAMER_HUB.md). |
| Tickets and suggestions | support_tickets/ticket_audit and suggestions; feature services |
| Fixed messages | existing feature setting keys; [Managed messages](MANAGED_MESSAGES.md) |

## Repair contract

### Explicit channel adoption

Desired state is GamerHQ's configured defaults plus persisted overrides; actual state is
Discord. `/server health` compares only. Explicit `/server sync-support`,
`/server instant-gaming` apply desired state within their existing scopes. `/server repair` fixes linked resources; `/server reconcile` only links IDs. There is no standalone `/server sync` command.

`/server adopt channel:<channel> aspect:<name|category|position|all>` runs in the opposite
direction: Discord → GamerHQ, after preview and **Confirm Adoption**. `all` is the
default. It does not edit Discord, messages, permissions or channel IDs. Cancel or
expiry writes nothing. Owner or Administrator access is required and checked again
at confirmation; only the preview author may confirm. A changed Discord snapshot,
mapping or saved override invalidates the preview.

Initial eligibility is deliberately limited to recorded IDs for `support-gamerhq`,
`gaming-news`, `gaming-deals`, `free-games`, `amazon`, `ai-tools` and `electricity`. Names include
emoji/prefixes. Category adoption accepts existing public START HERE, COMMUNITY or
MARKETPLACE only. Position means zero-based order among text channels in the
category, not Discord's raw position number. To adopt both a moved parent and position,
use `all` or adopt the category first. Name-only adoption leaves other desired fields
unchanged. Unknown channels, private feeds, roles and arbitrary overwrites are not
imported. This command does not import permissions; the event approval workflow below
supports one explicitly managed public posting setting.

Overrides use JSON in the existing `settings` table under
`managed_channel_state:<guild>:<logical-name>`, bound to the existing channel ID.
No new schema or channel registry is introduced. Missing/replaced IDs or protected
adopted categories require review; overrides are not silently transferred to another
channel. Setup, startup, sync and health never capture Discord as desired state.
Explicitly adopted names, parents and positions override the defaults, including the
default partner order. The default support channel is `💜・support-gamerhq`; its pinned
message content is independent of the channel emoji.

### Detected changes and approval

The first event-driven scope is the seven public boards above plus `ig-purchases` and
`ig-buyer-ranking`. Only their recorded managed IDs are monitored. Other core boards,
roles, voice channels, game areas, LFG and events are outside this first release.
`on_guild_channel_update` and `on_guild_channel_delete` detect name, parent, order,
managed-overwrite and deletion drift. Creation is not an import trigger. Discord events
do not identify the actor; changes by other integrations are treated as unapproved drift.

GamerHQ marks expected mutations before editing: per guild/channel/field, exact expected
value, consumed on a matching event, with a 90-second lifetime. Bulk ordering and category
permission propagation are covered. Unexpected fields remain reviewable; a known unsafe
private/bot permission change is repaired even if it matches an expected marker.
Non-critical updates coalesce for four seconds. Privacy/bot-access repair bypasses debounce.

Low risk (layout) and medium risk (public managed permission drift) create one private
bot-log notification with **Apply to Setup / Revert Change / Ignore**. Each action rechecks
owner/admin authorization, record revision, current Discord state and desired configuration.
Apply reuses persisted layout overrides for the seven public boards; it can additionally
adopt only `@everyone Send Messages` there. Unknown overwrites are never imported.
Private feed layout/permission adoption remains unavailable; Revert/Ignore are supported.
If no desired position was previously configured, position drift can be adopted or ignored,
but Revert refuses to invent a former desired order. Ignore does not change desired state;
Health still reports drift.

High risk means private exposure (including non-staff role/member grants) or lost required
bot access. GamerHQ immediately reapplies the existing known-safe permission helper and
notifies staff without an unsafe-approval button. A failed repair is recorded as critical
and retried by maintenance. This requires Discord permissions to edit the channel; it
cannot undo Administrator bypass or role changes outside the monitored scope.

Deletion does not immediately recreate a channel. **Restore Channel** reuses desired
layout, permission helpers and managed-message refresh, saving the new ID. Discord history
cannot be recovered. Ambiguous creation failures block blind retries to prevent duplicates.
**Remove from Setup** records `managed_channel_removed:<guild>:<key>`, clears only relevant
active channel/message/adoption mappings and retires editor state; future setup/sync honors
the removal. It does not delete another Discord resource. Re-enabling such a removal is a
deliberate configuration-maintenance action, not automatic name discovery.

Latest change records use existing settings keys `managed_change:<guild>:<channel-id>`.
Records and buttons survive restart; unresolved approvals expire after seven days without
adopting/reverting. The short debounce buffer and expected markers are in memory; events
missed during downtime are not replayed, so Health remains the read-only reconciliation tool.
The existing private bot-log is reused; no log channel is created and there is no public
fallback. Missing/unsafe logs retain records for retry and produce a Health warning. Pending
notification delivery is retried every minute; deleted approval messages can be recovered.
Health also lists pending, ignored, expired, failed and auto-repaired changes without writes.

Reuse persisted IDs first where supported. A rename does not prove that a resource disappeared. Feature resolvers differ: community/IG prefer mappings, partner resolution rejects conflicting names/IDs, and older game inspection still uses names in its plan. Inspect the relevant resolver; there is no universal resolver or ownership registry.

Fallback adoption must be unambiguous and scoped to the feature. Missing mapped channels can be recreated only by a feature's authorized repair path; not every feature auto-recreates. Persist replacement IDs, avoid repurposing unknown/private resources, and preserve history. See feature tests for exact retry policy.

onboarding_service preserves migrated welcome/newbies identity. Community repair adds guide/suggestions and reuses core boards. Partner migration journals known old resources; legacy_finance_service allows carefully verified retirement only during explicit owner repair. Startup and health never invoke that deletion.

For new managed channels: extend the existing owning service and inventory, persist identity, define initial visibility at creation, hook authorized repair and read-only health, register its fixed message if needed, and test repeat execution/deletion/ambiguity. Do not create channels solely in a one-off command callback.


## Server Booster experience

The Server Booster experience is optional and is not part of automatic core
server setup.

Use:

`/server manage → Server Boosters`

GamerHQ never creates a duplicate booster role. Discord's native Server Booster
role is authoritative.

Confirmed setup creates or adopts `💎・booster-lounge` under COMMUNITY and
applies a private allowlist for:

- current Discord Server Boosters;
- staff roles;
- GamerHQ bot.

Unknown/custom grants are removed during confirmed repair so a private booster
channel cannot remain accidentally exposed.

If Discord's native booster role is unavailable, setup fails closed and no
replacement role is created.

Future Progression/Achievement Skills may consume booster status for cosmetic
badges or milestones, but must not become the source of truth for booster
membership.
