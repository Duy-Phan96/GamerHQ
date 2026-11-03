import pytest

from tools.staging_auto_update import automatic_update


IDENT = "a" * 32


def responses():
    return [
        {"status": "HOST_READY", "gatewayChecked": True},
        {"operation": "fetch", "result": "PASS", "workerEnabled": True, "gatewayMayStart": False},
        {"operation": "verify", "result": "PASS", "workerEnabled": True, "gatewayMayStart": False},
        {"id": IDENT, "state": "READY"},
        {"id": IDENT, "state": "APPLIED"},
    ]


def execute(values):
    calls = []
    def call(phase, operation, **kwargs):
        calls.append((phase, operation, kwargs))
        return values[len(calls) - 1]
    return calls, call


def test_automatic_apply_is_bound_to_ready_deployment_and_validation_order():
    calls, call = execute(responses())
    assert automatic_update(call) == {"id": IDENT, "state": "APPLIED", "applied": True}
    assert [(p, o) for p, o, _ in calls] == [("configure", "health"), ("prepare", "fetch"),
        ("prepare", "verify"), ("update", "stage"), ("update", "apply")]
    assert calls[-1][2] == {"deployment_id": IDENT, "approval": "APPROVE " + IDENT}


@pytest.mark.parametrize("health", [{"status": "READY_OFFLINE", "gatewayChecked": False},
    {"status": "HOST_READY", "gatewayChecked": False}, {}])
def test_offline_or_unverified_host_never_reaches_stage_or_apply(health):
    calls, call = execute([health])
    assert automatic_update(call)["applied"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("index,value", [(1, {"result": "FAIL"}),
    (2, {"result": "PASS", "operation": "verify", "workerEnabled": True, "gatewayMayStart": True}),
    (3, {"id": IDENT, "state": "FAILED"}), (3, {"id": "../private", "state": "READY"})])
def test_invalid_validation_or_stage_never_applies(index, value):
    values = responses(); values[index] = value
    calls, call = execute(values)
    with pytest.raises(ValueError):
        automatic_update(call)
    assert not any(operation == "apply" for _, operation, _ in calls)


def test_failed_or_mismatched_application_is_not_success():
    for result in ({"id": IDENT, "state": "ROLLED_BACK"}, {"id": "b" * 32, "state": "APPLIED"}):
        values = responses(); values[-1] = result
        _, call = execute(values)
        with pytest.raises(ValueError, match="NOT_VERIFIED"):
            automatic_update(call)
