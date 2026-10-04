from __future__ import annotations


class HostCapabilityError(RuntimeError):
    """Safe, host-neutral failure raised across a Skill capability boundary."""

    code = "host_capability_error"


class CapabilityUnavailableError(HostCapabilityError, PermissionError):
    code = "capability_unavailable"


class ResourceNotFoundError(HostCapabilityError):
    code = "resource_not_found"


class HostPermissionDeniedError(HostCapabilityError, PermissionError):
    code = "permission_denied"


class InvalidHostOperationError(HostCapabilityError, ValueError):
    code = "invalid_operation"


class TransientHostError(HostCapabilityError):
    code = "transient_host_failure"
