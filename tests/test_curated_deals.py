import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from database import db
from services import curated_deal_service as deals
from services.server_service import ServerMessageError


class CuratedDealTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for path, value in [('database.db.DB_PATH', Path(directory.name)/'test.db'), ('config.GUILD_ID', 1),
                            ('config.GOCDKEYS_REFERRAL_CODE', 'kas66b')]:
            patcher = patch(path, value); patcher.start(); self.addCleanup(patcher.stop)
        db.init_db()
        self.actor = SimpleNamespace(id=5, guild_permissions=discord.Permissions(administrator=True))
        self.channel = SimpleNamespace(id=2, mention='<#2>', send=AsyncMock(return_value=SimpleNamespace(id=10)),
                                       permissions_for=lambda member: discord.Permissions.all())
        self.guild = SimpleNamespace(id=1, owner_id=6, me=SimpleNamespace(id=9),
            text_channels=[self.channel], get_channel=lambda cid: self.channel if cid == self.channel.id else None)
        db.set_setting('managed_channel:1:gaming-deals', 2)
        self.deal = deals.Deal('amazon', 'Mouse', '99,99', '149.99', 'https://www.amazon.de/dp/TEST?tag=demo-21')

    def rows(self):
        with db.connect() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM curated_deals')]

    def interaction(self, actor=None):
        return SimpleNamespace(guild=self.guild, user=actor or self.actor, response=AsyncMock(), edit_original_response=AsyncMock())

    async def test_partner_previews_prices_and_no_writes(self):
        for partner, url, label in [('amazon', self.deal.url, 'View on Amazon'),
            ('instant-gaming', 'https://www.instant-gaming.com/en/product', 'View on Instant Gaming'),
            ('gocdkeys', 'https://gocdkeys.com/buy-elden-ring-pc-cd-key', 'Compare / View on GoCDKeys'),
            ('other', 'https://example.com/deal', 'View Deal')]:
            payload = deals.render(replace(self.deal, partner=partner, url=url))
            self.assertEqual(payload['view'].children[0].label, label)
            self.assertEqual(payload['embed'].fields[-1].value, '≈ 33%')
            self.assertEqual(payload['embed'].footer.text, 'Affiliate / referral link')
            if partner == 'gocdkeys':
                self.assertTrue(payload['view'].children[0].url.endswith('#ref=kas66b'))
        self.assertEqual(self.rows(), [])
        self.channel.send.assert_not_called()

    def test_strict_urls(self):
        for url in ['javascript:alert(1)', 'file:///file', 'data:text/plain,x', 'http://example.com',
                    'https://' + 'user:pass' + '@example.com', 'https://amazon.de.evil.example/x', 'https://127.0.0.1',
                    'https://foo.local/x', 'https://amazon.de/x?token=private', 'https://amazon.de:abc/x',
                    'https://amazon.de/hello world', 'https://amazon.de\\@evil.example']:
            with self.subTest(url=url), self.assertRaises(ServerMessageError):
                deals.validate(replace(self.deal, url=url))
        self.assertEqual(deals.partner_url('amazon', 'https://amzn.to/test'), 'https://amzn.to/test')
        self.assertEqual(deals.partner_url('gocdkeys', 'https://gocdkeys.com/search?name=Test#manual'),
                         'https://gocdkeys.com/search?name=Test#manual')

    async def test_price_validation_and_no_misleading_discount(self):
        for current, regular in [('NaN', ''), ('-1', ''), ('1,234.56', ''), ('2', '1'), ('0', '0'), ('1.234', '')]:
            with self.subTest(current=current, regular=regular), self.assertRaises(ServerMessageError):
                deals.validate(replace(self.deal, current_price=current, regular_price=regular))
        for current, regular in [('0', ''), ('10', ''), ('10', '10')]:
            payload = deals.render(replace(self.deal, current_price=current, regular_price=regular))
            self.assertNotIn('Save', [f.name for f in payload['embed'].fields])
        payload = deals.render(replace(self.deal, current_price='0', regular_price='10'))
        self.assertEqual(payload['embed'].fields[-1].value, '≈ 100%')

    async def test_post_claim_is_persistent_and_concurrent_safe(self):
        outcomes = await asyncio.gather(*(deals.publish(self.guild, self.actor, 'draft', 2, self.deal) for _ in range(2)))
        self.assertEqual(sorted(outcomes), ['posted', 'retained'])
        db.init_db()
        self.assertEqual(await deals.publish(self.guild, self.actor, 'draft', 2, self.deal), 'retained')
        self.channel.send.assert_awaited_once()
        self.assertEqual((self.rows()[0]['created_by'], self.rows()[0]['discord_message_id']), (5, 10))
        self.assertFalse(self.channel.send.call_args.kwargs['allowed_mentions'].everyone)

    async def test_uncertain_delivery_does_not_retry(self):
        self.channel.send.side_effect = TimeoutError()
        self.assertEqual(await deals.publish(self.guild, self.actor, 'draft', 2, self.deal), 'uncertain')
        self.assertEqual(await deals.publish(self.guild, self.actor, 'draft', 2, self.deal), 'retained')
        self.channel.send.assert_awaited_once()

    async def test_scope_authorization_and_mapping_rechecked(self):
        self.actor.guild_permissions = discord.Permissions(manage_messages=True)
        with self.assertRaises(ServerMessageError):
            await deals.publish(self.guild, self.actor, 'draft', 2, self.deal)
        self.actor.id = self.guild.owner_id
        self.assertIs(deals.target(self.guild, self.actor), self.channel)
        self.channel.id = 3
        db.set_setting('managed_channel:1:gaming-deals', 3)
        with self.assertRaises(ServerMessageError):
            await deals.publish(self.guild, self.actor, 'draft', 2, self.deal)
        self.assertEqual(self.rows(), [])

    async def test_create_command_modal_preview_and_confirm(self):
        from cogs.gocdkeys import GoCdKeysWatcher
        cog, interaction = GoCdKeysWatcher(None), self.interaction()
        await cog.create.callback(cog, interaction, 'amazon')
        modal = interaction.response.send_modal.call_args.args[0]
        for field, value in [(modal.product, 'Mouse'), (modal.current, '99.99'),
                             (modal.regular, '149.99'), (modal.url, self.deal.url), (modal.note, '')]:
            field._value = value
        await modal.on_submit(interaction)
        self.channel.send.assert_not_called()
        self.assertEqual(self.rows(), [])
        preview = interaction.response.send_message.call_args.kwargs['view']
        await preview.post.callback(interaction)
        await preview.post.callback(interaction)
        self.channel.send.assert_awaited_once()

    async def test_ui_edit_cancel_and_revocation(self):
        from cogs.deal_editor import DealPreview
        from cogs.gocdkeys import GoCdKeysWatcher
        preview = DealPreview(1, 5, 2, 'draft', deals.validate(self.deal))
        interaction = self.interaction()
        await preview.edit.callback(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertEqual(modal.draft_id, 'draft')
        self.assertEqual(modal.product.default, 'Mouse')
        await preview.post.callback(interaction)
        self.channel.send.assert_not_called()
        cancel = DealPreview(1, 5, 2, 'other', deals.validate(self.deal))
        await cancel.cancel.callback(interaction)
        self.assertEqual(self.rows(), [])
        revoked = DealPreview(1, 5, 2, 'revoked', deals.validate(self.deal))
        self.actor.guild_permissions = discord.Permissions.none()
        await revoked.post.callback(interaction)
        self.channel.send.assert_not_called()
        cog = GoCdKeysWatcher(None)
        interaction.response.reset_mock()
        await cog.create.callback(cog, interaction, 'amazon')
        interaction.response.send_modal.assert_not_called()


class StaffGuidePagesTests(unittest.IsolatedAsyncioTestCase):
    import test_onboarding as fixtures
    setUp = fixtures.OnboardingTests.setUp

    async def test_registered_commands_fit_managed_pages_and_repeat(self):
        from cogs.server import ServerAdmin
        from cogs.games import Games
        from cogs.area import Areas
        from cogs.gocdkeys import GoCdKeysWatcher
        from services import command_guide_service as guide
        groups = [ServerAdmin.server, Games.game_admin, Areas.area, GoCdKeysWatcher.deals]
        bot = SimpleNamespace(tree=SimpleNamespace(get_commands=lambda **kw: groups))
        channel = self.guild.add_channel('mod-commands', self.staff)
        first = await guide.refresh_staff_command_guide(bot, self.guild, channel)
        self.assertGreater(len(channel.messages), 1)
        self.assertTrue(all(len(m.content) <= 2000 for m in channel.messages.values()))
        self.assertIn('/deals create', '\n'.join(m.content for m in channel.messages.values()))
        ids = list(channel.messages)
        again = await guide.refresh_staff_command_guide(bot, self.guild, channel)
        self.assertEqual(first.id, again.id)
        self.assertEqual(ids, list(channel.messages))
        groups.clear()
        await guide.refresh_staff_command_guide(bot, self.guild, channel)
        self.assertEqual(list(channel.messages), [first.id])
