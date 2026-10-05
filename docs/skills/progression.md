# Progression & Achievements Skill

## Purpose

The Progression Skill provides configurable XP, levels, achievements and rewards
without coupling portable Skill code to GamerHQ internals or discord.py.

V1 deliberately separates **progression rules** from **activity adapters**.

The Skill currently owns:

- level curve configuration;
- XP source configuration;
- achievement definitions;
- reward rules;
- deterministic level previews;
- namespaced Skill storage.

Voice, chat, LFG and event listeners are separate integration slices.

## Default level curve

The default XP required to advance from level `L` is:

```text
100 + 20L + 2L²
```

This keeps early levels fast enough to feel responsive while making high levels
meaningful long-term community milestones.

Examples:

| Current level | XP to next |
| ---: | ---: |
| 1 | 122 |
| 2 | 148 |
| 5 | 250 |
| 10 | 500 |
| 20 | 1,300 |
| 30 | 2,500 |
| 50 | 6,100 |

The curve and max level are server configuration rather than hard-coded product
policy.

## Default XP sources

Initial defaults are intentionally conservative and anti-farming oriented:

| Source | Default |
| --- | ---: |
| Voice | 5 XP / 10 active minutes |
| Voice daily cap | 180 XP |
| Chat | 3 XP / 5-minute active window |
| Chat daily cap | 120 XP |
| LFG participation | 25 XP |
| Event host | 40 XP |
| Community event | 50 XP |
| Tournament participation | 75 XP |
| Tournament win | 100 XP |

Voice defaults also require another human participant and exclude AFK behavior.
These rules are configuration only in the foundation slice; enforcement arrives
with the activity adapter.

## Achievements

Achievements are explicit definitions with stable IDs.

The default booster achievement is named exactly:

```text
💎 Server Booster
```

and awards 250 XP once.

Other initial definitions include First Game, First Mate, Event Regular,
Community Host and voice-time milestones.

## Rewards

Rewards are configuration-driven.

Supported rule triggers:

- reach a level;
- earn an achievement;
- reach a total XP milestone.

Supported grant types in the model:

- Discord role;
- profile badge;
- profile title;
- channel access;
- announcement;
- XP bonus.

A reward may contain multiple grants.

Example:

```text
Level 25
→ Veteran badge
→ Veteran role
→ profile title
→ channel access
```

The foundation validates these rules but does not execute Discord mutations yet.
Reward execution will use explicit host capabilities and managed-resource
ownership so the Skill never removes a role or access grant it did not own.

## Planned admin UX

The intended owner-facing flow is:

```text
/server manage
→ Skills
→ Progression & Achievements
→ Configure

Status
XP Sources
Level Curve
Achievements
Rewards
Leaderboards
Settings
```

Reward creation should use a guided flow:

```text
Trigger
→ Requirement
→ Reward Type(s)
→ Review
→ Confirm
```

Persistent or destructive changes must keep GamerHQ's existing
Review/Confirm and stale-state safety conventions.

## Portability

The Skill uses only public Skill Runtime contracts and namespaced Skill storage.
It does not import:

- GamerHQ database modules;
- GamerHQ services or cogs;
- discord.py;
- another Skill's private implementation.

This keeps the Skill suitable for later extraction into its own repository.


## Activity XP adapter V1

GamerHQ's host adapter samples voice presence once per minute. A member is
eligible only while:

- the channel is not Discord's AFK channel;
- at least two non-bot humans are present.

Eligible minutes accumulate in memory until the configured Voice window is
reached. The default 10-minute window then records one `voice` activity unit
through the versioned Progression management contract.

The portable Skill, not the Discord adapter, applies XP values, daily caps,
member totals and level calculation. A bot restart may discard an incomplete
voice window, but cannot duplicate completed XP windows.

The XP ledger is stored only inside the Progression Skill namespace. Member
state tracks total XP, per-source XP, daily cap accounting and activity metrics.
No GamerHQ core progression table is introduced.


## Announcements and web-ready configuration

Progression announcement rules are part of the same structured Skill
configuration used by the Discord management surface. They are not embedded in
Discord UI code.

Configuration includes:

- enabled / disabled;
- destination channel ID;
- level-up announcements;
- achievement announcements;
- reward announcements;
- editable message template.

Supported template values currently include:

`{member}`, `{member_id}`, `{level}`, `{xp}`, `{total_xp}`,
`{achievement_name}`, `{achievement_emoji}` and `{reward_name}`.

This is intentionally suitable for a future web control panel. A website can
render XP sources as checkboxes/toggles and numeric inputs, and can edit
achievements, rewards and announcements through the same versioned management
contracts used by Discord.

The product rule is:

```text
Progression configuration model
        ↑
Discord admin UI     Web admin UI
```

Neither UI becomes the source of truth.

## Reward execution V1

The portable Skill now executes reward grants that need only namespaced Skill
state:

- profile badge;
- profile title;
- XP bonus;
- announcement trigger.

Discord role and channel-access grants remain valid configuration types, but
are not marked claimed until the host exposes safe owned-grant capabilities.
This prevents a future host upgrade from losing rewards that were configured
before role/channel execution became available.

Member status reads are side-effect free and never emit announcements.
