# Server Booster Experience

GamerHQ uses Discord's managed Server Booster / premium-subscriber role as the
source of truth.

GamerHQ must not create or maintain a duplicate booster role.

## V1 scope

Server Management exposes:

`/server manage → Server Boosters`

The first Booster V1 slice supports:

- current booster count from Discord's managed role;
- Booster Lounge status;
- explicit Review → Confirm setup;
- creation/adoption/repair of `💎・booster-lounge`;
- strict private access for:
  - current Discord Server Boosters;
  - GamerHQ staff;
  - the GamerHQ bot;
- a single pinned welcome/thank-you message;
- idempotent repeat setup;
- stale-state protection if role/category/channel identity changes.

The Booster Lounge is optional. Core `/server setup` does not create it.

## Permissions

The lounge uses a strict channel-specific allowlist:

- `@everyone`: cannot view/read/send;
- Discord Server Booster role: view/read/send;
- GamerHQ staff: view/read/send, with moderation access;
- GamerHQ bot: view/read/send/manage messages/embed links.

An existing exact-name channel may be adopted only through an explicit review.
Ambiguous duplicates or unexpected placement block automatic mutation.

## UX

Boosting should feel appreciated without making normal community participation
second-class.

The default pinned message explains that boosting is appreciated but never
required.

Future booster perks should prefer:

- cosmetic badges;
- profile flair;
- early previews;
- optional booster polls/events;
- community thank-you milestones.

Avoid making core LFG, games, events or normal progression pay-to-win.

## Future Progression integration

A future Progression / Achievements Skill may consume booster status as a host
signal and award a visible badge or milestone.

The source remains Discord's managed booster state. The progression system
must not create its own independent booster truth.

## Future notifications

A later slice may add bounded boost/unboost recognition using Discord member
events if the host has the required member intent and the event can be handled
without duplicate announcements.

Any notification flow must be idempotent and must not reveal private account
details.
