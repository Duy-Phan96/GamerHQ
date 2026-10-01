"""Scoped Amazon publisher integration; synthetic offline Discord state only."""
import unittest
from unittest.mock import patch

import discord

from database import db
from services import amazon_integration_service as amazon
from services import bot_group_service as groups
from services import support_service as support
from services.server_setup_service import repair_server
from services.health_service import scan
import test_bot_organization as fixtures


class AmazonIntegrationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.BotOrganizationTests.setUp
    asyncSetUp = fixtures.BotOrganizationTests.asyncSetUp

    async def test_repair_grants_only_amazon_board_posting(self):
        await repair_server(self.guild, self.bot)
        publisher = self.members['amazon']
        board = support.resource(self.guild, 'amazon')
        marketplace = support.resource(self.guild, 'partners-benefits', True)

        self.assertTrue(all(getattr(board.overwrites_for(publisher), bit) is True
                            for bit in amazon.BOT_RIGHTS))
        self.assertTrue(marketplace.overwrites_for(publisher).view_channel)
        self.assertTrue(marketplace.overwrites_for(publisher).read_message_history)
        self.assertFalse(marketplace.overwrites_for(publisher).send_messages)
        for bit in amazon.DENIED_RIGHTS:
            self.assertFalse(getattr(board.overwrites_for(publisher), bit))
            self.assertFalse(getattr(marketplace.overwrites_for(publisher), bit))

        for name in ('gaming-news', 'gaming-deals', 'free-games', 'ai-tools', 'electricity'):
            other = support.resource(self.guild, name)
            self.assertIsNot(other.overwrites_for(publisher).send_messages, True, name)

        from services.instant_gaming_service import affiliate_category
        self.assertIsNot(affiliate_category(self.guild).overwrites_for(publisher).view_channel, True)
        self.assertIsNot(self.staff.overwrites_for(publisher).view_channel, True)
        self.assertNotIn(groups.resolve(self.guild, 'gaming'), publisher.roles)

    async def test_repair_restores_drift_and_generic_policy_keeps_publisher(self):
        await repair_server(self.guild, self.bot)
        publisher = self.members['amazon']
        board = support.resource(self.guild, 'amazon')
        marketplace = support.resource(self.guild, 'partners-benefits', True)

        board.overwrites[publisher] = discord.PermissionOverwrite(
            view_channel=False, send_messages=False, embed_links=False, attach_files=False)
        marketplace.overwrites[publisher] = discord.PermissionOverwrite(view_channel=False, send_messages=True, manage_channels=True)

        await repair_server(self.guild, self.bot)
        self.assertTrue(all(getattr(board.overwrites_for(publisher), bit) is True
                            for bit in amazon.BOT_RIGHTS))
        self.assertTrue(marketplace.overwrites_for(publisher).view_channel)
        self.assertFalse(marketplace.overwrites_for(publisher).send_messages)
        self.assertFalse(marketplace.overwrites_for(publisher).manage_channels)

        from services.channel_change_service import safe_rights
        desired = safe_rights(board, 'amazon')
        self.assertTrue(all(getattr(desired[publisher], bit) is True for bit in amazon.BOT_RIGHTS))

    async def test_health_is_read_only_and_reports_scoped_access(self):
        await repair_server(self.guild, self.bot)
        with db.connect() as conn:
            before = list(conn.iterdump())
        findings = await scan(self.guild, self.bot, messages=False)
        row = next(f for f in findings if f.name == 'Amazon bot #amazon access')
        self.assertEqual(row.state, 'PASS')
        with db.connect() as conn:
            self.assertEqual(before, list(conn.iterdump()))

        board = support.resource(self.guild, 'amazon')
        publisher = self.members['amazon']
        board.overwrites[publisher].send_messages = False
        findings = await scan(self.guild, self.bot, messages=False)
        row = next(f for f in findings if f.name == 'Amazon bot #amazon access')
        self.assertEqual(row.state, 'REPAIRABLE')

    async def test_unconfigured_amazon_is_optional_and_never_adopted_by_name(self):
        publisher = self.members['amazon']
        publisher.name = 'Amazon'
        with patch('config.AMAZON_BOT_ID', 0):
            db.set_setting(f'bot_member:{self.guild.id}:amazon', '')
            await repair_server(self.guild, self.bot)
            board = support.resource(self.guild, 'amazon')
            self.assertIsNot(board.overwrites_for(publisher).send_messages, True)
            self.assertIsNone(groups.member(self.guild, 'amazon'))
            findings = await scan(self.guild, self.bot, messages=False)
            row = next(f for f in findings if f.name == 'Amazon bot #amazon access')
            self.assertEqual(row.state, 'WARN')

    async def test_saved_assignment_precedes_optional_environment_id(self):
        publisher = self.members['amazon']
        db.set_setting(f'bot_member:{self.guild.id}:amazon', publisher.id)
        with patch('config.AMAZON_BOT_ID', 999999):
            self.assertEqual(groups.member_id(self.guild, 'amazon'), publisher.id)
            await repair_server(self.guild, self.bot)
        board = support.resource(self.guild, 'amazon')
        self.assertTrue(board.overwrites_for(publisher).send_messages)
