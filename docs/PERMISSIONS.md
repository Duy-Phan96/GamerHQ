# Permission contracts

**Staff/private channels must never become visible to normal members through repair, sync, migration or fallback logic.**

There is no single permission engine. Reuse the helper belonging to the feature; do not replace every overwrite with a shared template.

| Area | Existing implementation and policy |
| --- | --- |
| Public boards | onboarding_service.guide_overwrites / set_read_only preserve relevant custom policy while restricting member posts/threads; Staff/GamerHQ can publish |
| Instant Gaming feeds | instant_gaming_service.overwrites: News/Deals public read-only; Purchases/Ranking explicitly private; internal Staff posting follows the category |
| Staff identification | onboarding_service.is_staff recognizes non-default, non-managed roles with administrator/manage_guild/manage_messages/moderate_members |
| Tickets | ticket_service.private and check_private: creator + authorized Staff + GamerHQ; closed tickets preserve history and lock creator posting |
| Suggestions | cogs/suggestions.py private_overwrites and existing STAFF aliases/inbox mapping |
| Game areas | game_service creates category/children gated by the game's role; @everyone denied visibility |
| LFG private events | cogs/lfg.py participant-specific text/voice access, reconciled after membership changes |
| Temporary voice | temp_voice_service resolves DB ownership, rechecks owner/Staff, preserves/restores connect overrides on lock/unlock |
| Music bots | music_bot_service allowlist and TEXT_RIGHTS/VOICE_RIGHTS; denies management rights and excludes protected/private areas |
| Streamer resources | cogs/streamer.py owns its follower/area/voice policies; do not treat them as common game rooms |

## Overwrite safety

An @everyone deny alone is insufficient if another role/member explicitly grants access. Check all relevant targets and effective permissions. Discord Administrator bypasses channel overwrites; channel denies do not neutralize that role.

Build private overwrites at creation time. Move channels without blindly syncing permissions from the destination; apply necessary privacy changes together with relocation. Preserve unrelated bits where the feature allows them. Read-only includes thread posting/creation where implemented, not just send_messages.

For Instant Gaming, environment INSTANT_GAMING_BOT_ID must resolve to an actual external bot member. Grant only that identity the five posting/read/embed/attachment rights; do not grant everyone a bot permission or invent a role by name. Other integrations have separate boundaries.

Host/creator status is not blanket administration. /server setup is owner-only; administrative commands and persistent UI recheck current authorization. Temporary voice owners use GamerHQ controls rather than receiving general channel-management power.

## Verification

Managed-change approvals never import arbitrary overwrites. On the seven public support/
partner boards, an explicit approval may persist only `@everyone Send Messages`; other
managed bits remain authoritative. Private IG exposure or loss of required bot access is
auto-repaired and privately logged, never offered as an unsafe adoption. Log delivery
requires an existing private bot-log and fails closed if normal members could read it.

MARKETPLACE uses the existing public read-only guide policy. Known information boards
retain category inheritance when their overwrites match; Instant Gaming feeds also need the
configured external bot's explicit posting grants. Unknown channels with independent
interactive overrides are untouched. If an unknown child is still permission-synced with a
category needing repair, category permissions are retained to avoid propagating changes to
that child. Known boards/feeds are repaired individually and Health reports the category
as REPAIRABLE; review that child's intended permissions before repairing the category.

Test ordinary member, Staff, owner/admin, configured bot and an unrelated explicit grant; test revocation and repeat repair. Include private/public relocation, bot replacement and unrelated override preservation when affected. Health diagnoses without writing. Offline overwrites are evidence of policy, not proof of actual Discord role hierarchy or external-bot configuration; use the [release checklist](../RELEASE_CHECKLIST.md) for live acceptance.


AFFILIATE STATS denies normal-member visibility and grants only Staff, GamerHQ management and the verified Instant Gaming identity access. Gaming Bots is a cosmetic group, never a private-access role. DealGecko has explicit Free Games posting rights only. Existing bot Administrator permissions cannot be neutralized by channel denies; health requests manual reduction.

## Read-only modes

The existing `onboarding_service.guide_overwrites` selects its mode by persisted managed ID first, then the channel alias. Both modes allow everyone to view/read history and deny normal messages, thread messages and public/private thread creation. Staff and GamerHQ retain publishing rights. Feature helpers retain integration-specific grants and adopted posting policy.

| Mode | Boards | Member interaction |
| --- | --- | --- |
| READ_ONLY_STATIC | rules, announcements, guide, gaming-news, free-games; Marketplace category default | Add Reactions denied; existing application-command and external-emoji policy retained |
| READ_ONLY_INTERACTIVE | welcome (Get Started), choose-your-games, choose-your-roles, suggestions, looking-for-group, support-gamerhq, need-support, gaming-deals, amazon, ai-tools, electricity, community-events | Add Reactions and Use Application Commands explicitly allowed; existing buttons/selects retained |

Tournaments and Giveaways retain their existing permissions/content. Pure automatic news/free-game feeds do not need new reactions; deal/offer boards have link buttons or request actions. Unknown boards are not newly enrolled in repair. Custom visibility and unrelated overwrite bits remain untouched; only contract bits are repaired. Explicit child overrides override a restrictive parent on interactive boards; category changes retain the existing guard for unknown permission-synced children.

Discord has no separate channel permission for buttons/select menus. Allowing application commands does not bypass command-specific authorization. Standard Unicode/server emojis need no additional external-emoji grant here. `USE_EXTERNAL_EMOJIS` is the permission for other servers’ custom emojis and is left unchanged. `ADD_REACTIONS` denial prevents adding a new reaction, not joining an existing one. See the [official Discord permission reference](https://docs.discord.com/developers/topics/permissions).

Health compares these managed permission bits without writing, reports Repair availability, and checks Community Events placement/order. Owner `/server setup` → Repair applies changes; offline checks do not replace live ordinary-member acceptance.
