"""Offline ServerHome/PartialChannel compatibility contract.

The upstream discord.py PR for protocol type 20 is not a released dependency.
These synthetic models exercise its minimum public fields without importing,
installing or monkey-patching its unreleased source. No live API calls.
"""
import unittest
from types import SimpleNamespace

import discord

from tools.game_launch_audit_collect import (
    AuditUnavailable, finalize_projection, verified_rest_inventory,
)


class PartialChannelLike:
    """Deliberately lacks TextChannel/category operations and ACL semantics."""

    def __init__(self, guild, identifier):
        self.guild = guild
        self.id = identifier
        self.name = "SYNTHETIC_PRIVATE_SERVERHOME"
        self.category_id = None
        self.position = 0
        self.type = 20

    def permissions_for(self, _):
        raise AssertionError("Opaque ServerHome permissions are not verified")


class ServerHomeContractTests(unittest.TestCase):
    def setUp(self):
        self.guild = SimpleNamespace(id=111)
        self.text = SimpleNamespace(id=222, guild=self.guild)
        self.roles = [SimpleNamespace(id=111, guild=self.guild)]
        self.raw_roles = [{'id': '111'}]
        self.raw = [
            {'id': '222', 'type': 0, 'name': 'synthetic-game'},
            {'id': '333', 'type': 20, 'name': 'SYNTHETIC_PRIVATE_SERVERHOME'},
        ]

    def test_missing_or_partial_model_never_promotes_ready(self):
        for modeled in (False, True):
            with self.subTest(modeled=modeled):
                channels = [self.text]
                if modeled:
                    channels.append(PartialChannelLike(self.guild, 333))
                self.guild.channels = channels
                self.guild.roles = self.roles
                inventory = verified_rest_inventory(
                    self.guild, self.raw, self.raw_roles,
                    allow_opaque_type20=True)
                self.assertEqual(inventory[2], 'OPAQUE_TYPE20_UNVERIFIED')
                self.assertEqual(len(inventory[0]), 2 if modeled else 1)
                if modeled:
                    self.assertNotIsInstance(channels[-1], discord.TextChannel)
                    self.assertNotIsInstance(channels[-1], discord.CategoryChannel)
                receipt = finalize_projection(
                    {'inventory': {'state': 'UNVERIFIED',
                                   'reason': 'DISCORD_OPAQUE_CHANNEL_TYPE20'},
                     'games': [{'status': 'UNVERIFIED'}],
                     'gamesCategory': {'state': 'UNVERIFIED'}}, 3)
                self.assertEqual(receipt['state'], 'GAMES_AUDIT_UNAVAILABLE')
                self.assertEqual(receipt['reason'], 'DISCORD_OPAQUE_CHANNEL_TYPE20')
                self.assertEqual(receipt['games'][0]['status'], 'UNVERIFIED')

    def test_unknown_serverhome_duplicate_models_or_extra_resources_fail_closed(self):
        self.guild.channels = [self.text, PartialChannelLike(self.guild, 333)]
        self.guild.roles = self.roles
        scenarios = [
            # Extra, otherwise modeled, non-REST object breaks exact ID coverage.
            ([self.text, PartialChannelLike(self.guild, 333),
              PartialChannelLike(self.guild, 444)], self.raw),
            # Two REST ServerHome resources require a new scoped review.
            ([self.text, PartialChannelLike(self.guild, 333),
              PartialChannelLike(self.guild, 444)],
             self.raw + [{'id': '444', 'type': 20}]),
        ]
        for models, rows in scenarios:
            with self.subTest(rows=len(rows), models=len(models)):
                self.guild.channels = models
                with self.assertRaisesRegex(
                        AuditUnavailable, '^DISCORD_CHANNEL_MODEL_INCOMPLETE$'):
                    verified_rest_inventory(
                        self.guild, rows, self.raw_roles,
                        allow_opaque_type20=True)

    def test_type20_can_never_impersonate_a_games_text_channel(self):
        # Even if a future library accidentally constructs a plausible-looking
        # model, the REST protocol type is decisive and requires review.
        self.guild.channels = [self.text, SimpleNamespace(
            id=333, guild=self.guild, name='synthetic-game',
            category_id=None, overwrites={}, type=0)]
        self.guild.roles = self.roles
        inventory = verified_rest_inventory(
            self.guild, self.raw, self.raw_roles, allow_opaque_type20=True)
        self.assertEqual(inventory[2], 'OPAQUE_TYPE20_UNVERIFIED')
