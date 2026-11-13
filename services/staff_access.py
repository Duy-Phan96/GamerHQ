"""Owner-selected private-area access, separate from command authorization."""
from database import db

ROLE_NAME = 'GamerHQ Staff'


def key(guild):
    return f'private_staff_role:{guild.id}'


def valid(role):
    return bool(role and not role.is_default() and not role.managed and role.permissions.value == 0)


def mapped_role(guild):
    raw = db.get_setting(key(guild))
    role = guild.get_role(int(raw)) if raw and raw.isdecimal() else None
    return role if valid(role) else None


def is_mapped(guild, role):
    return valid(role) and db.get_setting(key(guild)) == str(role.id)


def creation_roles(guild):
    """A recorded but invalid mapping fails closed. Legacy guilds keep their policy."""
    if db.get_setting(key(guild)) is not None:
        role = mapped_role(guild)
        return [role] if role else []
    from services.onboarding_service import is_staff
    return [role for role in guild.roles if is_staff(role)]
