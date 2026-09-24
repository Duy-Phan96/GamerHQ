import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import test_curated_deals as fixtures
from database import db
from services import gocdkeys_import_service as imports
from services import curated_deal_service as curated
from services.gocdkeys_service import import_link, import_identity, manual_partner_url
from services.server_service import ServerMessageError

URL = 'https://gocdkeys.de/kaufen-grand-theft-auto-vi-pc-cd-key?ref=kas66b'
WOLVERINE = 'https://gocdkeys.de/kaufen-marvels-wolverine-ps5?ref=kas66b'
MINECRAFT = 'https://gocdkeys.com/buy-minecraft-dungeons-ii-pc-cd-key#ref=kas66b'


class ImportTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.CuratedDealTests.setUp
    rows = fixtures.CuratedDealTests.rows
    interaction = fixtures.CuratedDealTests.interaction

    async def asyncSetUp(self):
        imports._next_send.clear()
        self.sleep = patch('services.gocdkeys_import_service.asyncio.sleep', new_callable=AsyncMock)
        self.sleep_mock = self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_supplied_links_titles_and_tracking(self):
        for url, expected in [(URL, 'Grand Theft Auto VI'), (WOLVERINE, "Marvel's Wolverine"), (MINECRAFT, 'Minecraft Dungeons II')]:
            validated, key, title = import_link('  ' + url + '  ')
            self.assertEqual(validated, url)
            self.assertEqual(title, expected)
            self.assertNotIn('ref=', key)
        raw = MINECRAFT.split('#')[0]
        self.assertEqual(import_link(raw)[0], MINECRAFT)
        self.assertEqual(import_identity(URL), import_identity(URL.replace('gocdkeys.de/', 'www.gocdkeys.de/').replace('?ref=kas66b', '/?utm_source=discord&ref=kas66b')))
        self.assertNotEqual(import_identity(URL), import_identity(URL.replace('pc-cd-key', 'ps5')))

    def test_invalid_and_conflicting_urls_are_not_rewritten(self):
        for url in ['javascript:alert(1)', 'file:///file', 'data:text/plain,hi', URL.replace('https:', 'http:'),
                    URL.replace('gocdkeys.de', 'gocdkeys.de.evil.example'), URL.replace('gocdkeys.de', 'shop.gocdkeys.de'),
                    URL.replace('kas66b', 'another-code'), URL + '&ref=kas66b', URL + '#ref=another',
                    URL.split('?')[0], 'https://gocdkeys.com/search?ref=kas66b', URL.replace('https://', 'https://' + 'user:pass' + '@'),
                    URL.replace('gocdkeys.de/', 'gocdkeys.de:444/'), URL.replace('?ref=', '?REF=')]:
            with self.subTest(url=url), self.assertRaises(ServerMessageError):
                import_link(url)
        with self.assertRaises(ServerMessageError):
            manual_partner_url(MINECRAFT.replace('kas66b', 'another-code'))

    async def test_preview_is_read_only_bounded_and_counts_duplicates(self):
        with db.connect() as conn:
            before = list(conn.iterdump())
        with patch('aiohttp.ClientSession') as http:
            plan = imports.preview(self.guild, self.actor, '\n  ' + URL + '\n' + URL + '\nhttps://example.com/no\n' + WOLVERINE)
        http.assert_not_called()
        self.assertEqual([e.state for e in plan.entries], ['new', 'duplicate', 'invalid', 'new'])
        self.assertEqual([e.line for e in plan.entries], [2, 3, 4, 5])
        self.channel.send.assert_not_called()
        with db.connect() as conn:
            self.assertEqual(list(conn.iterdump()), before)
        for text in ['', '\n'.join([URL]*11), ' '*4001 + URL]:
            with self.assertRaises(ServerMessageError):
                imports.preview(self.guild, self.actor, text)

    async def test_confirm_posts_one_per_link_and_reimports_skip_after_restart(self):
        plan = imports.preview(self.guild, self.actor, URL + '\n' + WOLVERINE + '\n' + MINECRAFT)
        with patch('aiohttp.ClientSession') as http:
            results = await imports.publish(self.guild, self.actor, plan)
        self.assertEqual([state for _, state in results], ['created']*3)
        self.assertEqual(self.channel.send.await_count, 3)
        self.assertEqual(self.sleep_mock.await_count, 2)
        self.assertEqual([call.kwargs['view'].children[0].url for call in self.channel.send.call_args_list], [URL, WOLVERINE, MINECRAFT])
        for call in self.channel.send.call_args_list:
            self.assertEqual(call.kwargs['view'].children[0].label, 'Compare Prices')
            self.assertIn('Compare current game-key prices on GoCDKeys.', call.kwargs['content'])
            self.assertFalse(call.kwargs['allowed_mentions'].everyone)
        http.assert_not_called()
        self.assertTrue(all(row['status'] == 'posted' for row in self.rows()))
        db.init_db()
        second = imports.preview(self.guild, self.actor, URL + '\n' + WOLVERINE)
        self.assertEqual([e.state for e in second.entries], ['posted', 'posted'])
        await imports.publish(self.guild, self.actor, second)
        self.assertEqual(self.channel.send.await_count, 3)

    async def test_concurrent_imports_and_pending_claims_do_not_duplicate(self):
        plan = imports.preview(self.guild, self.actor, URL)
        await asyncio.gather(imports.publish(self.guild, self.actor, plan), imports.publish(self.guild, self.actor, plan))
        self.channel.send.assert_awaited_once()
        self.channel.send.side_effect = TimeoutError()
        plan = imports.preview(self.guild, self.actor, WOLVERINE)
        self.assertEqual((await imports.publish(self.guild, self.actor, plan))[0][1], 'uncertain')
        await imports.publish(self.guild, self.actor, plan)
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertEqual(imports.preview(self.guild, self.actor, WOLVERINE).entries[0].state, 'retained')

    async def test_existing_curated_gocdkeys_post_is_recognized(self):
        deal = replace(self.deal, partner='gocdkeys', url=MINECRAFT)
        await curated.publish(self.guild, self.actor, 'curated', 2, deal)
        self.assertEqual(imports.preview(self.guild, self.actor, MINECRAFT).entries[0].state, 'posted')
        with patch('config.GOCDKEYS_REFERRAL_CODE', 'newcode'):
            self.assertEqual(imports.preview(self.guild, self.actor, MINECRAFT.replace('kas66b', 'newcode')).entries[0].state, 'posted')

    async def test_uncertain_title_requires_edit_before_any_post(self):
        low = 'https://gocdkeys.de/kaufen-12345-pc-cd-key?ref=kas66b'
        plan = imports.preview(self.guild, self.actor, URL + '\n' + low)
        self.assertEqual(plan.entries[1].title, '')
        with self.assertRaises(ServerMessageError):
            await imports.publish(self.guild, self.actor, plan)
        self.channel.send.assert_not_called()
        plan = replace(plan, entries=(plan.entries[0], replace(plan.entries[1], title='Owner supplied title', note='Coming soon')))
        await imports.publish(self.guild, self.actor, plan)
        self.assertIn('Coming soon', self.channel.send.call_args.kwargs['content'])

    async def test_target_and_authorization_rechecked(self):
        plan = imports.preview(self.guild, self.actor, URL)
        self.actor.guild_permissions = discord.Permissions(manage_messages=True)
        with self.assertRaises(ServerMessageError):
            await imports.publish(self.guild, self.actor, plan)
        self.actor.id = self.guild.owner_id
        db.set_setting('managed_channel:1:gaming-deals', '')
        with self.assertRaises(ServerMessageError):
            await imports.publish(self.guild, self.actor, plan)
        self.assertEqual(self.rows(), [])

    async def test_revocation_during_pacing_stops_remaining_posts(self):
        plan = imports.preview(self.guild, self.actor, URL + '\n' + WOLVERINE)
        async def revoke(delay):
            self.actor.guild_permissions = discord.Permissions.none()
        self.sleep_mock.side_effect = revoke
        with self.assertRaises(ServerMessageError):
            await imports.publish(self.guild, self.actor, plan)
        self.channel.send.assert_awaited_once()
        self.assertEqual(len(self.rows()), 1)
        self.actor.guild_permissions = discord.Permissions(administrator=True)
        retry = imports.preview(self.guild, self.actor, URL + '\n' + WOLVERINE)
        self.assertEqual([e.state for e in retry.entries], ['posted', 'new'])

    async def test_command_modal_preview_edit_confirm_and_double_click(self):
        from cogs.gocdkeys import GoCdKeysWatcher
        from cogs.deal_import import preview_embed
        cog, interaction = GoCdKeysWatcher(None), self.interaction()
        await cog.import_gocdkeys.callback(cog, interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        modal.links._value = URL + '\n' + WOLVERINE
        await modal.on_submit(interaction)
        view = interaction.response.send_message.call_args.kwargs['view']
        self.assertIn('New deals: 2', preview_embed(view.plan, 0).description)
        self.assertEqual(self.rows(), [])
        await view.edit.callback(interaction)
        edit = interaction.response.send_modal.call_args.args[0]
        edit.titles._value = "1 | Grand Theft Auto VI\n2 | Marvel's Wolverine"
        edit.notes._value = '1 | Coming soon'
        await edit.on_submit(interaction)
        confirmed = interaction.response.send_message.call_args.kwargs['view']
        await view.post.callback(interaction)
        self.channel.send.assert_not_called()
        await confirmed.post.callback(interaction)
        await confirmed.post.callback(interaction)
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_ui_denies_other_users_revoked_admin_and_cancel(self):
        from cogs.gocdkeys import GoCdKeysWatcher
        from cogs.deal_import import ImportPreview
        plan = imports.preview(self.guild, self.actor, URL)
        view = ImportPreview(1, 5, plan)
        other = SimpleNamespace(id=100, guild_permissions=discord.Permissions(administrator=True))
        await view.post.callback(self.interaction(other))
        self.assertFalse(view.used)
        self.actor.guild_permissions = discord.Permissions.none()
        await view.post.callback(self.interaction())
        interaction = self.interaction()
        cog = GoCdKeysWatcher(None)
        await cog.import_gocdkeys.callback(cog, interaction)
        interaction.response.send_modal.assert_not_called()
        self.actor.guild_permissions = discord.Permissions(administrator=True)
        await view.cancel.callback(self.interaction())
        self.assertTrue(view.used)
        self.assertEqual(self.rows(), [])

    async def test_preview_pagination_stays_within_discord_limits(self):
        from cogs.deal_import import ImportPreview, preview_embed
        plan = imports.preview(self.guild, self.actor, '\n'.join(URL.replace('grand-theft-auto-vi', 'game-' + str(i)) for i in range(10)))
        view = ImportPreview(1, 5, plan)
        interaction = self.interaction()
        for page in range(4):
            embed = preview_embed(plan, page)
            self.assertLess(len(embed), 6000)
            self.assertLessEqual(len(embed.fields), 3)
            await view.next.callback(interaction)
        self.assertEqual(view.page, 3)
        self.assertTrue(view.next.disabled)
