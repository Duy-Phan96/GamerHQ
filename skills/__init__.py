"""First-party portable Skills shipped directly inside GamerHQ.

External/bundled packages are discovered through the standard gamerhq.skills
entry-point mechanism instead of being imported here.
"""


def first_party_skills():
    """Return process-local Skills that are intentionally built into GamerHQ."""
    return ()


__all__ = ["first_party_skills"]
