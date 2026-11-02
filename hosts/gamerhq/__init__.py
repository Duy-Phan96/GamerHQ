"""GamerHQ host implementation for the portable Skill Runtime."""

from importlib import import_module

# Importing a pure artifact preflight must not load live Host configuration,
# credentials, DB adapters or Discord. Preserve public exports lazily.
_MODULES = {
    "CapabilityPermissions": "skill_host", "GamerHQSkillAudit": "skill_host",
    "GamerHQSkillStateStore": "skill_host", "GamerHQSkillStorage": "skill_host",
    "GamerHQSchedulerStore": "skill_scheduler", "ExternalSkillLoadReport": "skill_packages",
    "load_external_skill_packages": "skill_packages", "GamerHQDiscordAdapter": "skill_discord",
    "GamerHQSkillRuntime": "skill_runtime", "GuildSkillStatus": "skill_runtime",
    "HOST_CAPABILITIES": "skill_runtime", "GamerHQExternalHttp": "skill_http",
    "GamerHQSkillSecrets": "skill_secrets",
}


def __getattr__(name):
    module = _MODULES.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module("." + module, __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "CapabilityPermissions",
    "GamerHQDiscordAdapter",
    "GamerHQExternalHttp",
    "GamerHQSkillSecrets",
    "ExternalSkillLoadReport",
    "load_external_skill_packages",
    "GamerHQSchedulerStore",
    "GamerHQSkillRuntime",
    "GuildSkillStatus",
    "HOST_CAPABILITIES",
    "GamerHQSkillAudit",
    "GamerHQSkillStateStore",
    "GamerHQSkillStorage",
]
