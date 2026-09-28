import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import test_curated_deals as fixtures
from database import db
from services import curated_deal_service as curated, gocdkeys_import_service as imports
from services.gocdkeys_service import promotion_link
from services.server_service import ServerMessageError

GIVEAWAY = 'https://gocdkeys.de/gewinnspiele/steam-guthabenkarte-20-euro-kostenlos-aktion?ref=creator&campaign=a%2Bb&unknown=1#entry'
PRODUCT = 'https://gocdkeys.com/buy-elden-ring-pc-cd-key#ref=kas66b'


class PromotionTests(unittest.IsolatedAsyncioTestCase):
    rows = fixtures.CuratedDealTests.rows
    interaction = fixtures.CuratedDealTests.interaction

    def setUp(self):
        fixtures.CuratedDealTests.setUp(self)
        self.giveaways = SimpleNamespace(id=3, mention='<#3>', send=AsyncMock(return_value=SimpleNamespace(id=11)),
                                        permissions_for=lambda member: discord.Permissions.all())
        self.guild.text_channels.append(self.giveaways)
        self.guild.get_channel = lambda cid: next((c for c in self.guild.text_channels if c.id == cid), None)
        db.set_setting('managed_channel:1:giveaways', 3)
        self.promotion = curated.Deal('gocdkeys', 'Steam Gift Card', '', '', GIVEAWAY, promotion_type='GIVEAWAY')
        imports._next_send.clear()
        sleeper = patch('services.gocdkeys_import_service.asyncio.sleep', new=AsyncMock())
        sleeper.start()
        self.addCleanup(sleeper.stop)

    async def test_giveaway_preview_optional_fields_and_preserved_url(self):
        payload = curated.render(self.promotion)
        self.assertEqual(payload['view'].children[0].url, GIVEAWAY)
        self.assertEqual(payload['view'].children[0].label, 'Enter Giveaway')
        self.assertEqual(payload['embed'].fields, [])
        detailed = curated.render(replace(self.promotion, prize='20 EUR', end_date='Tomorrow 18:00 UTC', note='Owner supplied'))
        self.assertEqual([f.name for f in detailed['embed'].fields], ['Prize', 'End date', 'Note'])
        self.assertEqual(self.rows(), [])
        self.giveaways.send.assert_not_called()

    async def test_fixed_targets_and_persistent_cross_workflow_dedupe(self):
        results = await asyncio.gather(*(curated.publish(self.guild, self.actor, str(i), 3, self.promotion) for i in range(2)))
        self.assertEqual(sorted(results), ['posted', 'retained'])
        db.init_db()
        self.assertEqual(imports.preview(self.guild, self.actor, GIVEAWAY).entries[0].state, 'posted')
        self.giveaways.send.assert_awaited_once()
        self.channel.send.assert_not_called()
        self.assertEqual(self.rows()[0]['channel_id'], 3)
        self.assertEqual(db.get_setting('managed_channel:1:giveaways'), '3')
        with self.assertRaises(ServerMessageError):
            await curated.publish(self.guild, self.actor, 'wrong', 2, self.promotion)
        self.assertEqual(await curated.publish(self.guild, self.actor, 'deal', 2, self.deal), 'posted')
        self.channel.send.assert_awaited_once()

    async def test_member_denied_and_giveaway_only_needs_own_target(self):
        self.actor.guild_permissions = discord.Permissions.none()
        with self.assertRaises(ServerMessageError):
            await curated.publish(self.guild, self.actor, 'member', 3, self.promotion)
        with self.assertRaises(ServerMessageError):
            imports.preview(self.guild, self.actor, GIVEAWAY)
        self.actor.guild_permissions = discord.Permissions(administrator=True)
        self.guild.text_channels.remove(self.channel)
        self.assertEqual(imports.preview(self.guild, self.actor, GIVEAWAY).giveaway_channel_id, 3)
        self.assertEqual(await curated.publish(self.guild, self.actor, 'admin', 3, self.promotion), 'posted')

    async def test_mixed_batch_edit_confirm_and_repeat(self):
        from cogs.deal_import import ImportPreview, TitlesModal, preview_embed
        plan = imports.preview(self.guild, self.actor, PRODUCT + '\n' + GIVEAWAY)
        self.assertEqual([e.promotion_type for e in plan.entries], ['DEAL', 'GIVEAWAY'])
        self.assertIn('GIVEAWAY', preview_embed(plan, 0).fields[1].value)
        self.assertTrue(ImportPreview(1, 5, plan).post.disabled)
        with self.assertRaises(ServerMessageError):
            await imports.publish(self.guild, self.actor, plan)
        self.channel.send.assert_not_called()
        edit = TitlesModal(1, 5, plan)
        edit.titles._value = '1 | Elden Ring\n2 | Steam Gift Card'
        edit.notes._value = ''
        interaction = self.interaction()
        await edit.on_submit(interaction)
        view = interaction.response.send_message.call_args.kwargs['view']
        self.assertEqual(self.rows(), [])
        await view.post.callback(interaction)
        await view.post.callback(interaction)
        self.channel.send.assert_awaited_once()
        self.giveaways.send.assert_awaited_once()
        self.assertIn('# 🎁 Steam Gift Card', self.giveaways.send.call_args.kwargs['content'])
        repeated = imports.preview(self.guild, self.actor, PRODUCT + '\n' + GIVEAWAY)
        self.assertEqual([state for _, state in await imports.publish(self.guild, self.actor, repeated)], ['posted', 'posted'])
        self.assertEqual(len(self.rows()), 2)

    async def test_exact_gift_card_slug_title_only(self):
        url = 'https://gocdkeys.de/gewinnspiele/steam-guthabenkarte-20-euro-kostenlos?creator=test'
        self.assertEqual(promotion_link(url)[2], 'Steam-Guthabenkarte über 20 €')
        self.assertIsNone(promotion_link(GIVEAWAY)[2])
        self.assertEqual(promotion_link(url)[0], url)

    async def test_unknown_type_requires_explicit_selection(self):
        url = 'https://gocdkeys.de/special/offer?creator=test&opaque=a%2Fb'
        self.assertEqual(imports.preview(self.guild, self.actor, url).entries[0].state, 'invalid')
        plan = imports.preview(self.guild, self.actor, url, 'GIVEAWAY')
        self.assertEqual(plan.entries[0].url, url)
        self.assertEqual(plan.entries[0].promotion_type, 'GIVEAWAY')
        with self.assertRaises(ServerMessageError):
            promotion_link(PRODUCT, 'GIVEAWAY')
        for bad in ['https://gocdkeys.de.evil.example/gewinnspiele/x', 'http://gocdkeys.de/gewinnspiele/x']:
            with self.assertRaises(ServerMessageError):
                promotion_link(bad)

    async def test_modal_giveaway_optional_fields_edit_and_confirmation(self):
        from cogs.gocdkeys import GoCdKeysWatcher
        cog, interaction = GoCdKeysWatcher(None), self.interaction()
        await cog.create.callback(cog, interaction, 'gocdkeys', promotion_type='GIVEAWAY')
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertFalse(modal.current.required)
        modal.product._value, modal.url._value = 'Gift Card', GIVEAWAY
        modal.current._value = modal.regular._value = modal.note._value = ''
        await modal.on_submit(interaction)
        view = interaction.response.send_message.call_args.kwargs['view']
        self.assertEqual(view.post.label, 'Post Giveaway')
        self.giveaways.send.assert_not_called()
        await view.edit.callback(interaction)
        edited = interaction.response.send_modal.call_args.args[0]
        self.assertEqual(edited.promotion_type, 'GIVEAWAY')
        edited.product._value, edited.url._value = 'Updated Gift Card', GIVEAWAY
        edited.current._value = edited.regular._value = edited.note._value = ''
        await edited.on_submit(interaction)
        confirmed = interaction.response.send_message.call_args.kwargs['view']
        await confirmed.post.callback(interaction)
        self.giveaways.send.assert_awaited_once()

    async def test_changed_giveaway_mapping_prevents_partial_batch(self):
        plan = imports.preview(self.guild, self.actor, PRODUCT + '\n' + GIVEAWAY)
        plan = replace(plan, entries=tuple(replace(e, title='Reviewed') for e in plan.entries))
        db.set_setting('managed_channel:1:giveaways', 999)
        with self.assertRaises(ServerMessageError):
            await imports.publish(self.guild, self.actor, plan)
        self.channel.send.assert_not_called()
        self.giveaways.send.assert_not_called()
