# Discord setup

1. Create a separate application/bot for development in the [Discord Developer Portal](https://discord.com/developers/applications). Keep its token private in `.env`; an OAuth client secret is not used by GamerHQ.
2. Enable **Server Members Intent** in the bot settings. The code uses default intents plus members and voice states; it does not enable privileged Presence or Message Content intents.
3. Install to your chosen server using the bot scope and application commands. See Discord's [bot authorization documentation](https://docs.discord.com/developers/topics/oauth2#bot-authorization-flow).
4. Grant the bot the permissions its implemented operations need: View Channels, Read Message History, Send Messages, Embed Links, Attach Files, Manage Messages (managed pins), Manage Channels, Manage Roles (role assignment/overwrites), Connect and Move Members (temporary voice handling). Keep its role above roles it manages. Review channel-specific denies; Administrator is not the installation recommendation. Streamer/community permissions do not justify granting unrelated moderation powers to normal users.
5. Set `DISCORD_TOKEN`, positive `GUILD_ID`, `CHOOSE_GAMES_CHANNEL_ID` and a separate `GAMERHQ_DB_PATH`. IDs are configuration, not example content; no live IDs are included in these docs.
6. Follow [local setup](SETUP.md). Starting `bot.py` is a real connection and command/resource reconciliation, not a dry run. Never run two copies against the same guild/database.
7. Run `/server health` and inspect missing resources. Owner `/server setup` is an incremental preview/confirmed repair workflow, requiring pre-existing START HERE and COMMUNITY; prepare the base channels and private STAFF category described in SETUP.md.
8. Verify that each support-gamerhq bullet opens its MARKETPLACE channel. If a destination is missing, inspect health and run owner setup repair; do not manually paste raw IDs.
9. Verify role assignment, public/private LFG, voice ownership, suggestions and two-user ticket isolation using the [release checklist](../RELEASE_CHECKLIST.md). Check that ordinary users cannot see other tickets or Staff logs.

Slash-command permissions restrict who can operate maintenance commands; they are separate from the bot's own Discord permissions. Missing hierarchy/permissions should be corrected explicitly instead of granting everyone broad access.

For `/server pinned-messages`, verify an owner/admin can select and preview a managed pin, while ordinary members and moderators without Administrator cannot edit it. Access is rechecked on components, modal submission and save. The editor only lists registered, bot-authored public boards with matching IDs/fingerprints and an existing pin; arbitrary pins and private tickets are excluded. See [managed message editing](MANAGED_MESSAGES.md).

## Partner feeds and bot groups

Owner Repair preserves News, Deals, Amazon, AI Tools and Germany-only Electricity, inserting **🎁・free-games** directly below Deals. Each public feed is read-only for normal members. Free Games is for free/free-to-keep offers (DealGecko); Gaming Deals is for discounted/commercial offers (Instant Gaming); Gaming News remains separate. Existing channel history, IDs, pins, buttons and customized content are preserved.

The Free Games adjacency rule takes precedence over older adopted absolute positions. If health reports an adopted-position conflict after repair, review the resulting order and adopt the intended positions again; unrelated channels retain their relative order.

Instant Gaming retains its verified environment/stored user-ID mapping. DealGecko, Jockie Music and Pancake have central public defaults in `config.THIRD_PARTY_BOTS`; no environment changes are needed when these IDs are unset or `0`. Positive environment values explicitly override the defaults. Configuration keys:

- `INSTANT_GAMING_BOT_ID`
- `DEALGECKO_BOT_ID`
- `JOCKIE_MUSIC_BOT_ID`
- `PANCAKE_BOT_ID`

Missing optional bots are warnings, not blockers. GamerHQ never guesses bot identity from a name. The existing `bot_member:<guild>:instant-gaming` mapping remains a fallback when its environment ID is unset; both grouping and private access use that same identity. Repair tries the member cache, then fetches an uncached exact ID, caching results for 60 seconds. Health stays read-only and never fetches. Each assignment reports success or a concise warning; one failed assignment does not prevent the others.

`/server setup` → **Repair / Setup → Confirm Repair** creates/reuses **🎵 Music Bots** (Jockie/Pancake) and **🤖 Gaming Bots** (Instant Gaming/DealGecko), enables hoist and assigns verified bot members. Roles are moved only below Staff/Admin and GamerHQ's manageable ceiling; Staff's relative order is preserved. If there is insufficient space or an uneditable higher hoisted integration role, health reports owner review. Do not elevate groups above Staff or grant Administrator. Existing Music Bots channel allow-list and voice permissions remain in place.

Gaming Bots is a grouping role with no global permissions; it grants neither bot blanket private access. DealGecko gets explicit View, Send, Read History, Embed Links and Attach Files in Free Games and Gaming Deals. Gaming Bots also gets these rights specifically in Gaming Deals; the public category grants visibility without category-wide posting. Configure its external posting target manually; GamerHQ does not control its campaigns.

## Private Affiliate Stats

Repair creates/reuses **🔒 AFFILIATE STATS**, then moves/renames existing mapped `ig-purchases` and `ig-buyer-ranking` channels to **💸・purchases** and **🏆・buyer-ranking**. Their internal mapping keys and Discord IDs stay unchanged. It does not create replacements or move unrelated STAFF channels.

Category permissions deny @everyone and unauthorized identities visibility, allow authorized Staff to view/read, and grant the verified Instant Gaming member the five posting/read/embed/attachment rights. GamerHQ retains the access needed for managed pins and health. Matching children inherit the category policy; legacy child-specific restrictions remain explicit where necessary. Gaming Bots and DealGecko receive no shared private grants.

Check `/server health`, repeat Repair to verify idempotency, then inspect using an ordinary member. Re-open Instant Gaming configuration and select **Purchase Notification → purchases**, **Buyer Ranking → buyer-ranking**. Until privacy/access acceptance, leave these external features disabled. Existing server-level Administrator permissions bypass channel denies: remove unnecessary broad permissions manually instead of making the channels public.

Repository audit found no runtime/config dependency on Carl-bot. It is not in the intended bot stack. A cached name match produces only a health warning; no code kicks any bot. After checking any live manual automations not represented in this repository, remove it manually: **right-click Carl-bot → Kick Carl-bot → Confirm**.

See [partner migration](PARTNERS.md), [Instant Gaming](INSTANT_GAMING.md) and [managed pin editing](MANAGED_MESSAGES.md).

## Streamer Hub beta

Keep both streamer flags false initially. Owner repair preserves existing channels/data and hides managed legacy choose-streamers. In an enabled Dev guild, manually assign the safe 🎥 Streamer role; stream-updates is the fixed read-only public target, while guide/commands are restricted to approved Streamers/staff. Public profiles, follower roles and custom notices are inactive. See [beta guide](STREAMER_HUB.md).

## Marketplace upgrade

Restart reviewed code, run `/server health`, then `/server setup` → Repair. Existing category/channel IDs are reused for 🛒 MARKETPLACE and 🇩🇪・electricity. Verify the English Electricity pin and private ⚡ Compare Electricity Tariffs action; repeat Repair to confirm stable IDs. Customized legacy pins require owner editor review or Reset to Default. See [Marketplace migration](PARTNERS.md).
