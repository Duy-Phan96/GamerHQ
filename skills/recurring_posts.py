"""Portable Recurring Posts reference Skill.

The Skill owns post configuration and uses only public Skill Runtime contracts.
Discord.py, GamerHQ database helpers and host services are intentionally absent.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import time
import uuid
from collections.abc import Mapping
from typing import Any

from skill_runtime import (
    EventContract,
    EventEnvelope,
    SkillCapability,
    SkillEvents,
    SkillHealth,
    SkillManifest,
)
from skill_runtime.contracts.schedule import (
    DailySchedule,
    IntervalSchedule,
    WeeklySchedule,
    schedule_from_dict,
    schedule_to_dict,
)

SKILL_ID = "recurring-posts"
HANDLER_ID = "recurring-post.execute.v1"
SENT_EVENT_ID = "recurring-post.sent.v1"
STORAGE_KEY = "posts.v1"
MAX_POSTS = 20
MIN_INTERVAL_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class RecurringPost:
    id: str
    name: str
    channel_id: int
    content: str
    schedule: Mapping[str, Any]
    active: bool = True
    pending_slot: int | None = None
    last_sent_slot: int | None = None
    last_message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "channelId": self.channel_id,
            "content": self.content,
            "schedule": dict(self.schedule),
            "active": self.active,
            "pendingSlot": self.pending_slot,
            "lastSentSlot": self.last_sent_slot,
            "lastMessageId": self.last_message_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RecurringPost":
        try:
            post = cls(
                id=str(value["id"]),
                name=str(value["name"]),
                channel_id=int(value["channelId"]),
                content=str(value["content"]),
                schedule=dict(value["schedule"]),
                active=bool(value.get("active", True)),
                pending_slot=_optional_int(value.get("pendingSlot")),
                last_sent_slot=_optional_int(value.get("lastSentSlot")),
                last_message_id=_optional_int(value.get("lastMessageId")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Stored Recurring Posts configuration is invalid.") from exc
        _validate_post(post)
        return post


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid integer.")
    return int(value)


def _validated_schedule(value: Mapping[str, Any]) -> dict[str, Any]:
    schedule = schedule_from_dict(value)
    if not isinstance(schedule, (IntervalSchedule, DailySchedule, WeeklySchedule)):
        raise ValueError("Recurring Posts supports interval, daily or weekly schedules.")
    if isinstance(schedule, IntervalSchedule) and schedule.seconds < MIN_INTERVAL_SECONDS:
        raise ValueError(
            f"Recurring Posts interval must be at least {MIN_INTERVAL_SECONDS // 60} minutes."
        )
    return schedule_to_dict(schedule)


def _validate_post(post: RecurringPost) -> None:
    if not post.id or len(post.id) > 64:
        raise ValueError("Recurring Post id is invalid.")
    if not post.name.strip() or len(post.name) > 80:
        raise ValueError("Recurring Post name must be 1-80 characters.")
    if post.channel_id <= 0:
        raise ValueError("Recurring Post channel must be positive.")
    if not post.content.strip():
        raise ValueError("Recurring Post content is required.")
    if len(post.content) > 2000:
        raise ValueError("Recurring Post content exceeds Discord's 2000 character limit.")
    _validated_schedule(post.schedule)


def schedule_label(value: Mapping[str, Any]) -> str:
    schedule = schedule_from_dict(value)
    if isinstance(schedule, IntervalSchedule):
        minutes = schedule.seconds // 60
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"Every {hours} hour{'s' if hours != 1 else ''}"
        return f"Every {minutes} minutes"
    if isinstance(schedule, DailySchedule):
        return f"Daily at {schedule.hour:02d}:{schedule.minute:02d} · {schedule.timezone}"
    if isinstance(schedule, WeeklySchedule):
        days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        return (
            f"{days[schedule.weekday]} at {schedule.hour:02d}:{schedule.minute:02d} "
            f"· {schedule.timezone}"
        )
    raise ValueError("Recurring Posts schedule type is unsupported.")


class RecurringPostsSkill:
    manifest = SkillManifest(
        id=SKILL_ID,
        name="Recurring Posts",
        version="1.0.0",
        runtime_api_version="1",
        description="Post configured messages automatically on interval, daily or weekly schedules.",
        author="GamerHQ",
        permissions=(
            SkillCapability.DISCORD_CHANNELS_READ.value,
            SkillCapability.DISCORD_MESSAGES_SEND.value,
            SkillCapability.SCHEDULER_JOBS.value,
            SkillCapability.STORAGE_SKILL.value,
            SkillCapability.EVENTS_EMIT.value,
            SkillCapability.AUDIT_WRITE.value,
        ),
        events=SkillEvents(
            emits=(
                EventContract(
                    SENT_EVENT_ID,
                    "Emitted after a configured recurring post is confirmed sent.",
                ),
            ),
        ),
    )

    def __init__(self):
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    async def register(self, ctx) -> None:
        ctx.scheduler.register_handler(HANDLER_ID, self._execute)

    async def enable(self, ctx) -> None:
        await ctx.audit.write(action="enabled")

    async def disable(self, ctx) -> None:
        # Jobs deliberately remain persisted. The shared Scheduler's existing
        # disabled-Skill gate prevents execution and preserves safe re-enable.
        await ctx.audit.write(action="disabled")

    async def start(self, ctx) -> None:
        # No private loop: all future work is owned by scheduler.jobs.
        return None

    async def stop(self, ctx) -> None:
        return None

    async def health_check(self, ctx) -> SkillHealth:
        posts = await self.list_posts(ctx)
        active = sum(post.active for post in posts)
        return SkillHealth("PASS", f"{active} active recurring post(s), {len(posts)} configured.")

    async def _load(self, ctx) -> dict[str, RecurringPost]:
        raw = await ctx.storage.get(STORAGE_KEY)
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise ValueError("Stored Recurring Posts configuration is invalid.")
        result: dict[str, RecurringPost] = {}
        for post_id, value in raw.items():
            if not isinstance(value, Mapping):
                raise ValueError("Stored Recurring Posts configuration is invalid.")
            post = RecurringPost.from_dict(value)
            if post.id != str(post_id):
                raise ValueError("Stored Recurring Posts identity is inconsistent.")
            result[post.id] = post
        return result

    async def _store(self, ctx, posts: Mapping[str, RecurringPost]) -> None:
        await ctx.storage.set(
            STORAGE_KEY,
            {post_id: post.to_dict() for post_id, post in sorted(posts.items())},
        )

    async def list_posts(self, ctx) -> tuple[RecurringPost, ...]:
        async with self._lock(ctx.guild_id):
            posts = await self._load(ctx)
        return tuple(sorted(posts.values(), key=lambda post: (post.name.casefold(), post.id)))

    async def get_post(self, ctx, post_id: str) -> RecurringPost:
        async with self._lock(ctx.guild_id):
            posts = await self._load(ctx)
            try:
                return posts[post_id]
            except KeyError as exc:
                raise KeyError("Recurring Post does not exist.") from exc

    async def create_post(
        self,
        ctx,
        *,
        name: str,
        channel_id: int,
        content: str,
        schedule: Mapping[str, Any],
        active: bool = True,
    ) -> RecurringPost:
        await ctx.discord.get_channel(channel_id=channel_id)
        normalized = _validated_schedule(schedule)
        post = RecurringPost(
            id=uuid.uuid4().hex,
            name=name.strip(),
            channel_id=int(channel_id),
            content=content.strip(),
            schedule=normalized,
            active=bool(active),
        )
        _validate_post(post)

        async with self._lock(ctx.guild_id):
            posts = await self._load(ctx)
            if len(posts) >= MAX_POSTS:
                raise ValueError(f"Recurring Posts is limited to {MAX_POSTS} posts per server.")
            posts[post.id] = post
            await self._store(ctx, posts)
            try:
                if post.active:
                    await self._schedule(ctx, post)
            except Exception:
                posts.pop(post.id, None)
                await self._store(ctx, posts)
                raise

        await ctx.audit.write(
            action="post-created",
            target=post.id,
            metadata={"channelId": post.channel_id, "scheduleType": post.schedule["type"]},
        )
        return post

    async def update_post(
        self,
        ctx,
        *,
        post_id: str,
        name: str,
        channel_id: int,
        content: str,
        schedule: Mapping[str, Any],
    ) -> RecurringPost:
        await ctx.discord.get_channel(channel_id=channel_id)
        normalized = _validated_schedule(schedule)

        async with self._lock(ctx.guild_id):
            posts = await self._load(ctx)
            if post_id not in posts:
                raise KeyError("Recurring Post does not exist.")
            previous = posts[post_id]
            updated = replace(
                previous,
                name=name.strip(),
                channel_id=int(channel_id),
                content=content.strip(),
                schedule=normalized,
                pending_slot=None,
            )
            _validate_post(updated)
            posts[post_id] = updated
            await self._store(ctx, posts)
            try:
                if updated.active:
                    await self._schedule(ctx, updated)
                else:
                    await ctx.scheduler.remove_job(key=self._job_key(updated.id))
            except Exception:
                posts[post_id] = previous
                await self._store(ctx, posts)
                if previous.active:
                    await self._schedule(ctx, previous)
                raise

        await ctx.audit.write(
            action="post-updated",
            target=updated.id,
            metadata={"channelId": updated.channel_id, "scheduleType": updated.schedule["type"]},
        )
        return updated

    async def set_active(self, ctx, *, post_id: str, active: bool) -> RecurringPost:
        async with self._lock(ctx.guild_id):
            posts = await self._load(ctx)
            if post_id not in posts:
                raise KeyError("Recurring Post does not exist.")
            previous = posts[post_id]
            updated = replace(previous, active=bool(active), pending_slot=None)
            posts[post_id] = updated
            await self._store(ctx, posts)
            try:
                if updated.active:
                    await self._schedule(ctx, updated)
                else:
                    await ctx.scheduler.remove_job(key=self._job_key(updated.id))
            except Exception:
                posts[post_id] = previous
                await self._store(ctx, posts)
                raise

        await ctx.audit.write(
            action="post-resumed" if updated.active else "post-paused",
            target=updated.id,
        )
        return updated

    async def delete_post(self, ctx, *, post_id: str) -> None:
        async with self._lock(ctx.guild_id):
            posts = await self._load(ctx)
            if post_id not in posts:
                raise KeyError("Recurring Post does not exist.")
            await ctx.scheduler.remove_job(key=self._job_key(post_id))
            posts.pop(post_id)
            await self._store(ctx, posts)
        await ctx.audit.write(action="post-deleted", target=post_id)

    async def _schedule(self, ctx, post: RecurringPost) -> None:
        await ctx.scheduler.upsert_job(
            key=self._job_key(post.id),
            handler_id=HANDLER_ID,
            schedule=post.schedule,
            payload={"postId": post.id},
        )

    @staticmethod
    def _job_key(post_id: str) -> str:
        return f"post:{post_id}"

    async def _execute(self, ctx, job) -> None:
        post_id = str(job.payload.get("postId", ""))
        if not post_id:
            return

        slot = int(job.next_run_at)
        async with self._lock(ctx.guild_id):
            posts = await self._load(ctx)
            post = posts.get(post_id)
            if post is None or not post.active:
                await ctx.scheduler.remove_job(key=self._job_key(post_id))
                return
            if post.last_sent_slot == slot or post.pending_slot == slot:
                return
            post = replace(post, pending_slot=slot)
            posts[post_id] = post
            await self._store(ctx, posts)

        try:
            message_id = await ctx.discord.send_message(
                channel_id=post.channel_id,
                content=post.content,
            )
        except Exception:
            # A known send failure clears the reservation so the Scheduler may
            # retry. If the process dies after reservation but around delivery,
            # the reservation survives and favors no duplicate over catch-up.
            async with self._lock(ctx.guild_id):
                posts = await self._load(ctx)
                current = posts.get(post_id)
                if current is not None and current.pending_slot == slot:
                    posts[post_id] = replace(current, pending_slot=None)
                    await self._store(ctx, posts)
            raise

        sent_at = int(time.time())
        async with self._lock(ctx.guild_id):
            posts = await self._load(ctx)
            current = posts.get(post_id)
            if current is not None:
                posts[post_id] = replace(
                    current,
                    pending_slot=None,
                    last_sent_slot=slot,
                    last_message_id=int(message_id),
                )
                await self._store(ctx, posts)

        await ctx.events.emit(EventEnvelope(
            event_id=SENT_EVENT_ID,
            producer_skill_id=SKILL_ID,
            guild_id=ctx.guild_id,
            occurred_at=sent_at,
            payload={
                "postId": post_id,
                "channelId": post.channel_id,
                "messageId": int(message_id),
                "scheduledFor": slot,
            },
        ))
