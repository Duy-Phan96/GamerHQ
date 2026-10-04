"""Offline developer checks for GamerHQ Skill implementations.

These checks validate public SDK shape only. They do not execute lifecycle
methods, access Discord, inspect production state, or install packages.
"""
from __future__ import annotations

from dataclasses import dataclass
import ast
import inspect
from pathlib import Path
import tomllib
from collections.abc import Callable, Iterable

from .contracts.manifest import SkillManifest, validate_manifest


_FORBIDDEN_IMPORT_ROOTS = frozenset({
    "bot",
    "cogs",
    "config",
    "database",
    "discord",
    "dotenv",
    "hosts",
    "services",
    "skills",
    "subprocess",
})

_REQUIRED_ASYNC_METHODS = (
    "register",
    "enable",
    "disable",
    "start",
    "stop",
    "health_check",
)


@dataclass(frozen=True, slots=True)
class SkillConformanceReport:
    skill_id: str
    version: str
    runtime_api_version: str
    lifecycle_methods: tuple[str, ...]


class SkillConformanceError(ValueError):
    """Safe SDK contract error for developer-facing validation."""


def validate_skill_implementation(skill: object) -> SkillConformanceReport:
    manifest = getattr(skill, "manifest", None)
    if not isinstance(manifest, SkillManifest):
        raise SkillConformanceError("Skill must expose a SkillManifest as manifest.")

    try:
        validate_manifest(manifest)
    except ValueError as exc:
        raise SkillConformanceError(str(exc)) from exc

    for name in _REQUIRED_ASYNC_METHODS:
        method = getattr(skill, name, None)
        if method is None or not callable(method):
            raise SkillConformanceError(f"Skill lifecycle method is missing: {name}.")
        if not inspect.iscoroutinefunction(method):
            raise SkillConformanceError(
                f"Skill lifecycle method must be async: {name}."
            )

    return SkillConformanceReport(
        skill_id=manifest.id,
        version=manifest.version,
        runtime_api_version=manifest.runtime_api_version,
        lifecycle_methods=_REQUIRED_ASYNC_METHODS,
    )


def validate_skill_factory(
    factory: Callable[[], object],
    *,
    expected_skill_id: str | None = None,
) -> SkillConformanceReport:
    if not callable(factory):
        raise SkillConformanceError("Skill factory must be callable.")
    try:
        skill = factory()
    except Exception as exc:
        raise SkillConformanceError("Skill factory failed.") from exc

    report = validate_skill_implementation(skill)
    if expected_skill_id is not None and report.skill_id != expected_skill_id:
        raise SkillConformanceError(
            "Skill factory result does not match the expected Skill ID."
        )
    return report


@dataclass(frozen=True, slots=True)
class SkillSourceFinding:
    path: str
    line: int
    rule: str
    detail: str


@dataclass(frozen=True, slots=True)
class SkillSourceAuditReport:
    files_checked: int
    findings: tuple[SkillSourceFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings


def audit_skill_source(paths: Iterable[str | Path]) -> SkillSourceAuditReport:
    """Statically check portable Skill source for forbidden host coupling.

    This is a conservative developer preflight, not a sandbox or malware scanner.
    It reads Python source only and never imports or executes the inspected files.
    """
    files: list[Path] = []
    for supplied in paths:
        path = Path(supplied)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        elif path.suffix == ".py":
            files.append(path)

    findings: list[SkillSourceFinding] = []
    checked = 0
    for path in sorted(set(files)):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            line = int(getattr(exc, "lineno", 0) or 0)
            findings.append(SkillSourceFinding(
                path=str(path),
                line=line,
                rule="source.invalid",
                detail="Python source could not be parsed safely.",
            ))
            continue

        checked += 1
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = (node.module,)

            for module in modules:
                root = module.split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    findings.append(SkillSourceFinding(
                        path=str(path),
                        line=int(getattr(node, "lineno", 0) or 0),
                        rule="import.forbidden",
                        detail=f"Portable Skills must not import {root}.",
                    ))

            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and func.attr in {"getenv", "system", "popen"}
                ):
                    findings.append(SkillSourceFinding(
                        path=str(path),
                        line=int(getattr(node, "lineno", 0) or 0),
                        rule="host.escape",
                        detail=f"Portable Skills must not call os.{func.attr}.",
                    ))
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "os"
                    and func.value.attr == "environ"
                ):
                    findings.append(SkillSourceFinding(
                        path=str(path),
                        line=int(getattr(node, "lineno", 0) or 0),
                        rule="host.escape",
                        detail="Portable Skills must not access os.environ directly.",
                    ))

            if isinstance(node, ast.Subscript):
                value = node.value
                if (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "os"
                    and value.attr == "environ"
                ):
                    findings.append(SkillSourceFinding(
                        path=str(path),
                        line=int(getattr(node, "lineno", 0) or 0),
                        rule="host.escape",
                        detail="Portable Skills must not access os.environ directly.",
                    ))

    return SkillSourceAuditReport(
        files_checked=checked,
        findings=tuple(sorted(
            findings,
            key=lambda item: (item.path, item.line, item.rule, item.detail),
        )),
    )


def require_clean_skill_source(paths: Iterable[str | Path]) -> SkillSourceAuditReport:
    report = audit_skill_source(paths)
    if report.findings:
        first = report.findings[0]
        raise SkillConformanceError(
            f"Skill source audit failed: {first.rule} at {first.path}:{first.line}."
        )
    return report


@dataclass(frozen=True, slots=True)
class SkillPackageMetadataReport:
    package_name: str
    skill_id: str
    runtime_api_version: str
    sdk_compatibility: str
    entry_point: str


def validate_skill_package_metadata(path: str | Path) -> SkillPackageMetadataReport:
    """Validate the developer-facing package metadata contract.

    External Skill repositories declare one Skill per Python distribution.
    The [tool.gamerhq] section documents the intended Runtime/SDK compatibility,
    while the Python entry point remains the executable discovery contract.
    """
    pyproject = Path(path)
    if pyproject.is_dir():
        pyproject = pyproject / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data["project"]
        package_name = str(project["name"]).strip()
        tool = data["tool"]["gamerhq"]
        skill_id = str(tool["skill-id"]).strip()
        runtime_api = str(tool["runtime-api"]).strip()
        sdk_compat = str(tool["sdk"]).strip()
        entry_points = project["entry-points"]["gamerhq.skills"]
    except (OSError, UnicodeError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise SkillConformanceError(
            "Skill package metadata is missing or invalid."
        ) from exc

    if not package_name:
        raise SkillConformanceError("Skill package name is required.")
    if not skill_id:
        raise SkillConformanceError("Skill package must declare tool.gamerhq.skill-id.")
    if not runtime_api:
        raise SkillConformanceError("Skill package must declare tool.gamerhq.runtime-api.")
    if not sdk_compat:
        raise SkillConformanceError("Skill package must declare tool.gamerhq.sdk.")
    if not isinstance(entry_points, dict) or len(entry_points) != 1:
        raise SkillConformanceError(
            "External Skill packages must expose exactly one gamerhq.skills entry point."
        )
    entry_id, target = next(iter(entry_points.items()))
    if str(entry_id) != skill_id:
        raise SkillConformanceError(
            "Package Skill ID must match its gamerhq.skills entry-point name."
        )
    if not isinstance(target, str) or ":" not in target:
        raise SkillConformanceError("Skill entry point must target module:factory.")
    return SkillPackageMetadataReport(
        package_name=package_name,
        skill_id=skill_id,
        runtime_api_version=runtime_api,
        sdk_compatibility=sdk_compat,
        entry_point=target,
    )
