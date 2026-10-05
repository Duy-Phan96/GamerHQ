# Member Profile & Onboarding

GamerHQ treats member profile choices as application state backed by managed
Discord roles. Discord onboarding is an integration surface, not the source of
truth.

## V1 profile taxonomy

### About You

Optional:

- Male
- Female
- Non-binary / Diverse
- Prefer not to say

### Age

Optional:

- Under 18
- 18–20
- 21–22
- 23–24
- 25+

The former broad age bands are legacy values:

- 18–24
- 25–34
- 35+

GamerHQ must never guess a narrower replacement for an existing member.

Explicit profile repair therefore:

1. creates/adopts the current managed profile roles;
2. retires old GamerHQ role mappings to `legacy-profile`;
3. preserves the old Discord roles;
4. preserves existing member assignments;
5. lets members choose a current age band themselves through Update Profile.

## Onboarding questions

The canonical initial design is:

1. What's your age group?
   - optional
   - single answer
   - before join
2. How would you like to describe yourself?
   - optional
   - single answer
   - available in Channels & Roles rather than required before join
3. What games do you play?
   - optional
   - multiple answers
   - before join
   - bounded popular-game subset only
   - More Games points members toward GamerHQ's full game selector
4. What would you like to hear about?
   - optional
   - multiple answers
   - notification/content interests

Do not duplicate the complete game catalog into Discord onboarding. The full
game library remains owned by GamerHQ's Choose Your Games flow.

## Server Management

Current path:

`/server manage → Member Onboarding`

The first slice supports:

- current profile/onboarding status;
- preview of GamerHQ's desired questions;
- review/confirm profile-role repair;
- safe legacy mapping retirement.

The question preview is read-only. It does not currently modify Discord's native
Community Onboarding configuration.

## Discord sync boundary

Before adding Discord onboarding write support, verify the currently supported
Discord API and discord.py surface.

Do not scrape or automate the Discord desktop UI.

If native API support is insufficient for a desired field, show that limitation
clearly rather than creating a second hidden source of truth.

Future sync must include:

- Preview;
- stale-state detection;
- explicit confirmation;
- bounded answer counts;
- role/channel ownership validation;
- idempotent update;
- safe handling when Discord onboarding is disabled or unavailable.

## UX standard

Every persistent onboarding/profile object should answer these questions:

- Can the user edit it later?
- Can they cancel/back out?
- Is current state visible?
- Are limits visible before submit?
- Is destructive intent confirmed?
- Does an error explain the next action?
- Can the operation be repeated safely?
- Are existing memberships/data preserved?

Member onboarding should remain short and understandable. More advanced
personalization belongs in GamerHQ's post-join profile/game selectors.
