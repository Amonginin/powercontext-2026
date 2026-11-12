"""The engineering-material store, backed by seekdb or OceanBase.

The topic requires the engineering material to live in seekdb or OceanBase and
to be read back through a real query when the context is built. This module
speaks the MySQL wire protocol, which both servers accept, so the same code
works against an embedded seekdb, a standalone seekdb server and an OceanBase
tenant.

The schema is deliberately small:

* ``sdd_material`` — one row per ingested file: the path, the role
  (``spec`` / ``code`` / ``test``), the body and a content digest.
* ``sdd_clause`` — one row per specification clause, with the section it came
  from, so the context can quote a clause instead of a whole file.

The digest is what makes the context verifiable: a reviewer can re-read a row
and compare its digest with the one the module reported.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .config import Settings

__all__ = ["MaterialStore", "MaterialRow", "ClauseRow", "DatabaseUnavailableError"]


class DatabaseUnavailableError(RuntimeError):
    """Raised when the engineering-material store cannot be reached."""


@dataclass(frozen=True)
class MaterialRow:
    """One ingested file."""

    material_id: str
    role: str
    path: str
    content: str
    content_digest: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ClauseRow:
    """One specification clause."""

    clause_id: str
    material_id: str
    spec_id: str
    section: str
    title: str
    body: str


def digest_of(content: str) -> str:
    """Return the stable digest used to prove a row still holds this content."""
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


class MaterialStore:
    """Reads and writes the engineering material in seekdb / OceanBase."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    def _connect(self):
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - dependency check
            raise DatabaseUnavailableError(
                "需要 pymysql：pip install pymysql"
            ) from exc
        try:
            return pymysql.connect(
                host=self.settings.database_host,
                port=self.settings.database_port,
                user=self.settings.database_user,
                password=self.settings.database_password,
                database=self.settings.database_name,
                charset="utf8mb4",
                connect_timeout=self.settings.database_timeout,
                read_timeout=self.settings.database_timeout,
                write_timeout=self.settings.database_timeout,
                autocommit=True,
            )
        except Exception as exc:  # pragma: no cover - depends on the server
            raise DatabaseUnavailableError(
                f"无法连接 {self.settings.database_host}:{self.settings.database_port}"
                f"/{self.settings.database_name} —— {exc}"
            ) from exc

    def _cursor(self):
        conn = self._connect()
        try:
            return conn, conn.cursor()
        except Exception:
            conn.close()
            raise

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Create the tables if they are not there yet."""
        conn, cur = self._cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sdd_material (
                  material_id   VARCHAR(256) NOT NULL,
                  role          VARCHAR(32)  NOT NULL,
                  path          VARCHAR(1024) NOT NULL,
                  content       MEDIUMTEXT   NOT NULL,
                  content_digest VARCHAR(128) NOT NULL,
                  metadata      TEXT,
                  PRIMARY KEY (material_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sdd_clause (
                  clause_id  VARCHAR(256) NOT NULL,
                  material_id VARCHAR(256) NOT NULL,
                  spec_id    VARCHAR(128) NOT NULL,
                  section   VARCHAR(256) NOT NULL,
                  title     VARCHAR(512) NOT NULL,
                  body      MEDIUMTEXT  NOT NULL,
                  PRIMARY KEY (clause_id)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sdd_task_state (
                  scope_id   VARCHAR(256) NOT NULL,
                  task_key   VARCHAR(256) NOT NULL,
                  payload    MEDIUMTEXT   NOT NULL,
                  updated_at VARCHAR(64)  NOT NULL,
                  PRIMARY KEY (scope_id, task_key)
                )
                """
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def upsert_material(
        self,
        material_id: str,
        role: str,
        path: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Insert or replace one material row and return its digest."""
        payload = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        conn, cur = self._cursor()
        try:
            cur.execute(
                """
                REPLACE INTO sdd_material
                  (material_id, role, path, content, content_digest, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (material_id, role, path, content, digest_of(content), payload),
            )
        finally:
            conn.close()
        return digest_of(content)

    def replace_clauses(
        self, material_id: str, clauses: Sequence[ClauseRow], path_prefix: str = ""
    ) -> int:
        """Replace the clauses that belong to one specification file.

        The identifiers are content addressed, so an edited specification gets
        a new ``material_id``. Deleting only by that identifier would leave the
        clauses of the previous revision behind, and the context would then
        quote both the old and the new wording for the same query. The delete
        therefore goes by the ``role:path:`` prefix, which stays the same
        across revisions of one file.
        """
        conn, cur = self._cursor()
        try:
            if path_prefix:
                cur.execute(
                    "DELETE FROM sdd_clause WHERE material_id LIKE %s",
                    (f"{path_prefix}%",),
                )
            else:
                cur.execute(
                    "DELETE FROM sdd_clause WHERE material_id = %s", (material_id,)
                )
            for clause in clauses:
                cur.execute(
                    """
                    REPLACE INTO sdd_clause
                      (clause_id, material_id, spec_id, section, title, body)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        clause.clause_id,
                        clause.material_id,
                        clause.spec_id,
                        clause.section,
                        clause.title,
                        clause.body,
                    ),
                )
            return len(clauses)
        finally:
            conn.close()

    def purge_stale_material(self, role: str, path: str, keep_material_id: str) -> int:
        """Drop earlier revisions of one file so a query cannot see both.

        A specification that was edited keeps its old rows under an old
        ``material_id``. They are unreachable from the clause table after
        :meth:`replace_clauses`, but they would still be returned by a code or
        material query, which reports a digest the current file no longer has.
        """
        conn, cur = self._cursor()
        try:
            cur.execute(
                "DELETE FROM sdd_material WHERE role = %s AND path = %s "
                "AND material_id <> %s",
                (role, path, keep_material_id),
            )
            return cur.rowcount
        finally:
            conn.close()

    def save_task_state(self, scope_id: str, task_key: str, payload: dict[str, Any]) -> None:
        """Persist the task state so a later process can pick it up."""
        import datetime

        conn, cur = self._cursor()
        try:
            cur.execute(
                """
                REPLACE INTO sdd_task_state (scope_id, task_key, payload, updated_at)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    scope_id,
                    task_key,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                ),
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    def get_material(self, material_id: str) -> MaterialRow | None:
        conn, cur = self._cursor()
        try:
            cur.execute(
                """
                SELECT material_id, role, path, content, content_digest, metadata
                FROM sdd_material WHERE material_id = %s
                """,
                (material_id,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            meta = json.loads(row[5] or "{}")
        except json.JSONDecodeError:
            meta = {}
        return MaterialRow(row[0], row[1], row[2], row[3], row[4], meta)

    def list_material(self, role: str | None = None) -> list[MaterialRow]:
        conn, cur = self._cursor()
        try:
            if role:
                cur.execute(
                    """
                    SELECT material_id, role, path, content, content_digest, metadata
                    FROM sdd_material WHERE role = %s ORDER BY path
                    """,
                    (role,),
                )
            else:
                cur.execute(
                    """
                    SELECT material_id, role, path, content, content_digest, metadata
                    FROM sdd_material ORDER BY role, path
                    """
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        out: list[MaterialRow] = []
        for row in rows:
            try:
                meta = json.loads(row[5] or "{}")
            except json.JSONDecodeError:
                meta = {}
            out.append(MaterialRow(row[0], row[1], row[2], row[3], row[4], meta))
        return out

    def search_clauses(self, keywords: Iterable[str], limit: int = 20) -> list[ClauseRow]:
        """Find the clauses whose text matches any of the keywords.

        The match runs inside the database, so the answer the context reports
        really comes from a query against seekdb rather than from a local scan.
        The rows come back grouped by clause; the ranking that decides which of
        them is most relevant happens here, by how often the keywords appear in
        the section title and body. Ordering by identifier alone would put
        "4. Non-goals" above the clause the task is actually about.
        """
        terms = [k.strip().lower() for k in keywords if k and k.strip()]
        if not terms:
            return []
        conn, cur = self._cursor()
        try:
            clauses: list[ClauseRow] = []
            seen: set[str] = set()
            for term in terms:
                pattern = f"%{term}%"
                cur.execute(
                    """
                    SELECT clause_id, material_id, spec_id, section, title, body
                    FROM sdd_clause
                    WHERE body LIKE %s OR title LIKE %s
                    ORDER BY clause_id
                    LIMIT %s
                    """,
                    (pattern, pattern, limit),
                )
                for row in cur.fetchall():
                    if row[0] in seen:
                        continue
                    seen.add(row[0])
                    clauses.append(ClauseRow(row[0], row[1], row[2], row[3], row[4], row[5]))
        finally:
            conn.close()

        def relevance(clause: ClauseRow) -> tuple[int, int, str]:
            title = clause.title.lower()
            body = clause.body.lower()
            # A hit in the heading is worth much more than one in the prose.
            score = sum(3 for term in terms if term in title)
            score += sum(1 for term in terms if term in body)
            # Longer clauses that match are usually the substantive ones.
            return (-score, -len(clause.body), clause.clause_id)

        clauses.sort(key=relevance)
        return clauses[:limit]

    def search_code(self, keywords: Iterable[str], limit: int = 20) -> list[MaterialRow]:
        """Find the code and test files whose content matches the keywords."""
        terms = [k.strip() for k in keywords if k and k.strip()]
        if not terms:
            return []
        conn, cur = self._cursor()
        try:
            found: list[MaterialRow] = []
            seen: set[str] = set()
            for term in terms:
                pattern = f"%{term}%"
                cur.execute(
                    """
                    SELECT material_id, role, path, content, content_digest, metadata
                    FROM sdd_material
                    WHERE role IN ('code', 'test') AND (content LIKE %s OR path LIKE %s)
                    ORDER BY path
                    LIMIT %s
                    """,
                    (pattern, pattern, limit),
                )
                for row in cur.fetchall():
                    if row[0] in seen:
                        continue
                    seen.add(row[0])
                    try:
                        meta = json.loads(row[5] or "{}")
                    except json.JSONDecodeError:
                        meta = {}
                    found.append(MaterialRow(row[0], row[1], row[2], row[3], row[4], meta))
            return found[:limit]
        finally:
            conn.close()

    def get_task_state(self, scope_id: str, task_key: str) -> dict[str, Any] | None:
        conn, cur = self._cursor()
        try:
            cur.execute(
                """
                SELECT payload FROM sdd_task_state
                WHERE scope_id = %s AND task_key = %s
                """,
                (scope_id, task_key),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None
