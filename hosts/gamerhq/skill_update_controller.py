"""Host-owned staged application state machine; never exposed to portable Skills.

Stage uses an isolated container and reviewed bytes. Apply requires an explicit
deployment-specific approval. Code-only rollback is permitted only when the
application database has not changed; otherwise stop and require recovery.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid
import re
import os

from database import db
from skill_runtime.contracts.capabilities import KNOWN_CAPABILITIES
from hosts.gamerhq.skill_artifacts import UpdatePolicy, update_decision, verify_wheel


def application_generation(conn):
    # Every application attempt enters one of these states permanently. READY
    # records staged before another attempt must be verified again afterwards.
    return conn.execute("SELECT count(*) FROM skill_deployments WHERE state IN ('APPLYING','APPLIED','ROLLED_BACK','RECOVERY_REQUIRED')").fetchone()[0]


def application_fingerprint(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("BEGIN")
        hasher = hashlib.sha256()
        hasher.update(json.dumps(conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND tbl_name!='skill_deployments' ORDER BY type,name").fetchall()).encode())
        hasher.update(str(conn.execute("PRAGMA user_version").fetchone()[0]).encode())
        tables = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='skill_deployments' ORDER BY name").fetchall()
        for name, schema in tables:
            hasher.update(schema.encode())
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")]
            order = ",".join('"' + value.replace('"', '""') + '"' for value in columns)
            for row in conn.execute(f"SELECT * FROM {quoted} ORDER BY {order}"):
                hasher.update(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=lambda value: {"bytes": value.hex()}).encode())
        return hasher.hexdigest()


class SkillUpdateController:
    def __init__(self, backend, *, backup_root):
        self.backend, self.backup_root = backend, Path(backup_root).resolve()

    def _state(self, ident, state, *, detail="", image=None, previous=None, evidence=None):
        with db.connect() as conn:
            conn.execute("UPDATE skill_deployments SET state=?,detail=?,updated_at=?,image=coalesce(?,image),"
                "previous_image=coalesce(?,previous_image),evidence_json=coalesce(?,evidence_json) WHERE id=?",
                (state, detail, int(time.time()), image, previous, json.dumps(evidence) if evidence is not None else None, ident))

    def get(self, ident):
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM skill_deployments WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise ValueError("Deployment does not exist.")
        return dict(row)

    def stage(self, *, release, wheel, current_version, current_capabilities, policy=UpdatePolicy(), manual_review=False, host_source_commit=None):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", str(release.get("skillId", ""))) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", str(release.get("distribution", ""))):
            raise ValueError("Invalid release identity.")
        if release.get("sourceReviewed") is not True or release.get("runtimeApi") != "1":
            raise ValueError("Reviewed source and a compatible runtime API are required.")
        capabilities = release.get("capabilities")
        if not isinstance(capabilities, list) or not set(capabilities) <= KNOWN_CAPABILITIES:
            raise ValueError("Release requests unsupported Host capabilities.")
        if release.get("migrationMode") not in ("none", "backward-compatible"):
            raise ValueError("This update needs a separately verified explicit migration before application.")
        decision = update_decision(current_version, release["version"], policy,
            migration_safe=True, capabilities_unchanged=set(capabilities) <= set(current_capabilities))
        if host_source_commit is not None:
            if not re.fullmatch(r"[0-9a-f]{40}", host_source_commit) or not manual_review or release["version"] != current_version:
                raise ValueError("Host promotion requires reviewed source and the unchanged installed Skill version.")
            decision = "owner-reviewed-host-promotion"
        if decision not in ("eligible-for-staging", "owner-reviewed-host-promotion") and not (decision == "manual-review-required" and manual_review):
            raise ValueError("Update policy does not permit staging this release.")
        artifact = verify_wheel(Path(wheel), distribution=release["distribution"], version=release["version"], sha256=release["sha256"])
        ident = uuid.uuid4().hex
        evidence = {"artifact": artifact, "release": release, "policyDecision": decision, "previousVersion": current_version}
        if host_source_commit is not None:
            evidence["hostSourceCommit"] = host_source_commit
        evidence["previousImage"] = self.backend.current_image()
        with db.connect() as conn:
            evidence["applicationGeneration"] = application_generation(conn)
            conn.execute("INSERT INTO skill_deployments VALUES(?,?,?,'STAGING',NULL,NULL,?,'',?,?)",
                (ident, release["skillId"], release["version"], json.dumps(evidence), int(time.time()), int(time.time())))
        try:
            probe = self.backend.stage(release=release, wheel=Path(wheel), deployment_id=ident)
            if probe.get("healthy") is not True or probe.get("isolated") is not True or probe.get("artifactSha256") != release["sha256"]:
                raise ValueError("Staged health, isolation or artifact verification failed.")
            if probe.get("runtimeApi") != release["runtimeApi"] or not set(probe.get("capabilities", [])) <= set(capabilities) or probe.get("gatewayStarted") is not False:
                raise ValueError("Staged runtime/capability evidence does not match the reviewed release.")
            evidence["probe"] = probe
            self._state(ident, "READY", image=probe["image"], evidence=evidence)
        except Exception:
            self._state(ident, "FAILED", detail="Isolated stage verification failed; no production mutation performed.")
            raise
        return self.get(ident)

    def apply(self, ident, *, approval):
        if approval != f"APPROVE {ident}":
            raise PermissionError("Explicit approval for this staged deployment is required.")
        current_image = self.backend.current_image()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM skill_deployments WHERE id=?", (ident,)).fetchone()
            if not row or row["state"] != "READY":
                raise ValueError("Only a verified READY deployment can be applied.")
            staged = json.loads(row["evidence_json"])
            if current_image != staged["previousImage"] or application_generation(conn) != staged["applicationGeneration"]:
                raise ValueError("Host changed after staging; verify a new candidate before application.")
            conn.execute("UPDATE skill_deployments SET state='APPLYING',updated_at=? WHERE id=?", (int(time.time()), ident))
        old_image = None
        fingerprint = None
        try:
            old_image = current_image
            self._state(ident, "APPLYING", previous=old_image)
            self.backend.stop()  # the old Gateway is stopped before any replacement starts
            self.backup_root.mkdir(parents=True, exist_ok=True)
            backup = self.backup_root / (ident + ".sqlite")
            if backup.exists():
                raise ValueError("A backup already exists for this deployment; inspect before retrying.")
            descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            with db.connect() as conn, closing(sqlite3.connect(backup)) as target:
                conn.backup(target)
            fingerprint = application_fingerprint(backup)
            evidence = json.loads(row["evidence_json"])
            evidence["backup"] = {"sha256": hashlib.sha256(backup.read_bytes()).hexdigest(), "applicationFingerprint": fingerprint}
            if hasattr(self.backend, "verify_backup"):
                evidence["backup"]["verification"] = self.backend.verify_backup(backup)
            self._state(ident, "APPLYING", evidence=evidence)
            self.backend.activate(row["image"])
            if not self.backend.healthy(expected_version=row["version"]):
                raise ValueError("Updated Host failed its health gate.")
            self._state(ident, "APPLIED", detail="Approved image applied; Host health checks passed.")
        except Exception:
            try:
                self.backend.stop()
                if old_image and fingerprint and application_fingerprint(db.DB_PATH) == fingerprint:
                    self.backend.activate(old_image)
                    if not self.backend.healthy(expected_version=json.loads(row["evidence_json"])["previousVersion"]):
                        raise ValueError("Previous Host image failed recovery health checks.")
                    self._state(ident, "ROLLED_BACK", detail="Application data unchanged; previous image restored and healthy.")
                else:
                    self._state(ident, "RECOVERY_REQUIRED", detail="Host stopped. Database state changed or backup evidence is unavailable; reviewed recovery required.")
            except Exception:
                self._state(ident, "RECOVERY_REQUIRED", detail="Host recovery requires operator action; no database restoration attempted.")
            raise
        return self.get(ident)


def deployment_history(*, skill_id):
    with db.connect() as conn:
        rows = conn.execute("SELECT id,skill_id,version,state,detail,created_at,updated_at FROM skill_deployments WHERE skill_id=? ORDER BY created_at DESC,id DESC LIMIT 100", (skill_id,)).fetchall()
    return [dict(row) for row in rows]
