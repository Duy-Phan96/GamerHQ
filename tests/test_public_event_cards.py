"""Offline public-card lifecycle, privacy and retry contracts."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from database import db
from services import public_event_cards as cards


def event(event_id, visibility="public", status="active", **config):
    return {"id": event_id, "ownerId": "123", "status": status, "config": {
        "name": "Ranked Night", "game": "League of Legends", "description": "Find teammates.",
        "visibility": visibility, "kind": "weekly", "durationMinutes": 60, "maxPlayers": 5, **config}}


def occurrence(event_id, start, status="scheduled", participants=("123",)):
    return {"eventId": event_id, "startAt": start, "status": status, "participants": list(participants)}


def missing():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "missing")


class FakeChannel:
    __class__ = discord.TextChannel

    def __init__(self, guild, channel_id=20):
        self.guild, self.id = guild, channel_id
        self.messages = {}
        self.sends = 0
        self.lose_response = False

    async def send(self, *, embed, view, **kwargs):
        self.sends += 1
        mid = 100 + self.sends
        msg = SimpleNamespace(id=mid, author=self.guild.me, embeds=[embed], view=view, edits=0)

        async def edit(*, embed, view, **kwargs):
            msg.embeds, msg.view = [embed], view
            msg.edits += 1

        async def delete():
            self.messages.pop(mid)

        msg.edit, msg.delete = AsyncMock(side_effect=edit), AsyncMock(side_effect=delete)
        self.messages[mid] = msg
        if self.lose_response:
            raise TimeoutError()
        return msg

    async def fetch_message(self, mid):
        if mid not in self.messages:
            raise missing()
        return self.messages[mid]

    async def history(self, **kwargs):
        for msg in self.messages.values():
            yield msg


class PublicCardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(tmp.name) / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        cards._LOCKS.clear()
        self.guild = SimpleNamespace(id=10, me=SimpleNamespace(id=99))
        self.channel = FakeChannel(self.guild)
        self.guild.fetch_channel = AsyncMock(return_value=self.channel)
        db.set_setting("managed_channel:10:looking-for-group", self.channel.id)

    def snapshot(self, *events, rows):
        return {"events": list(events), "occurrences": list(rows)}

    def test_projection_is_public_only_and_uses_next_open_occurrence(self):
        snapshot = self.snapshot(event("public"), event("private", "private"),
            event("paused", status="paused"), event("deleted", status="deleted"),
            event("expired"), event("no-chat", chatEnabled=False),
            rows=[occurrence("public", 20000), occurrence("public", 10000, "cancelled"),
                  occurrence("public", 11000, participants=("123", "456")),
                  occurrence("private", 12000), occurrence("paused", 12000),
                  occurrence("deleted", 12000), occurrence("expired", 100),
                  occurrence("no-chat", 12000)])
        result = cards.project(snapshot, now=10000)
        self.assertEqual(set(result), {"public", "no-chat"})
        card = result["public"]
        self.assertEqual((card.start_at, card.registered, card.capacity), (11000, 2, 5))
        self.assertTrue(card.recurring)
        self.assertNotIn("private", repr(result))

    def test_public_render_controls_are_bounded_and_full_join_disabled(self):
        card = cards.project(self.snapshot(event("one", maxPlayers=1),
            rows=[occurrence("one", 11000)]), now=10000)["one"]
        self.assertEqual(cards.render(10, card).footer.text, cards.marker(10, "one"))
        view = cards.controls(10, card)
        self.assertEqual([item.label for item in view.children], ["Join", "Leave", "Details", "Share"])
        self.assertTrue(view.children[0].disabled)
        self.assertFalse(view.children[1].disabled)

    async def test_repeat_restart_and_participant_changes_update_same_message(self):
        one = cards.project(self.snapshot(event("one"), rows=[occurrence("one", 11000)]), now=10000)["one"]
        await cards.publish(self.guild, self.channel, one)
        state = cards.load(10, "one")
        mid = state["messageId"]
        await cards.publish(self.guild, self.channel, one, cards.records(10)["one"])
        self.assertEqual(self.channel.sends, 1)
        self.assertEqual(self.channel.messages[mid].edits, 0)
        two = cards.project(self.snapshot(event("one"), rows=[occurrence("one", 12000, participants=("123", "456"))]),
                            now=10000)["one"]
        await cards.publish(self.guild, self.channel, two, cards.load(10, "one"))
        self.assertEqual(self.channel.sends, 1)
        self.assertEqual(self.channel.messages[mid].edits, 1)
        self.assertEqual(cards.load(10, "one")["startAt"], 12000)
        self.assertEqual(cards.resolve_control(self.guild, self.channel, self.channel.messages[mid],
            cards.card_token(10, "one"), 12000), ("one", 12000))
        self.assertIsNone(cards.resolve_control(self.guild, self.channel, self.channel.messages[mid],
            cards.card_token(10, "one"), 11000))

    async def test_lost_send_response_recovers_existing_marker_without_duplicate(self):
        card = cards.project(self.snapshot(event("one"), rows=[occurrence("one", 11000)]), now=10000)["one"]
        self.channel.lose_response = True
        with self.assertRaises(TimeoutError):
            await cards.publish(self.guild, self.channel, card)
        self.assertTrue(cards.load(10, "one")["pending"])
        await cards.publish(self.guild, self.channel, card, cards.load(10, "one"))
        self.assertEqual(self.channel.sends, 1)
        self.assertFalse(cards.load(10, "one")["pending"])

    async def test_privacy_barrier_retires_only_owned_message_and_blocks_unverified(self):
        card = cards.project(self.snapshot(event("one"), rows=[occurrence("one", 11000)]), now=10000)["one"]
        await cards.publish(self.guild, self.channel, card)
        mid = cards.load(10, "one")["messageId"]
        msg = self.channel.messages[mid]
        runtime = SimpleNamespace(_guild=lambda guild_id: self.guild)
        msg.author = SimpleNamespace(id=777)
        with self.assertRaisesRegex(ValueError, "not verified"):
            await cards.before_private_update(runtime, 10, {"eventId": "one", "visibility": "private"})
        self.assertIsNotNone(cards.load(10, "one"))
        msg.delete.assert_not_awaited()
        msg.author = self.guild.me
        await cards.before_private_update(runtime, 10, {"eventId": "one", "visibility": "private"})
        self.assertIsNone(cards.load(10, "one"))
        msg.delete.assert_awaited_once()
        self.assertEqual(self.channel.messages, {})
