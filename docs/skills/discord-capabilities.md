# Discord capabilities and resource scopes

Portable Skills do not receive the Discord client or bot token.

They interact with Discord through the host-provided `ctx.discord` port.

## Two independent checks

A Discord action requires both:

1. the Skill declared the required **capability** in its manifest;
2. the host allows the concrete **resource scope** for that Skill.

A capability alone never means "all channels".

Example:

```text
Capability:
discord.messages.send

Resource scope:
#announcements
#general
```

The same Skill may still be denied access to:

```text
#staff-chat
#ticket-logs
private support tickets
another guild
```

## Fail-closed default

The GamerHQ host uses a deny-all resource policy unless an explicit policy is supplied.

This means accidentally registering a Skill with `discord.messages.send` does not automatically grant access to every GamerHQ channel.

Future installation/configuration UI can build host-owned grants without changing the portable Skill API.

## Current message capabilities

Examples:

```text
discord.messages.send
discord.messages.edit_own
discord.messages.delete_own
discord.embeds.send
```

Edit/delete operations verify that the target message is owned by the current bot.

A Skill cannot use the public port to edit or delete arbitrary user messages.

## Mentions are separate permissions

Mention ability is deliberately not implied by message sending.

Available capability IDs include:

```text
discord.mentions.users
discord.mentions.roles
discord.mentions.everyone
```

The default is no mentions.

A Skill asking for role mentions must declare the role-mention capability **and** the host resource policy must allow that operation in the target channel.

`@everyone` / `@here` therefore require an explicit high-risk capability and resource approval.

Recurring Posts will expose this through deliberate admin configuration instead of silently enabling mass mentions.

## Embeds

Sending an embed requires:

```text
discord.messages.send
discord.embeds.send
```

The public adapter intentionally supports a bounded provider-neutral embed subset first. Additional Discord-specific features should be added as reviewed SDK capabilities/contracts rather than exposing the raw Discord.py object.

## Why resource scopes stay host-owned

The Skill manifest describes what category of operation the code requires.

The host controls where that operation may occur.

This separation allows a future installation screen such as:

```text
Install "Recurring Posts"

Capabilities
✓ Send messages
✓ Send embeds
✓ Scheduled jobs
✓ Skill storage

Allowed channels
✓ #announcements
✓ #general
✗ STAFF
✗ Support Tickets
```

A third-party Skill cannot widen these host grants by changing its own configuration.

## Future extensions

The same pattern can later apply to:

- role-management scope;
- voice-channel scope;
- HTTP domains;
- AI model/cost scopes;
- secrets owned by one Skill;
- rate limits;
- file/media access.

The portable SDK should express the capability request while each host remains responsible for granting concrete resources.
