from skill_runtime import SkillHealth, SkillManifest


class ExampleSkill:
    manifest = SkillManifest(
        id="example-skill",
        name="Example Skill",
        version="0.1.0",
        runtime_api_version="1",
        description="Minimal external GamerHQ Skill example.",
        author="Example Developer",
    )

    async def register(self, ctx):
        return None

    async def enable(self, ctx):
        return None

    async def disable(self, ctx):
        return None

    async def start(self, ctx):
        return None

    async def stop(self, ctx):
        return None

    async def health_check(self, ctx):
        return SkillHealth("PASS", "Example Skill is available.")


def create_skill():
    return ExampleSkill()
