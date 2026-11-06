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

"""Context Pack wire protocol for reusing a previous context prefix."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CONTEXT_CHUNK_BYTES = 512
# Runtime chunks stay small so a delta can keep every shared chunk of a context
# whose body only changes near the end.
CONTEXT_PACK_RUNTIME_CHUNK_BYTES = 64


class ContextPackError(ValueError):
    """Raised when a Context Pack or delta violates the wire contract."""


@dataclass(frozen=True)
class ContextPack:
    """One complete context payload split into deterministic chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int = 0

    def __post_init__(self) -> None:
        if not self.byte_count:
            object.__setattr__(self, "byte_count", len(self.to_bytes()))

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build a complete pack from already chunked bytes."""

        chunks = _validate_items(items)
        return cls(items=chunks, digest=_digest(chunks))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CONTEXT_CHUNK_BYTES) -> ContextPack:
        """Split UTF-8 content into bounded, valid chunks."""

        if not isinstance(content, str):
            raise ContextPackError("context content must be text")
        if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        return cls.build(_chunk_bytes(content.encode("utf-8"), chunk_bytes))

    @classmethod
    def after(cls, base: ContextPack, content: str, *, chunk_bytes: int = DEFAULT_CONTEXT_CHUNK_BYTES) -> ContextPack:
        """Split content so that it keeps the base pack's own chunks for the shared prefix.

        A delta can only say "keep the first *k* chunks of the base and append the rest", so
        the target has to be chunked the same way as the base. When the caller builds the base
        with a different chunk size than this method would pick, the two chunk sequences would
        not line up and no prefix could be reused at all.
        """

        if not isinstance(content, str):
            raise ContextPackError("context content must be text")
        if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        base_bytes = b"".join(base.items)
        target_bytes = content.encode("utf-8")
        limit = min(len(base_bytes), len(target_bytes))
        common = 0
        while common < limit and base_bytes[common] == target_bytes[common]:
            common += 1
        keep = 0
        consumed = 0
        for item in base.items:
            if consumed + len(item) > common:
                break
            keep += 1
            consumed += len(item)
        head = base.items[:keep]
        tail = _chunk_bytes(target_bytes[consumed:], chunk_bytes)
        return cls.build((*head, *tail))

    @property
    def wire_size(self) -> int:
        """Exact serialized size of this pack, including the byte_count field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this pack into its canonical wire form."""

        payload = {
            "schema": CONTEXT_PACK_SCHEMA,
            "items": [_encode_item(item) for item in self.items],
            "digest": self.digest,
            "byte_count": 0,
        }
        count = len(_canonical(payload))
        while True:
            payload["byte_count"] = count
            measured = len(_canonical(payload))
            if measured == count:
                return _canonical(payload)
            count = measured

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode and validate one complete pack."""

        payload = _decode(data, {CONTEXT_PACK_SCHEMA}, "schema")
        _require_fields(payload, {"schema", "items", "digest", "byte_count"}, "pack")
        items = _decode_items(payload["items"])
        digest = payload["digest"]
        if not isinstance(digest, str):
            raise ContextPackError("pack digest must be a string")
        expected_digest = _digest(items)
        if digest != expected_digest:
            raise ContextPackError("pack digest does not match its items")
        byte_count = payload["byte_count"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
            raise ContextPackError("pack byte_count must be a non-negative integer")
        pack = cls(items=items, digest=digest, byte_count=byte_count)
        if len(pack.to_bytes()) != byte_count:
            raise ContextPackError("pack byte_count does not match its serialized size")
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """One delta that rebuilds a target pack from a known base pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Keep the shared prefix of both packs and carry the target suffix."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta requires two context packs")
        prefix = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=tuple(target.items[prefix:]),
        )

    @property
    def byte_count(self) -> int:
        """Exact serialized size of this delta, excluding the field itself."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this delta into its canonical wire form."""

        payload = {
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_encode_item(item) for item in self.appended],
        }
        return _canonical(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode and validate one delta."""

        payload = _decode(data, {CONTEXT_PACK_DELTA_SCHEMA}, "schema")
        _require_fields(
            payload,
            {"schema", "base_digest", "target_digest", "prefix_items", "appended"},
            "delta",
        )
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not isinstance(base_digest, str) or not isinstance(target_digest, str):
            raise ContextPackError("delta digests must be strings")
        prefix_items = payload["prefix_items"]
        if isinstance(prefix_items, bool) or not isinstance(prefix_items, int) or prefix_items < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")
        appended = _decode_items(payload["appended"])
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=appended,
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a base pack and one delta."""

    if not isinstance(base, ContextPack):
        raise ContextPackError("a context pack base is required")
    if not isinstance(delta, ContextPackDelta):
        raise ContextPackError("a context pack delta is required")
    if delta.base_digest != base.digest:
        raise ContextPackError("delta does not belong to the given base pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items exceeds the base pack")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta does not rebuild its target pack")
    return target


def _chunk_bytes(data: bytes, chunk_bytes: int) -> tuple[bytes, ...]:
    """Split bytes into bounded chunks that each stay valid UTF-8."""

    chunks: list[bytes] = []
    start = 0
    while start < len(data):
        end = min(start + chunk_bytes, len(data))
        while end > start:
            try:
                data[start:end].decode("utf-8")
            except UnicodeDecodeError:
                end -= 1
                continue
            break
        else:
            raise ContextPackError("chunk_bytes is too small to hold one UTF-8 character")
        chunks.append(data[start:end])
        start = end
    return tuple(chunks)


def _validate_items(items: object) -> tuple[bytes, ...]:
    if isinstance(items, (str, bytes)) or not isinstance(items, (tuple, list)):
        raise ContextPackError("context pack items must be a sequence of bytes")
    chunks: list[bytes] = []
    for item in items:
        if not isinstance(item, (bytes, bytearray)):
            raise ContextPackError("context pack items must be bytes")
        chunks.append(bytes(item))
    return tuple(chunks)


def _encode_item(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("context pack items must be a list")
    items: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError("context pack items must be base64 strings")
        try:
            items.append(base64.b64decode(entry, validate=True))
        except (ValueError, TypeError) as error:
            raise ContextPackError("context pack item is not valid base64") from error
    return tuple(items)


def _digest(items: tuple[bytes, ...]) -> str:
    payload = {"schema": CONTEXT_PACK_SCHEMA, "items": [_encode_item(item) for item in items]}
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode(data: bytes, allowed: set[str], field: str) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)):
        raise ContextPackError("context pack payload must be bytes")
    try:
        payload: Any = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ContextPackError("context pack payload must be valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError("context pack payload must be a JSON object")
    schema = payload.get(field)
    if schema not in allowed:
        raise ContextPackError("unsupported context pack schema")
    return payload


def _require_fields(payload: dict[str, Any], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        raise ContextPackError(f"context {label} must contain exactly the required fields")


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_RUNTIME_CHUNK_BYTES",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CONTEXT_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
