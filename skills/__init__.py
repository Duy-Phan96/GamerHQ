"""First-party portable Skills shipped directly inside GamerHQ.

External/bundled packages are discovered through the standard gamerhq.skills
entry-point mechanism instead of being imported here.
"""


def first_party_skills():
    """Return process-local Skills that are intentionally built into GamerHQ."""
    from .progression import create_skill as create_progression_skill

    return (create_progression_skill(),)


__all__ = ["first_party_skills"]
