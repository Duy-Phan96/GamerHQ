"""Offline discord.py model regression for an opaque channel of type 20.

The source fixture is synthetic and never talks to Discord or opens Production.
"""
import unittest

import discord

from tools.game_launch_audit_collect import AuditUnavailable, verified_rest_inventory


class DiscordGuildModelCoverageTests(unittest.TestCase):
    def test_type20_channel_remains_opaque_and_fails_coverage(self):
        client = discord.Client(intents=discord.Intents.none())
        state = client._connection
        state.user = discord.ClientUser(state=state, data={
            'id': '999', 'username': 'synthetic-bot',
            'discriminator': '0', 'avatar': None, 'bot': True,
        })
        guild_id = 111
        roles = [{
            'id': str(guild_id), 'name': '@everyone', 'permissions': '0',
            'position': 0, 'color': 0, 'hoist': False,
            'managed': False, 'mentionable': False,
        }]
        raw_channels = [
            {
                'id': '222', 'type': 0, 'name': 'synthetic-game-text',
                'position': 0, 'permission_overwrites': [],
            },
            {
                'id': '333', 'type': 20, 'name': 'SYNTHETIC_PRIVATE',
                'position': 1, 'permission_overwrites': [],
            },
        ]
        guild = discord.Guild(data={
            'id': str(guild_id), 'name': 'synthetic-server',
            'owner_id': '999', 'roles': roles,
            'channels': raw_channels, 'members': [],
            'emojis': [], 'features': [],
        }, state=state)
        self.assertIsInstance(guild.get_channel(222), discord.TextChannel)
        # Unsupported protocol types must never be reinterpreted as game chats.
        self.assertIsNone(guild.get_channel(333))
        diagnostic = {}
        with self.assertRaisesRegex(AuditUnavailable, '^DISCORD_CHANNEL_MODEL_INCOMPLETE$'):
            verified_rest_inventory(guild, raw_channels, roles, diagnostic=diagnostic)
        self.assertEqual(diagnostic['channelModelCoverage'], {
            'rawCount': 2, 'modeledCount': 1, 'missingCount': 1,
            'missingByType': {'20': 1}, 'extraModelCount': 0,
        })
        self.assertNotIn('SYNTHETIC_PRIVATE', repr(diagnostic))
        self.assertNotIn('333', repr(diagnostic))
        # Explicitly opaque mode keeps the real discord.py model unchanged.
        accepted = verified_rest_inventory(
            guild, raw_channels, roles, allow_opaque_type20=True)
        self.assertEqual(accepted[2], 'OPAQUE_TYPE20_UNVERIFIED')
        self.assertEqual(len(accepted[0]), 1)
        self.assertIsNone(guild.get_channel(333))

