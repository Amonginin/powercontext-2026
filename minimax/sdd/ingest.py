"""Ingestion of the engineering material into seekdb / OceanBase.

The material is split by role so the context builder can ask for the right
kind of source:

* ``spec`` — the specification files, which are additionally split into
  clauses so a context can quote one section instead of a whole document;
* ``code`` — the source files;
* ``test`` — the test files.

Source identifiers are content addressed: ``spec:SPEC-001.md:ab12cd34ef56``.
Re-importing an unchanged file therefore reuses the same identifier, while a
changed file gets a new one and the old row stays where it is. That is what
lets the context report a specification version rather than "whatever was
there last time".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .database import ClauseRow, MaterialStore

__all__ = ["IngestedMaterial", "ingest_project", "parse_spec_clauses", "material_id_for"]

_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
_ROLE_BY_SUFFIX = {
    ".md": "spec",
    ".py": "code",
    ".txt": "code",
    ".sql": "code",
}


@dataclass(frozen=True)
class IngestedMaterial:
    """What was ingested, so the caller can report it."""

    material_id: str
    role: str
    path: str
    content_digest: str
    clause_count: int = 0


def material_id_for(role: str, path: str, content: str) -> str:
    """Return the content-addressed identifier of one material."""
    short = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"{role}:{path}:{short}"


def classify(path: Path, project_root: Path) -> str:
    """Decide whether a file is a specification, a source file or a test."""
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        relative = path
    parts = relative.parts
    if "spec" in parts:
        return "spec"
    if "tests" in parts or path.name.startswith("test_"):
        return "test"
    suffix = path.suffix.lower()
    return _ROLE_BY_SUFFIX.get(suffix, "code")


def parse_spec_clauses(spec_id: str, text: str) -> list[ClauseRow]:
    """Split a specification file into its clauses.

    A clause starts at a level-2 or level-3 heading and runs until the next
    heading of the same or higher level. The clause identifier contains the
    material identifier prefix, so it stays stable as long as the section does.
    """
    clauses: list[ClauseRow] = []
    current_title: str | None = None
    current_level: int = 0
    current_body: list[str] = []

    def flush() -> None:
        if current_title is None:
            return
        body = "\n".join(current_body).strip()
        if not body:
            return
        section = current_title
        digest = hashlib.sha256(
            f"{spec_id}|{section}|{body}".encode("utf-8")
        ).hexdigest()[:12]
        clauses.append(
            ClauseRow(
                clause_id=f"clause:{spec_id}:{digest}",
                material_id="",
                spec_id=spec_id,
                section=section,
                title=current_title,
                body=body,
            )
        )

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            current_level = len(match.group(1))
            current_title = match.group(2).strip()
            current_body = []
            continue
        if current_title is not None:
            current_body.append(line)
    flush()
    return clauses


def iter_project_files(project_root: Path) -> Iterable[Path]:
    """Yield the files of the sample project that are worth ingesting.

    ``staged-updates`` is skipped on purpose: it holds the edited specification
    used to demonstrate that an updated specification changes the context, and
    importing it up front would let the old and the new wording answer the same
    query. It gets copied into ``spec/`` only for that one demonstration.
    """
    skip_dirs = {
        "__pycache__", ".git", ".pytest_cache", "snapshots", ".venv", "staged-updates",
    }
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() in {".md", ".py", ".txt", ".sql"}:
            yield path


def ingest_project(
    store: MaterialStore, project_root: Path
) -> list[IngestedMaterial]:
    """Ingest every file of the project and return what was stored."""
    results: list[IngestedMaterial] = []
    for path in iter_project_files(project_root):
        role = classify(path, project_root)
        try:
            relative = path.relative_to(project_root)
        except ValueError:
            relative = path
        rel_path = relative.as_posix()
        content = path.read_text(encoding="utf-8")
        material_id = material_id_for(role, rel_path, content)
        digest = store.upsert_material(
            material_id,
            role,
            rel_path,
            content,
            metadata={"bytes": len(content.encode("utf-8")), "role": role},
        )
        clause_count = 0
        if role == "spec":
            spec_id = path.stem
            parsed = parse_spec_clauses(spec_id, content)
            # The parser cannot know which material a clause belongs to, so the
            # identifier is stamped on here; without it the stored clause has no
            # way back to the file and the context cannot report a digest.
            clauses = [
                ClauseRow(
                    clause_id=clause.clause_id,
                    material_id=material_id,
                    spec_id=clause.spec_id,
                    section=clause.section,
                    title=clause.title,
                    body=clause.body,
                )
                for clause in parsed
            ]
            clause_count = len(clauses)
            # Delete by the stable role:path: prefix so the clauses of an earlier
            # revision of this file cannot survive alongside the new ones.
            store.replace_clauses(material_id, clauses, path_prefix=f"{role}:{rel_path}:")
        # And drop the superseded material rows themselves.
        store.purge_stale_material(role, rel_path, material_id)
        results.append(
            IngestedMaterial(material_id, role, rel_path, digest, clause_count)
        )
    return results
