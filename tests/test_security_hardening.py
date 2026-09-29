"""Security regressions using isolated SQLite and offline Discord fixtures."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands
import test_role_settings as role_fixtures
import test_community_structure as suggestion_fixtures
import test_voice_area as voice_fixtures
import test_tickets as ticket_fixtures
from cogs.games import ToggleGameView, GameRoleConfirmView, GameSelectionSession, NotificationSelectionSession
from cogs import suggestions, server
from services import role_service as roles, response_service as responses
from services import server_setup_service as setup, temp_voice_service as voice, ticket_service as tickets
from services.url_service import validate_url
from services.server_service import ServerMessageError
from database import db


class GameRoleSecurityTests(unittest.IsolatedAsyncioTestCase):
    setUp = role_fixtures.RoleSettingsTests.setUp
    asyncSetUp = role_fixtures.RoleSettingsTests.asyncSetUp
    interaction = role_fixtures.RoleSettingsTests.interaction

    async def test_protected_managed_above_bot_and_shared_roles_denied(self):
        role = self.guild.get_role(self.game['role_id'])
        for permissions, managed in [(discord.Permissions(administrator=True), False),
                                     (discord.Permissions(manage_roles=True), False),
                                     (discord.Permissions.none(), True)]:
            role.permissions, role.managed = permissions, managed
            with self.assertRaises(ValueError):
                await roles.set_game_selection(self.member, [(self.game['id'], role.id, True, False)])
        role.permissions, role.managed = discord.Permissions.none(), False
        db.upsert_managed_role(role_id=role.id, role_kind='base', role_key='pc')
        with self.assertRaises(ValueError):
            roles.game_role(self.guild, self.game['id'], role.id)
        self.member.add_roles.assert_not_awaited()

    async def test_hierarchy_or_missing_bot_permission_denies_game_roles(self):
        role = self.guild.get_role(self.game['role_id'])
        role.__lt__.return_value = False
        with self.assertRaises(ValueError): roles.game_role(self.guild, self.game['id'], role.id)
        role.__lt__.return_value = True
        self.guild.me.guild_permissions = discord.Permissions.none()
        with self.assertRaises(ValueError): roles.game_role(self.guild, self.game['id'], role.id)

    async def test_stale_mapping_or_disabled_game_cannot_assign(self):
        old = self.game['role_id']
        replacement = await self.guild.create_role(name='Replacement')
        view = GameRoleConfirmView(self.game, self.member, remove=False)
        db.set_game_role(self.game['id'], replacement.id)
        await view.apply(self.interaction())
        self.member.add_roles.assert_not_awaited()
        db.set_game_role(self.game['id'], old)
        db.set_game_selectable(self.game['id'], False)
        with self.assertRaises(ValueError):
            await roles.set_game_selection(self.member, [(self.game['id'], old, True, False)])

    async def test_double_click_is_one_explicit_change_and_other_roles_preserved(self):
        role = self.guild.get_role(self.game['role_id'])
        self.member.roles = [self.guild.mod]
        view = ToggleGameView(self.game, role, self.member)
        await asyncio.gather(view.toggle.callback(self.interaction()), view.toggle.callback(self.interaction()))
        self.assertEqual(self.member.roles, [self.guild.mod, role])
        self.member.add_roles.assert_awaited_once()
        self.member.remove_roles.assert_not_awaited()

    async def test_confirm_cannot_be_replayed_by_another_member_or_guild(self):
        view = GameRoleConfirmView(self.game, self.member, remove=False)
        wrong = self.interaction(); wrong.user = SimpleNamespace(id=999)
        await view.apply(wrong)
        wrong = self.interaction(); wrong.guild = SimpleNamespace(id=999)
        await view.apply(wrong)
        self.member.add_roles.assert_not_awaited()
        await view.cancel(self.interaction())
        await view.apply(self.interaction())
        self.member.add_roles.assert_not_awaited()

    async def test_refresh_precedes_mapping_validation_and_batch_is_all_or_nothing(self):
        async def changed(_):
            self.guild.get_role(self.game['role_id']).permissions = discord.Permissions(administrator=True)
            return self.member
        self.guild.fetch_member.side_effect = changed
        session = GameSelectionSession(self.member, [self.game])
        await session.toggle(self.interaction(), self.game['id'])
        self.member.add_roles.assert_not_awaited()

    async def test_personal_panel_fits_discord_utf16_limit(self):
        games = [dict(self.game, id=i, name='🎮' * 100) for i in range(1, 121)]
        session = GameSelectionSession(self.member, games)
        self.assertLessEqual(len(session.status_text().encode('utf-16-le')) // 2, 2000)
        self.assertLessEqual(max(len(c.options) for c in session.children if isinstance(c, discord.ui.Select)), 25)

    async def test_notification_mapping_changed_after_preview_denied(self):
        session = NotificationSelectionSession(self.member, [self.game], notifications=True)
        session.pending_ids.add(self.game['id'])
        role = await self.guild.create_role(name='Replacement notifications')
        db.upsert_managed_role(role_id=role.id, role_kind='lfg', role_key=str(self.game['id']))
        await session.confirm_selection(self.interaction())
        self.member.add_roles.assert_not_awaited()

    async def test_lfg_confirmation_cannot_assign_privileged_game_role(self):
        from cogs.lfg import AddGameAndJoinView
        event = db.create_lfg_event(guild_id=self.guild.id, game_id=self.game['id'], host_id=99,
            title='Test', start_at=2000000000, max_players=5, invite_lead_minutes=10)
        self.guild.get_role(self.game['role_id']).permissions = discord.Permissions(administrator=True)
        interaction = self.interaction()
        interaction.client = SimpleNamespace(get_guild=lambda _: self.guild)
        await AddGameAndJoinView(event['id'], self.guild.id, self.member.id).add_and_join.callback(interaction)
        self.member.add_roles.assert_not_awaited()


class SuggestionSecurityTests(unittest.IsolatedAsyncioTestCase):
    setUp = suggestion_fixtures.SuggestionTests.setUp
    asyncSetUp = suggestion_fixtures.SuggestionTests.asyncSetUp

    async def test_varied_spam_is_throttled_and_exact_retry_idempotent(self):
        with patch.object(suggestions.time, 'time', return_value=100):
            await suggestions.submit(self.interaction, 'First', 'Idea')
            await suggestions.submit(self.interaction, 'Second', 'Different idea')
            self.assertIn('30 seconds', self.interaction.followup.send.call_args.args[0])
            await suggestions.submit(self.interaction, 'First', 'Idea')
        self.inbox.send.assert_awaited_once()
        with patch.object(suggestions.time, 'time', return_value=131):
            await suggestions.submit(self.interaction, 'Second', 'Different idea')
        self.assertEqual(self.inbox.send.await_count, 2)

    async def test_staff_revoked_during_acknowledgement_cannot_review(self):
        await suggestions.submit(self.interaction, 'Title', 'Idea')
        self.interaction.user.roles.append(self.guild.mod)
        current = SimpleNamespace(id=self.interaction.user.id, guild=self.guild, roles=[self.guild.default_role])
        async def revoked(**kwargs): self.guild.members = [current]
        self.interaction.response.defer.side_effect = revoked
        await suggestions.StaffSuggestionView().change_status(self.interaction, 'ACCEPTED')
        self.assertEqual(suggestions.get_suggestion(777, self.guild.id)['status'], 'NEW')
        self.interaction.message.edit.assert_not_awaited()


class RepairSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_repair_serialized(self):
        active = 0
        peak = 0
        async def run(*args):
            nonlocal active, peak
            active += 1; peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return [], []
        guild = SimpleNamespace(id=123)
        with patch.object(setup, '_repair_server', side_effect=run):
            await asyncio.gather(setup.repair_server(guild), setup.repair_server(guild))
        self.assertEqual(peak, 1)

    async def test_owner_changed_while_repair_queued_is_denied(self):
        guild = SimpleNamespace(id=456, owner_id=2)
        lock = setup._repair_locks.setdefault(guild.id, asyncio.Lock())
        await lock.acquire()
        with patch.object(setup, '_repair_server', new_callable=AsyncMock) as apply:
            queued = asyncio.create_task(setup.repair_server(guild, owner_id=2))
            await asyncio.sleep(0)
            guild.owner_id = 3
            lock.release()
            with self.assertRaises(ValueError): await queued
            apply.assert_not_awaited()

    async def test_owner_confirmation_rechecks_actor_and_guild(self):
        guild = SimpleNamespace(id=1, owner_id=2)
        view = server.ConfirmServerRepairView(guild=guild, owner_id=2)
        for user_id, guild_id in [(3, 1), (2, 9)]:
            interaction = SimpleNamespace(user=SimpleNamespace(id=user_id), guild=SimpleNamespace(id=guild_id),
                response=SimpleNamespace(send_message=AsyncMock()))
            with patch.object(setup, 'repair_server', new_callable=AsyncMock) as repair:
                await view.confirm.callback(interaction)
                repair.assert_not_awaited()
                interaction.response.send_message.assert_awaited_once()


class ErrorAndInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_errors_are_private_without_internal_detail(self):
        for error in [app_commands.MissingPermissions(['administrator']),
                      app_commands.BotMissingPermissions(['manage_roles']),
                      app_commands.CommandOnCooldown(app_commands.Cooldown(1, 10), 5)]:
            interaction = SimpleNamespace(response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))
            await responses.application_error(interaction, error)
            self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_tree_handler_does_not_duplicate_cog_error(self):
        interaction = SimpleNamespace(command=SimpleNamespace(_has_any_error_handlers=lambda: True))
        with patch.object(responses, 'application_error', new_callable=AsyncMock) as handler:
            await responses.tree_error(interaction, ValueError('private error'))
            handler.assert_not_awaited()

    async def test_url_controls_schemes_and_credentials_rejected(self):
        for url in ['https://example.com/\x00hidden', '\x00https://example.com',
                    'https://example.com/\x7f', 'javascript:alert(1)', 'data:text/plain,hi',
                    'file:///etc/passwd', 'https://' + 'user:password' + '@example.com',
                    'https://example.com/?access_token=sensitive']:
            with self.subTest(url=repr(url)), self.assertRaises(ServerMessageError): validate_url(url)
        validate_url('https://example.com/?ref=gamerhq&offer=123')


class VoiceReplayTests(unittest.IsolatedAsyncioTestCase):
    setUp = voice_fixtures.VoiceTests.setUp
    asyncSetUp = voice_fixtures.VoiceTests.asyncSetUp
    role = voice_fixtures.VoiceTests.role
    channel = voice_fixtures.VoiceTests.channel
    link_game = voice_fixtures.VoiceTests.link_game

    async def test_duplicate_voice_events_create_one_room(self):
        from cogs.voice import VoiceGenerator
        self.guild.create_voice_channel = AsyncMock(return_value=self.room)
        self.actor.move_to = AsyncMock()
        self.actor.display_name = 'Host'
        cog = VoiceGenerator(SimpleNamespace())
        args = (self.actor, SimpleNamespace(channel=None), SimpleNamespace(channel=self.generator))
        await asyncio.gather(cog.on_voice_state_update(*args), cog.on_voice_state_update(*args))
        self.guild.create_voice_channel.assert_awaited_once()

    async def test_missing_game_category_cannot_create_public_root_room(self):
        from cogs.voice import VoiceGenerator
        self.guild.channels.remove(self.category)
        self.actor.display_name = 'Host'
        self.guild.create_voice_channel = AsyncMock()
        await VoiceGenerator(SimpleNamespace()).on_voice_state_update(
            self.actor, SimpleNamespace(channel=None), SimpleNamespace(channel=self.generator))
        self.guild.create_voice_channel.assert_not_awaited()

    async def test_departed_voice_owner_denied(self):
        self.guild.get_member.side_effect = lambda _: None
        with self.assertRaises(ValueError): await voice.act(self.guild, self.actor, self.room.id, 'close')
        self.room.delete.assert_not_awaited()


class TicketStaleStaffTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ticket_fixtures.TicketTests.asyncSetUp
    member = ticket_fixtures.TicketTests.member
    create = ticket_fixtures.TicketTests.create

    async def test_stale_staff_snapshot_cannot_take_ticket(self):
        item = await self.create()
        stale = self.mod
        self.member(stale.id, staff=False)
        with self.assertRaises(ValueError): await tickets.change(self.guild, stale, item['id'], 'take')
        self.assertEqual(tickets.get(item['id'])['status'], 'OPEN')

    async def test_uncertain_private_channel_creation_retains_event_claim(self):
        from cogs.lfg import create_private_event_channel
        game = db.upsert_custom_game('Private lobby test', 'G', 'Test')
        args = dict(guild_id=self.guild.id, game_id=game['id'], host_id=self.a.id,
                    title='Private play', start_at=2000000000, max_players=5, invite_lead_minutes=10,
                    visibility='private', enforce_member_limits=True)
        event = db.create_lfg_event(**args)
        failure = discord.HTTPException(SimpleNamespace(status=500, reason='Server Error'), 'Uncertain delivery')
        with patch.object(self.guild, 'create_text_channel', new_callable=AsyncMock, side_effect=failure, create=True):
            with self.assertRaises(discord.HTTPException):
                await create_private_event_channel(self.guild, event, game)
        self.assertEqual(db.get_lfg_event(event['id'])['status'], 'scheduled')
        with self.assertRaises(ValueError): db.create_lfg_event(**args)

    async def test_lfg_limits_persist_and_duplicate_new_draft_is_denied(self):
        game = db.upsert_custom_game('Lobby test', 'G', 'Test')
        args = dict(guild_id=self.guild.id, game_id=game['id'], host_id=self.a.id,
                    title='Play', start_at=2000000000, max_players=5, invite_lead_minutes=10,
                    enforce_member_limits=True)
        with patch.object(db.time, 'time', return_value=100):
            first = db.create_lfg_event(**args)
            with self.assertRaises(ValueError): db.create_lfg_event(**{**args, 'title': 'Spam'})
        with patch.object(db.time, 'time', return_value=131):
            with self.assertRaises(ValueError): db.create_lfg_event(**args)
            second = db.create_lfg_event(**{**args, 'title': 'Different'})
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(db.get_active_lfg_events(self.guild.id)), 2)

    async def test_legacy_streamer_mutations_recheck_staff(self):
        from cogs.streamer import Streamer
        cog = Streamer(SimpleNamespace())
        interaction = SimpleNamespace(guild=self.guild, user=self.a,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()))
        for method, args in [(cog.create_streamer_channel, ('test', 'text')),
                             (cog.rename_streamer_channel, (123, 'test')),
                             (cog.delete_streamer_channel, (123,))]:
            await method(interaction, *args)
        self.assertEqual(interaction.response.send_message.await_count, 3)


class BackgroundRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lfg_inventory_database_failure_does_not_stop_next_cycle(self):
        import sqlite3
        from cogs.lfg import LFG
        cog = object.__new__(LFG)
        cog.bot = SimpleNamespace(guilds=[SimpleNamespace(id=1)])
        with patch('cogs.lfg.cleanup_ended', new_callable=AsyncMock), patch.object(
                db, 'get_active_lfg_events', side_effect=[sqlite3.OperationalError('busy'), []]) as inventory:
            with self.assertLogs('cogs.lfg', level='ERROR'):
                await LFG.voice_scheduler.coro(cog)
            await LFG.voice_scheduler.coro(cog)
            self.assertEqual(inventory.call_count, 2)

    async def test_channel_maintenance_database_failure_retries_next_cycle(self):
        import sqlite3
        from cogs.server_changes import ServerChanges, changes
        cog = ServerChanges(SimpleNamespace())
        with patch.object(changes, 'expire', side_effect=[sqlite3.OperationalError('busy'), None]) as expire, patch.object(changes, 'records', return_value=[]):
            with self.assertLogs('cogs.server_changes', level='ERROR'):
                await ServerChanges.maintenance.coro(cog)
            await ServerChanges.maintenance.coro(cog)
            self.assertEqual(expire.call_count, 2)
