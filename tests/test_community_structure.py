"""Core structure and private suggestion workflow regression tests; no Discord login."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import test_onboarding as fixtures
from database import db
from services import community_structure_service as structure
from services import onboarding_service as onboarding
from services import server_setup_service as setup
from services.lfg_service import find_lfg_channel
from services.server_service import ServerMessageError
from cogs import suggestions


class StructureTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    async def test_moves_preserve_ids_history_event_overrides_and_active_lobbies(self):
        lfg = structure.core_channel(self.guild, 'looking-for-group')
        tournament = structure.core_channel(self.guild, 'tournaments')
        giveaway = structure.core_channel(self.guild, 'giveaways')
        active = self.guild.add_channel('active-lobbies', self.community)
        custom = discord.PermissionOverwrite(view_channel=False, attach_files=True)
        tournament.overwrites[self.guild.custom] = custom
        lfg.overwrites[self.guild.default_role] = discord.PermissionOverwrite(use_application_commands=True, add_reactions=True)
        history = self.add_message(lfg, 'Existing LFG card', author=20)
        game = self.guild.add_category('Some game')
        per_game = self.guild.add_channel('looking-for-group', game)
        self.guild.text_channels.remove(per_game); self.guild.text_channels.insert(0, per_game)
        ids = [lfg.id, tournament.id, giveaway.id]
        _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertFalse(failed)
        self.assertEqual(ids, [lfg.id, tournament.id, giveaway.id])
        self.assertIs(lfg.category, self.start)
        self.assertEqual(onboarding.alias(tournament.category.name), 'events')
        self.assertIs(tournament.category, giveaway.category)
        self.assertEqual(tournament.overwrites[self.guild.custom], custom)
        self.assertFalse(history.deleted)
        self.assertFalse(lfg.overwrites[self.guild.default_role].send_messages)
        self.assertTrue(lfg.overwrites[self.guild.default_role].use_application_commands)
        self.assertTrue(lfg.overwrites[self.guild.default_role].add_reactions)
        self.assertTrue(lfg.overwrites[self.guild.me].send_messages)
        self.assertEqual(active.edits, [])
        self.assertEqual(per_game.edits, [])
        self.assertIs(find_lfg_channel(self.guild), lfg)
        self.assertIn(lfg.mention, structure.guide_text(self.guild))
        self.assertNotIn(per_game.mention, structure.guide_text(self.guild))

    async def test_setup_twice_and_lost_mappings_keep_single_canonical_pins(self):
        await setup.repair_server(self.guild, self.bot)
        guide = structure.core_channel(self.guild, 'guide')
        public = structure.core_channel(self.guild, 'suggestions')
        inbox = suggestions.inbox(self.guild)
        user = self.add_message(guide, '# 📘 GamerHQ Guide', author=20, pinned=True)
        unrelated = self.add_message(public, 'Unrelated team notice', pinned=True)
        ids = [c.id for c in self.guild.channels]
        db.set_setting(f'central_guide:{self.guild.id}', '')
        db.set_setting(f'suggestions_entry:{self.guild.id}', '')
        await setup.repair_server(self.guild, self.bot)
        self.assertEqual(ids, [c.id for c in self.guild.channels])
        self.assertEqual(guide.sends, 1)
        self.assertEqual(public.sends, 1)
        self.assertFalse(user.deleted or unrelated.deleted)
        self.assertEqual(sum(onboarding.alias(c.name) == 'events' for c in self.guild.categories), 1)
        self.assertIs(suggestions.inbox(self.guild), inbox)
        for ch in (guide, public):
            self.assertFalse(ch.overwrites[self.guild.default_role].send_messages)
            self.assertTrue(ch.overwrites[self.guild.default_role].view_channel)
        self.assertTrue(self.intro.overwrites[self.guild.default_role].send_messages)

    async def test_staff_inbox_private_from_creation_and_existing_exposure_repaired(self):
        await setup.repair_server(self.guild, self.bot)
        inbox = suggestions.inbox(self.guild)
        self.assertFalse(inbox.overwrites[self.guild.default_role].view_channel)
        self.assertTrue(inbox.overwrites[self.guild.mod].view_channel)
        self.assertTrue(inbox.overwrites[self.guild.mod].send_messages)
        self.assertTrue(inbox.overwrites[self.guild.me].manage_messages)
        inbox.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True)
        with self.assertRaises(ServerMessageError): suggestions.inbox(self.guild)
        await setup.repair_server(self.guild, self.bot)
        self.assertIs(suggestions.inbox(self.guild), inbox)
        self.assertFalse(inbox.overwrites[self.guild.custom].view_channel)

    async def test_missing_staff_reports_without_creating_staff(self):
        self.guild.categories.remove(self.staff)
        _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertTrue(any('No suitable STAFF' in f for f in failed))
        self.assertFalse(any(onboarding.alias(c.name) == 'staff' for c in self.guild.categories))
        with self.assertRaises(ServerMessageError): suggestions.inbox(self.guild)

    async def test_many_game_roles_do_not_exhaust_private_channel_overwrites(self):
        self.guild.roles.extend(self.guild.role(500 + n) for n in range(150))
        await setup.repair_server(self.guild, self.bot)
        inbox = suggestions.inbox(self.guild)
        self.assertEqual(set(inbox.overwrites), {self.guild.default_role, self.guild.mod, self.guild.me})

    async def test_duplicate_core_lfg_not_replaced_and_pergame_only_not_selected(self):
        lfg = structure.core_channel(self.guild, 'looking-for-group')
        self.guild.text_channels.remove(lfg)
        game = self.guild.add_category('Game')
        self.guild.add_channel('looking-for-group', game)
        self.assertIsNone(find_lfg_channel(self.guild))
        _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertTrue(any('looking-for-group' in f for f in failed))
        self.guild.add_channel('looking-for-group', self.start)
        self.guild.add_channel('looking-for-group', self.community)
        with self.assertRaises(ServerMessageError): find_lfg_channel(self.guild)


class SuggestionTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    async def asyncSetUp(self):
        await setup.repair_server(self.guild, self.bot)
        self.guild.owner_id = 99
        self.inbox = suggestions.inbox(self.guild)
        self.inbox.send = AsyncMock(return_value=SimpleNamespace(id=777))
        self.interaction = SimpleNamespace(guild=self.guild, user=SimpleNamespace(id=20, roles=[self.guild.default_role], guild=self.guild),
            response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock(), send_modal=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
            channel_id=self.inbox.id, message=SimpleNamespace(id=777, edit=AsyncMock()))

    async def test_submission_is_private_persisted_and_acknowledged_ephemerally(self):
        await suggestions.submit(self.interaction, ' More events ', ' Hearthstone please ', ' Fun ')
        kwargs = self.inbox.send.call_args.kwargs
        self.assertIsInstance(kwargs['view'], suggestions.StaffSuggestionView)
        self.assertEqual(kwargs['embed'].fields[1].value, 'More events')
        self.assertEqual(kwargs['embed'].fields[-1].value, 'NEW')
        self.assertEqual(kwargs['allowed_mentions'].to_dict(), discord.AllowedMentions.none().to_dict())
        item = suggestions.get_suggestion(777, self.guild.id)
        self.assertEqual(item['author_discord_id'], 20)
        self.assertEqual(item['content'], 'Hearthstone please')
        self.assertEqual(item['staff_channel_id'], self.inbox.id)
        self.interaction.followup.send.assert_awaited_once_with(suggestions.SUCCESS, ephemeral=True)
        public = structure.core_channel(self.guild, 'suggestions')
        self.assertEqual(public.sends, 1)  # Only the entrypoint.

    async def test_modal_fields_entry_button_and_whitespace_validation(self):
        view = suggestions.SuggestionEntryView()
        self.assertTrue(view.is_persistent())
        await view.children[0].callback(self.interaction)
        modal = self.interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal, suggestions.SuggestionModal)
        self.assertEqual([f.required for f in modal.children], [True, True, False])
        self.assertEqual([f.label for f in modal.children], ['Title', 'Your Idea', 'Why would this be useful?'])
        for title, content, reason in [(' ', 'idea', ''), ('title', ' ', ''), ('x'*101, 'idea', ''), ('title', 'x'*1001, ''), ('title', 'idea', 'x'*1001)]:
            await suggestions.submit(self.interaction, title, content, reason)
        self.inbox.send.assert_not_awaited()
        self.assertEqual(self.interaction.response.send_message.await_count, 5)
        modal.heading._value = 'Title'
        modal.idea._value = 'An idea'
        modal.reason._value = ''
        await modal.on_submit(self.interaction)
        self.inbox.send.assert_awaited_once()

    async def test_normal_member_status_denied_and_persistent_staff_buttons_work(self):
        await suggestions.submit(self.interaction, 'Title', 'Idea')
        restarted = suggestions.StaffSuggestionView()
        self.assertTrue(restarted.is_persistent())
        await restarted.children[0].callback(self.interaction)
        self.assertEqual(suggestions.get_suggestion(777, self.guild.id)['status'], 'NEW')
        self.interaction.message.edit.assert_not_awaited()
        self.interaction.user.roles.append(self.guild.mod)
        for button, status in zip([restarted.children[i] for i in (0,1,3)], ['REVIEWING', 'ACCEPTED', 'IMPLEMENTED']):
            await button.callback(self.interaction)
            self.assertEqual(suggestions.get_suggestion(777, self.guild.id)['status'], status)
        self.assertEqual(self.interaction.message.edit.await_count, 3)
        self.interaction.user.roles.remove(self.guild.mod)
        await restarted.children[0].callback(self.interaction)
        self.assertEqual(suggestions.get_suggestion(777, self.guild.id)['status'], 'IMPLEMENTED')

    async def test_wrong_channel_guild_or_message_cannot_mutate(self):
        await suggestions.submit(self.interaction, 'Title', 'Idea')
        self.interaction.user.roles.append(self.guild.mod)
        self.interaction.channel_id = 123
        await suggestions.StaffSuggestionView().change_status(self.interaction, 'ACCEPTED')
        self.assertEqual(suggestions.get_suggestion(777, self.guild.id)['status'], 'NEW')
        self.assertIsNone(suggestions.get_suggestion(777, 999))
        self.interaction.channel_id = self.inbox.id
        self.interaction.message.id = 888
        await suggestions.StaffSuggestionView().change_status(self.interaction, 'ACCEPTED')
        self.interaction.message.edit.assert_not_awaited()

    async def test_private_channel_misconfiguration_blocks_delivery(self):
        self.inbox.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True)
        await suggestions.submit(self.interaction, 'Title', 'Idea')
        self.inbox.send.assert_not_awaited()
        self.assertNotEqual(self.interaction.followup.send.call_args.args[0], suggestions.SUCCESS)
        self.assertIsNone(suggestions.get_suggestion(777, self.guild.id))

    async def test_delivery_failure_never_reports_success_or_posts_publicly(self):
        self.inbox.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'test')
        await suggestions.submit(self.interaction, 'Title', 'Idea')
        self.assertNotEqual(self.interaction.followup.send.call_args.args[0], suggestions.SUCCESS)
        with db.connect() as conn:
            item = dict(conn.execute('SELECT * FROM suggestions').fetchone())
        self.assertIsNone(item['staff_message_id'])
        self.assertEqual(structure.core_channel(self.guild, 'suggestions').sends, 1)

    async def test_restart_registers_views_and_recovers_send_db_window(self):
        await suggestions.submit(self.interaction, 'Title', 'Idea')
        item = suggestions.get_suggestion(777, self.guild.id)
        with db.connect() as conn:
            conn.execute('UPDATE suggestions SET staff_message_id=NULL, status=? WHERE id=?', ('REVIEWING', item['id']))
        message = fixtures.FakeMessage(self.inbox, 777, embeds=[suggestions.card(item)])
        message.edit = AsyncMock()
        self.inbox.messages[777] = message
        await suggestions.reconcile_cards(self.guild)
        self.assertEqual(suggestions.get_suggestion(777, self.guild.id)['status'], 'REVIEWING')
        self.assertEqual(message.edit.call_args.kwargs['embed'].fields[-1].value, 'REVIEWING')
        bot = SimpleNamespace(add_view=MagicMock())
        await suggestions.Suggestions(bot).cog_load()
        self.assertEqual(bot.add_view.call_count, 2)
        self.assertTrue(all(call.args[0].is_persistent() for call in bot.add_view.call_args_list))
        self.assertEqual(self.inbox.send.await_count, 1)


if __name__ == '__main__': unittest.main()
