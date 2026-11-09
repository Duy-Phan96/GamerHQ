import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cogs.game_selector import GameSelectionSession
from config import DISPLAY_GROUP_ORDER
from database import db
from tools.backup_database import verify
from tools.game_catalog_import import import_catalog


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    target = tmp_path / "catalog.db"
    monkeypatch.setattr(db, "DB_PATH", target)
    db.init_db()
    return target


def write_manifest(path, rows):
    path.write_text(json.dumps({"games": rows}), encoding="utf-8")
    return path


def apply_preview(target, manifest, backup):
    preview = import_catalog(target, manifest)
    return import_catalog(target, manifest, apply=True, review_token=preview["review_token"], backup=backup)


def test_existing_identity_mapping_and_selection_survive_repeat_import(catalog, tmp_path):
    group = DISPLAY_GROUP_ORDER[0]
    existing = db.upsert_custom_game("Diablo IV", "🎮", group)
    with db.connect() as conn:
        conn.execute("UPDATE games SET aliases_json=?, active=1, selectable=1, role_id=71, "
                     "channel_id=81, chat_channel_id=82 WHERE id=?",
                     (json.dumps(["Diablo 4"]), existing["id"]))
    original = db.get_game_by_id(existing["id"])
    source = write_manifest(tmp_path / "games.json", [
        {"name": "Diablo 4"}, {"name": "Diablo II: Resurrected", "display_group": group},
    ])
    preview = import_catalog(catalog, source)
    assert preview["existing"][0]["id"] == existing["id"]
    assert preview["existing"][0]["channel_id"] == 81
    assert len(preview["insert"]) == 1
    assert db.get_game_by_name("Diablo II: Resurrected") is None
    backup = tmp_path / "verified.db"
    apply_preview(catalog, source, backup)
    verify(backup)
    assert db.get_game_by_id(existing["id"]) == original
    addition = db.get_game_by_name("Diablo II: Resurrected")
    assert not addition["active"] and not addition["selectable"] and not addition["area_enabled"]
    assert addition["role_id"] is None and addition["channel_id"] is None
    assert db.get_selector_games() == [original]
    second_backup = tmp_path / "repeat.db"
    assert apply_preview(catalog, source, second_backup)["insert"] == []
    assert not second_backup.exists()
    assert db.get_game_by_name(addition["name"])["id"] == addition["id"]


def test_title_editions_stay_distinct_and_colliding_aliases_need_review(catalog, tmp_path):
    group = DISPLAY_GROUP_ORDER[0]
    db.upsert_custom_game("Minecraft", "🎮", group)
    db.upsert_custom_game("World of Warcraft", "🎮", group)
    source = write_manifest(tmp_path / "games.json", [
        {"name": "Minecraft (Bedrock)", "display_group": group},
        {"name": "World of Warcraft Classic", "display_group": group},
    ])
    result = apply_preview(catalog, source, tmp_path / "backup.db")
    assert len(result["insert"]) == 2
    source = write_manifest(source, [
        {"name": "Minecraft", "aliases": ["Minecraft Bedrock"]},
        {"name": "Another Game", "aliases": ["Minecraft"]},
    ])
    preview = import_catalog(catalog, source)
    assert len(preview["ambiguous"]) == 2
    assert len(preview["ambiguous"][0]["existing_ids"]) == 2
    with pytest.raises(ValueError, match="ambiguous"):
        apply_preview(catalog, source, tmp_path / "rejected.db")


def test_stale_preview_and_tombstone_are_respected(catalog, tmp_path):
    group = DISPLAY_GROUP_ORDER[0]
    old = db.upsert_custom_game("Old Game", "🎮", group)
    db.delete_game_permanently(old["id"])
    source = write_manifest(tmp_path / "games.json", [
        {"name": "OLD GAME"}, {"name": "New Game", "display_group": group},
    ])
    preview = import_catalog(catalog, source)
    assert preview["retired"] == ["OLD GAME"]
    db.upsert_custom_game("New Game", "🎮", group)
    backup = tmp_path / "stale.db"
    with pytest.raises(ValueError, match="changed"):
        import_catalog(catalog, source, apply=True, review_token=preview["review_token"], backup=backup)
    assert not backup.exists()


def test_approval_is_bound_to_database_and_other_customer_is_untouched(catalog, tmp_path, monkeypatch):
    other = tmp_path / "other.db"
    monkeypatch.setattr(db, "DB_PATH", other)
    db.init_db()
    source = write_manifest(tmp_path / "games.json", [
        {"name": "Test Game", "display_group": DISPLAY_GROUP_ORDER[0]},
    ])
    preview = import_catalog(catalog, source)
    with pytest.raises(ValueError, match="changed"):
        import_catalog(other, source, apply=True, review_token=preview["review_token"], backup=tmp_path / "wrong.db")
    apply_preview(catalog, source, tmp_path / "right.db")
    assert db.get_game_by_name("Test Game") is None
    assert import_catalog(catalog, source)["existing"][0]["name"] == "Test Game"


def test_large_catalog_uses_existing_popular_and_letter_pagination(catalog, tmp_path):
    source = write_manifest(tmp_path / "games.json", [
        {"name": f"Arcade Game {index:03}", "display_group": DISPLAY_GROUP_ORDER[0]}
        for index in range(400)
    ])
    apply_preview(catalog, source, tmp_path / "large-backup.db")
    assert db.get_selector_games() == []
    # Simulate separate, reviewed role provisioning without Discord or patch subscriptions.
    with db.connect() as conn:
        conn.execute("UPDATE games SET role_id=id+1000, active=1, selectable=1")
    games = db.get_selector_games()

    async def check():
        guild = SimpleNamespace(id=1, members=[])
        member = SimpleNamespace(id=2, guild=guild, roles=[SimpleNamespace(id=games[0]["role_id"])])
        selector = GameSelectionSession(member, games)
        assert selector.page_count() == 2
        assert selector.children[0].label.startswith("✅ ")
        selector.current_group = "A–E"
        assert selector.page_count() == 20
        selector.page = selector.page_count() - 1
        selector.rebuild()
        assert len(selector.children) <= 25
        assert any(child.label.endswith("399") for child in selector.children)

    asyncio.run(check())

def test_supplied_manifest_seed_baseline(catalog, monkeypatch):
    monkeypatch.setattr(db, "SEED_PATH", Path("data/games_seed.json"))
    db.seed_catalog()
    result = import_catalog(catalog, Path("data/game_catalog_expansion.json"))
    assert len(result["existing"]) == 38
    assert len(result["insert"]) == 32
    assert result["ambiguous"] == []
    assert result["alias_updates"] == []
    gta = next(row for row in result["existing"] if row["title"] == "Grand Theft Auto V")
    assert gta["name"] == "GTA Online"
    assert len(db.get_all_games()) == 60


def test_gta_alias_apply_preserves_id_resources_and_custom_alias(catalog, tmp_path):
    from services.game_service import search_games

    game = db.upsert_custom_game("GTA Online", "🎮", DISPLAY_GROUP_ORDER[0])
    with db.connect() as conn:
        conn.execute("UPDATE games SET aliases_json=?, active=1, selectable=1, role_id=71, "
                     "channel_id=81, chat_channel_id=82 WHERE id=?",
                     (json.dumps(["GTA V", "GTA 5", "Custom GTA Alias"]), game["id"]))
    original = db.get_game_by_id(game["id"])
    source = write_manifest(tmp_path / "gta.json", [
        {"name": "Grand Theft Auto V", "existing_name": "GTA Online", "aliases": ["GTA V", "GTA 5"]},
    ])
    preview = import_catalog(catalog, source)
    assert preview["insert"] == []
    assert preview["existing"][0]["id"] == game["id"]
    assert preview["alias_updates"][0]["add"] == ["Grand Theft Auto V"]
    assert db.get_game_by_id(game["id"]) == original
    backup = tmp_path / "gta-backup.db"
    apply_preview(catalog, source, backup)
    verify(backup)
    updated = db.get_game_by_id(game["id"])
    assert json.loads(updated.pop("aliases_json")) == ["GTA V", "GTA 5", "Custom GTA Alias", "Grand Theft Auto V"]
    original.pop("aliases_json")
    assert updated == original
    assert len(db.get_all_games()) == 1
    for alias in ("GTA V", "GTA 5", "Grand Theft Auto V"):
        assert search_games(alias, limit=1)[0]["id"] == game["id"]
    assert import_catalog(catalog, source)["alias_updates"] == []


def test_gta_alias_cannot_create_target_or_claim_another_row(catalog, tmp_path):
    source = write_manifest(tmp_path / "gta.json", [
        {"name": "Grand Theft Auto V", "existing_name": "GTA Online", "aliases": ["GTA V", "GTA 5"]},
    ])
    missing = import_catalog(catalog, source)
    assert missing["insert"] == []
    assert missing["ambiguous"][0]["expected_name"] == "GTA Online"
    for title in ("GTA Online", "GTA V"):
        db.upsert_custom_game(title, "🎮", DISPLAY_GROUP_ORDER[0])
    conflict = import_catalog(catalog, source)
    assert len(conflict["ambiguous"][0]["existing_ids"]) == 2
    assert conflict["alias_updates"] == []
    with pytest.raises(ValueError, match="ambiguous"):
        apply_preview(catalog, source, tmp_path / "rejected.db")
