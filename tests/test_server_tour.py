"""Get Started opens a read-only, member-bound tour over existing GamerHQ features."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import test_onboarding as fixtures
from cogs.server_tour import ServerTour, STEPS
from cogs.roles import OnboardingEntry


class ServerTourTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    def member(self, uid=42):
        member = SimpleNamespace(id=uid, guild=self.guild, roles=[])
        self.guild.members = [member]
        return member

    def interaction(self, member=None):
        member = member or self.member()
        return SimpleNamespace(
            guild=self.guild,
            user=member,
            response=SimpleNamespace(
                send_message=AsyncMock(),
                edit_message=AsyncMock(),
                defer=AsyncMock(),
                is_done=lambda: False,
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_get_started_opens_tour_not_profile(self):
        interaction = self.interaction()
        entry = OnboardingEntry()
        with patch('cogs.server_tour.open_tour', new_callable=AsyncMock) as tour,              patch('cogs.roles.open_profile', new_callable=AsyncMock) as profile:
            await entry.start.callback(interaction)
        tour.assert_awaited_once_with(interaction)
        profile.assert_not_awaited()

    async def test_tour_is_six_steps_and_navigation_changes_no_state(self):
        member = self.member()
        view = ServerTour(self.guild, member.id)
        self.assertEqual(len(STEPS), 6)
        self.assertEqual(view.step, 0)
        self.assertIn('quick tour', view.content(member).lower())

        request = self.interaction(member)
        await view.next(request)
        self.assertEqual(view.step, 1)
        self.assertIn('Choose your games', request.response.edit_message.call_args.kwargs['content'])

        request = self.interaction(member)
        await view.skip(request)
        self.assertEqual(view.step, 5)
        self.assertIn("You're ready", request.response.edit_message.call_args.kwargs['content'])

    async def test_games_step_reuses_canonical_selector(self):
        member = self.member()
        view = ServerTour(self.guild, member.id, step=1)
        labels = [c.label for c in view.children if isinstance(c, discord.ui.Button)]
        self.assertIn('Choose Games', labels)
        request = self.interaction(member)
        with patch('cogs.games.open_game_selector', new_callable=AsyncMock) as open_games:
            await view.choose_games(request)
        open_games.assert_awaited_once_with(request)

    async def test_profile_is_only_optional_final_action(self):
        member = self.member()
        view = ServerTour(self.guild, member.id, step=5)
        labels = [c.label for c in view.children if isinstance(c, discord.ui.Button)]
        self.assertIn('Update Optional Profile', labels)
        self.assertNotIn('Update Optional Profile', [
            c.label for c in ServerTour(self.guild, member.id, step=0).children
            if isinstance(c, discord.ui.Button)
        ])
        request = self.interaction(member)
        with patch('cogs.roles.open_profile', new_callable=AsyncMock) as profile:
            await view.profile(request)
        profile.assert_awaited_once_with(request)

    async def test_channel_links_use_existing_resources_and_omit_missing_targets(self):
        member = self.member()
        general = self.guild.add_channel('💬・general', self.community)
        view = ServerTour(self.guild, member.id, step=2)
        links = {c.label: c.url for c in view.children if getattr(c, 'url', None)}
        self.assertIn('Open General', links)
        self.assertIn(str(general.id), links['Open General'])
        self.assertIn('Open Introductions', links)

        # Community Events is intentionally absent in this fixture; the LFG step
        # must not promise a broken destination.
        view = ServerTour(self.guild, member.id, step=3)
        labels = [c.label for c in view.children if isinstance(c, discord.ui.Button)]
        self.assertIn('Open LFG', labels)
        self.assertNotIn('Open Events', labels)

    async def test_other_member_and_expired_session_are_denied(self):
        owner = self.member()
        view = ServerTour(self.guild, owner.id)
        other = SimpleNamespace(id=999, guild=self.guild, roles=[])
        request = self.interaction(other)
        await view.next(request)
        self.assertEqual(view.step, 0)
        request.response.send_message.assert_awaited_once()

        view.expires = 0
        request = self.interaction(owner)
        await view.next(request)
        self.assertEqual(view.step, 0)
        request.response.send_message.assert_awaited_once()

    async def test_finish_closes_session_without_role_or_channel_changes(self):
        member = self.member()
        view = ServerTour(self.guild, member.id, step=5)
        before_channels = [(c.id, c.name) for c in self.guild.channels]
        request = self.interaction(member)
        await view.finish(request)
        self.assertTrue(view.closed)
        self.assertEqual(before_channels, [(c.id, c.name) for c in self.guild.channels])
        self.assertIn('Tour complete', request.response.edit_message.call_args.kwargs['content'])
