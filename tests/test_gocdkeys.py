import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from database import db, affiliate_deals
from services import gocdkeys_service as service


URL = 'https://gocdkeys.com/buy-elden-ring-pc-cd-key'
PAGE = '<html><title>Buy Elden Ring Steam Key 🏷️ at best prices | Gocdkeys</title></html>'


class ComparisonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for target, value in [('database.db.DB_PATH', Path(directory.name)/'test.db'),
                              ('config.GOCDKEYS_ENABLED', True), ('config.GOCDKEYS_AUTOMATIC_SUPPORTED', True), ('config.GOCDKEYS_REFERRAL_CODE', 'kas66b'),
                              ('config.GUILD_ID', 1), ('config.INSTANT_GAMING_BOT_ID', 42)]:
            patcher = patch(target, value); patcher.start(); self.addCleanup(patcher.stop)
        db.init_db()
        self.channel = SimpleNamespace(id=2, mention='<#2>')
        self.channel.permissions_for = lambda member: discord.Permissions.all()
        self.guild = SimpleNamespace(id=1, me=SimpleNamespace(id=9, bot=True), get_channel=lambda cid: self.channel if cid == 2 else None,
                                     text_channels=[self.channel])
        db.set_setting('managed_channel:1:gaming-deals', '2')
        self.message = SimpleNamespace(id=100, guild=self.guild, channel=self.channel,
            author=SimpleNamespace(id=42, bot=True), webhook_id=None, content='€19.99',
            embeds=[discord.Embed(title='ELDEN RING - Steam')], reply=AsyncMock(return_value=SimpleNamespace(id=200)))
        self.service = service.GoCdKeysService()
        self.service.fetch_page = AsyncMock(return_value=PAGE)

    def backfill_history(self, messages):
        self.history_limits = []
        async def history(*, limit):
            self.history_limits.append(limit)
            for message in messages:
                yield message  # Service also enforces the bound.
        self.channel.history = history
        self.channel.fetch_message = AsyncMock(side_effect=lambda mid: next(m for m in messages if m.id == mid))

    async def test_backfill_preview_read_only_then_confirm_and_restart(self):
        self.backfill_history([self.message])
        plan = await self.service.preview_backfill(self.guild)
        self.assertEqual((plan['scanned'], plan['supported'], plan['candidates']), (1, 1, [100]))
        self.assertFalse(affiliate_deals.processed(100))
        self.service.fetch_page.assert_not_called()
        self.message.reply.assert_not_called()
        result = await self.service.run_backfill(self.guild, plan)
        self.assertEqual(result['created'], 1)
        restarted = service.GoCdKeysService()
        result = await restarted.run_backfill(self.guild, plan)
        self.assertEqual(result['created'], 0)
        await restarted.handle(self.message)
        self.message.reply.assert_awaited_once()
        self.assertEqual((await restarted.preview_backfill(self.guild))['enriched'], 1)

    async def test_backfill_and_live_share_concurrent_claim(self):
        self.backfill_history([self.message])
        plan = await self.service.preview_backfill(self.guild)
        await asyncio.gather(self.service.run_backfill(self.guild, plan), self.service.handle(self.message))
        self.message.reply.assert_awaited_once()

    async def test_backfill_silent_hill_embed_and_referral(self):
        self.message.content = ''
        self.message.embeds = [discord.Embed(title='Silent Hill: Townfall', description='34.19 €')]
        self.service.fetch_page.return_value = PAGE.replace('Elden Ring', 'Silent Hill: Townfall')
        self.backfill_history([self.message])
        plan = await self.service.preview_backfill(self.guild)
        self.assertEqual(plan['candidates'], [100])
        await self.service.run_backfill(self.guild, plan)
        self.assertEqual(self.message.reply.call_args.kwargs['view'].children[0].url,
                         'https://gocdkeys.com/buy-silent-hill-townfall-pc-cd-key#ref=kas66b')

    async def test_backfill_scan_limits_and_untrusted_messages(self):
        from copy import copy
        messages = []
        for index in range(105):
            message = copy(self.message)
            message.id = 100 + index
            message.author = SimpleNamespace(id=777, bot=True)
            messages.append(message)
        self.backfill_history(messages)
        for limit in (25, 50, 100):
            plan = await self.service.preview_backfill(self.guild, limit)
            self.assertEqual((plan['scanned'], plan['skipped']), (limit, limit))
            self.assertEqual(plan['candidates'], [])
        self.assertEqual(self.history_limits, [25, 50, 100])
        with self.assertRaises(ValueError):
            await self.service.preview_backfill(self.guild, 101)

    async def test_backfill_retains_unmapped_companions_and_uncertain_claims(self):
        companion = SimpleNamespace(id=200, author=self.guild.me, content=service.COPY,
                                    reference=SimpleNamespace(message_id=100), channel=self.channel, guild=self.guild)
        self.backfill_history([companion, self.message])
        plan = await self.service.preview_backfill(self.guild)
        self.assertEqual((plan['enriched'], plan['candidates']), (1, []))
        self.assertFalse(affiliate_deals.processed(100))
        self.backfill_history([self.message])
        plan = await self.service.preview_backfill(self.guild)
        affiliate_deals.claim(self.message, URL, 'Elden Ring', 'instant-gaming')
        affiliate_deals.finish(100, 'uncertain')
        result = await self.service.run_backfill(self.guild, plan)
        self.assertEqual(result['retained'], 1)
        self.assertEqual((await self.service.preview_backfill(self.guild))['retained'], 1)
        self.message.reply.assert_not_called()

    async def test_backfill_rechecks_mapping_permissions_source_and_companions(self):
        self.backfill_history([self.message])
        plan = await self.service.preview_backfill(self.guild)
        db.set_setting('managed_channel:1:gaming-deals', '')
        with self.assertRaises(ValueError):
            await self.service.run_backfill(self.guild, plan)
        db.set_setting('managed_channel:1:gaming-deals', '2')
        self.channel.permissions_for = lambda member: discord.Permissions.none()
        with self.assertRaises(ValueError):
            await self.service.run_backfill(self.guild, plan)
        self.channel.permissions_for = lambda member: discord.Permissions.all()
        self.message.author = SimpleNamespace(id=777, bot=True)
        self.assertEqual((await self.service.run_backfill(self.guild, plan))['skipped'], 1)
        self.message.author = SimpleNamespace(id=42, bot=True)
        companion = SimpleNamespace(id=200, author=self.guild.me, content=service.COPY,
                                    reference=SimpleNamespace(message_id=100), channel=self.channel, guild=self.guild)
        self.backfill_history([companion, self.message])
        self.assertEqual((await self.service.run_backfill(self.guild, plan))['retained'], 1)
        self.message.reply.assert_not_called()

    async def test_backfill_provider_waits_instead_of_skipping_busy_batch(self):
        import time
        self.service._next_request = time.monotonic() + 0.01
        with patch('services.gocdkeys_service.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.assertEqual(await self.service.find_game_page('Elden Ring', wait=True), URL)
            sleep.assert_awaited_once()

    async def test_backfill_session_authorization_and_double_click(self):
        from cogs.gocdkeys import BackfillView, GoCdKeysWatcher
        self.guild.owner_id = 1
        actor = SimpleNamespace(id=1, guild_permissions=discord.Permissions.none())
        interaction = SimpleNamespace(guild=self.guild, user=actor, response=AsyncMock(),
                                      edit_original_response=AsyncMock())
        view = BackfillView(self.service, self.guild, actor.id, 50)
        self.assertTrue(await view.interaction_check(interaction))
        self.backfill_history([self.message])
        await view.proceed.callback(interaction)
        await view.proceed.callback(interaction)
        self.assertEqual(len(self.history_limits), 1)
        confirm = interaction.edit_original_response.call_args.kwargs['view']
        actor.id = 2
        self.assertFalse(await confirm.interaction_check(interaction))
        await confirm.proceed.callback(interaction)
        self.message.reply.assert_not_called()
        cog = GoCdKeysWatcher(None)
        await cog.backfill.callback(cog, interaction)
        self.assertNotIn('view', interaction.response.send_message.call_args.kwargs)
        actor.id = 1
        await confirm.cancel.callback(interaction)
        self.assertTrue(confirm.used)
        self.assertFalse(affiliate_deals.processed(100))

    async def test_matching_deal_reply_is_compact_and_attributed(self):
        await self.service.handle(self.message)
        kwargs = self.message.reply.call_args.kwargs
        self.assertEqual(kwargs['view'].children[0].url, URL+'#ref=kas66b')
        self.assertFalse(kwargs['mention_author'])
        self.assertFalse(kwargs['allowed_mentions'].everyone)
        self.assertFalse(kwargs['allowed_mentions'].roles)
        self.assertTrue(kwargs['suppress_embeds'])
        self.assertNotIn('embed', kwargs)
        with db.connect() as conn:
            row = conn.execute('SELECT * FROM processed_affiliate_deals').fetchone()
        self.assertEqual((row['guild_id'], row['source_message_id'], row['status'], row['response_message_id']), (1,100,'posted',200))
        self.assertGreater(row['processed_at'], 0)

    async def test_filters_users_other_bots_channels_guilds_and_webhooks(self):
        for field, replacement in [('channel', SimpleNamespace(id=3)), ('author', SimpleNamespace(id=42,bot=False)),
                                   ('author', SimpleNamespace(id=99,bot=True)), ('guild', None),
                                   ('guild', SimpleNamespace(id=4)), ('webhook_id', 123)]:
            original = getattr(self.message, field)
            setattr(self.message, field, replacement)
            await self.service.handle(self.message)
            setattr(self.message, field, original)
        self.service.fetch_page.assert_not_called()
        self.message.reply.assert_not_called()

    async def test_missing_mapping_does_not_discover_by_name(self):
        db.set_setting('managed_channel:1:gaming-deals', '')
        await self.service.handle(self.message)
        self.message.reply.assert_not_called()

    async def test_empty_or_generic_title_is_skipped(self):
        self.message.embeds = [discord.Embed(title='SALE -50% €19.99')]
        await self.service.handle(self.message)
        self.message.reply.assert_not_called()
        self.service.fetch_page.assert_not_called()

    async def test_unresolved_wrong_edition_or_http_block_never_posts(self):
        for document in [None, '<title>Access denied</title>', PAGE.replace('Elden Ring', 'Elden Ring Deluxe Edition')]:
            self.service = service.GoCdKeysService()
            self.service.fetch_page = AsyncMock(return_value=document)
            await self.service.handle(self.message)
        self.message.reply.assert_not_called()
        self.assertFalse(affiliate_deals.processed(100))

    async def test_duplicate_concurrent_and_restart_events(self):
        await asyncio.gather(self.service.handle(self.message), self.service.handle(self.message))
        restarted = service.GoCdKeysService()
        restarted.fetch_page = AsyncMock()
        db.init_db()
        await restarted.handle(self.message)
        self.message.reply.assert_awaited_once()
        restarted.fetch_page.assert_not_called()

    async def test_uncertain_send_is_never_retried(self):
        self.message.reply.side_effect = TimeoutError()
        await self.service.handle(self.message)
        await service.GoCdKeysService().handle(self.message)
        self.message.reply.assert_awaited_once()
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM processed_affiliate_deals').fetchone()[0], 'uncertain')

    async def test_unexpected_lookup_failure_does_not_escape(self):
        self.service.fetch_page.side_effect = RuntimeError('test')
        await self.service.handle(self.message)
        self.message.reply.assert_not_called()

    async def test_disabled_no_lookup(self):
        with patch('config.GOCDKEYS_ENABLED', False): await self.service.handle(self.message)
        self.service.fetch_page.assert_not_called()

    def test_normalization_extraction_order_and_edition_identity(self):
        self.assertEqual(service.normalize_game_name('ELDEN RING - Steam'), 'ELDEN RING')
        self.assertEqual(service.normalize_game_name('Cyberpunk 2077 Ultimate Edition PC'), 'Cyberpunk 2077 Ultimate Edition')
        embed = discord.Embed(title='DEAL', description='Wrong Game')
        embed.add_field(name='Price', value='€10.00')
        embed.add_field(name='Game', value='Elden Ring')
        self.message.embeds=[embed]
        self.assertEqual(service.extract_game_name(self.message), 'Elden Ring')
        self.assertFalse(service.matches_page(PAGE, 'Elden Ring Deluxe Edition'))

    def test_url_validation_and_referral_replacement(self):
        self.assertEqual(service.build_affiliate_url(URL+'#old'), URL+'#ref=kas66b')
        for url in ['http://gocdkeys.com/buy-elden-ring-pc-cd-key', 'https://gocdkeys.com.evil/buy-elden-ring-pc-cd-key', 'https://gocdkeys.com/', URL+'?redirect=evil']:
            with self.assertRaises(ValueError): service.build_affiliate_url(url)

    async def test_console_is_not_matched_to_pc(self):
        self.assertIsNone(await self.service.find_game_page('Elden Ring PS5'))
        self.assertFalse(service.matches_page(PAGE, 'Elden Ring', 'PS5'))

    async def test_console_page_platform_is_validated(self):
        self.service.fetch_page.return_value = '<title>Buy Cheap Elden Ring PS5 Code 🏷️ | Gocdkeys</title>'
        self.assertEqual(await self.service.find_game_page('Elden Ring PS5'), 'https://gocdkeys.com/buy-elden-ring-ps5')

    def test_health_status_no_network_or_database_writes(self):
        with db.connect() as conn: before=list(conn.iterdump())
        row=service.status(self.guild, SimpleNamespace(intents=SimpleNamespace(message_content=True)))
        self.assertEqual(row[1], 'PASS')
        self.assertIn('Referral: Configured', row[2])
        with db.connect() as conn: self.assertEqual(before,list(conn.iterdump()))

    async def test_provider_never_scrapes_even_with_legacy_opt_in(self):
        with patch('services.gocdkeys_service.aiohttp.ClientSession') as http:
            result = await service.GoCdKeysService().fetch_page(URL)
        self.assertIsNone(result)
        http.assert_not_called()

    async def test_automatic_mode_is_unsupported_and_preserves_existing_posts(self):
        with patch('config.GOCDKEYS_AUTOMATIC_SUPPORTED', False):
            await self.service.handle(self.message)
            await self.service.handle_delete(self.guild, 2, 100)
            self.assertIsNone(await self.service.find_game_page('Elden Ring'))
            with self.assertRaisesRegex(ValueError, 'unsupported'):
                await self.service.preview_backfill(self.guild)
            self.assertEqual(service.status(self.guild)[1], 'INFO')
            self.assertIn('Unsupported', service.status(self.guild)[2])
            self.service.fetch_page.assert_not_called()
            self.message.reply.assert_not_called()
            self.assertFalse(affiliate_deals.processed(100))

    def test_description_link_and_content_fallbacks(self):
        for embeds, content in [([discord.Embed(description='**Elden Ring**')], ''),
                                 ([], '[Elden Ring](https://example.invalid/product)'),
                                 ([], 'Elden Ring - Steam')]:
            self.message.embeds, self.message.content = embeds, content
            self.assertEqual(service.extract_game_name(self.message), 'Elden Ring')

    async def test_independent_services_share_atomic_claim(self):
        other = service.GoCdKeysService()
        other.fetch_page = AsyncMock(return_value=PAGE)
        await asyncio.gather(self.service.handle(self.message), other.handle(self.message))
        self.message.reply.assert_awaited_once()

    async def test_embed_only_without_price_is_enriched(self):
        self.message.content = ''
        self.message.embeds = [discord.Embed(title='Silent Hill: Townfall')]
        self.service.fetch_page.return_value = '<title>Buy Silent Hill: Townfall Steam Key at best prices | Gocdkeys</title>'
        await self.service.handle(self.message)
        self.message.reply.assert_awaited_once()
        self.assertEqual(affiliate_deals.record(100)['normalized_game'], 'Silent Hill: Townfall')
        self.assertEqual(self.message.reply.call_args.kwargs['view'].children[0].url,
                         'https://gocdkeys.com/buy-silent-hill-townfall-pc-cd-key#ref=kas66b')

    async def test_structured_field_and_trusted_application(self):
        self.message.content = ''
        self.message.embeds = [discord.Embed(title='DEAL')]
        self.message.embeds[0].add_field(name='Game', value='Elden Ring')
        self.message.webhook_id = 123
        self.message.author.id = 123
        self.message.application_id = 42
        await self.service.handle(self.message)
        self.message.reply.assert_awaited_once()

    async def test_untrusted_application_and_self_are_ignored(self):
        self.message.webhook_id = 123
        self.message.application_id = 999
        await self.service.handle(self.message)
        self.message.webhook_id = None
        self.message.author.id = 9
        with patch('config.INSTANT_GAMING_BOT_ID', 9):
            await self.service.handle(self.message)
        self.message.reply.assert_not_called()

    async def test_dealgecko_uses_same_pipeline(self):
        import config
        self.message.author.id = config.DEALGECKO_BOT_ID
        self.message.content = ''
        await self.service.handle(self.message)
        self.message.reply.assert_awaited_once()
        self.assertEqual(affiliate_deals.record(100)['source_key'], 'dealgecko')

    def test_title_formats_preserve_identity(self):
        for raw, expected in [('ELDEN RING - Steam Key', 'ELDEN RING'),
                              ('Cyberpunk 2077 (PC)', 'Cyberpunk 2077'),
                              ('Silent Hill: Townfall', 'Silent Hill: Townfall'),
                              ('EA SPORTS FC™ 27 Ultimate Edition', 'EA SPORTS FC 27 Ultimate Edition'),
                              ('Deal or No Deal', 'Deal or No Deal')]:
            self.assertEqual(service.normalize_game_name(raw), expected)
        self.message.embeds = [discord.Embed(title='Offer')]
        self.message.embeds[0].add_field(name='Unknown', value='Uncertain promotion')
        self.message.content = ''
        self.assertIsNone(service.extract_game_name(self.message))

    def companion(self):
        companion = SimpleNamespace(id=200, author=self.guild.me, reference=SimpleNamespace(message_id=100), content=service.COPY)
        async def edit(**kwargs):
            companion.content = kwargs['content']
        companion.edit = AsyncMock(side_effect=edit)
        companion.delete = AsyncMock()
        self.channel.fetch_message = AsyncMock(return_value=companion)
        return companion

    async def test_changed_title_updates_same_companion_and_mapping(self):
        companion = self.companion()
        await self.service.handle(self.message)
        self.message.embeds = [discord.Embed(title='Silent Hill: Townfall')]
        restarted = service.GoCdKeysService()
        restarted.fetch_page = AsyncMock(return_value='<title>Buy Silent Hill: Townfall Steam Key at best prices | Gocdkeys</title>')
        await restarted.handle(self.message)
        await restarted.handle(self.message)
        companion.edit.assert_awaited_once()
        row = affiliate_deals.record(100)
        self.assertEqual(row['normalized_game'], 'Silent Hill: Townfall')
        self.assertEqual(row['response_message_id'], 200)
        self.assertIn('silent-hill-townfall', row['gocdkeys_url'])
        self.message.reply.assert_awaited_once()

    async def test_uncertain_edit_disables_and_valid_edit_restores(self):
        companion = self.companion()
        await self.service.handle(self.message)
        self.message.embeds = []
        self.message.content = ''
        await self.service.handle(self.message)
        self.assertEqual(affiliate_deals.record(100)['status'], 'disabled')
        self.message.embeds = [discord.Embed(title='ELDEN RING - Steam')]
        await self.service.handle(self.message)
        self.assertEqual(affiliate_deals.record(100)['status'], 'posted')
        self.assertEqual(companion.edit.await_count, 2)
        self.message.reply.assert_awaited_once()

    async def test_delete_is_scoped_owned_and_durable(self):
        companion = self.companion()
        await self.service.handle(self.message)
        await self.service.handle_delete(self.guild, 3, 100)
        companion.delete.assert_not_called()
        await self.service.handle_delete(self.guild, 2, 100)
        await self.service.handle_delete(self.guild, 2, 100)
        await service.GoCdKeysService().handle(self.message)
        companion.delete.assert_awaited_once()
        self.assertEqual(affiliate_deals.record(100)['status'], 'deleted')
        self.message.reply.assert_awaited_once()

    async def test_manual_or_unrelated_companions_are_never_edited_or_deleted(self):
        companion = self.companion()
        await self.service.handle(self.message)
        for field, wrong in [('author', SimpleNamespace(id=777)),
                             ('reference', SimpleNamespace(message_id=999)), ('content', 'Manual message')]:
            old = getattr(companion, field)
            setattr(companion, field, wrong)
            self.message.embeds = []
            self.message.content = ''
            await self.service.handle(self.message)
            await self.service.handle_delete(self.guild, 2, 100)
            setattr(companion, field, old)
        companion.edit.assert_not_called()
        companion.delete.assert_not_called()

    async def test_delete_permission_failure_retains_mapping(self):
        companion = self.companion()
        await self.service.handle(self.message)
        companion.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        await self.service.handle_delete(self.guild, 2, 100)
        self.assertEqual(affiliate_deals.record(100)['status'], 'posted')

    def test_health_reports_missing_posting_permissions_and_intent(self):
        self.channel.permissions_for = lambda member: discord.Permissions.none()
        row = service.status(self.guild, SimpleNamespace(intents=SimpleNamespace(message_content=False)))
        self.assertEqual(row[1], 'WARN')
        self.assertIn('send_messages', row[2])
        self.assertIn('Message Content intent: Disabled', row[2])

    async def test_free_games_scope_even_when_paid(self):
        self.channel.name='free-games'
        self.message.channel=SimpleNamespace(id=3, name='free-games')
        await self.service.handle(self.message)
        self.message.reply.assert_not_called()

    async def test_raw_edits_are_scoped_and_fetch_current_source(self):
        from cogs.gocdkeys import GoCdKeysWatcher
        watcher = GoCdKeysWatcher(SimpleNamespace(get_guild=lambda gid: self.guild))
        watcher.service.handle = AsyncMock()
        self.channel.fetch_message = AsyncMock(return_value=self.message)
        payload = SimpleNamespace(guild_id=1, channel_id=3, message_id=100, data={'content':'€0'})
        await watcher.on_raw_message_edit(payload)
        self.channel.fetch_message.assert_not_called()
        payload.channel_id=2
        await watcher.on_raw_message_edit(payload)
        watcher.service.handle.assert_awaited_once_with(self.message)
        payload.data={'pinned':True}
        await watcher.on_raw_message_edit(payload)
        self.channel.fetch_message.assert_awaited_once_with(100)

    async def test_raw_delete_and_bulk_delete_use_existing_mapping(self):
        from cogs.gocdkeys import GoCdKeysWatcher
        watcher = GoCdKeysWatcher(SimpleNamespace(get_guild=lambda gid: self.guild if gid == 1 else None))
        watcher.service.handle_delete = AsyncMock()
        await watcher.on_raw_message_delete(SimpleNamespace(guild_id=1, channel_id=2, message_id=100))
        watcher.service.handle_delete.assert_awaited_once_with(self.guild, 2, 100)
        await watcher.on_raw_bulk_message_delete(SimpleNamespace(guild_id=1, channel_id=2, message_ids={101, 102}))
        self.assertEqual(watcher.service.handle_delete.await_count, 3)

    async def test_already_missing_companion_retains_deleted_claim(self):
        self.companion()
        await self.service.handle(self.message)
        self.channel.fetch_message.side_effect = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'Missing')
        await self.service.handle_delete(self.guild, 2, 100)
        self.assertEqual(affiliate_deals.record(100)['status'], 'deleted')
        self.assertEqual(affiliate_deals.record(100)['response_message_id'], 200)

    def test_additive_migration_preserves_legacy_delivery_claim(self):
        with db.connect() as conn:
            conn.execute('DROP TABLE processed_affiliate_deals')
            conn.execute('CREATE TABLE processed_affiliate_deals(source_message_id INTEGER PRIMARY KEY,guild_id INTEGER,channel_id INTEGER,gocdkeys_url TEXT,processed_at INTEGER,status TEXT,response_message_id INTEGER)')
            conn.execute('INSERT INTO processed_affiliate_deals VALUES(100,1,2,?,1,\'posted\',200)', (URL,))
        db.init_db()
        row=affiliate_deals.record(100)
        self.assertEqual(row['response_message_id'],200)
        self.assertIn('normalized_game', row)
        self.assertFalse(affiliate_deals.claim(self.message, URL, 'Elden Ring', 'dealgecko'))
