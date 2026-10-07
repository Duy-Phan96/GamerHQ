# Member Profile V1

Member Profile is a **read-only projection** of state GamerHQ already owns or
integrates with. It does not create a second profile database.

## Entry point

`/profile [member]`

Without a member argument, the command shows the caller's profile. A supplied
member shows that member's public GamerHQ profile.

## Authoritative sources

| Profile field | Source of truth |
| --- | --- |
| Age | managed GamerHQ age role |
| Gender | managed GamerHQ gender role |
| Platforms | managed GamerHQ platform roles |
| Games | Game Library role mapping + current Discord membership |
| Server Booster | Discord native Server Booster role |
| Member since | Discord member join timestamp |

Unknown roles, retired mappings, notification roles, staff roles and internal
administrative metadata are not rendered.

## Privacy

V1 only projects information that is already represented as visible member
roles or ordinary Discord member state. It does not expose moderation state,
internal IDs, hidden configuration or arbitrary onboarding answers.

Future onboarding questions must **not** automatically become profile fields.
Each new field needs an explicit profile-safe decision.

## Relationship to onboarding and game selection

Member Onboarding and Update Profile remain the write paths for personal role
state. Choose Your Games remains the write path for game membership.

Member Profile only reads those existing sources.

## Future Skill integration

Progression, achievements, event history and similar features are intentionally
out of scope for V1. A future Skill-facing profile contribution contract may be
added when a real external Skill requires it.

Until then, Skills must not import private profile implementation details or
create competing profile state.
