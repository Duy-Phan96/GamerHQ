"""Apply an already-approved staging release only after live health and READY proof."""
import json
import os
import re
import sys

from .staging_ssh_client import StagingAccessError, invoke


def automatic_update(call):
    # Health does not start or repair anything. A stopped/offline Host cannot
    # reach stage/apply; first Gateway activation remains separately authorized.
    health = call("configure", "health")
    if health.get("status") != "HOST_READY" or health.get("gatewayChecked") is not True:
        return {"state": "NOT_READY", "applied": False, "gatewayStarted": False}
    for operation in ("fetch", "verify"):
        result = call("prepare", operation)
        if result.get("result") != "PASS" or result.get("operation") != operation or result.get("workerEnabled") is not True or result.get("gatewayMayStart") is not False:
            raise ValueError("STAGING_VALIDATION_REQUIRED")
    stage = call("update", "stage")
    ident = stage.get("id", "")
    if stage.get("state") != "READY" or not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident):
        raise ValueError("STAGING_READY_DEPLOYMENT_REQUIRED")
    # The installed Host enforces approved source/capabilities/migration mode,
    # immutable bytes, freshness/generation, backup, health and guarded rollback.
    applied = call("update", "apply", deployment_id=ident, approval="APPROVE " + ident)
    if applied.get("id") != ident or applied.get("state") != "APPLIED":
        raise ValueError("STAGING_APPLICATION_NOT_VERIFIED")
    return {"id": ident, "state": "APPLIED", "applied": True}


def main():
    def call(phase, operation, *, deployment_id="", approval=""):
        body = invoke({"version": 1, "phase": phase, "operation": operation, "approval": approval,
            "deploymentId": deployment_id}, host=os.environ.get("GAMERHQ_STAGING_SSH_HOST", ""),
            port=int(os.environ.get("GAMERHQ_STAGING_SSH_PORT", "22")),
            private_key=os.environ.get("GAMERHQ_STAGING_SSH_KEY", ""),
            known_hosts=os.environ.get("GAMERHQ_STAGING_SSH_KNOWN_HOSTS", ""))
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("STAGING_RESPONSE_INVALID")
        return value
    print(json.dumps(automatic_update(call)))


if __name__ == "__main__":
    try:
        main()
    except StagingAccessError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        raise SystemExit('{"stagingError":"AUTOMATIC_STAGING_VALIDATION_FAILED"}') from None
