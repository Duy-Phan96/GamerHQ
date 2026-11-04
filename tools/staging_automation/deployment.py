"""Development import for the canonical Host deployment state machine.

The owner-reviewed controller bundle installs the exact canonical source bytes
at this module path, with its existing Host package context (see installer).
"""
from hosts.gamerhq.skill_update_controller import SkillUpdateController, application_fingerprint
