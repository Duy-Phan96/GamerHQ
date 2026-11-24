"""Offline proof that the sealed collector never silently drops REST resources."""
import unittest
from types import SimpleNamespace

from tools.game_launch_audit_collect import AuditUnavailable, verified_rest_inventory


class VerifiedRestInventoryTests(unittest.TestCase):
    def setUp(self):
        self.guild = SimpleNamespace(id=111)
        self.channel = SimpleNamespace(id=222, guild=self.guild)
        self.role = SimpleNamespace(id=333, guild=self.guild)
        self.guild.channels = [self.channel]
        self.guild.roles = [self.role]
        self.channels = [{'id': '222'}]
        self.roles = [{'id': '333'}]

    def test_complete_model_id_coverage_is_accepted(self):
        channels, roles = verified_rest_inventory(self.guild, self.channels, self.roles)
        self.assertEqual(channels, [self.channel])
        self.assertEqual(roles, [self.role])

    def test_unmodeled_channel_is_explicit_failure_not_false_ready(self):
        with self.assertRaisesRegex(AuditUnavailable, '^DISCORD_CHANNEL_MODEL_INCOMPLETE$'):
            verified_rest_inventory(self.guild,
                                    self.channels + [{'id': '444', 'type': 999}],
                                    self.roles)

    def test_missing_channel_types_are_aggregate_only(self):
        diagnostic = {}
        raw = self.channels + [
            {'id': '444', 'type': 16, 'name': 'SECRET_PRIVATE_NAME'},
            {'id': '445', 'type': 16, 'name': 'SECRET_PRIVATE_NAME'},
            {'id': '446', 'type': 9999, 'name': 'SECRET_PRIVATE_NAME'},
        ]
        with self.assertRaisesRegex(AuditUnavailable, '^DISCORD_CHANNEL_MODEL_INCOMPLETE$'):
            verified_rest_inventory(self.guild, raw, self.roles, diagnostic=diagnostic)
        self.assertEqual(diagnostic['channelModelCoverage'], {
            'rawCount': 4, 'modeledCount': 1, 'missingCount': 3,
            'missingByType': {'16': 2, 'OTHER': 1}, 'extraModelCount': 0,
        })
        import json
        serialized = json.dumps(diagnostic)
        for private in ('444', '445', '446', '9999', 'SECRET_PRIVATE_NAME'):
            self.assertNotIn(private, serialized)

    def test_no_diagnostic_on_success(self):
        diagnostic = {}
        verified_rest_inventory(self.guild, self.channels, self.roles, diagnostic=diagnostic)
        self.assertEqual(diagnostic, {})

    def test_unmodeled_role_is_explicit_failure(self):
        with self.assertRaisesRegex(AuditUnavailable, '^DISCORD_ROLE_MODEL_INCOMPLETE$'):
            verified_rest_inventory(self.guild, self.channels,
                                    self.roles + [{'id': '555'}])

    def test_foreign_model_is_explicit_failure(self):
        self.channel.guild = SimpleNamespace(id=999)
        with self.assertRaisesRegex(AuditUnavailable, '^DISCORD_CHANNEL_MODEL_INCOMPLETE$'):
            verified_rest_inventory(self.guild, self.channels, self.roles)

    def test_duplicate_model_id_is_explicit_failure(self):
        self.guild.channels.append(SimpleNamespace(id=222, guild=self.guild))
        with self.assertRaisesRegex(AuditUnavailable, '^DISCORD_CHANNEL_MODEL_INCOMPLETE$'):
            verified_rest_inventory(self.guild, self.channels, self.roles)
