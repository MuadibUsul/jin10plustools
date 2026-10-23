"""Synchronous, isolated DAG runner; no scheduler, database or publisher."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

from .manifest import SkillRegistry


class WorkflowError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _schema_at(schema: Mapping[str, Any], path: list[str]) -> Mapping[str, Any]:
    current = schema
    for part in path:
        current = current.get("properties", {}).get(part)
        if not isinstance(current, dict):
            raise WorkflowError(f"input binding path is absent from schema: {'.'.join(path)}")
    return current


def _value_at(value: Mapping[str, Any], path: list[str]) -> Any:
    result: Any = value
    for part in path:
        if not isinstance(result, dict) or part not in result:
            raise WorkflowError(f"input binding value is absent: {'.'.join(path)}")
        result = result[part]
    return result


@dataclass(frozen=True)
class Node:
    id: str
    skill_id: str
    skill_version: str
    depends_on: tuple[str, ...]
    bindings: dict[str, str]
    review_gate: bool = False


@dataclass(frozen=True)
class Workflow:
    id: str
    input_schema: dict[str, Any]
    nodes: tuple[Node, ...]
    order: tuple[str, ...]

    @classmethod
    def load_yaml(cls, path: Path, registry: SkillRegistry) -> Workflow:
        return cls.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")), registry)

    @classmethod
    def from_dict(cls, source: Any, registry: SkillRegistry) -> Workflow:
        if not isinstance(source, dict) or source.get("api_version") != "news.workflow/v1":
            raise WorkflowError("api_version must be news.workflow/v1")
        if not isinstance(source.get("id"), str) or not isinstance(source.get("input_schema"), dict):
            raise WorkflowError("workflow id and input_schema are required")
        try:
            Draft202012Validator.check_schema(source["input_schema"])
        except Exception as exc:
            raise WorkflowError(f"invalid workflow input schema: {exc}") from exc
        rows = source.get("nodes")
        if not isinstance(rows, list) or not rows:
            raise WorkflowError("workflow needs at least one node")
        nodes: list[Node] = []
        for row in rows:
            if not isinstance(row, dict):
                raise WorkflowError("node must be an object")
            try:
                node = Node(id=row["id"], skill_id=row["skill_id"], skill_version=row["skill_version"],
                            depends_on=tuple(row.get("depends_on", [])), bindings=dict(row["bindings"]),
                            review_gate=row.get("review_gate", False))
            except (KeyError, TypeError, ValueError) as exc:
                raise WorkflowError(f"invalid node: {exc}") from exc
            if not isinstance(node.id, str) or not node.id or not isinstance(node.review_gate, bool):
                raise WorkflowError("node id or review_gate is invalid")
            registry.get(node.skill_id, node.skill_version)
            nodes.append(node)
        by_id = {node.id: node for node in nodes}
        if len(by_id) != len(nodes):
            raise WorkflowError("duplicate node id")
        for node in nodes:
            if any(dep not in by_id for dep in node.depends_on):
                raise WorkflowError(f"node {node.id} has missing dependency")
        # Kahn traversal is deterministic in declaration order and detects cycles.
        pending = set(by_id)
        order: list[str] = []
        while pending:
            ready = [node.id for node in nodes if node.id in pending and set(node.depends_on) <= set(order)]
            if not ready:
                raise WorkflowError("workflow contains a cycle")
            order.extend(ready)
            pending.difference_update(ready)
        for node in nodes:
            skill = registry.get(node.skill_id, node.skill_version)
            schema = skill.manifest["input_schema"]
            if not set(schema.get("required", [])) <= node.bindings.keys():
                raise WorkflowError(f"node {node.id} omits required input binding")
            if any(key not in schema.get("properties", {}) for key in node.bindings):
                raise WorkflowError(f"node {node.id} binds unknown input field")
            for key, reference in node.bindings.items():
                if not isinstance(reference, str) or not reference.startswith("$"):
                    raise WorkflowError(f"node {node.id} has invalid binding")
                parts = reference[1:].split(".")
                if parts[0] == "input" and len(parts) >= 2:
                    origin = _schema_at(source["input_schema"], parts[1:])
                elif parts[0] == "node" and len(parts) >= 2:
                    upstream = parts[1]
                    if upstream not in node.depends_on:
                        raise WorkflowError(f"node {node.id} must directly depend on {upstream}")
                    origin = _schema_at(registry.get(by_id[upstream].skill_id,
                                                     by_id[upstream].skill_version).manifest["output_schema"], parts[2:])
                else:
                    raise WorkflowError(f"node {node.id} has invalid binding path")
                expected = schema["properties"][key]
                if origin.get("type") and expected.get("type") and origin["type"] != expected["type"]:
                    raise WorkflowError(f"node {node.id} binding {key} has incompatible schema type")
            if node.review_gate and "approved" not in skill.manifest["output_schema"].get("properties", {}):
                raise WorkflowError(f"review gate {node.id} must return approved")
        return cls(source["id"], source["input_schema"], tuple(nodes), tuple(order))


@dataclass(frozen=True)
class NodeRun:
    run_id: str
    node_id: str
    skill_version: str
    status: str
    input_digest: str
    output_digest: str | None
    source_snapshot_hashes: tuple[str, ...]
    duration_ms: int
    attempts: int
    cached: bool = False
    error: str | None = None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    outputs: dict[str, dict[str, Any]]
    records: tuple[NodeRun, ...]


def _claim_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {str(item) for item in value.get("claim_ids", [])} if isinstance(value.get("claim_ids"), list) else set()
        for item in value.values():
            found.update(_claim_ids(item))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for item in value:
            found.update(_claim_ids(item))
        return found
    return set()


@dataclass
class _CacheEntry:
    output: dict[str, Any]
    claim_ids: set[str] = field(default_factory=set)


class WorkflowEngine:
    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry
        self._cache: dict[tuple[str, str, str], _CacheEntry] = {}

    def invalidate_claims(self, claim_ids: set[str]) -> int:
        """Drop only deterministic cache entries whose inputs/outputs name changed claims."""
        affected = [key for key, entry in self._cache.items() if entry.claim_ids & claim_ids]
        for key in affected:
            del self._cache[key]
        return len(affected)

    def run(self, workflow: Workflow, payload: dict[str, Any], *, cancelled: bool = False) -> RunResult:
        problems = list(Draft202012Validator(workflow.input_schema).iter_errors(payload))
        if problems:
            raise WorkflowError(f"workflow input: {problems[0].message}")
        run_id = str(uuid.uuid4())
        outputs: dict[str, dict[str, Any]] = {}
        records: list[NodeRun] = []
        nodes = {node.id: node for node in workflow.nodes}
        statuses: dict[str, str] = {}
        for node_id in workflow.order:
            node = nodes[node_id]
            skill = self.registry.get(node.skill_id, node.skill_version)
            if cancelled or any(statuses[dep] != "succeeded" for dep in node.depends_on):
                status = "cancelled" if cancelled else "blocked"
                statuses[node_id] = status
                records.append(NodeRun(run_id, node_id, node.skill_version, status, "", None, (), 0, 0))
                continue
            values: dict[str, Any] = {}
            for key, reference in node.bindings.items():
                parts = reference[1:].split(".")
                values[key] = _value_at(payload if parts[0] == "input" else outputs[parts[1]],
                                        parts[1:] if parts[0] == "input" else parts[2:])
            input_hash = digest(values)
            cache_key = (node.skill_id, node.skill_version, input_hash)
            cacheable = skill.manifest["idempotency"] == "deterministic" and set(skill.manifest["allowed_tools"]) <= {"local_fixture"}
            entry = self._cache.get(cache_key) if cacheable else None
            start = time.monotonic()
            attempts = 0
            error: str | None = None
            result: dict[str, Any] | None = entry.output if entry else None
            if result is None:
                limit = skill.manifest["retry_policy"]["max_attempts"] if cacheable else 1
                for attempt in range(limit):
                    attempts = attempt + 1
                    try:
                        result = skill.run(values)
                        if time.monotonic() - start > skill.manifest["timeouts"]["seconds"]:
                            raise TimeoutError("skill exceeded its declared time budget")
                        error = None
                        break
                    except Exception as exc:
                        result = None
                        error = f"{type(exc).__name__}: {exc}"
            duration = round((time.monotonic() - start) * 1000)
            if result is None:
                statuses[node_id] = "failed"
                records.append(NodeRun(run_id, node_id, node.skill_version, "failed", input_hash, None, (),
                                       duration, attempts, error=error))
                continue
            if cacheable and entry is None:
                self._cache[cache_key] = _CacheEntry(result, _claim_ids(values) | _claim_ids(result))
            status = "blocked" if node.review_gate and result.get("approved") is not True else "succeeded"
            outputs[node_id] = result
            statuses[node_id] = status
            hashes = tuple(str(x) for x in values.get("source_snapshot_hashes", []) if isinstance(x, str))
            records.append(NodeRun(run_id, node_id, node.skill_version, status, input_hash, digest(result),
                                   hashes, duration, attempts, cached=entry is not None))
        overall = "cancelled" if cancelled else ("failed" if "failed" in statuses.values() else
                  "blocked" if "blocked" in statuses.values() else "succeeded")
        return RunResult(run_id, overall, outputs, tuple(records))
