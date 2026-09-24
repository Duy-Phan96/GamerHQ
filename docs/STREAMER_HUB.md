# Twitch Streamer Hub — hidden beta

The beta connects an approved Discord streamer to a verified Twitch account and
posts a fixed live notice in the persisted `stream-updates` channel. It is disabled
by default. No public profiles, choose-streamers directory, follower/ping roles,
custom message, target selector, analytics or other platforms are active.
Legacy profile/area data is retained. Existing area/channel/voice commands remain
staff-only, including when the Twitch beta is disabled. Legacy
profile creation/following is disabled; setup/profile entry points open the beta.

## Flags and authorization

```dotenv
STREAMER_HUB_ENABLED=false
STREAMER_ROLE_SELECTION_ENABLED=false
TWITCH_CLIENT_ID=
```

The first flag gates OAuth, controls, workers and public notifications server-side.
The second independently permits future Streamer self-selection in Optional
Settings. Leave both false in production. Existing platform/profile choices are
unchanged. No Twitch credentials are required when disabled; enabled startup
requires a client ID. Staff may manually assign the existing **🎥 Streamer** role.
Owner repair reuses/persists a safe role ID and refuses ambiguous or privileged
role candidates. The bot must be above this zero-permission role.

Every connection/confirmation and live post rechecks current Discord membership:
the configured guild, approved Streamer role or staff/owner access. An unauthorized
attempt receives: “This feature is currently available to approved GamerHQ
streamers.” Hidden controls are not the authorization boundary.

## OAuth and private storage

Use a Twitch Developer **Public** client with the official
[Device Code Grant](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow).
Discord is the input interface; GamerHQ has no public web UI/callback. This flow
lets an approved member authorize in a browser without adding a public endpoint.
Only `TWITCH_CLIENT_ID` is used; no client secret or redirect environment variable
is needed for this public-client flow. No Twitch scopes are requested: online and
offline EventSub subscriptions use a user token without additional permissions.

Click **🟣 Connect Twitch** in the managed guide. The private Discord response
contains Twitch's activation link. Never share it. GamerHQ polls only this short
OAuth transaction at Twitch's interval (minimum five seconds, slow-down honored,
maximum ten minutes). Once authorized, it validates client/account identity and
asks the same Discord member to confirm the displayed Twitch account. Confirmation
is single-use, expires after three minutes and is tied to the current random
attempt, guild and Discord user. Roles are checked again. Old/replaced attempts
and confirmations from other users fail. No callback is exposed, so browser
callback CSRF state is inapplicable; the device transaction and Discord-bound
confirmation provide the binding. A restart cancels uncompleted authorization.

`twitch_connections` stores guild/Discord/Twitch IDs, login/display name,
connection/update time, notification flag, live state, subscription IDs and the
access/refresh tokens required for EventSub recovery. It uses the existing private
SQLite volume, **not source files**. Tokens are not encrypted by this application;
protect the runtime directory and backups as secrets (existing VPS mode 700,
private .env mode 600). Never upload a runtime DB or log OAuth responses/links.
A Twitch account cannot be silently linked to another Discord account; uniqueness
constraints reject duplicates/replacement. Disconnect the prior connection first.

Token identity is validated on startup and at least every hour (55-minute interval).
Tokens refresh before expiry and rotated refresh tokens are persisted immediately.
Public-client refresh tokens are single-use and expire after 30 days of inactivity;
long downtime or revoked authorization may require reconnection. A crash between token rotation and its SQLite save also requires reconnection. Permanent auth
failure disables notifications and is reported by health. Connection recovery uses
[validation](https://dev.twitch.tv/docs/authentication/validate-tokens/) and
[refresh](https://dev.twitch.tv/docs/authentication/refresh-tokens/) endpoints.

## EventSub and delivery

[EventSub WebSockets](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/)
fit the existing outbound-only Docker service. Each connected user has a dedicated
socket and two subscriptions: `stream.online` and `stream.offline`. Subscription
IDs are persisted for inspection; desired subscriptions are reconstructed from
persisted connections after process restart. A Twitch reconnect URL is restricted
to its secure EventSub host; subscriptions transfer without being created again.
Keepalive loss/network failures reconnect with bounded exponential backoff.
No aggressive stream polling or new dependency is added (existing aiohttp is used).

A current stream lookup happens on online events and connection/reconnection
recovery. This compensates for Twitch's lack of missed-event replay while a
session is still live. Streams entirely within downtime are not replayed. Stale
online session IDs and offline timestamps are ignored. Offline updates only the
private state; existing Discord notices remain.

The fixed notice is:

```text
# 🔴 {streamer} is live!

**{title}**

🎮 {game}

[ 🟣 Watch on Twitch ]
```

Missing title/category lines are omitted. Mentions are disabled and text escaped.
The button URL is built from the verified Twitch login, never a user-entered URL.
No channel selection, custom templates or pings exist. The only target is
`managed_channel:<guild>:stream-updates`; absent mapping means no post.

`twitch_live_deliveries` has a unique Twitch user/session key, stores channel,
start time, status and Discord notification ID. An atomic claim is written before
sending. Concurrent events, restarts and disconnect/reconnect cannot send twice
for a claimed session. As with deal replies, this is **at most once**, not guaranteed
delivery: a crash/timeout after reservation may omit a notice. An uncertain claim
is retained, never automatically retried. Do not clear claims to force a replay.

**Disconnect Twitch → Confirm Disconnect** cancels pending OAuth/listeners, removes
the account and tokens locally and attempts Twitch token revocation. Closing the
socket removes its session subscriptions. Local disconnection remains effective
if remote revocation is unavailable. Delivery claims and old notices remain;
Discord roles and legacy profiles are not removed.

## Managed resources and rollout

Explicit `/server setup` → Repair applies the hidden/enabled policy. Startup does
not create streamer channels. When disabled, confidently managed guide/commands,
updates and choose-streamers are staff-only, and the guide has no Connect controls.
When enabled, stream-updates is public read-only; guide/commands are available to
approved Streamers and staff; choose-streamers remains staff-only/inactive.
Existing IDs/history/pins are preserved. No replacement choose-streamers is created.

Legacy guide/directory/command channels reuse their stored channel ID where available,
or are adopted through their stored bot-owned managed-message reference and known heading. The old generated updates
channel can be migrated within that verified guide's category if unambiguous and
not explicitly private. Unknown/ambiguous candidates are preserved and reported
for ownership review, never overwritten or duplicated. Unrelated category children
and custom channels are untouched. A managed guide message updates in place using
the existing pinned-message helper; repeat repair does not duplicate the pin.

Health is read-only: disabled beta yields one informational entry. Enabled checks
cover role, fixed channel mapping, client configuration, service initialization and
readable persistence; revoked connections warn. Health does not prove Twitch/Discord
live delivery. Live acceptance remains a separate Dev-server step.

## Future Dev-server acceptance (not production activation)

1. Register a separate Public Twitch Developer application; record its client ID
   only in the private Dev environment. If the registration console requires a
   redirect placeholder, it is unused by Device OAuth; no callback server is run.
2. Use a separate Discord bot/guild and SQLite volume. Set its existing `GUILD_ID`;
   set `STREAMER_HUB_ENABLED=true`, keep `STREAMER_ROLE_SELECTION_ENABLED=false`.
3. Restart the Dev bot; run `/server setup`, confirm Repair, then `/server health`.
   Resolve any ownership review before continuing. Manually assign 🎥 Streamer.
4. Connect through streamer-guide and confirm the correct account. An ordinary
   member and a second user's confirmation must be denied.
5. Go live: one stream-updates notice, correct title/category/button, no ping.
   Restart while live: no duplicate. Go offline: no new public post. Start a new
   session: one new notice. Disconnect: no subsequent notices.
6. Keep production flags false until a separate owner-approved release/activation.

The [VPS deployment](../DEPLOY.md) stays unchanged: one bot instance, persistent
SQLite volume, outbound HTTPS/WSS 443, no additional listening port or reverse proxy.
Offline tests mock Twitch/Discord; no live OAuth or stream delivery is claimed.
