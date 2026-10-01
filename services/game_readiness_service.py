"""Read-only presentation of the existing game visibility lifecycle.

No provisioner, background repair or extra persisted visibility flag lives here.
One shared REST inventory feeds every game; writes still belong to the existing
reviewed game_visibility_service. Unknown reads are never treated as absence.
"""
from __future__ import annotations

import json
import time

import discord
from database import db
from services import game_visibility_service as visibility
from services import game_channel_service as channels
from services.authorization_service import authorized
from services.operation_context import read_scope

MAX_AGE = 90
READY = 'READY'
NOT_SET_UP = 'NOT_SET_UP'
HIDDEN = 'HIDDEN'
SETUP_NEEDED = 'SETUP_NEEDED'
PENDING = 'PENDING'
REVIEW = 'REVIEW'
UNVERIFIED = 'UNVERIFIED'


def state(code, reason, *, target=None, channel_id=None, repairable=False):
    return dict(code=code, reason=reason, target=target, channel_id=channel_id, repairable=repairable)


def unverified(reason='Refresh to check the current game and channel state.'):
    return state(UNVERIFIED, reason)


def _reservation(raw, expected_id):
    """A successful old reservation may remain stored; an uncertain one is a gate."""
    if not raw:
        return
    try:
        row = json.loads(raw)
        identifier = row.get('channel_id', row.get('id'))
        if (row.get('state') == 'CREATED' and type(identifier) is int
                and identifier > 0 and identifier == expected_id):
            return
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError('An earlier resource creation is unverified. Review it before retrying; no duplicate will be created.')


def _inspect_one(guild, actor, game):
    gid = game['id']
    visible = bool(game['selectable'])
    # Reuse ownership, additional-access, role-hierarchy and tombstone checks.
    plan = visibility._plan(guild, actor, gid, visible)
    channel = guild.get_channel(plan['channel_id']) if plan['channel_id'] else None
    role = guild.get_role(plan['role_signature'][0]) if plan['role_signature'] else None
    if db.get_setting(f'game_channel_removed:{guild.id}:{gid}') == '1':
        raise ValueError('This game chat was deliberately removed. Review explicit recreation in Game Channels.')
    hidden = db.get_setting(channels.hidden_key(guild, gid))
    if hidden not in (None, '', '0', '1'):
        raise ValueError('The saved channel visibility policy is invalid. Review this game first.')
    operation = db.get_setting(visibility.operation_key(guild, gid))
    try:
        op = json.loads(operation) if operation else {}
    except (TypeError, ValueError):
        raise ValueError('The saved setup result is invalid. Review this game before retrying.') from None
    if operation and (not isinstance(op, dict) or op.get('state') not in ('PENDING', 'DONE')
                      or type(op.get('visible')) is not bool):
        raise ValueError('The saved setup result is invalid. Review this game before retrying.')
    _reservation(db.get_setting(f'game_channel_creation:{guild.id}:{gid}'), channel.id if channel else None)
    _reservation(db.get_setting(f'game_visibility_role_creation:{guild.id}:{gid}'), role.id if role else None)
    if visible:
        _reservation(db.get_setting(f'games_category_creation:{guild.id}'), plan['parent_id'])
    cid = channel.id if channel else None
    if op.get('state') == 'PENDING':
        return state(PENDING, 'A previous change did not finish verification. Review the requested setup again.',
                     target=visible, channel_id=cid)
    if operation and (op['visible'] != visible or op.get('channel_id') != cid):
        return state(SETUP_NEEDED, 'The saved visibility and last completed result disagree. Review setup.',
                     target=visible, channel_id=cid)
    if not visible and channel is None:
        return state(NOT_SET_UP, 'Hidden in the library; no associated game text channel exists.', target=True)
    if visible and channel is None:
        return state(SETUP_NEEDED, 'Visible in the library, but its game chat still needs to be set up.', target=True)
    if game.get('channel_id') != cid:
        return state(SETUP_NEEDED, 'An existing legacy chat needs a confirmed canonical game-channel link.',
                     target=True if visible else False, channel_id=cid)
    if channel.overwrites != channels.overwrites(guild, role, channel, visible=visible):
        return state(SETUP_NEEDED, 'The channel access does not match the saved visibility. Review the access update.',
                     target=visible, channel_id=cid)
    if not visible:
        if hidden != '1':
            return state(SETUP_NEEDED, 'The chat is hidden, but its saved hide policy needs confirmation.',
                         target=False, channel_id=cid)
        return state(HIDDEN, 'Hidden from game members; the existing channel and its history are kept.',
                     target=True, channel_id=cid)
    if (role is None or plan['role_action'] != 'keep' or plan['category_action'] != 'keep'
            or plan['parent_id'] is None or channel.category_id != plan['parent_id']
            or channel.name != channels.slug(game['name']) or hidden == '1'):
        return state(SETUP_NEEDED, 'Confirm setup to finish the game role, Games placement, name or visibility policy.',
                     target=True, channel_id=cid)
    return state(READY, 'Visible game with its verified, role-gated text channel in Games.', target=False, channel_id=cid)


def inspect_one(guild, actor, game):
    """Turn a per-game review gate into display data, never a repair side effect."""
    try:
        return _inspect_one(guild, actor, game)
    except ValueError as exc:
        reason = str(exc)
        repairable = reason == 'The saved game chat is unavailable or is not a text channel. Review its mapping first.'
        return state(REVIEW, reason, repairable=repairable)
    except (KeyError, TypeError, AttributeError):
        return state(REVIEW, 'Game mappings or saved setup data are invalid. Review this game first.')


async def inventory(guild, actor):
    """Read all active games using at most one channel and one role inventory."""
    if not authorized(guild, actor):
        raise ValueError('Administrator access is required.')
    error = None
    with db.read_only(), read_scope():
        try:
            if not guild.me or getattr(guild, 'unavailable', False):
                raise ValueError('The server or bot membership is unavailable. Refresh after reconnecting.')
            fresh = await visibility._snapshot(guild)
        except discord.Forbidden:
            error = 'Discord denied the configuration check. Review the bot access, then refresh.'
        except (discord.HTTPException, TimeoutError):
            error = 'Discord could not verify the configuration. Refresh to retry; no resources were assumed missing.'
        except ValueError as exc:
            error = str(exc)
        if not authorized(guild, actor):
            raise ValueError('Administrator access changed. Reopen Manage Visible Games.')
        games = db.get_all_games(active_only=True)
        states = {g['id']: unverified(error) if error else inspect_one(fresh, actor, g) for g in games}
    return dict(games=games, states=states, checked_at=time.monotonic(), error=error)
