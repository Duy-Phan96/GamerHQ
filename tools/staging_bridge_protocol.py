"""Bounded staging-only trigger protocol. Carries no credentials or file paths."""
import base64
import json
import re

PREFIX = "gamerhq-staging-v1"
PREPARE = {"inspect", "initialize", "build-base", "fetch", "verify-offline", "verify", "build", "bootstrap", "install"}
APPROVALS = {"initialize": "INITIALIZE ISOLATED STAGING", "build-base": "BUILD ISOLATED STAGING BASE",
    "bootstrap": "BOOTSTRAP ISOLATED STAGING", "install": "INSTALL IN ISOLATED STAGING"}


def validate(value):
    if not isinstance(value, dict) or set(value) - {"version", "phase", "operation", "approval", "deploymentId"}:
        raise ValueError("Unsupported staging request fields.")
    if type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("Unsupported staging protocol version.")
    phase, operation = value.get("phase"), value.get("operation")
    approval, deployment = value.get("approval", ""), value.get("deploymentId", "")
    if not isinstance(approval, str) or len(approval) > 80 or not isinstance(deployment, str):
        raise ValueError("Invalid staging request.")
    if phase == "configure" and operation in {"initial", "release", "authorize-live", "revoke", "health"}:
        expected = {"initial": "CONFIGURE ISOLATED STAGING", "release": "APPROVE STAGING RELEASE",
            "authorize-live": "AUTHORIZE ISOLATED STAGING", "revoke": "REVOKE STAGING ACCESS", "health": ""}[operation]
        if approval != expected or deployment:
            raise ValueError("Explicit staging configuration approval is required.")
    elif phase == "prepare" and operation in PREPARE:
        if approval != APPROVALS.get(operation, "") or deployment:
            raise ValueError("Explicit staging operation approval is required.")
    elif phase == "update" and operation in {"stage", "apply", "history"}:
        if operation == "apply":
            if not re.fullmatch(r"[0-9a-f]{32}", deployment) or approval != "APPROVE " + deployment:
                raise ValueError("Deployment-bound staging approval is required.")
        elif approval or deployment:
            raise ValueError("Unexpected staging approval or deployment identity.")
    else:
        raise ValueError("Only supported staging operations are permitted.")
    return {"version": 1, "phase": phase, "operation": operation, "approval": approval, "deploymentId": deployment}


def encode(value):
    return PREFIX + " " + base64.b64encode(json.dumps(validate(value), separators=(",", ":")).encode()).decode()


def decode(command):
    if not isinstance(command, str) or len(command) > 6000 or command.count(" ") != 1:
        raise ValueError("Invalid staging command envelope.")
    prefix, payload = command.split(" ")
    if prefix != PREFIX:
        raise ValueError("Unsupported staging command envelope.")
    body = base64.b64decode(payload, validate=True)
    if len(body) > 4096:
        raise ValueError("Staging request exceeds its bound.")
    return validate(json.loads(body))
