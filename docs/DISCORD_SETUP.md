# Discord setup

1. Create a separate application/bot for development in the [Discord Developer Portal](https://discord.com/developers/applications). Keep its token private in `.env`; an OAuth client secret is not used by GamerHQ.
2. Enable **Server Members Intent** in the bot settings. The code uses default intents plus members and voice states; it does not enable privileged Presence or Message Content intents.
3. Install to your chosen server using the bot scope and application commands. See Discord's [bot authorization documentation](https://docs.discord.com/developers/topics/oauth2#bot-authorization-flow).
4. Grant the bot the permissions its implemented operations need: View Channels, Read Message History, Send Messages, Embed Links, Attach Files, Manage Messages (managed pins), Manage Channels, Manage Roles (role assignment/overwrites), Connect and Move Members (temporary voice handling). Keep its role above roles it manages. Review channel-specific denies; Administrator is not the installation recommendation. Streamer/community permissions do not justify granting unrelated moderation powers to normal users.
5. Set `DISCORD_TOKEN`, positive `GUILD_ID`, `CHOOSE_GAMES_CHANNEL_ID` and a separate `GAMERHQ_DB_PATH`. IDs are configuration, not example content; no live IDs are included in these docs.
6. Follow [local setup](SETUP.md). Starting `bot.py` is a real connection and command/resource reconciliation, not a dry run. Never run two copies against the same guild/database.
7. Run `/server health` and inspect missing resources. Owner `/server setup` is an incremental preview/confirmed repair workflow, requiring pre-existing START HERE and COMMUNITY; prepare the base channels and private STAFF category described in SETUP.md.
8. Verify role assignment, public/private LFG, voice ownership, suggestions and two-user ticket isolation using the [release checklist](../RELEASE_CHECKLIST.md). Check that ordinary users cannot see other tickets or Staff logs.

Slash-command permissions restrict who can operate maintenance commands; they are separate from the bot's own Discord permissions. Missing hierarchy/permissions should be corrected explicitly instead of granting everyone broad access.
