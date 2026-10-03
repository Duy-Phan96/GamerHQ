"""Friendly Server Check dashboard over the existing read-only health scan."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import test_onboarding as fixtures
from cogs.health import HealthView, dashboard, group_detail, group_for, grouped, open_server_check
from services.health_service import Finding


class ServerCheckDashboardTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp

    def owner(self):
        self.guild.owner_id = 42
        owner = SimpleNamespace(
            id=42,
            guild=self.guild,
            guild_permissions=discord.Permissions(administrator=True),
            roles=[],
        )
        self.guild.members = [owner]
        return owner

    def interaction(self, owner=None):
        owner = owner or self.owner()
        return SimpleNamespace(
            guild=self.guild,
            user=owner,
            client=self.bot,
            response=SimpleNamespace(
                defer=AsyncMock(),
                send_message=AsyncMock(),
                edit_message=AsyncMock(),
                is_done=lambda: False,
            ),
            edit_original_response=AsyncMock(),
        )

    def findings(self):
        return [
            Finding("Database", "PASS", "Required tables readable."),
            Finding("Game channel", "MANUAL_REVIEW", "Minecraft mapping needs review."),
            Finding("Create Voice", "PASS", "1 entry point detected."),
            Finding("Ticket System", "PASS", "Private support ready."),
            Finding("Amazon bot #amazon access", "REPAIRABLE", "Scoped overwrite drifted."),
            Finding("Staff suggestions privacy", "PASS", "Private inbox checks passed."),
        ]

    def test_findings_are_grouped_into_owner_friendly_areas(self):
        rows = grouped(self.findings())
        self.assertEqual(len(rows["Games"]), 1)
        self.assertEqual(len(rows["LFG & Voice"]), 1)
        self.assertEqual(len(rows["Support"]), 1)
        self.assertEqual(len(rows["Integrations"]), 1)
        self.assertEqual(len(rows["Security & Privacy"]), 1)
        self.assertEqual(group_for(Finding("Runtime / commands", "PASS", "ok")), "Core")
        self.assertEqual(group_for(Finding("Skill Runtime", "WARN", "review")), "Skills")

    def test_dashboard_shows_area_status_not_long_flat_finding_list(self):
        text = dashboard(self.findings())
        self.assertIn("# 🩺 GamerHQ Server Check", text)
        self.assertIn("Games", text)
        self.assertIn("Integrations", text)
        self.assertIn("areas need attention", text)
        self.assertNotIn("Minecraft mapping needs review", text)

    def test_selected_area_shows_only_important_findings_and_pass_count(self):
        text = group_detail(self.findings(), "Games")
        self.assertIn("Minecraft mapping needs review", text)
        self.assertNotIn("Required tables readable", text)
        self.assertIn("No repair is performed here", text)

    async def test_open_server_check_runs_fast_read_only_scan(self):
        interaction = self.interaction()
        rows = self.findings()
        with patch("cogs.health.health_service.scan", AsyncMock(return_value=rows)) as scan:
            await open_server_check(interaction)
        scan.assert_awaited_once_with(self.guild, self.bot, messages=False)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        final = interaction.edit_original_response.await_args.kwargs
        self.assertIn("GamerHQ Server Check", final["content"])
        self.assertIsInstance(final["view"], HealthView)

    async def test_refresh_and_deep_check_reuse_same_health_service(self):
        owner = self.owner()
        view = HealthView(self.guild, owner.id, self.findings())
        interaction = self.interaction(owner)
        fast = self.findings()
        with patch("cogs.health.health_service.scan", AsyncMock(return_value=fast)) as scan:
            await view.refresh(interaction)
        scan.assert_awaited_once_with(self.guild, self.bot, messages=False)

        interaction = self.interaction(owner)
        with patch("cogs.health.health_service.scan", AsyncMock(return_value=fast)) as scan, \
             patch("cogs.health.health_service.command_inventory", return_value=[("server health", "check")]):
            await view.details(interaction)
        scan.assert_awaited_once_with(self.guild, self.bot, messages=True)
        final = interaction.edit_original_response.await_args.kwargs
        self.assertIn("Complete technical results are attached", final["content"])
        self.assertEqual(final["attachments"][0].filename, "gamerhq-server-check.txt")

    async def test_area_selection_changes_no_server_or_database_state(self):
        owner = self.owner()
        view = HealthView(self.guild, owner.id, self.findings())
        before_channels = [(c.id, c.name) for c in self.guild.channels]
        view.selected = "Games"
        view.rebuild()
        self.assertEqual(before_channels, [(c.id, c.name) for c in self.guild.channels])
        self.assertIn("Minecraft mapping needs review", view.text())

    async def test_other_member_cannot_use_existing_server_check_session(self):
        owner = self.owner()
        view = HealthView(self.guild, owner.id, self.findings())
        other = SimpleNamespace(id=999, guild=self.guild, guild_permissions=discord.Permissions.none(), roles=[])
        interaction = self.interaction(other)
        allowed = await view.interaction_check(interaction)
        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once()
