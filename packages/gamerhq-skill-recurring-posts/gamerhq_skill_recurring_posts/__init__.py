from .skill import (
    CREATE_API,
    DELETE_API,
    GET_API,
    HANDLER_ID,
    LIST_API,
    MAX_POSTS,
    MIN_INTERVAL_SECONDS,
    SENT_EVENT_ID,
    SET_ACTIVE_API,
    SKILL_ID,
    STORAGE_KEY,
    RecurringPost,
    RecurringPostsSkill,
)


def create_skill():
    return RecurringPostsSkill()


__all__ = [
    "CREATE_API",
    "DELETE_API",
    "GET_API",
    "HANDLER_ID",
    "LIST_API",
    "MAX_POSTS",
    "MIN_INTERVAL_SECONDS",
    "SENT_EVENT_ID",
    "SET_ACTIVE_API",
    "SKILL_ID",
    "STORAGE_KEY",
    "RecurringPost",
    "RecurringPostsSkill",
    "create_skill",
]
