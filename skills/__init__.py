"""First-party portable Skills shipped with GamerHQ."""

from .recurring_posts import RecurringPostsSkill


def first_party_skills():
    """Return new process-local Skill instances for the GamerHQ host."""
    return (RecurringPostsSkill(),)


__all__ = ["RecurringPostsSkill", "first_party_skills"]
