# Discord message capabilities

Portable Skills do not receive the raw Discord client or bot token.

GamerHQ exposes a scoped Discord message adapter through `SkillContext.discord`.

## Current message capabilities

### `discord.messages.send`

Allows a Skill to create a message in a Discord text channel where GamerHQ has effective send access.

### `discord.embeds.send`

Required in addition to `discord.messages.send` when the Skill includes an embed.

### `discord.messages.edit_own`

Allows editing only messages recorded as owned by the current guild + Skill.

### `discord.messages.delete_own`

Allows deleting only messages recorded as owned by the current guild + Skill.

### `discord.mentions.everyone`

Required before a Skill may explicitly enable `@everyone` / `@here` parsing.

Ordinary mention parsing is disabled by default.

## Ownership model

When a Skill sends through the host adapter, GamerHQ stores only:

- guild ID
- Skill ID
- channel ID
- Discord message ID
- timestamps

The host does **not** duplicate message content into the ownership registry.

A Skill cannot edit/delete another Skill's message even though all messages are technically authored by the same installed Discord bot account.

This is important because one bot hosts multiple independently permissioned Skills.

## Safe send behavior

By default:

```text
everyone mentions: disabled
user mentions: disabled
role mentions: disabled
```

A Skill must explicitly supply allowlisted member/role IDs for ordinary mentions.

`@everyone/@here` additionally requires the dedicated capability.

## Embeds

The current host adapter accepts a bounded portable embed configuration:

- title
- description
- URL
- color
- footer
- thumbnail
- image
- fields

URLs reuse GamerHQ's existing safe public-URL validation.

Discord field/size limits are checked before sending where practical.

## Effective Discord permissions

Declaring a Skill capability does not override Discord permissions.

A send may proceed only when the GamerHQ bot itself has the required effective channel permissions.

For embeds, effective `Embed Links` access is also required.

## Recovery / manually deleted messages

If an owned message was manually deleted:

- delete-own becomes safely idempotent and removes stale ownership;
- edit-own reports that the message no longer exists and removes stale ownership.

A higher-level Skill such as Recurring Posts may then recreate the message according to its own behavior contract.

## Persistence failure

A message is sent before Discord provides its message ID.

If GamerHQ cannot persist ownership after a successful send, the host attempts to delete that just-created message and reports a safe failure rather than silently leaving an unowned Skill message.

## Provider independence

These rules are host capabilities, not Recurring Posts rules.

Any future Skill using Discord messages receives the same ownership and mention protections.
