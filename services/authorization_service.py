"""Current guild owner/admin authorization; no permission cache."""


def authorized(guild, user):
    if not guild or not user:
        return False
    # Re-read cached member state on every component/modal/save interaction.
    member = guild.get_member(user.id)
    return bool(member and (member.id == guild.owner_id or member.guild_permissions.administrator))
