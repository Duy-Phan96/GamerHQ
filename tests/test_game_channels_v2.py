"""New game model: personal roles, deliberately created channels, scoped recovery."""
import asyncio
from contextlib import closing
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import test_onboarding as fixtures
import test_role_settings as role_fixtures
import test_production as production_fixtures
from database import db
from cogs.game_selector import GameSelectionSession
from services import game_catalog_service as catalog, game_channel_service as channels
from tools import import_database as importer, compare_databases as compare


class TextChannel(fixtures.FakeChannel):
    __class__ = discord.TextChannel

    async def edit(self, **kwargs):
        result = await super().edit(**kwargs)
        if 'position' in kwargs: self.position = kwargs['position']
        return result


class SelectorTests(unittest.IsolatedAsyncioTestCase):
    setUp = role_fixtures.RoleSettingsTests.setUp
    asyncSetUp = role_fixtures.RoleSettingsTests.asyncSetUp
    interaction = role_fixtures.RoleSettingsTests.interaction

    async def test_direct_add_remove_is_private_and_preserves_unrelated_roles(self):
        unrelated = self.guild.custom
        self.member.roles = [unrelated]
        view = GameSelectionSession(self.member, db.get_selector_games())
        self.assertTrue(any(c.label.startswith('➕ ') for c in view.children))
        self.assertFalse(any('Confirm' in (getattr(c, 'label', '') or '') for c in view.children))
        self.assertFalse(any('Save' in c.label for c in view.children))
        interaction = self.interaction()
        interaction.message = SimpleNamespace(edit=AsyncMock())
        await view.toggle(interaction, self.game['id'])
        self.assertIn(self.guild.get_role(self.game['role_id']), self.member.roles)
        self.assertTrue(any(c.label.startswith('✅ ') for c in view.children))
        await view.toggle(self.interaction(), self.game['id'])
        self.assertEqual(self.member.roles, [unrelated])
        interaction.message.edit.assert_not_awaited()
        self.assertIsNone(db.get_game_by_id(self.game['id'])['channel_id'])

    async def test_existing_selection_is_green_on_open(self):
        self.member.roles = [self.guild.get_role(self.game['role_id'])]
        view = GameSelectionSession(self.member, [self.game])
        self.assertEqual(view.children[0].label, '✅ ' + self.game['name'])
        self.assertEqual(view.children[0].style, discord.ButtonStyle.success)

    async def test_actor_binding_and_stale_role_do_not_mutate(self):
        view = GameSelectionSession(self.member, [self.game])
        other = self.interaction()
        other.user = SimpleNamespace(id=999)
        await view.toggle(other, self.game['id'])
        self.member.add_roles.assert_not_awaited()
        db.set_game_selectable(self.game['id'], False)
        await view.toggle(self.interaction(), self.game['id'])
        self.member.add_roles.assert_not_awaited()

    async def test_button_toggle_keeps_letter_page_and_selected_style(self):
        games = [dict(self.game, id=900+n, name=f'A Game {n:02}') for n in range(20)] + [self.game]
        view = GameSelectionSession(self.member, games)
        await view.navigate(self.interaction(), group='A–E', delta=1)
        button = next(c for c in view.children if c.custom_id == f'gamerhq:personal_game:{self.game["id"]}')
        self.assertEqual(button.style, discord.ButtonStyle.secondary)
        interaction = self.interaction()
        await button.callback(interaction)
        self.assertEqual((view.current_group, view.page), ('A–E', 1))
        button = next(c for c in view.children if c.custom_id == button.custom_id)
        self.assertEqual(button.style, discord.ButtonStyle.success)
        self.assertTrue(button.label.startswith('✅ '))
        self.assertIs(interaction.edit_original_response.call_args.kwargs['view'], view)
        await button.callback(self.interaction())
        self.assertNotIn(self.guild.get_role(self.game['role_id']), self.member.roles)
        self.assertEqual(view.page, 1)

    async def test_button_navigation_reaches_popular_and_all_ranges_without_queries(self):
        games = [dict(self.game, id=n+1, name=f'{chr(65+n%26)} Game {n:03}') for n in range(60)]
        games.append(dict(self.game, id=1000, name='123 Game'))
        view = GameSelectionSession(self.member, games)
        self.assertEqual(view.status_text().count('Popular'), 1)
        self.assertEqual(sum(c.label.startswith('➕ ') for c in view.children), 20)
        async def click(label):
            await next(c for c in view.children if c.label == label).callback(self.interaction())
        with patch.object(db, 'get_selector_games', side_effect=AssertionError('navigation queried DB')), patch('cogs.game_selector.member_counts', side_effect=AssertionError('navigation reranked')):
            await click('Next ▶')
            self.assertEqual(sum(c.label.startswith('➕ ') for c in view.children), 5)
            await click('Browse A–Z')
            self.assertEqual([c.label for c in view.children[:-1]], ['A–E', 'F–J', 'K–O', 'P–T', 'U–Z', '0–9 / Other'])
            await click('0–9 / Other')
            self.assertTrue(any(c.label == '➕ 123 Game' for c in view.children))
            await click('Back to Popular')
        self.guild.fetch_member.assert_not_awaited()
        self.assertEqual({g['id'] for k, rows in view.games_by_group.items() if k != '🔥 Popular' for g in rows}, {g['id'] for g in games})

    async def test_missing_role_is_friendly_and_logged_without_closing_panel(self):
        view = GameSelectionSession(self.member, [self.game])
        interaction = self.interaction()
        with patch.object(self.guild, 'get_role', return_value=None), self.assertLogs('cogs.game_selector', level='WARNING') as logs:
            await view.children[0].callback(interaction)
        interaction.followup.send.assert_awaited_once_with('This game is temporarily unavailable.', ephemeral=True)
        self.assertIn(f'game={self.game["id"]}', logs.output[0])
        self.member.add_roles.assert_not_awaited()
        self.assertFalse(view.busy)
        await view.children[0].callback(self.interaction())
        self.assertIn(self.game['id'], view.selected)

    async def test_empty_library_distinguishes_broken_role_mappings(self):
        from cogs.games import ChooseGamesButtons
        for rows, expected in [([], 'No games are currently available for selection.'),
                               ([dict(self.game, role_id=None)], 'Game selection is temporarily unavailable. Please try again later.')]:
            interaction = self.interaction()
            with patch.object(db, 'get_selector_games', return_value=[]), patch.object(db, 'get_selectable_games', return_value=rows):
                await ChooseGamesButtons().select_games.callback(interaction)
            interaction.response.send_message.assert_awaited_once_with(expected, ephemeral=True)

    async def test_public_copy_preserves_guidance_without_notification_section(self):
        from services.game_service import build_choose_games_message
        text = build_choose_games_message()
        for phrase in ('game roles', 'dedicated game channels', 'Select Games', 'Suggest Game', '/game select', '/game suggest'):
            self.assertIn(phrase, text)
        self.assertNotIn('LFG', text)

    async def test_open_selector_one_batch_no_inventory_and_no_public_lfg_button(self):
        from cogs.games import ChooseGamesButtons
        view = ChooseGamesButtons()
        interaction = self.interaction()
        with patch.object(db, 'get_selector_games', wraps=db.get_selector_games) as read, patch.object(self.guild, 'fetch_channels', new_callable=AsyncMock) as fetch:
            await view.select_games.callback(interaction)
        read.assert_called_once()
        fetch.assert_not_awaited()
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
        self.assertEqual([c.label for c in view.children], ['Select Games', 'Suggest Game'])

    async def test_popular_current_counts_ties_and_all_alphabetical_games(self):
        games = [dict(self.game, id=n+1, name=f'{chr(65+n%26)} Game {n:03}', role_id=n+100) for n in range(120)]
        roles = [self.guild.role(g['role_id']) for g in games]
        self.guild.members = [SimpleNamespace(roles=roles[:30]), SimpleNamespace(roles=[roles[22]])]
        groups = catalog.sections(games, catalog.member_counts(self.guild, games))
        self.assertEqual(len(groups['🔥 Popular']), 25)
        self.assertEqual(groups['🔥 Popular'][0], games[22])
        ordered = sorted(games[:30], key=lambda g: g['name'].casefold())
        self.assertEqual(groups['🔥 Popular'][1:], [g for g in ordered if g != games[22]][:24])
        self.assertEqual({g['id'] for k, rows in groups.items() if k != '🔥 Popular' for g in rows}, {g['id'] for g in games})
        self.guild.members += [SimpleNamespace(roles=[roles[40]]) for _ in range(3)]
        self.assertEqual(catalog.sections(games, catalog.member_counts(self.guild, games))['🔥 Popular'][0], games[40])
        session = GameSelectionSession(self.member, games)
        for group in session.games_by_group:
            if group != '🔥 Popular':
                self.assertEqual(session.games_by_group[group], sorted(session.games_by_group[group], key=lambda g: (g['name'].casefold(), g['id'])))
            session.current_group = group
            for page in range((len(session.games_by_group[group])+19)//20):
                session.page = page
                session.rebuild()
                self.assertLessEqual(len(session.children), 25)
                self.assertTrue(all(isinstance(c, discord.ui.Button) for c in session.children))
                self.assertTrue(all(sum(c.row == row for c in session.children) <= 5 for row in range(5)))


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    async def asyncSetUp(self):
        self.type_patch = patch.object(fixtures, 'FakeChannel', TextChannel)
        self.type_patch.start()
        self.addCleanup(self.type_patch.stop)
        self.guild.owner_id = 42
        self.actor = SimpleNamespace(id=42, roles=[], guild_permissions=discord.Permissions(administrator=True))
        self.guild.members = [self.actor]
        self.guild.get_member = lambda mid: self.actor if mid == 42 else None
        self.guild.fetch_channel = AsyncMock(side_effect=self.guild.get_channel)
        self.gaming = self.guild.add_category('🎮 GAMES')
        self.gaming.overwrites = {self.guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        db.set_setting('managed_category:1:gaming', self.gaming.id)
        db.set_setting('managed_category:1:staff', self.staff.id)
        self.logs = self.guild.add_channel('🎮・games-log', self.staff)
        from services.server_log_service import overwrites
        self.logs.overwrites = overwrites(self.guild, self.logs)
        self.logs.send = AsyncMock(return_value=SimpleNamespace(id=7000))
        db.set_setting('managed_channel:1:games-log', self.logs.id)
        self.game = await self.make_game('Terraria')
        self.guild.create_text_channel = AsyncMock(side_effect=self.create)
        channels._guild_locks.clear()

    async def make_game(self, name):
        game = db.upsert_custom_game(name, '🎮', 'Other')
        role = await self.guild.create_role(name='🎮 '+name)
        db.set_game_role(game['id'], role.id)
        db.set_game_selectable(game['id'], True)
        return db.get_game_by_id(game['id'])

    async def create(self, name, **kwargs):
        channel = self.guild.add_channel(name, kwargs['category'])
        channel.overwrites = kwargs.get('overwrites', {})
        return channel

    async def test_threshold_once_ignore_persisted_and_only_affected_game(self):
        for count in (9,10,11,12):
            await channels.check_threshold(self.guild, self.game['id'], count=count)
        self.logs.send.assert_awaited_once()
        row = channels.candidates(self.guild)[0]
        self.assertEqual(row['log_message_id'], 7000)
        self.assertEqual(row['status'], 'PENDING')
        channels.ignore(self.guild, self.actor, self.game['id'])
        await channels.check_threshold(self.guild, self.game['id'], count=13)
        self.logs.send.assert_awaited_once()
        self.guild.create_text_channel.assert_not_awaited()

    async def test_manual_creation_reuse_privacy_and_concurrent_confirmation(self):
        plan = channels.preview(self.guild, self.actor, self.game['id'])
        results = await asyncio.gather(channels.apply(self.guild,self.actor,plan), channels.apply(self.guild,self.actor,plan), return_exceptions=True)
        self.guild.create_text_channel.assert_awaited_once()
        channel = next(r for r in results if not isinstance(r, Exception))
        self.assertEqual(channel.category_id, self.gaming.id)
        self.assertFalse(channel.overwrites_for(self.guild.default_role).view_channel)
        role = self.guild.get_role(self.game['role_id'])
        self.assertTrue(channel.overwrites_for(role).view_channel)
        self.assertTrue(channel.overwrites_for(role).send_messages)
        self.assertTrue(channel.overwrites_for(self.guild.mod).view_channel)
        self.assertTrue(channel.overwrites_for(self.guild.me).manage_channels)
        again = await channels.apply(self.guild,self.actor,channels.preview(self.guild,self.actor,self.game['id']))
        self.assertEqual(again.id, channel.id)
        self.guild.create_text_channel.assert_awaited_once()

    async def test_soft_limit_explicit_override_and_alphabetical_order(self):
        await channels.apply(self.guild,self.actor,channels.preview(self.guild,self.actor,self.game['id']))
        other = await self.make_game('Ark')
        with patch('config.GAME_CHANNEL_SOFT_LIMIT', 1):
            plan = channels.preview(self.guild,self.actor,other['id'])
            with self.assertRaises(ValueError): await channels.apply(self.guild,self.actor,plan)
            await channels.apply(self.guild,self.actor,plan,override=True)
        current, ordered = channels.order_plan(self.guild)[0]
        self.assertEqual([c.name for c in ordered], ['ark','terraria'])
        self.assertEqual(len(self.gaming.channels), 2)

    async def test_legacy_reuse_preserves_history_and_leaves_other_resources(self):
        old = self.guild.add_category('TERRARIA')
        chat = self.guild.add_channel('chat', old)
        chat.messages[500] = SimpleNamespace(content='History')
        lfg = self.guild.add_channel('lfg', old)
        with db.connect() as conn:
            conn.execute('UPDATE games SET category_id=?,chat_channel_id=?,lfg_channel_id=?,area_enabled=1 WHERE id=?', (old.id,chat.id,lfg.id,self.game['id']))
        with self.assertRaises(ValueError): channels.preview(self.guild,self.actor,self.game['id'])
        plan = channels.preview(self.guild,self.actor,self.game['id'],'migrate')
        result = await channels.apply(self.guild,self.actor,plan)
        self.assertEqual(result.id, chat.id)
        self.assertEqual(result.messages[500].content,'History')
        self.assertIn(lfg,self.guild.text_channels)
        self.assertIn(old,self.guild.categories)
        game = db.get_game_by_id(self.game['id'])
        self.assertEqual(game['channel_id'],chat.id)
        self.assertIsNone(game['category_id'])
        self.assertEqual(channels.legacy_hints(game)['lfg_channel_id'],lfg.id)
        self.guild.create_text_channel.assert_not_awaited()

    async def test_system_v3_moves_shared_lfg_and_legacy_chat_without_deleting_legacy_resources(self):
        start = self.guild.add_category('👋 START HERE')
        lfg = self.guild.add_channel('🎯・looking-for-group', start)
        db.set_setting('managed_channel:1:looking-for-group', lfg.id)

        old = self.guild.add_category('🎮 TERRARIA')
        chat = self.guild.add_channel('💬・chat', old)
        legacy_lfg = self.guild.add_channel('🎯・looking-for-group', old)
        legacy_voice = self.guild.add_channel('➕・create-voice', old)
        chat.messages[500] = SimpleNamespace(content='keep history')
        with db.connect() as conn:
            conn.execute(
                'UPDATE games SET category_id=?,chat_channel_id=?,lfg_channel_id=?,create_voice_channel_id=?,area_enabled=1 WHERE id=?',
                (old.id, chat.id, legacy_lfg.id, legacy_voice.id, self.game['id']),
            )

        plan = channels.system_preview(self.guild, self.actor)
        self.assertEqual(plan['category_id'], self.gaming.id)
        self.assertEqual(plan['lfg_id'], lfg.id)
        self.assertTrue(any(item['source_id'] == chat.id for item in plan['migrations']))
        result = await channels.apply_system_migration(self.guild, self.actor, plan)

        self.assertEqual(self.gaming.name, '🎮 GAMES')
        self.assertEqual(lfg.category_id, self.gaming.id)
        self.assertEqual(lfg.name, '🔎・looking-for-group')
        gaming_chat = self.guild.get_channel(result['gaming_chat'])
        self.assertEqual(gaming_chat.name, '💬・gaming-chat')
        game = db.get_game_by_id(self.game['id'])
        self.assertEqual(game['channel_id'], chat.id)
        self.assertEqual(chat.category_id, self.gaming.id)
        self.assertEqual(chat.name, 'terraria')
        self.assertEqual(chat.messages[500].content, 'keep history')
        self.assertIn(legacy_lfg, self.guild.text_channels)
        self.assertIn(legacy_voice, self.guild.text_channels)
        self.assertIn(old, self.guild.categories)
        self.assertTrue(result['cleanup'])
        self.assertIsNone(game['category_id'])
        self.assertIsNone(game['lfg_channel_id'])
        self.assertIsNone(game['create_voice_channel_id'])

    async def test_removal_requires_unchanged_preview_and_preserves_catalog(self):
        channel = await channels.apply(self.guild,self.actor,channels.preview(self.guild,self.actor,self.game['id']))
        plan = channels.preview(self.guild,self.actor,self.game['id'],'remove')
        channel.name = 'changed'
        with self.assertRaises(ValueError): await channels.apply(self.guild,self.actor,plan)
        plan = channels.preview(self.guild,self.actor,self.game['id'],'remove')
        await channels.apply(self.guild,self.actor,plan)
        game = db.get_game_by_id(self.game['id'])
        self.assertIsNone(game['channel_id'])
        self.assertEqual(game['role_id'],self.game['role_id'])
        self.assertTrue(game['selectable'])

    async def test_health_is_read_only_and_no_role_candidate_on_private_log_leak(self):
        self.logs.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True)
        await channels.check_threshold(self.guild,self.game['id'],count=10)
        self.logs.send.assert_not_awaited()
        from services.health_service import scan
        with db.connect() as conn: before = list(conn.iterdump())
        findings = await scan(self.guild,self.bot,messages=False)
        self.assertTrue(any(f.name=='GAMES category' for f in findings))
        self.assertTrue(any(f.name=='Games log' and f.state!='PASS' for f in findings))
        with db.connect() as conn: self.assertEqual(before,list(conn.iterdump()))

    async def test_hidden_channel_repairs_and_blocks_permanent_game_deletion(self):
        from services import server_operations as operations
        channel = await channels.apply(self.guild,self.actor,channels.preview(self.guild,self.actor,self.game['id']))
        db.set_game_selectable(self.game['id'],False)
        row = next(r for r in operations.definitions(self.guild) if r.get('game_id') == self.game['id'])
        self.assertEqual(operations.mapped(row),str(channel.id))
        self.assertEqual(operations.rights(self.guild,row,channel),channel.overwrites)
        with self.assertRaises(RuntimeError): db.delete_game_permanently(self.game['id'])
        self.assertIsNotNone(db.get_game_by_id(self.game['id']))

    async def test_removal_can_be_followed_by_deliberate_recreation(self):
        first = await channels.apply(self.guild,self.actor,channels.preview(self.guild,self.actor,self.game['id']))
        await channels.apply(self.guild,self.actor,channels.preview(self.guild,self.actor,self.game['id'],'remove'))
        second = await channels.apply(self.guild,self.actor,channels.preview(self.guild,self.actor,self.game['id']))
        self.assertNotEqual(first.id,second.id)
        self.assertEqual(len(self.gaming.channels),1)

    async def test_fresh_resource_drift_blocks_removal_without_touching_cache(self):
        channel = await channels.apply(self.guild,self.actor,channels.preview(self.guild,self.actor,self.game['id']))
        plan = channels.preview(self.guild,self.actor,self.game['id'],'remove')
        fresh = TextChannel(self.guild,'manual-rename',self.gaming,channel.id)
        fresh.overwrites = channel.overwrites
        async def fetch(cid): return fresh if cid == channel.id else self.guild.get_channel(cid)
        self.guild.fetch_channel.side_effect = fetch
        with self.assertRaises(ValueError): await channels.apply(self.guild,self.actor,plan)
        self.assertIn(channel,self.guild.text_channels)
        self.assertEqual(db.get_game_by_id(self.game['id'])['channel_id'],channel.id)


class RecoveryTests(unittest.TestCase):
    setUp = production_fixtures.ProductionTests.setUp

    def fixture(self):
        source = self.root/'source.db'
        with patch.object(db,'DB_PATH',source): db.init_db()
        with closing(sqlite3.connect(source)) as conn:
            conn.executemany('INSERT INTO games(name,display_group,active,selectable,role_id,category_id,chat_channel_id) VALUES(?,?,?,?,?,?,?)',
                [(f'Game {n:03}','Other',int(n<61),int(n<48),100+n if n<64 else None,200+n if n<12 else None,300+n if n<12 else None) for n in range(65)])
            conn.commit()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("ALTER TABLE games ADD COLUMN production_extra TEXT DEFAULT 'keep'")
            conn.executemany('INSERT INTO games(name,display_group) VALUES(?,?)',[(f'Game {n:03}','Other') for n in range(60)])
            conn.execute("INSERT INTO settings VALUES('private-key','private-sentinel')")
            conn.execute('CREATE TABLE production_only(value TEXT)')
            conn.execute("INSERT INTO production_only VALUES('retain')")
            conn.commit()
        return source

    def test_65_60_fixture_dry_run_apply_preserves_production(self):
        source = self.fixture()
        report = compare.compare(source,self.path)
        for name,value in [('games',65),('active_games',61),('selector_games',48),('visible_games',48),('game_role_mappings',64),('game_area_mappings',12)]:
            self.assertEqual(report['source']['counts'][name],value)
        self.assertEqual(report['production']['counts']['games'],60)
        self.assertEqual(report['production']['counts']['selector_games'],0)
        before = source.read_bytes(),self.path.read_bytes()
        output = importer.import_database(source,self.path,scope='games')
        self.assertIn('Result selector games: 48',output)
        self.assertNotIn('private-sentinel',output)
        self.assertEqual(before,(source.read_bytes(),self.path.read_bytes()))
        importer.import_database(source,self.path,scope='games',apply=True,bots_stopped=True,backup=self.root/'before.db',confirm_comparison=report['review_token'])
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM games').fetchone()[0],65)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM games WHERE category_id IS NOT NULL OR channel_id IS NOT NULL').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM game_legacy_hints').fetchone()[0],12)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM games WHERE production_extra=?',('keep',)).fetchone()[0],65)
            self.assertEqual(conn.execute('SELECT value FROM production_only').fetchone()[0],'retain')
            self.assertEqual(conn.execute('SELECT value FROM settings WHERE key=?',('private-key',)).fetchone()[0],'private-sentinel')
        with patch.object(db,'DB_PATH',self.path): self.assertEqual(len(db.get_selector_games()),48)

    def test_recovery_preserves_adopted_role_channel_hidden_state_and_requires_token(self):
        source = self.fixture()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('UPDATE games SET role_id=9000,channel_id=9001,active=1,selectable=0 WHERE id=1')
            conn.commit()
        with self.assertRaises(ValueError):
            importer.import_database(source,self.path,scope='games',apply=True,bots_stopped=True,backup=self.root/'bad.db')
        report = compare.compare(source,self.path)
        importer.import_database(source,self.path,scope='games',apply=True,bots_stopped=True,backup=self.root/'good.db',confirm_comparison=report['review_token'])
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT role_id,channel_id,selectable FROM games WHERE id=1').fetchone(),(9000,9001,0))
