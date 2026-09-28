"""Global audit and explicit cleanup exercise real feature keys/renderers offline."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from database import db
from cogs import server
from services import message_reconciliation as reconcile, managed_message_service as managed
from services import game_service, command_guide_service, streamer_hub_service, server_setup_service
from services.server_service import ServerMessageError
from tests import test_onboarding as fixtures


class TextChannel(fixtures.FakeChannel):
    __class__ = discord.TextChannel


class GlobalDuplicateTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    def owner(self):
        self.guild.owner_id = 42
        owner = SimpleNamespace(id=42, guild_permissions=discord.Permissions(administrator=True))
        self.guild.members = [owner]
        return owner

    def lfg(self):
        channel = next(c for c in self.guild.text_channels if c.name == 'looking-for-group')
        channel.__class__ = TextChannel
        return channel, server.setting_key_for(channel), server.default_copy_for(channel)

    async def test_global_lfg_giveaway_unique_and_unknown_messages(self):
        channel, key, content = self.lfg()
        originals = [self.add_message(channel, content, pinned=True) for _ in range(2)]
        human = self.add_message(channel, content, author=12)
        other_bot = self.add_message(channel, content, author=901)
        manual = self.add_message(channel, 'My manual Looking for Group notice')
        wrong_channel = self.add_message(self.intro, content)
        giveaway = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        for _ in range(2):
            self.add_message(giveaway, server.future_community_copies()['giveaways'])
        from services.onboarding_service import welcome_text
        welcome = self.add_message(self.old, welcome_text(self.guild))
        rows = await reconcile.audit(self.guild, self.owner(), bot=self.bot)
        self.assertEqual({r['key'] for r in rows if r['status'] == 'DUPLICATE'},
                         {key, 'server_future_giveaways_message_id'})
        self.assertEqual(next(r for r in rows if r['channel_id'] == self.old.id)['status'], 'UNIQUE')
        for message in [*originals, human, other_bot, manual, wrong_channel, welcome]:
            self.assertFalse(message.deleted)
            self.assertEqual(message.edits, 0)
        self.assertIsNone(db.get_setting(key))
        health = await reconcile.diagnostics(self.guild, self.bot)
        self.assertIn(('Managed Messages', 'MANUAL_REVIEW', '2 duplicate groups detected. Run /server message-duplicates.'), health)

    async def test_lfg_original_kept_setup_no_third_and_repeat_scan_empty(self):
        channel, key, content = self.lfg()
        first, second = [self.add_message(channel, content, pinned=True) for _ in range(2)]
        view = server.managed_channel_view(channel)
        first.components = second.components = [SimpleNamespace(to_dict=lambda: row) for row in view.to_components()]
        db.set_setting(key, second.id)
        owner = self.owner()
        # The actual setup path must report review and leave both alone.
        _, failures = await server_setup_service.repair_server(self.guild, self.bot)
        self.assertTrue(any('Duplicate' in failure for failure in failures), failures)
        self.assertEqual(channel.sends, 0)
        draft = await reconcile.preview(self.guild, owner, key, bot=self.bot)
        self.assertEqual(draft['recommended'], second.id)
        self.assertIn('persisted DB', draft['reason'])
        self.assertEqual(draft['metadata'][1]['mapped'], True)
        before_controls = reconcile.controls(first)
        await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True, bot=self.bot)
        self.assertTrue(second.deleted)
        self.assertFalse(first.deleted)
        self.assertTrue(first.pinned)
        self.assertEqual(reconcile.controls(first), before_controls)
        self.assertEqual(db.get_setting(key), str(first.id))
        await server_setup_service.repair_server(self.guild, self.bot)
        await server_setup_service.repair_server(self.guild, self.bot)
        self.assertEqual(channel.sends, 0)
        self.assertEqual(list(channel.messages), [first.id])
        self.assertIsNotNone(first.view)
        self.assertFalse(any(r['status'] == 'DUPLICATE' for r in await reconcile.audit(self.guild, owner, bot=self.bot)))
        with self.assertRaises(ServerMessageError):
            await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True, bot=self.bot)

    async def test_exact_content_preferred_over_older_line_ending_variant(self):
        channel, key, content = self.lfg()
        older = self.add_message(channel, content.replace('\n', '\r\n'))
        exact = self.add_message(channel, content)
        draft = await reconcile.preview(self.guild, self.owner(), key)
        self.assertEqual(draft['recommended'], exact.id)
        self.assertIn('Exact canonical', draft['reason'])
        self.assertEqual(draft['ids'], [older.id, exact.id])
        await reconcile.confirm(self.guild, self.owner(), draft, older.id, confirmed=True)
        await server.refresh_lfg_guide_message(self.guild)
        self.assertEqual(channel.sends, 0)
        self.assertEqual(older.content, content)

    async def test_untrusted_customization_metadata_blocks_deletion(self):
        channel, key, content = self.lfg()
        for _ in range(2):
            self.add_message(channel, content)
        managed.store(dict(key=key, guild_id=1, channel_id=channel.id, content=content,
                           message_id=next(iter(channel.messages)), content_hash='invalid', pending=False))
        with self.assertRaisesRegex(ServerMessageError, 'customization'):
            await reconcile.preview(self.guild, self.owner(), key)
        self.assertFalse(any(m.deleted for m in channel.messages.values()))

    async def test_cleanup_audit_fingerprint_is_selected_payload(self):
        channel, key, content = self.lfg()
        self.add_message(channel, content.replace('\n', '\r\n'))
        selected = self.add_message(channel, content)
        owner = self.owner()
        draft = await reconcile.preview(self.guild, owner, key)
        await reconcile.confirm(self.guild, owner, draft, selected.id, confirmed=True)
        with db.connect() as conn:
            row = conn.execute('SELECT before_hash, after_hash FROM managed_message_audit').fetchone()
        self.assertEqual(row['before_hash'], reconcile.fingerprint(selected))
        self.assertEqual(row['after_hash'], reconcile.fingerprint(selected))

    async def test_support_legacy_alias_tracks_kept_overview_only(self):
        from services.onboarding_service import migrate_onboarding
        await migrate_onboarding(self.guild)
        key = 'partner_message:1:intro'
        state = managed.load(key)
        channel = self.guild.get_channel(state['channel_id'])
        original = channel.messages[state['message_id']]
        duplicate = self.add_message(channel, original.content)
        db.set_setting('support_message:1', duplicate.id)
        owner = self.owner()
        draft = await reconcile.preview(self.guild, owner, key)
        await reconcile.confirm(self.guild, owner, draft, original.id, confirmed=True)
        self.assertEqual(db.get_setting('support_message:1'), str(original.id))
        self.assertTrue(duplicate.deleted)

    async def test_identical_tie_and_large_group_require_individual_confirmations(self):
        channel, key, content = self.lfg()
        messages = [self.add_message(channel, content) for _ in range(3)]
        owner = self.owner()
        draft = await reconcile.preview(self.guild, owner, key)
        self.assertEqual(draft['recommended'], messages[0].id)
        await reconcile.confirm(self.guild, owner, draft, messages[0].id, confirmed=True)
        self.assertEqual(sum(m.deleted for m in messages), 1)
        next_draft = await reconcile.preview(self.guild, owner, key)
        await reconcile.confirm(self.guild, owner, next_draft, messages[0].id, confirmed=True)
        self.assertEqual(list(channel.messages), [messages[0].id])

    async def test_different_controls_are_manual_review_without_recommendation(self):
        channel, key, content = self.lfg()
        self.add_message(channel, content)
        changed = self.add_message(channel, content)
        changed.components = [SimpleNamespace(to_dict=lambda: {'custom_id': 'unrecognized'})]
        row = (await reconcile.audit(self.guild, self.owner(), managed_key=key))[0]
        self.assertEqual(row['status'], 'DUPLICATE')
        self.assertFalse(row['safe'])
        self.assertIsNone(row['recommended'])
        with self.assertRaisesRegex(ServerMessageError, 'MANUAL_REVIEW'):
            await reconcile.preview(self.guild, self.owner(), key)
        self.assertFalse(changed.deleted)

    async def test_group_change_wrong_channel_and_expiry_rejected(self):
        channel, key, content = self.lfg()
        first, second = [self.add_message(channel, content) for _ in range(2)]
        owner = self.owner()
        draft = await reconcile.preview(self.guild, owner, key)
        third = self.add_message(channel, content)
        with self.assertRaisesRegex(ServerMessageError, 'State changed'):
            await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True)
        await third.delete()
        second.channel = self.intro
        with self.assertRaises(ServerMessageError):
            await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True)
        second.channel = channel
        draft['created'] -= 181
        with self.assertRaises(ServerMessageError):
            await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True)
        self.assertFalse(first.deleted or second.deleted)

    async def test_game_sections_use_existing_json_mapping_and_preserve_other_entries(self):
        channel = next(c for c in self.guild.text_channels if c.name == 'choose-your-games')
        title = '## Synthetic Games'
        slot = game_service._choose_games_section_key(title)
        key = reconcile.SECTION_PREFIX + slot
        first, second = [self.add_message(channel, title) for _ in range(2)]
        db.set_setting('choose_games_section_message_ids', json.dumps({slot: second.id, 'other': 990001}))
        with patch.object(game_service, 'CHOOSE_GAMES_CHANNEL_ID', channel.id), patch.object(game_service, 'build_choose_games_sections', return_value=[(title, [])]):
            owner = self.owner()
            draft = await reconcile.preview(self.guild, owner, key)
            self.assertEqual(draft['recommended'], second.id)
            await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True)
        self.assertEqual(json.loads(db.get_setting('choose_games_section_message_ids')), {slot: first.id, 'other': 990001})
        self.assertIsNone(db.get_setting(key))
        self.assertTrue(second.deleted)

    async def test_game_section_shared_reference_blocks_cleanup(self):
        channel = next(c for c in self.guild.text_channels if c.name == 'choose-your-games')
        title = '## Synthetic Games'
        slot = game_service._choose_games_section_key(title)
        first, second = [self.add_message(channel, title) for _ in range(2)]
        db.set_setting('choose_games_section_message_ids', json.dumps({slot: first.id, 'other': second.id}))
        with patch.object(game_service, 'CHOOSE_GAMES_CHANNEL_ID', channel.id), patch.object(game_service, 'build_choose_games_sections', return_value=[(title, [])]):
            with self.assertRaisesRegex(ServerMessageError, 'referenced elsewhere'):
                await reconcile.preview(self.guild, self.owner(), reconcile.SECTION_PREFIX + slot)
        self.assertFalse(first.deleted or second.deleted)

    async def test_registry_includes_generated_guides_and_hub_without_writes(self):
        staff = self.guild.add_channel('mod-commands', self.staff)
        hub = self.guild.add_channel('streamer-guide', self.streamers)
        db.set_setting(command_guide_service.STAFF_GUIDE_CHANNEL_KEY, staff.id)
        db.set_setting('managed_channel:1:streamer-guide', hub.id)
        for channel, content in [(staff, command_guide_service.staff_command_pages(self.bot, self.guild)[0][1]),
                                 (hub, streamer_hub_service.guide_text(self.guild))]:
            for _ in range(2):
                self.add_message(channel, content)
        rows = await reconcile.audit(self.guild, self.owner(), bot=self.bot)
        self.assertEqual({r['key'] for r in rows if r['status'] == 'DUPLICATE'},
                         {command_guide_service.STAFF_GUIDE_MESSAGE_KEY, 'streamer_guide_message_id'})
        self.assertEqual(staff.sends + hub.sends, 0)

    async def test_editor_registry_extension_automatically_enters_global_audit(self):
        key = 'synthetic_new_board:1'
        db.set_setting('synthetic_channel:1', self.intro.id)
        managed.store(dict(key=key, guild_id=1, channel_id=self.intro.id, message_id=99999,
                           content='New registered board', label='Synthetic', customized=True, version=1,
                           content_hash=managed.digest('New registered board'), pending=False))
        for _ in range(2):
            self.add_message(self.intro, 'New registered board')
        original = managed.specs
        with patch.object(managed, 'specs', side_effect=lambda guild: {**original(guild), key: ('Synthetic', 'synthetic_channel:1', [])}):
            rows = await reconcile.audit(self.guild, self.owner())
        self.assertTrue(any(r['key'] == key and r['status'] == 'DUPLICATE' for r in rows))

    async def test_optional_command_pagination_and_actor_bound_skip(self):
        self.assertFalse(server.ServerAdmin.message_duplicates.parameters[0].required)
        channel, key, content = self.lfg()
        for _ in range(2):
            self.add_message(channel, content)
        owner = self.owner()
        rows = await reconcile.audit(self.guild, owner, bot=self.bot)
        view = server.DuplicateAuditView(self.guild, owner.id, rows * 4, self.bot)
        self.assertLess(len(view.text()), 2000)
        draft = await reconcile.preview(self.guild, owner, key)
        text = server.duplicate_review_text(draft)
        self.assertIn('Created:', text)
        self.assertIn('Current DB mapping:', text)
        self.assertIn('Candidate B', text)
        review = server.DuplicateMessageView(self.guild, draft, bot=self.bot)
        interaction = SimpleNamespace(user=owner, guild=self.guild,
                                      response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                                      edit_original_response=AsyncMock())
        await review.skip.callback(interaction)
        self.assertTrue(review.finished)
        self.assertFalse(any(m.deleted for m in channel.messages.values()))
        self.assertIsNone(db.get_setting(key))
