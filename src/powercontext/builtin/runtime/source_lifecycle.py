# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Propagate Source availability across the scoped evidence dependency graph."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator


if TYPE_CHECKING:
    from powercontext.artifacts import ArtifactRef, MemoryCitation, SourceRef

LIFECYCLE_ARTIFACT_FAMILY = "source-lifecycle"
LIFECYCLE_ARTIFACT_ID = "lifecycle"
_PURPOSES = ("generation", "audit")
_KINDS = ("source", "memory", "topic")
_POLICIES = ("all", "any")


class SourceLifecycleError(ValueError):
    """One lifecycle registration, event, or resolution violates the contract."""


class SourceLifecycleEvent(BaseModel):
    """One validated availability event for a registered Source."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    revision: int
    sequence: int
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_text(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence", mode="before")
    @classmethod
    def require_positive_integer(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("field must be a positive integer")  # noqa: TRY003
        return value


@dataclass
class _Node:
    """One registered node of the scoped dependency graph."""

    node_id: str
    kind: str
    parents: tuple[str, ...] = ()
    policy: str | None = None
    revision: int | None = None
    sequence: int = 0
    available: bool = True
    reason: str = ""
    ref: dict[str, Any] = field(default_factory=dict)

    def dump(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "parents": list(self.parents),
            "policy": self.policy,
            "revision": self.revision,
            "sequence": self.sequence,
            "available": self.available,
            "reason": self.reason,
            "ref": self.ref,
        }

    @classmethod
    def load(cls, value: dict[str, Any]) -> _Node:
        return cls(
            node_id=str(value["node_id"]),
            kind=str(value["kind"]),
            parents=tuple(str(parent) for parent in value.get("parents") or ()),
            policy=None if value.get("policy") is None else str(value["policy"]),
            revision=None if value.get("revision") is None else int(value["revision"]),
            sequence=int(value.get("sequence") or 0),
            available=bool(value.get("available", True)),
            reason=str(value.get("reason") or ""),
            ref=dict(value.get("ref") or {}),
        )


class SourceLifecycleService:
    """One scope's persisted lifecycle graph and its native-state propagation."""

    def __init__(self, runtime: Any, scope_id: str) -> None:
        self._runtime = runtime
        self._scope_id = scope_id
        self._nodes: dict[str, _Node] | None = None

    def _database(self) -> Any:
        database = getattr(self._runtime, "_lifecycle_database", None)
        if database is None:
            raise SourceLifecycleError("lifecycle persistence is not configured")
        return database

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        """Register one captured Source at its lifecycle revision."""

        _require_identifier(node_id)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")
        node = _Node(
            node_id=node_id,
            kind="source",
            revision=revision,
            available=True,
            ref=_source_ref_payload(source_ref),
        )
        await self._register(node)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        """Register one readable Memory entry as a derived node."""

        _require_identifier(node_id)
        _require_policy(policy)
        nodes = await self._load()
        _require_parents(node_id, parents, nodes)
        async with self._runtime._context(self._scope_id) as context:
            service = context.artifacts.memory
            try:
                entry = await service.validate_citation(citation)
            except Exception as error:  # noqa: BLE001 - one contract for any unreadable citation
                raise SourceLifecycleError("memory citation must be readable") from error
            text = entry.text
        ref = {
            "memory_ref": _artifact_ref_payload(citation.memory_ref),
            "entry_id": citation.entry_id,
            "entry_version_id": citation.entry_version_id,
            "text": text,
        }
        node = _Node(
            node_id=node_id,
            kind="memory",
            parents=tuple(parents),
            policy=policy,
            available=_derived_availability(parents, policy, nodes),
            ref=ref,
        )
        await self._register(node, nodes=nodes)
        if not node.available:
            # Registering under withdrawn evidence must not leave the entry usable.
            await self._sync_native(nodes)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"],
    ) -> None:
        """Register one readable Topic Memory revision as a derived node."""

        _require_identifier(node_id)
        _require_policy(policy)
        nodes = await self._load()
        _require_parents(node_id, parents, nodes)
        try:
            await self._runtime.records.for_scope(self._scope_id).get_artifact_revision(
                artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision
            )
        except Exception as error:  # noqa: BLE001 - one contract for any unreadable revision
            raise SourceLifecycleError("topic revision must be readable") from error
        node = _Node(
            node_id=node_id,
            kind="topic",
            parents=tuple(parents),
            policy=policy,
            available=_derived_availability(parents, policy, nodes),
            ref={"artifact_ref": _artifact_ref_payload(artifact_ref)},
        )
        await self._register(node, nodes=nodes)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Apply one Source event and propagate the new availability to every descendant."""

        nodes = await self._load()
        node = nodes.get(event.node_id)
        if node is None or node.kind != "source":
            raise SourceLifecycleError("event must address a registered source")
        if node.revision != event.revision:
            raise SourceLifecycleError("event revision must match the registered revision")
        if event.sequence < node.sequence:
            raise SourceLifecycleError("event sequence must not move backwards")
        if event.sequence == node.sequence:
            if node.available != event.available or node.reason != event.reason:
                raise SourceLifecycleError("same sequence with a different event")
            return sorted(_active_ids(nodes))
        node.sequence = event.sequence
        node.available = event.available
        node.reason = event.reason
        _propagate(nodes)
        await self._save(nodes)
        await self._sync_native(nodes)
        return sorted(_active_ids(nodes))

    async def resolve(self, node_id: str, *, purpose: str) -> Any:
        """Return the original reference for one node, honouring the requested purpose."""

        if purpose not in _PURPOSES:
            raise SourceLifecycleError("unknown purpose")
        nodes = await self._load()
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError("unknown node")
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("node is not available for generation")
        return _node_reference(node)

    async def withdrawn_topics(self) -> frozenset[str]:
        """Artifact ids of topic nodes that the current graph does not support."""

        nodes = await self._load()
        return frozenset(
            str(node.ref.get("artifact_ref", {}).get("artifact_id"))
            for node in nodes.values()
            if node.kind == "topic" and not node.available
        )

    async def _register(self, node: _Node, *, nodes: dict[str, _Node] | None = None) -> None:
        graph = nodes if nodes is not None else await self._load()
        existing = graph.get(node.node_id)
        if existing is not None:
            if _same_binding(existing, node):
                return
            raise SourceLifecycleError("node id is already bound to a different registration")
        for parent in node.parents:
            if parent == node.node_id:
                raise SourceLifecycleError("a node cannot depend on itself")
        graph[node.node_id] = node
        await self._save(graph)

    async def _load(self) -> dict[str, _Node]:
        if self._nodes is not None:
            return self._nodes
        from sqlalchemy import select

        from powercontext.builtin.persistence.source_lifecycle_schema import SOURCE_LIFECYCLE_NODES_TABLE

        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database().transaction() as connection:
            rows = (
                await connection.execute(
                    select(table.c.node_id, table.c.payload).where(table.c.scope_id == self._scope_id)
                )
            ).all()
        nodes: dict[str, _Node] = {}
        for node_id, payload in rows:
            try:
                value = json.loads(payload)
            except ValueError:
                continue
            if isinstance(value, dict):
                nodes[str(node_id)] = _Node.load(value)
        self._nodes = nodes
        return nodes

    async def _save(self, nodes: dict[str, _Node]) -> None:
        from sqlalchemy import delete, insert

        from powercontext.builtin.persistence.source_lifecycle_schema import SOURCE_LIFECYCLE_NODES_TABLE

        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database().transaction() as connection:
            await connection.execute(delete(table).where(table.c.scope_id == self._scope_id))
            if nodes:
                await connection.execute(
                    insert(table),
                    [
                        {
                            "scope_id": self._scope_id,
                            "node_id": node_id,
                            "payload": json.dumps(node.dump(), ensure_ascii=False, separators=(",", ":")),
                        }
                        for node_id, node in nodes.items()
                    ],
                )

    async def _sync_native(self, nodes: dict[str, _Node]) -> None:
        """Align native Memory availability with the derived states.

        Topic Memory availability is answered by :meth:`withdrawn_topics` at search
        time, so its published revision, stored body, and exact reads stay untouched.
        """

        if not any(node.kind == "memory" for node in nodes.values()):
            return
        async with self._runtime._context(self._scope_id) as context:
            async with self._runtime._locked(self._scope_id):
                service = context.artifacts.memory
                for node in nodes.values():
                    if node.kind != "memory":
                        continue
                    await self._sync_memory(service, node)

    async def _sync_memory(self, service: Any, node: _Node) -> None:
        entry_id = str(node.ref["entry_id"])
        current = await service.head(str(node.ref["memory_ref"]["artifact_id"]))
        manifest = {item.entry_id: item for item in current.content.manifest.entries}
        item = manifest.get(entry_id)
        if item is None:
            return
        entries = await service.entries(current)
        entry = next((candidate for candidate in entries if candidate.entry_id == entry_id), None)
        if entry is None:
            return
        is_active = getattr(item, "state", "active") == "active"
        if node.available and not is_active:
            await service.reactivate(current, entries=(entry,), reason="lifecycle restore")
        elif not node.available and is_active:
            await service.forget(current, entries=(entry,), reason="lifecycle withdrawal")


def _require_identifier(node_id: object) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node id must be a non-empty string")


def _require_policy(policy: object) -> None:
    if policy not in _POLICIES:
        raise SourceLifecycleError("policy must be all or any")


def _require_parents(node_id: str, parents: object, nodes: dict[str, _Node]) -> None:
    if not isinstance(parents, tuple) or not parents:
        raise SourceLifecycleError("a derived node needs at least one parent")
    if len(set(parents)) != len(parents):
        raise SourceLifecycleError("parents must not repeat")
    for parent in parents:
        if parent == node_id:
            raise SourceLifecycleError("a node cannot depend on itself")
        if parent not in nodes:
            raise SourceLifecycleError("every parent must be registered first")


def _derived_availability(parents: tuple[str, ...], policy: str, nodes: dict[str, _Node]) -> bool:
    support = [nodes[parent].available for parent in parents]
    return all(support) if policy == "all" else any(support)


def _propagate(nodes: dict[str, _Node]) -> None:
    changed = True
    while changed:
        changed = False
        for node in nodes.values():
            if node.kind == "source":
                continue
            available = _derived_availability(node.parents, node.policy or "all", nodes)
            if node.available != available:
                node.available = available
                changed = True


def _active_ids(nodes: dict[str, _Node]) -> set[str]:
    return {node_id for node_id, node in nodes.items() if node.available}


def _same_binding(left: _Node, right: _Node) -> bool:
    return (
        left.kind == right.kind
        and left.revision == right.revision
        and left.parents == right.parents
        and left.policy == right.policy
        and left.ref == right.ref
    )


def _source_ref_payload(source_ref: SourceRef) -> dict[str, Any]:
    return {"source_type": source_ref.source_type, "source_id": source_ref.source_id}


def _artifact_ref_from_payload(payload: dict[str, Any]) -> ArtifactRef:
    from powercontext.artifacts import ArtifactRef

    return ArtifactRef(
        family=str(payload["family"]),
        artifact_id=str(payload["artifact_id"]),
        revision=int(payload["revision"]),
    )


def _artifact_ref_payload(ref: ArtifactRef) -> dict[str, Any]:
    return {"family": ref.family, "artifact_id": ref.artifact_id, "revision": ref.revision}


def _node_reference(node: _Node) -> Any:
    from powercontext.artifacts import ArtifactRef

    if node.kind == "source":
        from powercontext.sources.models import SourceRef as _SourceRef

        payload = node.ref
        return _SourceRef(source_type=str(payload["source_type"]), source_id=str(payload["source_id"]))
    if node.kind == "memory":
        from powercontext.artifacts import MemoryCitation

        ref = node.ref["memory_ref"]
        return MemoryCitation(
            memory_ref=ArtifactRef(
                family=str(ref["family"]), artifact_id=str(ref["artifact_id"]), revision=int(ref["revision"])
            ),
            entry_id=str(node.ref["entry_id"]),
            entry_version_id=str(node.ref["entry_version_id"]),
        )
    ref = node.ref["artifact_ref"]
    return ArtifactRef(
        family=str(ref["family"]), artifact_id=str(ref["artifact_id"]), revision=int(ref["revision"])
    )


__all__ = [
    "LIFECYCLE_ARTIFACT_FAMILY",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "SourceLifecycleService",
]
