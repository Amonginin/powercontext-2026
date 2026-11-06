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

"""Row-level CSV Sources whose evidence can be traced back to one record."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_PREFIX = "csv"


class CSVDocumentInput(BaseModel):
    """One offline CSV document to split into row-level Sources."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One parsed CSV record, in original column order and value fidelity."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Captured CSV record addressed by document identity and primary key."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable identity of one CSV row within one document."""

    _validate_document_id(document_id)
    if not isinstance(primary_key_value, str):
        raise ValueError("primary_key_value must be a string")  # noqa: TRY003
    identity = f"{CSV_ROW_SOURCE_PREFIX}:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(
            f"csv row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )  # noqa: TRY003
    return identity


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse one offline CSV document into row inputs, or fail atomically."""

    _validate_document_id(value.document_id)
    primary_key = value.primary_key
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
    if not primary_key.strip():
        raise ValueError("primary_key must not be blank")  # noqa: TRY003

    records = _read_records(value.content)
    if not records:
        raise ValueError("csv document must start with a header record")  # noqa: TRY003

    header = tuple(records[0])
    _validate_header(header)
    if primary_key not in header:
        raise ValueError(f"primary_key {primary_key!r} is not a column of the header")  # noqa: TRY003
    key_index = header.index(primary_key)

    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != len(header):
            raise ValueError(  # noqa: TRY003
                f"record width {len(record)} does not match header width {len(header)}"
            )
        values = tuple(record)
        primary_key_value = values[key_index]
        if not primary_key_value.strip():
            raise ValueError("primary key value must not be blank")  # noqa: TRY003
        if primary_key_value in seen:
            raise ValueError(f"duplicate primary key value {primary_key_value!r}")  # noqa: TRY003
        seen.add(primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=primary_key,
                primary_key_value=primary_key_value,
                columns=header,
                values=values,
            )
        )
    return tuple(rows)


class _CSVRowAdapter:
    """Resolve and read the row-level CSV Source Definition."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one CSV row as canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row: dict[str, JsonValue] = {}
        for column, value in zip(source.columns, source.values, strict=True):
            row[column] = value
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    _CSVRowAdapter(),
    version="1",
    projections=(CSVRowTextEvidenceProjection(),),
)


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003


def _read_records(content: bytes) -> list[list[str]]:
    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("content must be bytes")  # noqa: TRY003
    try:
        text = bytes(content).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("content must be valid UTF-8") from error
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        return list(reader)
    except csv.Error as error:
        raise ValueError(f"malformed csv document: {error}") from error


def _validate_header(header: tuple[str, ...]) -> None:
    if not header:
        raise ValueError("csv document must start with a non-empty header record")  # noqa: TRY003
    seen: set[str] = set()
    for column in header:
        if not column.strip():
            raise ValueError("header fields must not be blank")  # noqa: TRY003
        if column in seen:
            raise ValueError(f"duplicate header field {column!r}")  # noqa: TRY003
        seen.add(column)


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
