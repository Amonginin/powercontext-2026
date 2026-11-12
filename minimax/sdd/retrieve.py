"""Building the development context for a task.

Given a task description, this module asks seekdb / OceanBase for the
specification clauses, the source files and the test files that apply, and
returns them as a structured document.

Two properties matter for the topic:

* **Every source is real.** Each entry carries the identifier of the database
  row it came from, the path and the content digest, so a reviewer can read
  the row back and compare the digest with what the context claims.
* **An updated specification changes the answer.** The clauses come from the
  database, so re-importing an edited specification replaces them and the
  context quotes the new text rather than a fixed answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .database import MaterialStore

__all__ = ["ContextBuilder", "build_context", "extract_keywords"]

_STOPWORDS = {
    "的", "了", "和", "是", "在", "有", "给", "把", "为", "与", "或", "要",
    "一个", "这个", "那个", "我们", "可以", "需要", "实现", "增加", "修改",
    "请", "把", "让", "并", "以及", "相关", "进行", "使用", "通过", "让",
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with",
    "add", "implement", "support", "make", "update", "change", "use",
}

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")


def extract_keywords(task: str) -> list[str]:
    """Pull the searchable terms out of a task description.

    Chinese runs are kept whole because the matching happens with a LIKE
    pattern against the clause body; English identifiers are split on
    underscores so ``min_value`` also matches ``min``.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        term = term.strip().lower()
        if not term or term in _STOPWORDS or term in seen:
            return
        seen.add(term)
        terms.append(term)

    for match in _WORD.finditer(task or ""):
        token = match.group(0)
        if token[0].isascii():
            add(token)
            for part in token.split("_"):
                if len(part) >= 3:
                    add(part)
        else:
            add(token)
    return terms


@dataclass
class SourceRef:
    """One piece of context, with everything needed to verify it."""

    kind: str
    identifier: str
    path: str
    title: str
    content_digest: str
    excerpt: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DevelopmentContext:
    """The context handed to the development assistant."""

    task: str
    keywords: list[str]
    specs: list[SourceRef] = field(default_factory=list)
    code: list[SourceRef] = field(default_factory=list)
    tests: list[SourceRef] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "keywords": self.keywords,
            "specs": [s.to_dict() for s in self.specs],
            "code": [s.to_dict() for s in self.code],
            "tests": [s.to_dict() for s in self.tests],
            "state": self.state,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def as_prompt(self) -> str:
        """Render the context as the text handed to the development assistant."""
        lines = [f"# 开发任务\n\n{self.task}\n"]

        lines.append("\n## 适用的规格\n")
        if self.specs:
            for spec in self.specs:
                lines.append(
                    f"### {spec.title}  (来源: {spec.identifier})\n\n{spec.excerpt}\n"
                )
        else:
            lines.append("（数据库中没有检索到适用的规格条目。）\n")

        lines.append("\n## 需要修改的代码\n")
        if self.code:
            for item in self.code:
                lines.append(
                    f"### {item.path}  (来源: {item.identifier})\n\n"
                    f"内容摘要: {item.extra.get('digest', '')}\n"
                )
        else:
            lines.append("（数据库中没有检索到相关代码文件。）\n")

        lines.append("\n## 用于验证的测试\n")
        if self.tests:
            for item in self.tests:
                lines.append(
                    f"### {item.path}  (来源: {item.identifier})\n\n"
                    f"包含测试: {', '.join(item.extra.get('test_names', []))}\n"
                )
        else:
            lines.append("（数据库中没有检索到相关测试文件。）\n")

        if self.state:
            lines.append("\n## 已有的任务状态\n")
            lines.append(json.dumps(self.state, ensure_ascii=False, indent=2))
            lines.append("\n")
        return "".join(lines)


def _excerpt(text: str, keywords: list[str], limit: int = 1200) -> str:
    """Return the part of a document that mentions the keywords."""
    if not text:
        return ""
    lowered = text.lower()
    positions = [lowered.find(k) for k in keywords if lowered.find(k) >= 0]
    if not positions:
        return text[:limit]
    start = max(0, min(positions) - 200)
    return text[start : start + limit]


def _test_names(content: str) -> list[str]:
    return sorted(set(re.findall(r"def (test_\w+)", content)))


class ContextBuilder:
    """Builds the development context out of the engineering-material store."""

    def __init__(self, store: MaterialStore) -> None:
        self.store = store

    def build(self, task: str, state: dict[str, Any] | None = None) -> DevelopmentContext:
        """Query the database and assemble the context."""
        keywords = extract_keywords(task)
        context = DevelopmentContext(task=task, keywords=keywords, state=state or {})

        for clause in self.store.search_clauses(keywords):
            material = self.store.get_material(clause.material_id)
            context.specs.append(
                SourceRef(
                    kind="spec_clause",
                    identifier=clause.clause_id,
                    path=material.path if material else clause.material_id,
                    title=f"{clause.spec_id} · {clause.section}",
                    content_digest=material.content_digest if material else "",
                    excerpt=clause.body,
                    extra={
                        "spec_id": clause.spec_id,
                        "section": clause.section,
                        "material_id": clause.material_id,
                    },
                )
            )

        for row in self.store.search_code(keywords):
            is_test = row.role == "test"
            ref = SourceRef(
                kind="test_file" if is_test else "code_file",
                identifier=row.material_id,
                path=row.path,
                title=row.path,
                content_digest=row.content_digest,
                excerpt=_excerpt(row.content, keywords),
                extra={"role": row.role, "digest": row.content_digest},
            )
            if is_test:
                ref.extra["test_names"] = _test_names(row.content)
                context.tests.append(ref)
            else:
                context.code.append(ref)

        return context


def build_context(
    store: MaterialStore, task: str, state: dict[str, Any] | None = None
) -> DevelopmentContext:
    """Convenience wrapper around :class:`ContextBuilder`."""
    return ContextBuilder(store).build(task, state=state)
