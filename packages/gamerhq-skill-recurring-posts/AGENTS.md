# Recurring Posts Skill repository instructions

This package is a portable GamerHQ Skill.

Follow the canonical GamerHQ Skill Developer Guide, Authoring Contract and
Review Checklist.

Hard rules:

- Keep Skill ID `recurring-posts` stable.
- Do not import Discord.py or GamerHQ bot/cogs/services/database/hosts/config.
- Use only public `skill_runtime` contracts.
- Keep configuration behind versioned Management APIs.
- Use Skill Storage and the shared Scheduler.
- Keep tests offline and token-free.
- Preserve `posts.v1`, scheduler job keys and handler IDs across compatible releases.
