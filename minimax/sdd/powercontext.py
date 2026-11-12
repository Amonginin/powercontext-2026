"""A small PowerContext HTTP client.

Only the calls the context module needs are implemented: saving the task
progress and reading it back. The client uses the standard library so the
module has no third-party dependency beyond the database driver.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import Settings

__all__ = ["PowerContextClient", "PowerContextError"]


class PowerContextError(RuntimeError):
    """Raised when PowerContext cannot satisfy a request."""


class PowerContextClient:
    """Talks to the PowerContext server over HTTP."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.powercontext_url.rstrip('/')}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers=self.settings.powercontext_headers, method="POST"
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.powercontext_timeout
            ) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise PowerContextError(f"{path} 返回 HTTP {exc.code}: {detail[:400]}") from exc
        except urllib.error.URLError as exc:
            raise PowerContextError(
                f"无法连接 PowerContext {url} —— {exc.reason}。"
                f"请先执行 `powercontext server run`。"
            ) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PowerContextError(f"{path} 返回的不是 JSON：{raw[:200]}") from exc

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.settings.powercontext_url.rstrip('/')}{path}"
        request = urllib.request.Request(url, headers=self.settings.powercontext_headers)
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.powercontext_timeout
            ) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise PowerContextError(f"{path} 返回 HTTP {exc.code}: {detail[:400]}") from exc
        except urllib.error.URLError as exc:
            raise PowerContextError(
                f"无法连接 PowerContext {url} —— {exc.reason}。"
            ) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PowerContextError(f"{path} 返回的不是 JSON：{raw[:200]}") from exc

    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        """Return the readiness payload. This endpoint is always public."""
        return self._get("/health/ready")

    def create_scope(self, title: str, summary: str, idempotency_key: str) -> str:
        """Create the scope and return its id. Repeating the key reuses it."""
        payload = self._post(
            "/v1/scopes",
            {"title": title, "summary": summary, "idempotency_key": idempotency_key},
        )
        scope_id = payload.get("scope_id") or payload.get("id")
        if not scope_id:
            raise PowerContextError(f"创建 scope 的响应里没有 scope_id：{payload}")
        return str(scope_id)

    def remember(self, scope_id: str, kind: str, text: str, reason: str = "") -> dict[str, Any]:
        """Save one curated memory entry."""
        payload: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        return self._post("/v1/memory/remember", payload)

    def search_memory(
        self, scope_id: str, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search the memory entries and return the hits."""
        payload = self._post(
            "/v1/memory/search", {"scope_id": scope_id, "query": query, "limit": limit}
        )
        hits = payload.get("hits") or []
        return hits if isinstance(hits, list) else []

    def record_outcome(self, scope_id: str, source_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
        """Record the verification result of the task."""
        return self._post(
            "/v1/work/outcomes/record",
            {"scope_id": scope_id, "source_id": source_id, "outcome": outcome},
        )

    def create_contract(self, scope_id: str, source_id: str, contract: dict[str, Any]) -> dict[str, Any]:
        """Create the work contract that describes the task."""
        return self._post(
            "/v1/work/contracts/create",
            {"scope_id": scope_id, "source_id": source_id, "contract": contract},
        )

    def continue_handoff(self, scope_id: str) -> dict[str, Any]:
        """Read back the most recent handoff, which carries the task state."""
        return self._post(
            "/v1/handoff/continue", {"scope_id": scope_id, "selection": "latest"}
        )
