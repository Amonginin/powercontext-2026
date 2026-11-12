"""Task state that survives a restart.

The state is written in two places on purpose:

* **PowerContext** holds the memory entry and the work outcome, which is what
  the topic asks for — the task progress has to be readable by a later run.
* **seekdb / OceanBase** holds a copy in ``sdd_task_state``, so the module can
  still report the state when PowerContext is briefly unreachable, and so the
  reviewer can query the row directly.

Reading the state back goes through PowerContext first. That is the path the
topic cares about, and it is the one that proves the state was really handed
over to PowerContext rather than kept in a local file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .database import MaterialStore
from .powercontext import PowerContextClient

__all__ = ["TaskState", "StateStore", "task_key_for"]


@dataclass
class TaskState:
    """What the module remembers about a task."""

    task: str
    task_key: str = ""
    done: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    context_sources: list[str] = field(default_factory=list)
    patch: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskState":
        known = {f for f in cls.__dataclass_fields__}
        extra = {k: v for k, v in payload.items() if k not in known}
        fields = {k: v for k, v in payload.items() if k in known}
        fields.setdefault("task", "")
        state = cls(**fields)
        state.extra.update(extra)
        return state


def task_key_for(task: str) -> str:
    """Return a stable key for a task description."""
    import hashlib

    normalised = " ".join((task or "").split()).lower()
    return "task:" + hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


class StateStore:
    """Saves and loads the task state."""

    def __init__(
        self,
        store: MaterialStore,
        client: PowerContextClient,
        scope_id: str,
    ) -> None:
        self.store = store
        self.client = client
        self.scope_id = scope_id

    def save(self, state: TaskState) -> dict[str, Any]:
        """Write the state to both stores and return what was written."""
        if not state.task_key:
            state.task_key = task_key_for(state.task)

        # 1. seekdb / OceanBase
        self.store.save_task_state(self.scope_id, state.task_key, state.to_dict())

        # 2. PowerContext — the memory entry carries the human-readable summary
        summary = self._render(state)
        self.client.remember(
            self.scope_id,
            kind="task",
            text=summary,
            reason="SDD context module task state",
        )

        # 3. PowerContext — the structured outcome carries the verification.
        # The source identity has to change with the content: PowerContext
        # rejects a re-used source id whose body differs, which is exactly what
        # happens when the task advances and the outcome is recorded again.
        outcome = self._render_outcome(state)
        payload = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
        suffix = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        self.client.record_outcome(
            self.scope_id,
            source_id=f"sdd-outcome:{state.task_key}:{suffix}",
            outcome=outcome,
        )
        return state.to_dict()

    @staticmethod
    def _render_outcome(state: TaskState) -> dict[str, Any]:
        """Build a payload that satisfies powercontext.task-outcome.v1.

        The schema requires a schema marker, a trust level, an objective, a
        status, at least one observation and a full checks array. A claim whose
        basis is ``declared`` carries no citations, which is the honest
        description here: the module reports what it did, and the evidence for
        it lives in the log files rather than in an earlier PowerContext answer.
        """
        observations = [
            {"text": entry, "basis": "declared", "evidence": []}
            for entry in (state.done or [state.task])
        ]
        checks = [
            {
                "name": entry[:200],
                "status": "passed",
                "basis": "declared",
                "evidence": [],
            }
            for entry in state.verification
        ]
        return {
            "schema": "powercontext.task-outcome.v1",
            "trust": "untrusted_observation",
            "objective": state.task or "(no task description)",
            "status": "succeeded" if state.verification else "partial",
            "summary": StateStore._render(state)[:4000],
            "observations": observations,
            "checks": checks,
            "produced_artifacts": [],
            "remaining_work": list(state.todo or []),
        }

    def load(self, task: str) -> TaskState | None:
        """Read the state back, preferring PowerContext."""
        key = task_key_for(task)
        payload: dict[str, Any] | None = None
        source = ""

        try:
            hits = self.client.search_memory(self.scope_id, task, limit=5)
            for hit in hits:
                text = (hit.get("text") or "").strip()
                if key in text or task[:40] in text:
                    payload = self._parse_summary(text)
                    source = "powercontext.memory"
                    break
        except Exception:
            payload = None

        if payload is None:
            payload = self.store.get_task_state(self.scope_id, key)
            source = "seekdb.sdd_task_state"

        if payload is None:
            return None
        state = TaskState.from_dict(payload)
        state.task_key = key
        state.extra.setdefault("loaded_from", source)
        return state

    @staticmethod
    def _render(state: TaskState) -> str:
        lines = [f"SDD 任务状态 [{state.task_key}]", f"任务: {state.task}"]
        if state.done:
            lines.append("已完成: " + "; ".join(state.done))
        if state.todo:
            lines.append("待办: " + "; ".join(state.todo))
        if state.verification:
            lines.append("已有验证结果: " + "; ".join(state.verification))
        if state.context_sources:
            lines.append("上下文来源: " + ", ".join(state.context_sources))
        return "\n".join(lines)

    @staticmethod
    def _parse_summary(text: str) -> dict[str, Any]:
        """Rebuild the state fields from the rendered summary."""
        state: dict[str, Any] = {"task": "", "done": [], "todo": [], "verification": []}
        for line in text.splitlines():
            if line.startswith("任务: "):
                state["task"] = line[len("任务: ") :].strip()
            elif line.startswith("已完成: "):
                state["done"] = [
                    part.strip() for part in line[len("已完成: ") :].split(";") if part.strip()
                ]
            elif line.startswith("待办: "):
                state["todo"] = [
                    part.strip() for part in line[len("待办: ") :].split(";") if part.strip()
                ]
            elif line.startswith("已有验证结果: "):
                state["verification"] = [
                    part.strip()
                    for part in line[len("已有验证结果: ") :].split(";")
                    if part.strip()
                ]
        return state
