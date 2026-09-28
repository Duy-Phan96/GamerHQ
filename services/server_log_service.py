"""Private, best-effort operational notices. Never discovers or creates channels."""
import hashlib
import logging

import discord
from database import db
from services.onboarding_service import is_staff

log = logging.getLogger(__name__)


def overwrites(guild, channel):
    # Preserve unrelated deny flags; remove posting/public grants on this log only.
    result = {target: discord.PermissionOverwrite.from_pair(*value.pair())
              for target, value in channel.overwrites.items()}
    result.setdefault(guild.default_role, discord.PermissionOverwrite()).view_channel = False
    for target in set(result) | {role for role in guild.roles if is_staff(role)}:
        if target == guild.default_role or target == guild.me:
            continue
        staff = (target in guild.roles and is_staff(target)) or any(is_staff(r) for r in getattr(target, 'roles', []))
        value = result.setdefault(target, discord.PermissionOverwrite())
        value.view_channel = bool(staff)
        value.read_message_history = bool(staff)
        value.send_messages = False
        value.send_messages_in_threads = False
        value.create_public_threads = False
        value.create_private_threads = False
    result[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
        read_message_history=True, embed_links=True)
    return result


def channel(guild):
    raw = db.get_setting(f'managed_channel:{guild.id}:server-log')
    parent = db.get_setting(f'managed_category:{guild.id}:staff')
    value = guild.get_channel(int(raw)) if raw and raw.isdigit() else None
    if not value or value not in guild.text_channels or not parent or str(value.category_id) != parent:
        return None
    if value.overwrites_for(guild.default_role).view_channel is not False:
        return None
    for target, rights in value.overwrites.items():
        staff = target in guild.roles and is_staff(target) or any(is_staff(r) for r in getattr(target, 'roles', []))
        if rights.view_channel is True and target != guild.me and not staff:
            return None
    permissions = value.permissions_for(guild.me)
    return value if permissions.view_channel and permissions.send_messages and permissions.embed_links else None


def claim(guild_id, event):
    key = f'server_log:{guild_id}:' + hashlib.sha256(event.encode()).hexdigest()
    with db.connect() as connection:
        cursor = connection.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)', (key, 'reserved'))
        return cursor.rowcount == 1


async def emit(guild, event, title, description, *, management=True):
    """Trusted, bounded copy only. Reserve before send: uncertain delivery is not retried."""
    try:
        destination = channel(guild)
        if destination is None or not claim(guild.id, event):
            return False
        from cogs.server_management import OpenManagementView
        await destination.send(embed=discord.Embed(title=title[:200], description=description[:3500]),
            view=OpenManagementView() if management else None, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception:
        # Startup/logging must never fail the operation or leak HTTP payloads/private values.
        log.warning('Server operational notice unavailable; review /server manage. No automatic resend.')
        return False


async def startup(guild, bot):
    from release_info import get_version, get_commit, release_notes
    version, commit = get_version(), get_commit()
    await emit(guild, f'deployment:{version}:{commit}', '🚀 GamerHQ Updated',
        f'**Version**\n{version} · {commit[:12]}\n\n**What’s New**\n' + release_notes() +
        '\n\n**Status**\n✅ Startup successful\n✅ Database ready\n✅ Persistent views loaded')
    from services.health_service import scan
    findings = await scan(guild, bot, messages=False)
    issues = [f for f in findings if f.state in {'CRITICAL', 'REPAIRABLE', 'RECONCILE', 'MANUAL_REVIEW'}]
    if issues:
        signature = hashlib.sha256('\n'.join(sorted(f.name + f.state for f in issues)).encode()).hexdigest()
        await emit(guild, 'warning:' + signature, '⚠️ GamerHQ Needs Attention',
            f'GamerHQ started successfully, but {len(issues)} server settings need review.\n'
            'Open **Server Management** or use `/server manage` to review structure, permissions and messages.')
