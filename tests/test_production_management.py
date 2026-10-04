"""Production recovery and administration, using synthetic offline state only."""
from contextlib import closing
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
from database import db
from tests import test_production, test_onboarding
from tools import compare_databases as compare, import_database as importer


class DatabaseRecoveryTests(unittest.TestCase):
    setUp = test_production.ProductionTests.setUp

    def snapshots(self):
        source = self.root / 'source.db'
        with patch.object(db, 'DB_PATH', source):
            db.init_db()
            with db.connect() as conn:
                conn.execute("INSERT INTO games(name,display_group,active,selectable,role_id,category_id,chat_channel_id) VALUES ('Fixture Game','Other',1,1,100,200,300)")
                conn.execute("INSERT INTO settings VALUES ('managed_channel:1:choose-your-games','400')")
        return source

    def test_compare_counts_and_no_private_values_or_mutation(self):
        source = self.snapshots()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("INSERT INTO settings VALUES ('private-key','private-sentinel')")
            conn.commit()
        originals = (source.read_bytes(), self.path.read_bytes())
        report = compare.compare(source, self.path)
        self.assertEqual(report['source']['counts']['visible_games'], 1)
        self.assertEqual(report['source']['counts']['game_role_mappings'], 1)
        self.assertEqual(report['differences']['settings']['production_only'], 1)
        self.assertNotIn('private-sentinel', compare.render(report))
        self.assertNotIn('private-key', compare.render(report))
        self.assertEqual(originals, (source.read_bytes(), self.path.read_bytes()))

    def test_conflict_refused_until_exact_review_then_backup_and_catalog_restored(self):
        source = self.snapshots()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("INSERT INTO support_tickets(guild_id,creator_discord_id,subject,description,created_at,updated_at) VALUES (1,2,'fixture','private-sentinel',1,1)")
            conn.commit()
        old = self.path.read_bytes()
        report = compare.compare(source, self.path)
        self.assertEqual(report['differences']['support_tickets']['production_only'], 1)
        self.assertIn('No changes made', importer.import_database(source, self.path))
        self.assertEqual(old, self.path.read_bytes())
        backup = self.root / 'backup.db'
        for token in (None, 'outdated'):
            with self.assertRaises(ValueError):
                importer.import_database(source, self.path, apply=True, bots_stopped=True, backup=backup, confirm_comparison=token)
        self.assertFalse(backup.exists())
        importer.import_database(source, self.path, apply=True, bots_stopped=True, backup=backup, confirm_comparison=report['review_token'])
        with patch.object(db, 'DB_PATH', self.path):
            games = db.get_selectable_games()
            self.assertEqual([(g['name'], g['role_id'], g['category_id'], g['chat_channel_id']) for g in games], [('Fixture Game', 100, 200, 300)])
            self.assertEqual(db.get_setting('managed_channel:1:choose-your-games'), '400')
            db.seed_catalog()
            self.assertEqual(db.get_selectable_games()[0]['role_id'], 100)
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM support_tickets').fetchone()[0], 1)

    def test_source_change_during_rehearsal_refused(self):
        source = self.snapshots()
        migrate = importer.migrate_copy
        def change(src, dest):
            migrate(src, dest)
            with closing(sqlite3.connect(src)) as conn:
                conn.execute('UPDATE games SET selectable=0')
                conn.commit()
        with patch.object(importer, 'migrate_copy', side_effect=change), self.assertRaisesRegex(ValueError, 'Source changed'):
            importer.import_database(source, self.path)

    def test_active_but_hidden_game_diagnosed_without_enabling_it(self):
        source = self.snapshots()
        with closing(sqlite3.connect(source)) as conn:
            conn.execute('UPDATE games SET selectable=0')
            conn.commit()
        self.assertIn('none are selectable', compare.render(compare.compare(source, self.path)))

    def test_unknown_tables_and_schema_objects_are_compared_without_names(self):
        source = self.snapshots()
        for path in (source, self.path):
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript('CREATE TABLE private_first(id INTEGER PRIMARY KEY); CREATE TABLE private_second(id INTEGER PRIMARY KEY);')
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('CREATE INDEX private_extra_index ON games(name)')
        report = compare.compare(source, self.path)
        self.assertEqual(report['differences']['other_tables']['production_only'], 0)
        self.assertEqual(report['differences']['schema_objects']['production_only'], 1)
        self.assertGreater(report['potential_conflicts'], 0)
        self.assertNotIn('private_', compare.render(report))


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_onboarding.OnboardingTests.setUp

    def actor(self, uid=42, admin=True):
        self.guild.owner_id = 42
        actor = SimpleNamespace(id=uid, guild_permissions=discord.Permissions(administrator=admin))
        self.guild.members = [actor]
        return actor

    def interaction(self, actor):
        return SimpleNamespace(guild=self.guild, user=actor, client=SimpleNamespace(),
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock(), is_done=lambda: False),
            followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    async def test_manage_fast_and_authorized_setup_state(self):
        from cogs.server_management import open_manage, open_setup, SetupWizard, SetupCompleteView, setup_key
        owner = self.actor()
        interaction = self.interaction(owner)
        with patch('services.server_operations.scan', new_callable=AsyncMock) as scan:
            await open_manage(interaction)
            scan.assert_not_awaited()
        await open_setup(interaction)
        self.assertIsInstance(interaction.response.send_message.call_args.kwargs['view'], SetupWizard)
        db.set_setting(setup_key(self.guild), '1')
        await open_setup(interaction)
        self.assertIsInstance(interaction.response.send_message.call_args.kwargs['view'], SetupCompleteView)
        ordinary = self.interaction(self.actor(43, False))
        await open_manage(ordinary)
        self.assertNotIn('view', ordinary.response.send_message.call_args.kwargs)

    async def test_skills_management_is_in_main_admin_flow_and_uses_runtime_state(self):
        from cogs.server_management import ManagementView, SkillsView, open_skills
        from hosts.gamerhq.skill_runtime import GuildSkillStatus

        owner = self.actor()
        interaction = self.interaction(owner)
        status = GuildSkillStatus(
            skill_id='fixture-skill',
            name='Fixture Skill',
            version='1.0.0',
            description='Portable fixture',
            enabled=False,
            running=False,
            health='DISABLED',
            health_detail='Skill is disabled for this guild.',
            required_capabilities=('discord.messages.send',),
            missing_capabilities=(),
        )
        runtime = SimpleNamespace(statuses=AsyncMock(return_value=(status,)))
        interaction.client.skill_runtime = runtime

        manage = ManagementView(self.guild, owner.id)
        self.assertIn('Skills', [child.label for child in manage.children if isinstance(child, discord.ui.Button)])

        await open_skills(interaction, self.guild, owner.id)
        runtime.statuses.assert_awaited_once_with(guild_id=self.guild.id)
        kwargs = interaction.response.edit_message.call_args.kwargs
        self.assertIsInstance(kwargs['view'], SkillsView)
        self.assertIn('Fixture Skill', kwargs['content'])
        self.assertIn('Disabled', kwargs['content'])

    def test_skill_details_show_external_package_provenance(self):
        from cogs.server_management import _skill_detail_text
        from hosts.gamerhq.skill_runtime import GuildSkillStatus

        status = GuildSkillStatus(
            skill_id='external-skill',
            name='External Skill',
            version='1.0.0',
            description='Portable external fixture',
            enabled=False,
            running=False,
            health='DISABLED',
            health_detail='Skill is disabled for this guild.',
            required_capabilities=(),
            missing_capabilities=(),
            source_kind='external',
            source_distribution='gamerhq-skill-external',
        )
        text = _skill_detail_text(status)
        self.assertIn('External package: gamerhq-skill-external', text)

    def test_unavailable_external_skill_has_no_enable_action(self):
        from cogs.server_management import SkillDetailsView, _skill_detail_text
        from hosts.gamerhq.skill_runtime import GuildSkillStatus

        status = GuildSkillStatus(
            skill_id='missing-external',
            name='Missing External',
            version='unknown',
            description='Configured external Skill package is unavailable.',
            enabled=False,
            running=False,
            health='UNAVAILABLE',
            health_detail='The configured external Skill package could not be loaded.',
            required_capabilities=(),
            missing_capabilities=(),
            source_kind='external',
            source_distribution=None,
        )
        view = SkillDetailsView(self.guild, 42, status)
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]
        self.assertNotIn('Review Enable', labels)
        self.assertIn('Unavailable', _skill_detail_text(status))

    async def test_skill_lifecycle_change_requires_owner_review(self):
        from cogs.server_management import SkillDetailsView, SkillToggleConfirmView
        from hosts.gamerhq.skill_runtime import GuildSkillStatus

        owner = self.actor()
        status = GuildSkillStatus(
            skill_id='fixture-skill',
            name='Fixture Skill',
            version='1.0.0',
            description='Portable fixture',
            enabled=False,
            running=False,
            health='DISABLED',
            health_detail='Skill is disabled for this guild.',
            required_capabilities=('discord.messages.send',),
            missing_capabilities=(),
        )
        runtime = SimpleNamespace(
            status=AsyncMock(return_value=status),
            enable_skill=AsyncMock(return_value=True),
            disable_skill=AsyncMock(return_value=True),
        )

        admin = SimpleNamespace(id=43, guild_permissions=discord.Permissions(administrator=True))
        self.guild.members = [owner, admin]
        denied = self.interaction(admin)
        denied.client.skill_runtime = runtime
        view = SkillDetailsView(self.guild, admin.id, status)
        await view.review_toggle(denied)
        runtime.status.assert_not_awaited()
        self.assertIn('Only the server owner', denied.response.send_message.call_args.args[0])

        allowed = self.interaction(owner)
        allowed.client.skill_runtime = runtime
        owner_view = SkillDetailsView(self.guild, owner.id, status)
        await owner_view.review_toggle(allowed)
        confirm = allowed.response.edit_message.call_args.kwargs['view']
        self.assertIsInstance(confirm, SkillToggleConfirmView)
        runtime.enable_skill.assert_not_awaited()

    async def test_enabled_recurring_posts_skill_exposes_configure_action(self):
        from cogs.server_management import SkillDetailsView
        from hosts.gamerhq.skill_runtime import GuildSkillStatus

        owner = self.actor()
        status = GuildSkillStatus(
            skill_id='recurring-posts',
            name='Recurring Posts',
            version='1.0.0',
            description='Portable fixture',
            enabled=True,
            running=True,
            health='PASS',
            health_detail='0 active recurring post(s), 0 configured.',
            required_capabilities=('scheduler.jobs',),
            missing_capabilities=(),
        )
        view = SkillDetailsView(self.guild, owner.id, status)
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]
        self.assertIn('Configure', labels)
        self.assertIn('Review Disable', labels)

    def test_recurring_post_interval_modal_builds_scheduler_contract(self):
        from cogs.server_management import RecurringPostModal

        modal = RecurringPostModal(self.guild, 42, 123, 'interval')
        modal.schedule._value = '180'
        self.assertEqual(
            modal._schedule_value(),
            {'type': 'interval', 'seconds': 10800},
        )

    async def test_imported_catalog_is_offered_by_actual_select_games_button(self):
        from cogs.games import ChooseGamesButtons, GameSelectionSession
        from config import DISPLAY_GROUP_ORDER
        source = db.DB_PATH.parent / 'old-catalog.db'
        target = db.DB_PATH
        with patch.object(db, 'DB_PATH', source):
            db.init_db()
            with db.connect() as conn:
                conn.execute('INSERT INTO games(name,display_group,active,selectable,role_id) VALUES (?, ?,1,1,100)',
                             ('Fixture Game', DISPLAY_GROUP_ORDER[0]))
        report = compare.compare(source, target)
        importer.import_database(source, target, backup=target.parent / 'before.db', apply=True,
            bots_stopped=True, confirm_comparison=report['review_token'])
        actor = self.actor()
        actor.guild, actor.roles = self.guild, []
        self.guild.roles.append(self.guild.role(100))
        interaction = self.interaction(actor)
        view = ChooseGamesButtons()
        await view.select_games.callback(interaction)
        session = interaction.response.send_message.call_args.kwargs['view']
        self.assertIsInstance(session, GameSelectionSession)
        self.assertEqual(session.games[0]['name'], 'Fixture Game')
        self.assertEqual(session.games[0]['role_id'], 100)
        self.assertEqual(session.role_ids[session.games[0]['id']], 100)
        options = [child.label for child in session.children if isinstance(child, discord.ui.Button)]
        self.assertIn('➕ Fixture Game', options)

    async def test_server_log_creation_and_repair_reuse_stored_channel(self):
        from services import server_operations as ops
        actor = self.actor()
        self.staff.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        db.set_setting('managed_category:1:staff', self.staff.id)
        draft = await ops.preview(self.guild, actor, 'setup', self.bot)
        draft['rows'] = [r for r in draft['rows'] if r['name'] == 'server-log']
        done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(len(done), 1)
        self.assertFalse(skipped)
        recorded = db.get_setting('managed_channel:1:server-log')
        for mode in ('setup', 'repair'):
            draft = await ops.preview(self.guild, actor, mode, self.bot)
            draft['rows'] = [r for r in draft['rows'] if r['name'] == 'server-log']
            done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
            self.assertFalse(done)
            self.assertFalse(skipped)
            self.assertEqual(db.get_setting('managed_channel:1:server-log'), recorded)
        destination = self.guild.get_channel(int(recorded))
        self.assertEqual(destination.category_id, self.staff.id)
        self.assertFalse(destination.overwrites_for(self.guild.default_role).view_channel)
        self.assertNotIn(self.guild.custom, destination.overwrites)

    async def test_dev_and_sessions_recheck_owner_admin(self):
        from cogs.server_management import DeveloperView, ManagementView
        owner = self.actor()
        view = DeveloperView(self.guild, owner.id)
        self.assertTrue(await view.interaction_check(self.interaction(owner)))
        self.guild.owner_id = 99
        self.assertFalse(await view.interaction_check(self.interaction(owner)))
        admin = self.actor(43)
        manage = ManagementView(self.guild, admin.id)
        self.assertTrue(await manage.interaction_check(self.interaction(admin)))
        self.guild.members = []
        self.assertFalse(await manage.interaction_check(self.interaction(admin)))

    async def test_finish_requires_ready_structure_and_persists_completion(self):
        from cogs.server_management import SetupWizard, setup_key
        owner = self.actor()
        interaction = self.interaction(owner)
        view = SetupWizard(self.guild, owner.id, page=5)
        with patch('cogs.server_management.structure_issues', return_value=['Server Log']):
            await view.section.callback(interaction)
        self.assertIsNone(db.get_setting(setup_key(self.guild)))
        with patch('cogs.server_management.structure_issues', return_value=[]), patch('services.server_log_service.emit', new_callable=AsyncMock):
            await view.section.callback(interaction)
        self.assertEqual(db.get_setting(setup_key(self.guild)), '1')

    async def test_selected_bot_persists_and_database_precedes_environment(self):
        from cogs.server_management import ConfirmBotView
        from services.bot_group_service import member_id, member
        owner = self.actor()
        external = SimpleNamespace(id=1234, bot=True)
        self.guild.fetch_member = AsyncMock(return_value=external)
        view = ConfirmBotView(self.guild, owner.id, 'dealgecko', external.id)
        with patch('config.DEALGECKO_BOT_ID', 5555), patch('services.server_log_service.emit', new_callable=AsyncMock):
            self.assertEqual(member_id(self.guild, 'dealgecko'), 5555)
            await view.confirm.callback(self.interaction(owner))
            self.assertEqual(member_id(self.guild, 'dealgecko'), 1234)
            self.assertIs(member(self.guild, 'dealgecko'), external)
            self.assertEqual(db.get_setting('bot_member:1:dealgecko'), '1234')

    async def test_human_or_departed_bot_not_saved(self):
        from cogs.server_management import ConfirmBotView
        owner = self.actor()
        self.guild.fetch_member = AsyncMock(return_value=SimpleNamespace(id=1234, bot=False))
        view = ConfirmBotView(self.guild, owner.id, 'dealgecko', 1234)
        await view.confirm.callback(self.interaction(owner))
        self.assertIsNone(db.get_setting('bot_member:1:dealgecko'))

    async def test_friendly_preview_preserves_ambiguity_choices_without_keys(self):
        from cogs.server import OperationsView, MappingChoiceView
        owner = self.actor()
        a = self.guild.add_channel('community-events', self.guild.categories[0])
        b = self.guild.add_channel('community-events', self.guild.categories[1])
        row = dict(kind='text', key='private-internal-key', label='Community Events', status='AMBIGUOUS',
                   candidates=[a,b], safe=[a.id,b.id], resource=None, name='community-events')
        view = OperationsView(self.guild, dict(actor_id=owner.id, mode='reconcile', rows=[row]), None, friendly=True)
        self.assertNotIn('AMBIGUOUS', view.text())
        self.assertNotIn('private-internal-key', view.text())
        chooser = MappingChoiceView(view, row)
        select = next(c for c in chooser.children if isinstance(c, discord.ui.Select))
        self.assertEqual([o.label for o in select.options], [a.name,b.name])
        self.assertEqual([o.value for o in select.options], [str(a.id),str(b.id)])

    async def test_server_log_private_durable_and_no_startup_reconnect_spam(self):
        from services import server_log_service as log
        owner = self.actor()
        category = self.guild.add_category('STAFF')
        destination = self.guild.add_channel('server-log', category)
        destination.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True)
        destination.overwrites = log.overwrites(self.guild, destination)
        self.assertFalse(destination.overwrites_for(self.guild.default_role).view_channel)
        self.assertFalse(destination.overwrites_for(self.guild.custom).view_channel)
        self.assertTrue(destination.overwrites_for(self.guild.mod).view_channel)
        self.assertFalse(destination.overwrites_for(self.guild.mod).send_messages)
        self.assertTrue(destination.overwrites_for(self.guild.me).embed_links)
        db.set_setting('managed_category:1:staff', category.id)
        db.set_setting('managed_channel:1:server-log', destination.id)
        destination.send = AsyncMock()
        from services.health_service import Finding
        with patch('release_info.get_commit', return_value='a'*40), patch('services.health_service.scan', new_callable=AsyncMock,
                return_value=[Finding('private-mapping-key', 'REPAIRABLE', 'private-value')]):
            await log.startup(self.guild, None)
            await log.startup(self.guild, None)
        self.assertEqual(destination.send.await_count, 2)  # deployment plus one warning, no repeats
        notice = destination.send.call_args.kwargs['embed'].description
        self.assertIn('/server manage', notice)
        self.assertNotIn('private-', notice)
        with patch('release_info.get_commit', return_value='b'*40), patch('services.health_service.scan', new_callable=AsyncMock, return_value=[]):
            await log.startup(self.guild, None)
        self.assertEqual(destination.send.await_count, 3)
        destination.overwrites[self.guild.custom].view_channel = True
        self.assertFalse(await log.emit(self.guild, 'unsafe', 'Test', 'Safe'))

    async def test_uncertain_log_delivery_never_retried(self):
        from services import server_log_service as log
        sender = SimpleNamespace(send=AsyncMock(side_effect=TimeoutError))
        with patch.object(log, 'channel', return_value=sender):
            await log.emit(self.guild, 'fixture-event', 'Test', 'Safe')
            await log.emit(self.guild, 'fixture-event', 'Test', 'Safe')
        sender.send.assert_awaited_once()
