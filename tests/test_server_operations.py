"""Separate operational intents over disposable SQLite and Discord fakes."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import discord
from database import db
from services import server_operations as ops, message_reconciliation as duplicates, health_service
from services.server_service import ServerMessageError
from tests import test_onboarding as fixtures


class OperationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    def owner(self):
        self.guild.owner_id = 42
        actor = SimpleNamespace(id=42, guild_permissions=discord.Permissions(administrator=True))
        self.guild.members = [actor]
        return actor

    async def test_fresh_database_links_existing_structure_without_discord_writes(self):
        actor = self.owner()
        before = {c.id for c in self.guild.channels}
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        row = next(r for r in draft['rows'] if r['name'] == 'looking-for-group')
        self.assertEqual(row['status'], 'AMBIGUOUS')  # Existing LFG is in COMMUNITY, not START HERE.
        channel = row['candidates'][0]
        done, _ = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices={row['key']: channel.id})
        self.assertTrue(done)
        self.assertEqual(db.get_setting(row['key']), str(channel.id))
        self.assertEqual(before, {c.id for c in self.guild.channels})
        self.assertFalse(any(c.edits or c.sends for c in self.guild.text_channels))
        again = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        done, _ = await ops.apply(self.guild, actor, again, self.bot, confirmed=True)
        self.assertEqual(done, [])

    async def test_ambiguous_channel_selection_persists_only_chosen_id(self):
        actor = self.owner()
        events = self.guild.add_category('EVENTS')
        other = self.guild.add_category('KAMEX')
        first = self.guild.add_channel('community-events', events)
        second = self.guild.add_channel('community-events', other)
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        row = next(r for r in draft['rows'] if r['name'] == 'community-events')
        self.assertEqual(row['status'], 'AMBIGUOUS')
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices={row['key']: second.id})
        self.assertEqual(db.get_setting(row['key']), str(second.id))
        self.assertEqual(first.edits + second.edits, [])

    async def test_duplicate_scan_survives_unrelated_structure_warning(self):
        self.guild.add_channel('community-events', self.staff)
        from cogs.server import future_community_copies
        channel = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        for _ in range(2): self.add_message(channel, future_community_copies()['giveaways'])
        rows = await duplicates.audit(self.guild, self.owner(), bot=self.bot)
        self.assertEqual(sum(r['status'] == 'DUPLICATE' for r in rows), 1)
        self.assertTrue(any(r['key'] == 'structure' for r in rows))
        draft = await duplicates.preview(self.guild, self.owner(), 'server_future_giveaways_message_id', bot=self.bot)
        self.assertEqual(len(draft['ids']), 2)

    async def test_reconcile_messages_never_deletes_and_links_explicit_choice(self):
        actor = self.owner()
        from cogs.server import future_community_copies
        channel = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        first, second = [self.add_message(channel, future_community_copies()['giveaways']) for _ in range(2)]
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        key = 'server_future_giveaways_message_id'
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices={key: first.id})
        self.assertEqual(db.get_setting(key), str(first.id))
        self.assertFalse(first.deleted or second.deleted)
        self.assertEqual(first.edits + second.edits, 0)

    async def test_repair_only_known_resources_and_repeated_repair_idempotent(self):
        actor = self.owner()
        guide = self.guild.add_channel('guide', self.community)
        unknown = self.guild.add_channel('manual', self.community)
        db.set_setting('managed_channel:1:guide', guide.id)
        db.set_setting('managed_category:1:start-here', self.start.id)
        before = {c.id for c in self.guild.channels}
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(guide.category, self.start)
        self.assertFalse(guide.overwrites_for(self.guild.default_role).send_messages)
        self.assertFalse(unknown.edits)
        self.assertEqual(before, {c.id for c in self.guild.channels})
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        done, _ = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(done, [])

    async def test_setup_skips_existing_ambiguous_resource(self):
        actor = self.owner()
        self.guild.add_channel('community-events', self.staff)
        draft = await ops.preview(self.guild, actor, 'setup', self.bot)
        # Isolate this reviewed resource so the test specifically checks ambiguity.
        draft['rows'] = [r for r in draft['rows'] if r['name'] == 'community-events']
        before = {c.id for c in self.guild.channels}
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(before, {c.id for c in self.guild.channels})

    async def test_setup_creates_missing_category_channel_role_and_message_once(self):
        actor = self.owner()
        draft = await ops.preview(self.guild, actor, 'setup', self.bot)
        draft['rows'] = [r for r in draft['rows'] if r['name'] in ('events', 'community-events', 'english')]
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(db.get_setting('managed_channel:1:community-events'))
        again = await ops.preview(self.guild, actor, 'setup', self.bot)
        again['rows'] = [r for r in again['rows'] if r['key'] == 'community_events:1']
        await ops.apply(self.guild, actor, again, self.bot, confirmed=True)
        cid = int(db.get_setting('managed_channel:1:community-events'))
        channel = self.guild.get_channel(cid)
        self.assertEqual(channel.sends, 1)
        again = await ops.preview(self.guild, actor, 'setup', self.bot)
        again['rows'] = [r for r in again['rows'] if r['key'] == 'community_events:1']
        await ops.apply(self.guild, actor, again, self.bot, confirmed=True)
        self.assertEqual(channel.sends, 1)

    async def test_health_and_scan_are_database_read_only(self):
        self.owner()
        before = db.DB_PATH.read_bytes()
        await health_service.scan(self.guild, self.bot)
        await ops.scan(self.guild, self.bot)
        self.assertEqual(before, db.DB_PATH.read_bytes())
        with db.read_only(), self.assertRaises(Exception): db.set_setting('forbidden', 'change')

    async def test_confirmation_auth_and_changed_candidate_are_rechecked(self):
        actor = self.owner()
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        with self.assertRaises(ServerMessageError):
            await ops.apply(self.guild, actor, draft, self.bot)
        self.start.name = 'Changed while preview was open'
        done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(skipped)
        self.assertIsNone(db.get_setting('managed_category:1:start-here'))
        self.guild.members = []
        with self.assertRaises(ServerMessageError):
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)

    async def test_private_category_cannot_be_made_public_by_generic_repair(self):
        actor = self.owner()
        market = self.guild.add_category('MARKETPLACE')
        market.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        db.set_setting('managed_category:1:partners-benefits', market.id)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        _, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(any('private category' in s for s in skipped))
        self.assertIs(market.overwrites_for(self.guild.default_role).view_channel, False)

    async def test_ambiguous_choice_ui_cancel_and_auth_do_not_write(self):
        from cogs.server import OperationsView, MappingChoiceView, ResourceChoice
        actor = self.owner()
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        parent = OperationsView(self.guild, draft, self.bot)
        row = next(r for r in draft['rows'] if r['status'] == 'AMBIGUOUS' and r['safe'])
        parent.page = draft['rows'].index(row) // 8
        parent.refresh_choices()
        self.assertTrue(any(isinstance(c, ResourceChoice) for c in parent.children))
        choice = MappingChoiceView(parent, row)
        interaction = SimpleNamespace(guild=self.guild, user=SimpleNamespace(id=99), response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock(), edit_message=AsyncMock()))
        self.assertFalse(await choice.interaction_check(interaction))
        interaction.user = actor
        self.assertTrue(await choice.interaction_check(interaction))
        await choice.cancel.callback(interaction)
        self.assertIsNone(db.get_setting(row['key']))
        self.assertFalse(await choice.interaction_check(interaction))

    async def test_repair_pins_same_message_and_preserves_customized_body(self):
        from services import managed_message_service as managed
        from services.community_structure_service import EVENTS_INTRO
        actor = self.owner()
        events = self.guild.add_category('EVENTS')
        channel = self.guild.add_channel('community-events', events)
        db.set_setting('managed_channel:1:community-events', channel.id)
        db.set_setting('managed_category:1:events', events.id)
        message = self.add_message(channel, EVENTS_INTRO)
        db.set_setting('community_events:1', message.id)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(message.pinned)
        state = managed.load('community_events:1')
        self.assertIsNotNone(state)
        state.update(content='Custom owner text', customized=True, content_hash=managed.digest('Custom owner text'))
        managed.store(state)
        message.content, message.pinned = state['content'], False
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(message.pinned)
        self.assertEqual(message.content, 'Custom owner text')
        self.assertEqual(channel.sends, 0)

    async def test_destination_changes_after_preview_block_repair(self):
        actor = self.owner()
        channel = self.guild.add_channel('guide', self.community)
        db.set_setting('managed_channel:1:guide', channel.id)
        db.set_setting('managed_category:1:start-here', self.start.id)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        self.start.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(channel.category_id, self.community.id)

    async def test_configured_selector_id_prevents_duplicate_after_rename(self):
        import config
        actor = self.owner()
        channel = next(c for c in self.guild.text_channels if c.name == 'choose-your-games')
        channel.name = 'My renamed selector'
        with patch.object(config, 'CHOOSE_GAMES_CHANNEL_ID', channel.id):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot)
            row = next(r for r in draft['rows'] if r['name'] == 'choose-your-games')
            self.assertEqual([c.id for c in row['candidates']], [channel.id])
            draft['rows'] = [row]
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertFalse(channel.edits)
        self.assertIsNone(db.get_setting(row['key']))

    async def test_repair_event_order_only_moves_mapped_channels_and_is_repeatable(self):
        actor = self.owner()
        events = self.guild.add_category('EVENTS')
        names = ['giveaways', 'manual-event', 'tournaments', 'community-events']
        for pos, name in enumerate(names):
            channel = self.guild.add_channel(name, events)
            channel.position = pos
            if name != 'manual-event': db.set_setting(f'managed_channel:1:{name}', channel.id)
        db.set_setting('managed_category:1:events', events.id)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        rows = [r for r in draft['rows'] if r['kind'] == 'order']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['changed'])
        unknown = next(c for c in self.guild.channels if c.name == 'manual-event')
        self.assertNotIn(unknown.id, [r['id'] for r in rows[0]['positions']])
        draft['rows'] = rows
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        again = await ops.preview(self.guild, actor, 'repair', self.bot)
        again['rows'] = [r for r in again['rows'] if r['kind'] == 'order']
        done, _ = await ops.apply(self.guild, actor, again, self.bot, confirmed=True)
        self.assertEqual(done, [])

    async def test_unavailable_generated_content_never_clears_existing_message(self):
        from services import managed_message_service as managed
        actor = self.owner()
        message = self.add_message(self.intro, 'Known body')
        db.set_setting('test-board', message.id)
        def boards(*args, defaults=False, **kwargs):
            return {'test-board': (self.intro, None if defaults else 'Known body')}
        with patch.object(managed, 'canonical_boards', side_effect=boards):
            draft = await ops.preview(self.guild, actor, 'repair', self.bot)
            self.assertTrue(any('default content unavailable' in r['label'] for r in draft['rows']))
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(message.content, 'Known body')
        self.assertEqual(message.edits, 0)

    async def test_fresh_rest_category_privacy_wins_over_stale_gateway_cache(self):
        import copy
        actor = self.owner()
        guide = self.guild.add_channel('guide', self.community)
        db.set_setting('managed_channel:1:guide', guide.id)
        db.set_setting('managed_category:1:start-here', self.start.id)
        rest_category = copy.copy(self.start)
        rest_category.overwrites = {self.guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        snapshot = [rest_category if c.id == self.start.id else c for c in self.guild.channels]
        with patch.object(self.guild, 'fetch_channels', AsyncMock(return_value=snapshot)):
            draft = await ops.preview(self.guild, actor, 'repair', self.bot)
            row = next(r for r in draft['rows'] if r['name'] == 'guide')
            self.assertEqual(row['status'], 'AMBIGUOUS')
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertFalse(guide.edits)

    async def test_message_repair_registers_rest_result_before_gateway_cache_updates(self):
        import copy
        from services import managed_message_service as managed
        from services.community_structure_service import EVENTS_INTRO
        actor = self.owner()
        events = self.guild.add_category('EVENTS')
        channel = self.guild.add_channel('community-events', events)
        db.set_setting('managed_channel:1:community-events', channel.id)
        db.set_setting('managed_category:1:events', events.id)
        message = self.add_message(channel, EVENTS_INTRO.split('\n')[0] + '\nOld generated text')
        db.set_setting('community_events:1', message.id)
        returned = copy.copy(message)
        returned.content = EVENTS_INTRO
        message.edit = AsyncMock(return_value=returned)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        draft['rows'] = [r for r in draft['rows'] if r['key'] == 'community_events:1']
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        state = managed.load('community_events:1')
        self.assertEqual(state['content'], EVENTS_INTRO)
        self.assertEqual(state['message_id'], message.id)
        self.assertTrue(returned.pinned)
        self.assertEqual(channel.sends, 0)
