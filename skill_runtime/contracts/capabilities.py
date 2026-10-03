from __future__ import annotations

from enum import StrEnum


class SkillCapability(StrEnum):
    """Stable, host-neutral capabilities a Skill may request.

    Values are public contract IDs. Renaming Python members is allowed later;
    changing the string values is a breaking SDK change.
    """

    DISCORD_MESSAGES_SEND = "discord.messages.send"
    DISCORD_MESSAGES_EDIT_OWN = "discord.messages.edit_own"
    DISCORD_MESSAGES_DELETE_OWN = "discord.messages.delete_own"
    DISCORD_EMBEDS_SEND = "discord.embeds.send"
    DISCORD_ROLES_MANAGE = "discord.roles.manage"
    DISCORD_VOICE_MANAGE = "discord.voice.manage"
    SCHEDULER_JOBS = "scheduler.jobs"
    STORAGE_SKILL = "storage.skill"
    EVENTS_EMIT = "events.emit"
    EVENTS_SUBSCRIBE = "events.subscribe"
    SKILL_API_CALL = "skills.api.call"
    AUDIT_WRITE = "audit.write"
    HTTP_EXTERNAL = "http.external"


KNOWN_CAPABILITIES = frozenset(item.value for item in SkillCapability)
