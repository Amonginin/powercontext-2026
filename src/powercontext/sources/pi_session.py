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

"""Capture the visible events of one selected Pi session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SOURCE_VERSION = "1"
_CHECKPOINT_VERSION = 1


class PiSessionInput(BaseModel):
    """One Pi v3 session archive plus the branch leaf to capture."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible Pi event, with the complete original record retained."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Pi event addressed by session and event identity."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity of one event inside one Pi session."""

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")  # noqa: TRY003
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")  # noqa: TRY003
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(
            f"pi event identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )  # noqa: TRY003
    return identity


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Resolve the selected branch and return its visible events from root to leaf."""

    records = _read_records(value.content)
    header, *nodes = records
    session_id = _session_id(header)

    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for node in nodes:
        event_id = node.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("every record must carry a non-empty string id")  # noqa: TRY003
        if event_id in by_id:
            raise ValueError(f"duplicate record id {event_id!r}")  # noqa: TRY003
        parent_id = node.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or parent_id not in by_id:
                raise ValueError(f"record {event_id!r} has an unknown parent {parent_id!r}")  # noqa: TRY003
        by_id[event_id] = node
        order.append(event_id)

    if not order:
        return ()

    leaf_id = value.leaf_id if value.leaf_id is not None else order[-1]
    if leaf_id not in by_id:
        raise ValueError(f"leaf {leaf_id!r} does not exist in this session")  # noqa: TRY003

    path: list[dict[str, Any]] = []
    current: str | None = leaf_id
    while current is not None:
        node = by_id[current]
        path.append(node)
        parent = node.get("parentId")
        current = parent if isinstance(parent, str) else None
    path.reverse()

    events: list[PiEventInput] = []
    for node in path:
        rendered = _render(node)
        if rendered is None:
            continue
        role, body = rendered
        if not body.strip():
            continue
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=str(node["id"]),
                parent_id=node.get("parentId"),
                event_type=str(node.get("type", "")),
                role=role,
                content=f"{role}: {body}",
                record=node,
            )
        )
    return tuple(events)


class _PiEventAdapter:
    """Resolve and read the Pi event Source Definition."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one Pi event as canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    _PiEventAdapter(),
    version=PI_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import the visible events of one Pi session branch incrementally."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SOURCE_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))
        imported = _checkpoint_state(session.checkpoint)
        for event in events:
            key = f"{event.session_id}:{event.event_id}"
            digest = _record_digest(event.record)
            known = imported.get(key)
            if known is not None:
                if known != digest:
                    raise ValueError(  # noqa: TRY003
                        f"event {event.event_id!r} changed after it was imported"
                    )
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            imported[key] = digest
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": _CHECKPOINT_VERSION, "events": imported},
        )


def _read_records(content: bytes) -> list[dict[str, Any]]:
    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("content must be bytes")  # noqa: TRY003
    try:
        text = bytes(content).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("content must be valid UTF-8") from error
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"record is not valid JSON: {error}") from error  # noqa: TRY003
        if not isinstance(record, dict):
            raise ValueError("every record must be a JSON object")  # noqa: TRY003
        records.append(record)
    if not records:
        raise ValueError("session archive must not be empty")  # noqa: TRY003
    return records


def _session_id(header: dict[str, Any]) -> str:
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("first record must be a version 3 session header")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session header must carry a non-empty string id")  # noqa: TRY003
    return session_id


def _render(node: dict[str, Any]) -> tuple[str, str] | None:
    record_type = node.get("type")
    if record_type == "message":
        return _render_message(node.get("message"))
    if record_type in {"compaction", "branch_summary"}:
        summary = node.get("summary")
        return (str(record_type), summary if isinstance(summary, str) else "")
    if record_type == "custom_message":
        return ("custom", _render_blocks(node.get("content")))
    return None


def _render_message(message: object) -> tuple[str, str] | None:
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str):
        return None
    if role == "toolResult":
        tool_name = str(message.get("toolName", ""))
        call_id = str(message.get("toolCallId", ""))
        return (role, f"[tool_result {tool_name} {call_id}] {_render_blocks(message.get('content'))}")
    if role == "bashExecution":
        command = str(message.get("command", ""))
        output = str(message.get("output", ""))
        return (role, f"$ {command}\n{output}")
    return (role, _render_blocks(message.get("content")))


def _render_blocks(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == "toolCall":
            name = str(block.get("name", ""))
            call_id = str(block.get("id", ""))
            parts.append(f"[tool_call {name} {call_id}] {_compact_json(block.get('arguments', {}))}")
    return "\n".join(parts)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_digest(record: dict[str, JsonValue]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checkpoint_state(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("pi checkpoint must be a JSON object")  # noqa: TRY003
    events = checkpoint.get("events", {})
    if not isinstance(events, dict):
        raise ValueError("pi checkpoint events must be a JSON object")  # noqa: TRY003
    state: dict[str, str] = {}
    for key, value in events.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("pi checkpoint entries must map strings to strings")  # noqa: TRY003
        state[key] = value
    return state


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
