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
                              ('config.GOCDKEYS_ENABLED', True), ('config.GOCDKEYS_REFERRAL_CODE', 'kas66b'),
                              ('config.GUILD_ID', 1), ('config.INSTANT_GAMING_BOT_ID', 42)]:
            patcher = patch(target, value); patcher.start(); self.addCleanup(patcher.stop)
        db.init_db()
        self.channel = SimpleNamespace(id=2, mention='<#2>')
        self.guild = SimpleNamespace(id=1, get_channel=lambda cid: self.channel if cid == 2 else None,
                                     text_channels=[self.channel])
        db.set_setting('managed_channel:1:gaming-deals', '2')
        self.message = SimpleNamespace(id=100, guild=self.guild, channel=self.channel,
            author=SimpleNamespace(id=42, bot=True), webhook_id=None, content='€19.99',
            embeds=[discord.Embed(title='ELDEN RING - Steam')], reply=AsyncMock(return_value=SimpleNamespace(id=200)))
        self.service = service.GoCdKeysService()
        self.service.fetch_page = AsyncMock(return_value=PAGE)

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
        self.assertEqual(service.normalize_game_name('ELDEN RING - Steam'), 'Elden Ring')
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

    async def test_http_policy_blocks_redirects_errors_and_oversized_html(self):
        for status, body, expected in [(200, PAGE.encode(), PAGE), (403, b'Forbidden', None),
                                      (302, PAGE.encode(), None), (200, b'x'*2_000_001, None)]:
            async def chunks(size):
                yield body
            response = SimpleNamespace(status=status, headers={'Content-Type':'text/html'},
                                       content=SimpleNamespace(iter_chunked=chunks))
            request = AsyncMock(); request.__aenter__.return_value=response
            session = MagicMock(); session.get.return_value=request
            context = AsyncMock(); context.__aenter__.return_value=session
            with patch('services.gocdkeys_service.aiohttp.ClientSession', return_value=context):
                result = await service.GoCdKeysService().fetch_page(URL)
            self.assertEqual(result, expected)
            session.get.assert_called_once_with(URL, allow_redirects=False)

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

    async def test_paid_sources_and_free_states(self):
        import config
        for author in (42, config.DEALGECKO_BOT_ID):
            self.message.author.id = author
            for price in ('€0', '€0.00', 'Price = 0', '**FREE**', 'Gratis', '100% OFF', '~~€19.99~~ €0'):
                self.message.content = price
                self.assertEqual(service.price_state(self.message), 'free')
                await self.service.handle(self.message)
        self.message.reply.assert_not_called()
        self.service.fetch_page.assert_not_called()
        self.message.author.id = config.DEALGECKO_BOT_ID
        self.message.content = 'Steam\n€4.99'
        await self.service.handle(self.message)
        self.message.reply.assert_awaited_once()
        self.assertEqual(affiliate_deals.record(100)['source_key'], 'dealgecko')

    def test_positive_currencies_and_title_formats(self):
        for price in ('€4.99', '€19.99', '$9.99', '£12.50', '24,99 EUR'):
            self.message.content = price
            self.assertEqual(service.price_state(self.message), 'paid')
        self.message.content = 'Price unknown'
        self.assertEqual(service.price_state(self.message), 'unknown')
        self.message.content = 'Save €19.99'
        self.assertEqual(service.price_state(self.message), 'unknown')
        for raw, expected in [('Cyberpunk 2077 - Steam Key', 'Cyberpunk 2077'), ('ELDEN RING | 40% OFF','Elden Ring'), ('Hogwarts Legacy (PC)', 'Hogwarts Legacy')]:
            self.assertEqual(service.normalize_game_name(raw), expected)

    async def test_paid_free_paid_edits_reuse_owned_companion(self):
        self.guild.me = SimpleNamespace(id=9)
        companion = SimpleNamespace(id=200, author=self.guild.me, reference=SimpleNamespace(message_id=100), content=service.COPY)
        async def edit(**kwargs):
            companion.content=kwargs['content']
        companion.edit = AsyncMock(side_effect=edit)
        self.channel.fetch_message=AsyncMock(return_value=companion)
        await self.service.handle(self.message)
        self.message.content='FREE'
        await self.service.handle(self.message)
        self.assertEqual(affiliate_deals.record(100)['status'], 'disabled')
        self.assertIsNone(companion.edit.call_args.kwargs['view'])
        self.message.content='€4.99'
        restarted = service.GoCdKeysService()
        restarted.fetch_page = AsyncMock(return_value=PAGE)
        await restarted.handle(self.message)
        self.assertEqual(affiliate_deals.record(100)['status'], 'posted')
        self.assertEqual(companion.edit.call_args.kwargs['view'].children[0].url, URL+'#ref=kas66b')
        self.message.reply.assert_awaited_once()

    async def test_free_to_paid_and_unknown_companion_preserved(self):
        self.message.content='Gratis'
        await self.service.handle(self.message)
        self.assertIsNone(affiliate_deals.record(100))
        self.message.content='$9.99'
        await self.service.handle(self.message)
        self.guild.me=SimpleNamespace(id=9)
        other=SimpleNamespace(author=SimpleNamespace(id=777), edit=AsyncMock())
        self.channel.fetch_message=AsyncMock(return_value=other)
        self.message.content='€0'
        await self.service.handle(self.message)
        other.edit.assert_not_called()

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
