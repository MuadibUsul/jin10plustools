"""Strict, local-only registration for versioned News Skill OS skills."""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml
from jsonschema import Draft202012Validator


class ManifestError(ValueError):
    """A manifest cannot be safely registered or used."""


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema() -> dict[str, Any]:
    return json.loads(files("app.news_skill_os").joinpath("skill-manifest.schema.json").read_text(encoding="utf-8"))


MANIFEST_SCHEMA = _schema()
_MANIFEST_VALIDATOR = Draft202012Validator(MANIFEST_SCHEMA)
_TOOL_PERMISSION = {
    "network_read": "network",
    "database_read": "database_read",
    "database_write": "database_write",
    "publish": "publish",
}


@dataclass(frozen=True)
class RegisteredSkill:
    manifest: dict[str, Any]
    digest: str
    execute: Callable[[dict[str, Any]], dict[str, Any]]

    @property
    def key(self) -> tuple[str, str]:
        return self.manifest["id"], self.manifest["version"]

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        problems = list(Draft202012Validator(self.manifest["input_schema"]).iter_errors(payload))
        if problems:
            raise ManifestError(f"{self.key}: input: {problems[0].message}")
        result = self.execute(payload)
        if not isinstance(result, dict):
            raise ManifestError(f"{self.key}: output must be an object")
        problems = list(Draft202012Validator(self.manifest["output_schema"]).iter_errors(result))
        if problems:
            raise ManifestError(f"{self.key}: output: {problems[0].message}")
        return result


class SkillRegistry:
    def __init__(self, *, granted_permissions: frozenset[str] = frozenset()) -> None:
        self._skills: dict[tuple[str, str], RegisteredSkill] = {}
        self._granted = granted_permissions

    def register_yaml(self, path: Path) -> RegisteredSkill:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ManifestError(f"{path}: YAML root must be an object")
        return self.register(loaded)

    def register(self, manifest: Mapping[str, Any]) -> RegisteredSkill:
        data = dict(manifest)
        errors = sorted(_MANIFEST_VALIDATOR.iter_errors(data), key=lambda error: list(map(str, error.path)))
        if errors:
            error = errors[0]
            location = ".".join(map(str, error.path)) or "manifest"
            raise ManifestError(f"{location}: {error.message}")
        for field in ("input_schema", "output_schema"):
            try:
                Draft202012Validator.check_schema(data[field])
            except Exception as exc:
                raise ManifestError(f"{field}: invalid JSON Schema: {exc}") from exc

        for permission, enabled in data["permissions"].items():
            if enabled and permission not in self._granted:
                raise ManifestError(f"permission denied: {permission}")
        for tool in data["allowed_tools"]:
            permission = _TOOL_PERMISSION.get(tool)
            if permission and not data["permissions"].get(permission):
                raise ManifestError(f"tool {tool} requires permission {permission}")

        for dependency in data.get("dependencies", []):
            dep_id, dep_version = dependency.rsplit("@", 1)
            if (dep_id, dep_version) not in self._skills:
                raise ManifestError(f"missing registered dependency: {dependency}")

        key = data["id"], data["version"]
        digest = _digest(data)
        existing = self._skills.get(key)
        if existing:
            if existing.digest != digest:
                raise ManifestError(f"immutable version conflict: {key[0]}@{key[1]}")
            return existing

        module_name, function_name = data["entrypoint"].split(":", 1)
        # External imports are deliberately unsupported in v1. The JSON Schema
        # confines entrypoints to this package; this is not a process sandbox.
        try:
            module = importlib.import_module(module_name)
            function = getattr(module, function_name)
        except (ImportError, AttributeError) as exc:
            raise ManifestError(f"entrypoint not found: {data['entrypoint']}") from exc
        if not callable(function):
            raise ManifestError(f"entrypoint is not callable: {data['entrypoint']}")
        skill = RegisteredSkill(data, digest, function)
        self._skills[key] = skill
        return skill

    def get(self, skill_id: str, version: str) -> RegisteredSkill:
        try:
            return self._skills[skill_id, version]
        except KeyError as exc:
            raise ManifestError(f"skill is not registered: {skill_id}@{version}") from exc

    def keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._skills)


def bundled_registry() -> SkillRegistry:
    registry = SkillRegistry()
    directory = Path(str(files("app.news_skill_os").joinpath("manifests")))
    lock = json.loads(files("app.news_skill_os").joinpath("manifest.lock.json").read_text(encoding="utf-8"))
    seen: set[str] = set()
    for name in ("event-extraction", "fact-verification", "background-research",
                 "causal-annotation", "quality-review", "fixed-volume", "fixed-turnover",
                 "fixed-valuation", "fixed-liquidity", "fixed-oil-etf"):
        registered = registry.register_yaml(directory / f"{name}.yaml")
        key = f"{registered.key[0]}@{registered.key[1]}"
        if lock.get(key) != registered.digest:
            raise ManifestError(f"manifest lock mismatch: {key}; publish a new version instead of mutating it")
        seen.add(key)
    if seen != set(lock):
        raise ManifestError("manifest lock includes missing or unexpected skill versions")
    return registry
