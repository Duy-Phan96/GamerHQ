import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from database import db
from tools.event_manager_export import export_snapshot


def test_export_is_read_only_and_preserves_all_event_tables():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "copy.db"
        with patch.object(db, "DB_PATH", path):
            db.init_db()
            event = db.create_lfg_event(guild_id=1, game_id=0, host_id=10, title="Private raid", start_at=2000000000,
                max_players=5, invite_lead_minutes=15, visibility="private")
            db.set_lfg_event_member(event["id"], 20, "joined")
            db.set_lfg_event_private_channel(event["id"], 30)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot = export_snapshot(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        assert snapshot["events"][0]["private_channel_id"] == 30
        assert snapshot["events"][0]["visibility"] == "private"
        assert {row["user_id"] for row in snapshot["members"]} == {10, 20}
        assert set(snapshot) == {"schemaVersion", "series", "events", "members", "messages", "voiceNotifications", "timeProposals"}
