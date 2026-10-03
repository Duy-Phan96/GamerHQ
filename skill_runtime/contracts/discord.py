class SkillDiscordError(RuntimeError):
    """Base portable Discord capability error."""


class SkillDiscordNotFound(SkillDiscordError):
    """A referenced Discord resource or GamerHQ-authored message no longer exists."""


class SkillDiscordPermissionDenied(SkillDiscordError):
    """The host or Skill lacks permission for the requested Discord operation."""


class SkillDiscordOperationFailed(SkillDiscordError):
    """Discord rejected or could not complete an otherwise valid operation."""
