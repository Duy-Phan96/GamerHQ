"""Deterministic request counts; no wall-clock performance assertions or live I/O."""
import unittest
import asyncio
import logging
import discord
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from services import server_operations as ops, managed_message_service as managed
from services import health_service, message_reconciliation as messages, operation_context as contexts
from services.server_service import ServerMessageError, upsert_fixed_message
from database import db
from tests import test_onboarding as fixtures


class RestTextChannel(fixtures.FakeChannel):
    __class__ = discord.TextChannel


class ScanPerformanceTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    def owner(self):
        self.guild.owner_id = 42
        owner = SimpleNamespace(id=42, guild_permissions=discord.Permissions(administrator=True))
        self.guild.members = [owner]
        return owner

    async def test_six_mapped_boards_request_counts(self):
        boards = {}
        for i in range(6):
            key, content = f'performance:{i}', f'Canonical board {i}'
            message = self.add_message(self.intro, content, pinned=True)
            db.set_setting(key, message.id)
            boards[key] = (self.intro, content)
        counts = dict(history=0, pins=0, connections=0)
        history, pins, connect = self.intro.history, self.intro.pins, db.sqlite3.connect
        def counted_connect(*args, **kwargs):
            counts['connections'] += 1
            return connect(*args, **kwargs)
        async def counted_history(**kwargs):
            counts['history'] += 1
            async for item in history(**kwargs): yield item
        async def counted_pins(**kwargs):
            counts['pins'] += 1
            async for item in pins(**kwargs): yield item
        with patch.object(managed, 'canonical_boards', return_value=boards), \
             patch.object(self.intro, 'history', counted_history), patch.object(self.intro, 'pins', counted_pins), \
             patch.object(self.guild, 'fetch_channels', AsyncMock(wraps=self.guild.fetch_channels)) as channels, \
             patch.object(self.intro, 'fetch_message', AsyncMock(wraps=self.intro.fetch_message)) as fetch, \
             patch.object(db.sqlite3, 'connect', side_effect=counted_connect):
            rows = await ops.scan(self.guild, self.bot)
        self.assertEqual(sum(r['kind'] == 'message' and r['status'] == 'EXACT_MATCH' for r in rows), 6)
        self.assertEqual(counts, dict(history=0, pins=0, connections=1))
        self.assertEqual(channels.await_count, 0)
        self.assertEqual(fetch.await_count, 6)

    async def test_missing_mappings_share_one_channel_discovery(self):
        boards = {}
        for i in range(6):
            content = f'Canonical board {i}'
            self.add_message(self.intro, content, pinned=True)
            boards[f'performance:{i}'] = (self.intro, content)
        count = 0
        history = self.intro.history
        async def counted(**kwargs):
            nonlocal count
            count += 1
            async for item in history(**kwargs): yield item
        with patch.object(managed, 'canonical_boards', return_value=boards), patch.object(self.intro, 'history', counted):
            rows = await ops.scan(self.guild, self.bot)
        self.assertEqual(count, 1)
        self.assertEqual(sum(r['kind'] == 'message' and r['status'] == 'SAFE_ADOPTION' for r in rows), 6)

    async def test_fast_health_has_no_network_or_duplicate_scan(self):
        with patch.object(messages, 'audit', AsyncMock(side_effect=AssertionError('deep scan'))), \
             patch.object(self.guild, 'fetch_channels', AsyncMock(side_effect=AssertionError('network'))):
            for channel in self.guild.text_channels:
                channel.fetch_message = AsyncMock(side_effect=AssertionError('message GET'))
            rows = await health_service.scan(self.guild, self.bot, messages=False)
        self.assertTrue(rows)

    async def test_bounded_duplicate_history_never_claims_absence(self):
        message = self.add_message(self.intro, 'Unrelated')
        limits = []
        async def busy(**kwargs):
            limits.append(kwargs['limit'])
            for _ in range(kwargs['limit']): yield message
        with patch.object(self.intro, 'history', busy), self.assertRaises(ServerMessageError):
            await messages.candidates(self.intro, content='Missing')
        self.assertEqual(limits, [101])
        self.assertEqual(self.intro.sends, 0)

    async def test_duplicate_scan_scans_shared_channel_once(self):
        boards = {'test-one': (self.intro, 'One'), 'test-two': (self.intro, 'Two')}
        for content in ('One', 'One', 'Two', 'Two'): self.add_message(self.intro, content)
        count = 0
        original = self.intro.history
        async def history(**kwargs):
            nonlocal count
            count += 1
            async for item in original(**kwargs): yield item
        with patch.object(managed, 'canonical_boards', return_value=boards), patch.object(self.intro, 'history', history):
            rows = await messages.audit(self.guild, bot=self.bot)
        self.assertEqual(count, 1)
        self.assertEqual(sum(row['status'] == 'DUPLICATE' for row in rows), 2)

    async def test_deep_readers_reuse_pins_and_messages_from_channel_snapshot(self):
        message = self.add_message(self.intro, 'Canonical', pinned=True)
        count = 0
        original = self.intro.pins
        async def pins(**kwargs):
            nonlocal count
            count += 1
            async for item in original(**kwargs): yield item
        with contexts.read_scope(), patch.object(self.intro, 'pins', pins), \
             patch.object(self.intro, 'fetch_message', AsyncMock(side_effect=AssertionError('repeated GET'))):
            await messages.pins_snapshot(self.intro)
            await messages.history_snapshot(self.intro)
            self.assertIs(await contexts.fetch_message(self.intro, message.id), message)
        self.assertEqual(count, 1)

    async def test_cached_roles_avoid_fetch_and_valid_ids_avoid_name_discovery(self):
        role = self.guild.roles[1]
        row = dict(key='base:test', kind='role', name='test', label='Test', aliases={'test'}, parent=None, group='Test')
        db.upsert_managed_role(role_id=role.id, role_kind='base', role_key='test', role_group='Test')
        with patch.object(ops, 'definitions', return_value=[row]), \
             patch.object(managed, 'canonical_boards', return_value={}), \
             patch.object(self.guild, 'fetch_roles', AsyncMock(side_effect=AssertionError('roles GET')), create=True), \
             patch('services.role_service.normalize_role_name', side_effect=AssertionError('name scan')):
            result = await ops.scan(self.guild, self.bot)
        self.assertEqual(result[0]['status'], 'EXACT_MATCH')

    async def test_concurrency_coalescing_and_cache_end_at_request_boundary(self):
        active = peak = calls = 0
        async def read():
            nonlocal active, peak, calls
            calls += 1
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return 'value'
        with contexts.read_scope():
            await contexts.gather_reads([contexts.read_once(i, read) for i in range(12)])
            await contexts.gather_reads([contexts.read_once('same', read) for _ in range(6)])
        self.assertEqual(peak, 3)
        self.assertEqual(calls, 13)
        with contexts.read_scope(): await contexts.read_once('same', read)
        self.assertEqual(calls, 14)

    async def test_metrics_count_queries_and_rate_limits_without_values(self):
        db.set_setting('performance:private', 'private-value-sentinel')
        with self.assertLogs('discord.gamerhq.performance', level='INFO') as captured:
            with contexts.operation('offline_fixture') as metrics, db.read_only():
                for _ in range(6): db.get_setting('performance:private')
                record = logging.LogRecord('discord.http', logging.WARNING, '', 0, 'HTTP 429 rate limited', (), None)
                contexts.RateLimitCounter().filter(record)
        self.assertEqual(metrics['db_queries'], 1)
        self.assertEqual(metrics['rate_limits'], 1)
        self.assertNotIn('private-value-sentinel', ' '.join(captured.output))
        self.assertIn('operation=offline_fixture', captured.output[0])

    async def test_unchanged_fixed_message_does_not_patch_discord(self):
        message = self.add_message(self.intro, 'Canonical body')
        message.components = []
        db.set_setting('performance-board', message.id)
        await upsert_fixed_message(self.intro, setting_key='performance-board', content='Canonical body')
        self.assertEqual(message.edits, 0)

    async def test_repair_revalidates_only_selected_board_once(self):
        actor = self.owner()
        boards = {}
        for i in range(3):
            key, content = f'performance:{i}', f'Canonical board {i}'
            msg = self.add_message(self.intro, content)
            db.set_setting(key, msg.id)
            boards[key] = (self.intro, content)
        with patch.object(managed, 'canonical_boards', return_value=boards):
            draft = await ops.preview(self.guild, actor, 'repair', self.bot)
            selected = next(r for r in draft['rows'] if r['key'] == 'performance:0')
            draft['rows'] = [selected]
            with patch.object(self.intro, 'fetch_message', AsyncMock(wraps=self.intro.fetch_message)) as fetch, \
                 patch.object(self.guild, 'fetch_channels', AsyncMock(side_effect=AssertionError('unrelated topology GET'))):
                done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(done, [selected['label']])
        self.assertEqual(skipped, [])
        fetch.assert_awaited_once_with(selected['resource'].id)
        self.assertTrue(selected['resource'].pinned)

    async def test_channel_confirmation_fetches_only_channel_and_parent(self):
        actor = self.owner()
        guide = self.guild.add_channel('guide', self.start)
        db.set_setting('managed_channel:1:guide', guide.id)
        db.set_setting('managed_category:1:start-here', self.start.id)
        with patch.object(managed, 'canonical_boards', return_value={}):
            draft = await ops.preview(self.guild, actor, 'repair', self.bot)
            draft['rows'] = [r for r in draft['rows'] if r['key'] == 'managed_channel:1:guide']
            with patch.object(self.guild, 'fetch_channel', AsyncMock(side_effect=self.guild.get_channel), create=True) as fetch, \
                 patch.object(self.guild, 'fetch_channels', AsyncMock(side_effect=AssertionError('full topology GET'))):
                done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(len(done), 1)
        self.assertEqual(skipped, [])
        self.assertEqual({c.args[0] for c in fetch.await_args_list}, {guide.id, self.start.id})
        self.assertFalse(guide.overwrites_for(self.guild.default_role).send_messages)

    async def test_preview_acknowledges_before_scan_and_reuses_response(self):
        from cogs.server import open_operation
        actor = self.owner()
        interaction = SimpleNamespace(guild=self.guild, user=actor, client=self.bot,
            response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())
        async def preview(*args):
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            self.assertEqual(interaction.edit_original_response.await_count, 1)
            return dict(guild_id=self.guild.id, actor_id=actor.id, mode='reconcile', rows=[])
        with patch.object(ops, 'preview', side_effect=preview):
            await open_operation(interaction, 'reconcile')
        self.assertEqual(interaction.edit_original_response.await_count, 2)

    async def test_http_metrics_preserve_request_arguments_result_and_exception(self):
        request = AsyncMock(return_value='response')
        client = SimpleNamespace(request=request)
        contexts.install_http_metrics(client)
        contexts.install_http_metrics(client)
        with contexts.operation('offline_http') as metrics:
            self.assertEqual(await client.request('route', json={'synthetic': True}), 'response')
            request.assert_awaited_once_with('route', json={'synthetic': True})
            request.side_effect = TimeoutError
            with self.assertRaises(TimeoutError): await client.request('route')
        self.assertEqual(metrics['discord_api_calls'], 2)
        self.assertEqual(request.await_count, 2)  # No custom retries.

    async def test_read_failures_are_coalesced_and_do_not_escape_scope(self):
        read = AsyncMock(side_effect=TimeoutError)
        with contexts.read_scope():
            results = await asyncio.gather(*(contexts.read_once('slow', read) for _ in range(3)), return_exceptions=True)
        self.assertTrue(all(isinstance(r, TimeoutError) for r in results))
        self.assertEqual(read.await_count, 1)
        with contexts.read_scope(), self.assertRaises(TimeoutError):
            await contexts.read_once('slow', read)
        self.assertEqual(read.await_count, 2)

    async def test_database_read_scope_does_not_cache_across_writes(self):
        with db.read_only():
            self.assertIsNone(db.get_setting('performance:new'))
            with db.read_only(): self.assertIsNone(db.get_setting('performance:new'))
        db.set_setting('performance:new', 'current')
        with db.read_only(): self.assertEqual(db.get_setting('performance:new'), 'current')

    async def test_health_command_is_fast_and_details_explicitly_runs_deep_checks(self):
        from cogs.server import ServerAdmin
        from cogs.health import HealthView
        interaction = SimpleNamespace(guild=self.guild, user=self.owner(), client=self.bot,
            response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())
        async def scan(*args, messages):
            interaction.response.defer.assert_awaited_once()
            self.assertEqual(interaction.edit_original_response.await_count, 1)
            return []
        with patch.object(health_service, 'scan', side_effect=scan) as check:
            await ServerAdmin.health.callback(ServerAdmin(self.bot), interaction)
            check.assert_awaited_once_with(self.guild, self.bot, messages=False)
            self.assertEqual(interaction.edit_original_response.await_count, 2)
            interaction.response.defer.reset_mock()
            interaction.edit_original_response.reset_mock()
            check.reset_mock()
            view = HealthView(self.guild, interaction.user.id, [])
            await view.details.callback(interaction)
            check.assert_awaited_once_with(self.guild, self.bot, messages=True)
        self.assertEqual(interaction.edit_original_response.await_count, 2)
        self.assertIn('attachments', interaction.edit_original_response.await_args.kwargs)

    async def test_preview_timeout_finishes_the_same_status_response(self):
        from cogs.server import open_operation
        interaction = SimpleNamespace(guild=self.guild, user=self.owner(), client=self.bot,
            response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())
        with patch.object(ops, 'preview', AsyncMock(side_effect=TimeoutError)):
            await open_operation(interaction, 'repair')
        self.assertEqual(interaction.edit_original_response.await_count, 2)
        self.assertIn('Nothing was changed', interaction.edit_original_response.await_args.kwargs['content'])

    async def test_targeted_confirmation_detects_private_destination_despite_stale_cache(self):
        actor = self.owner()
        guide = self.guild.add_channel('guide', self.start)
        db.set_setting('managed_channel:1:guide', guide.id)
        db.set_setting('managed_category:1:start-here', self.start.id)
        with patch.object(managed, 'canonical_boards', return_value={}):
            draft = await ops.preview(self.guild, actor, 'repair', self.bot)
            draft['rows'] = [r for r in draft['rows'] if r['key'] == 'managed_channel:1:guide']
            changed = fixtures.FakeCategory(self.guild, self.start.name, self.start.id)
            changed.overwrites = {self.guild.default_role: discord.PermissionOverwrite(view_channel=False)}
            fetch = AsyncMock(side_effect=lambda cid: changed if cid == changed.id else self.guild.get_channel(cid))
            with patch.object(self.guild, 'fetch_channel', fetch, create=True):
                done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertFalse(done)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(guide.edits, [])

    async def test_unmapped_adoption_rechecks_candidate_inventory_for_new_ambiguity(self):
        actor = self.owner()
        first = self.guild.add_channel('fixture-board', self.start)
        parent = 'managed_category:1:start-here'
        db.set_setting(parent, self.start.id)
        key = 'managed_channel:1:fixture-board'
        definition = dict(key=key, name='fixture-board', kind='text', label='Fixture',
                          aliases={'fixture-board'}, parent=parent, private=False)
        with patch.object(ops, 'definitions', side_effect=lambda _: [definition.copy()]), \
             patch.object(managed, 'canonical_boards', return_value={}):
            draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
            self.assertEqual(draft['rows'][0]['status'], 'SAFE_ADOPTION')
            duplicate = RestTextChannel(self.guild, first.name, self.start, 999991)
            with patch.object(self.guild, 'fetch_channels', AsyncMock(return_value=[*self.guild.channels, duplicate])) as inventory, \
                 patch.object(self.guild, 'fetch_channel', AsyncMock(side_effect=AssertionError('incomplete discovery')), create=True):
                done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        inventory.assert_awaited_once()
        self.assertFalse(done)
        self.assertEqual(len(skipped), 1)
        self.assertIsNone(db.get_setting(key))

    async def test_cancelled_read_releases_waiters_without_background_work(self):
        started = asyncio.Event()
        finished = asyncio.Event()
        async def read():
            started.set()
            try: await asyncio.Event().wait()
            finally: finished.set()
        with contexts.read_scope():
            owner = asyncio.create_task(contexts.read_once('cancelled', read))
            await started.wait()
            waiter = asyncio.create_task(contexts.read_once('cancelled', read))
            owner.cancel()
            results = await asyncio.gather(owner, waiter, return_exceptions=True)
        self.assertTrue(finished.is_set())
        self.assertTrue(all(isinstance(r, asyncio.CancelledError) for r in results))
