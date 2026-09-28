"""Fresh/stale runtime state and destructive cleanup use synthetic offline data."""
import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from database import db
from services import message_reconciliation as reconcile, role_service
from services.server_service import upsert_fixed_message, ServerMessageError
from tests import test_onboarding as fixtures
from tests import test_production as production_fixtures
from tools import import_database as importer


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    def board(self):
        from cogs.server import future_community_copies
        channel = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        return channel, 'server_future_giveaways_message_id', future_community_copies()['giveaways']

    def admin(self):
        self.guild.owner_id = 42
        owner = SimpleNamespace(id=42, guild_permissions=discord.Permissions(administrator=True))
        self.guild.members = [owner]
        return owner

    async def test_fresh_and_stale_mapping_adopt_existing_no_duplicate(self):
        channel, key, content = self.board()
        message = self.add_message(channel, content, pinned=True)
        for raw in ('', '999999'):
            db.set_setting(key, raw)
            result = await upsert_fixed_message(channel, setting_key=key, content=content, pin=True)
            self.assertEqual(result.id, message.id)
            self.assertEqual(db.get_setting(key), str(message.id))
        self.assertEqual(channel.sends, 0)
        self.assertEqual(len(channel.messages), 1)

    async def test_missing_concurrent_retries_create_only_one(self):
        channel, key, content = self.board()
        await asyncio.gather(*(upsert_fixed_message(channel, setting_key=key, content=content, pin=True) for _ in range(3)))
        self.assertEqual(channel.sends, 1)

    async def test_duplicates_even_with_valid_mapping_require_review(self):
        channel, key, content = self.board()
        first = self.add_message(channel, content, pinned=True)
        second = self.add_message(channel, content, pinned=True)
        for raw in ('', str(first.id), '999999'):
            db.set_setting(key, raw)
            with self.assertRaisesRegex(ServerMessageError, 'MANUAL_REVIEW.*Duplicate'):
                await upsert_fixed_message(channel, setting_key=key, content=content, pin=True)
        self.assertEqual((first.edits, second.edits, channel.sends), (0, 0, 0))
        self.assertFalse(first.deleted or second.deleted)

    async def test_unknown_manual_messages_preserved(self):
        channel, key, content = self.board()
        human = self.add_message(channel, content, author=99, pinned=True)
        unknown = self.add_message(channel, 'Unrelated bot message', pinned=True)
        await upsert_fixed_message(channel, setting_key=key, content=content, pin=True)
        self.assertFalse(human.deleted or unknown.deleted)
        self.assertEqual(human.edits + unknown.edits, 0)
        self.assertEqual(channel.sends, 1)

    async def test_confirmation_keeps_selected_and_repair_reuses_it(self):
        channel, key, content = self.board()
        first, second = [self.add_message(channel, content, pinned=True) for _ in range(2)]
        owner = self.admin()
        draft = await reconcile.preview(self.guild, owner, key)
        with self.assertRaises(ServerMessageError):
            await reconcile.confirm(self.guild, owner, draft, second.id)
        await reconcile.confirm(self.guild, owner, draft, second.id, confirmed=True)
        self.assertTrue(first.deleted)
        self.assertFalse(second.deleted)
        await upsert_fixed_message(channel, setting_key=key, content=content, pin=True)
        self.assertEqual(db.get_setting(key), str(second.id))
        self.assertEqual(channel.sends, 0)
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM managed_message_audit').fetchone()[0], 1)

    async def test_cleanup_refuses_reference_changed_content_and_revoked_admin(self):
        channel, key, content = self.board()
        first, second = [self.add_message(channel, content, pinned=True) for _ in range(2)]
        owner = self.admin()
        draft = await reconcile.preview(self.guild, owner, key)
        db.set_setting('another_message', second.id)
        with self.assertRaisesRegex(ServerMessageError, 'referenced elsewhere'):
            await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True)
        db.set_setting('another_message', '')
        second.content = 'Changed after preview'
        with self.assertRaises(ServerMessageError):
            await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True)
        second.content = content
        self.guild.members = []
        with self.assertRaises(ServerMessageError):
            await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True)
        self.assertFalse(first.deleted or second.deleted)

    async def test_cleanup_refuses_buttons_differences(self):
        channel, key, content = self.board()
        first, second = [self.add_message(channel, content, pinned=True) for _ in range(2)]
        second.components = [SimpleNamespace(to_dict=lambda: {'different': 'controls'})]
        with self.assertRaisesRegex(ServerMessageError, 'differ'):
            await reconcile.preview(self.guild, self.admin(), key)

    async def test_cancel_during_confirm_does_not_report_false_cancellation(self):
        from cogs.server import DuplicateMessageView
        owner = self.admin()
        view = DuplicateMessageView(self.guild, {'actor_id': owner.id})
        view.finished = True
        interaction = SimpleNamespace(user=owner, response=SimpleNamespace(
            send_message=AsyncMock(), edit_message=AsyncMock()))
        await view.cancel.callback(interaction)
        interaction.response.edit_message.assert_not_awaited()
        self.assertIn('processing or closed', interaction.response.send_message.call_args.args[0])

    async def test_uncertain_delete_keeps_selected_mapping_without_retry(self):
        channel, key, content = self.board()
        first, second = [self.add_message(channel, content, pinned=True) for _ in range(2)]
        owner = self.admin()
        draft = await reconcile.preview(self.guild, owner, key)
        failure = discord.HTTPException(SimpleNamespace(status=500, reason='Uncertain'), 'synthetic')
        with patch.object(second, 'delete', AsyncMock(side_effect=failure)) as delete:
            with self.assertRaisesRegex(ServerMessageError, 'unconfirmed'):
                await reconcile.confirm(self.guild, owner, draft, first.id, confirmed=True)
            self.assertEqual(delete.await_count, 1)
        self.assertEqual(db.get_setting(key), str(first.id))
        self.assertFalse(first.deleted)

    async def test_cleanup_updates_registered_state_and_preserves_custom_content(self):
        from services.onboarding_service import migrate_onboarding
        from services import managed_message_service as managed
        await migrate_onboarding(self.guild)
        key = 'central_guide:1'
        state = managed.load(key)
        channel = self.guild.get_channel(state['channel_id'])
        message = channel.messages[state['message_id']]
        state.update(customized=True, content='Custom canonical guide', content_hash=managed.digest('Custom canonical guide'))
        managed.store(state)
        message.content = state['content']
        second = self.add_message(channel, state['content'], pinned=True)
        owner = self.admin()
        draft = await reconcile.preview(self.guild, owner, key)
        await reconcile.confirm(self.guild, owner, draft, second.id, confirmed=True)
        updated = managed.load(key)
        self.assertEqual(updated['message_id'], second.id)
        self.assertEqual(updated['version'], state['version'] + 1)
        self.assertEqual(updated['content'], state['content'])
        self.assertTrue(updated['customized'])

    async def test_private_lookalike_and_duplicate_category_never_adopt(self):
        from services.community_structure_service import core_channel, core_category
        self.guild.add_channel('guide', self.staff)
        with self.assertRaises(ServerMessageError):
            core_channel(self.guild, 'guide')
        self.guild.add_category('START HERE')
        with self.assertRaises(ServerMessageError):
            core_category(self.guild, 'start-here')

    async def test_selector_duplicate_is_not_deleted_or_edited(self):
        from services import game_service
        from cogs import games
        from tests.test_stability import message
        from unittest.mock import MagicMock
        first = message(100, game_service.build_choose_games_message(), games.ChooseGamesButtons())
        second = message(101, first.content, games.ChooseGamesButtons())
        channel = MagicMock(spec=discord.TextChannel)
        channel.guild.me.id = 99
        async def history(**kwargs):
            for item in (first, second):
                yield item
        channel.history = history
        with patch.object(game_service, 'CHOOSE_GAMES_CHANNEL_ID', 1):
            with self.assertRaisesRegex(game_service.GameStructureError, 'MANUAL_REVIEW'):
                await game_service.refresh_choose_games_message(SimpleNamespace(get_channel=lambda _: channel),
                    view=games.GameCategoryView, intro_view=games.ChooseGamesButtons)
        first.delete.assert_not_awaited()
        second.delete.assert_not_awaited()
        first.edit.assert_not_awaited()
        channel.send.assert_not_called()

    async def test_health_duplicates_and_adoption_are_read_only(self):
        channel, key, content = self.board()
        first = self.add_message(channel, content, pinned=True)
        findings = await reconcile.diagnostics(self.guild)
        self.assertTrue(any(k == key and 'Adoption available' in detail for k, state, detail in findings))
        self.assertIsNone(db.get_setting(key))
        self.add_message(channel, content, pinned=True)
        findings = await reconcile.diagnostics(self.guild)
        self.assertTrue(any(k == key and state == 'MANUAL_REVIEW' for k, state, detail in findings))
        self.assertEqual(first.edits, 0)

    async def test_history_failure_and_truncation_never_create(self):
        channel, key, content = self.board()
        for _ in range(3):
            self.add_message(channel, 'unrelated')
        with patch.object(reconcile, 'SCAN_LIMIT', 2):
            with self.assertRaisesRegex(ServerMessageError, 'scan limit'):
                await upsert_fixed_message(channel, setting_key=key, content=content)
        self.assertEqual(channel.sends, 0)
        canonical = self.add_message(channel, content, pinned=True)
        db.set_setting(key, canonical.id)
        with patch.object(reconcile, 'SCAN_LIMIT', 2):
            await upsert_fixed_message(channel, setting_key=key, content=content)
        self.assertEqual(channel.sends, 0)
        self.assertEqual(db.get_setting(key), str(canonical.id))

    async def test_fresh_roles_adopt_and_ambiguous_roles_fail_closed(self):
        option = role_service.ROLE_GROUPS['🖥️ Platform'][0]
        existing = await self.guild.create_role(name=option.label)
        await role_service.ensure_base_roles(self.guild)
        self.assertEqual(db.get_managed_role_by_key('base', option.key)['role_id'], existing.id)
        before = len(self.guild.roles)
        await role_service.ensure_base_roles(self.guild)
        self.assertEqual(len(self.guild.roles), before)
        db.delete_managed_role(existing.id)
        await self.guild.create_role(name=option.label)
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            await role_service.ensure_base_roles(self.guild)

    async def test_fresh_existing_core_channels_adopt_without_recreation(self):
        from services.onboarding_service import migrate_onboarding
        # A modern, read-only welcome must not become the legacy newbies channel.
        self.old.overwrites[self.guild.default_role] = discord.PermissionOverwrite(send_messages=False, view_channel=True)
        ids = {c.name: c.id for c in self.guild.text_channels}
        await migrate_onboarding(self.guild)
        self.assertEqual(db.get_setting('onboarding:1:welcome'), str(self.old.id))
        self.assertEqual(self.old.name, '👋・welcome')
        first = {c.id for c in self.guild.channels}
        await migrate_onboarding(self.guild)
        self.assertEqual(first, {c.id for c in self.guild.channels})
        for name in ('tournaments', 'giveaways'):
            self.assertEqual(db.get_setting('managed_channel:1:' + name), str(ids[name]))


class ImportTests(unittest.TestCase):
    setUp = production_fixtures.ProductionTests.setUp

    def source(self):
        source = self.root / 'old.db'
        with patch.object(db, 'DB_PATH', source):
            db.init_db()
            with db.connect() as conn:
                conn.execute("INSERT INTO settings VALUES ('managed_channel:1:giveaways','12345')")
                conn.execute("INSERT INTO support_tickets (guild_id,creator_discord_id,subject,description,created_at,updated_at) VALUES (1,2,'synthetic','private fixture',1,1)")
                conn.execute("INSERT INTO lfg_events (guild_id,game_id,host_id,title,start_at,max_players) VALUES (1,1,2,'synthetic',1,4)")
                conn.execute('PRAGMA user_version=0')
        return source

    def test_preview_unchanged_apply_preserves_runtime_and_backup(self):
        source = self.source()
        before, original = self.path.read_bytes(), source.read_bytes()
        importer.import_database(source, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(source.read_bytes(), original)
        backup = self.root / 'backup.db'
        importer.import_database(source, self.path, backup=backup, apply=True, bots_stopped=True)
        self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM support_tickets').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM lfg_events').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT value FROM settings WHERE key='managed_channel:1:giveaways'").fetchone()[0], '12345')
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], db.SCHEMA_VERSION)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM support_tickets').fetchone()[0], 0)

    def test_rejects_no_confirmation_existing_backup_future_schema_and_corruption(self):
        source = self.source()
        backup = self.root / 'backup.db'
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            importer.import_database(source, self.path, backup=backup, apply=True)
        backup.write_text('do not overwrite')
        with self.assertRaises(ValueError):
            importer.import_database(source, self.path, backup=backup, apply=True, bots_stopped=True)
        with closing(sqlite3.connect(source)) as conn:
            conn.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError):
            importer.import_database(source, self.path)
        source.write_bytes(b'corrupted')
        with self.assertRaises((ValueError, sqlite3.Error)):
            importer.import_database(source, self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_sidecar_blocks_import_and_incompatible_schema_rejected(self):
        source = self.source()
        Path(str(self.path) + '-wal').touch()
        with self.assertRaisesRegex(ValueError, 'sidecars'):
            importer.import_database(source, self.path, backup=self.root / 'backup.db', apply=True, bots_stopped=True)
        with closing(sqlite3.connect(source)) as conn:
            conn.execute('DROP TABLE settings')
        with self.assertRaisesRegex(ValueError, 'compatible'):
            importer.import_database(source, self.path)
