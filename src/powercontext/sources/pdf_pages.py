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

"""Whole-document PDF Sources addressed by physical page."""

from __future__ import annotations

import hashlib
import io
import re
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.artifacts import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_PREFIX = "pdf"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class PDFDocumentInput(BaseModel):
    """One complete PDF document to split into page-level Sources."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One parsed physical page of one PDF document."""

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str


class PDFPageSource(Source):
    """Captured PDF page addressed by document identity and physical page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable identity of one page within one document revision."""

    _validate_document_id(document_id)
    _validate_document_sha256(document_sha256)
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    identity = (
        f"{PDF_PAGE_SOURCE_PREFIX}:{quote(document_id, safe='')}:"
        f"{document_sha256}:{page_number}"
    )
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(
            f"pdf page identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )  # noqa: TRY003
    return identity


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse one complete PDF into page inputs in physical order, or fail atomically."""

    _validate_document_id(value.document_id)
    pages = _read_pages(value.content)
    document_sha256 = hashlib.sha256(value.content).hexdigest()
    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=document_sha256,
            page_number=number,
            text=text,
        )
        for number, text in enumerate(pages, start=1)
    )


class _PDFPageAdapter:
    """Resolve and read the page-level PDF Source Definition."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one PDF page as canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    _PDFPageAdapter(),
    version="1",
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search one scoped Memory for the pages that answer one natural-language question.

    The scoped Memory already holds one entry per page, so the exact citations follow
    the coarse lexical ranking: hits arrive best-first and distinct entries correspond
    to distinct physical pages.
    """

    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.runtime import SearchMemoryRequest

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")  # noqa: TRY003
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003

    page = await memory.search(
        SearchMemoryRequest(query=question, mode="fts", limit=max(limit * 3, limit))
    )
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, str, int, str]] = set()
    for hit in page.hits:
        key = (
            hit.memory_ref.family,
            hit.memory_ref.artifact_id,
            hit.memory_ref.revision,
            hit.entry_id,
        )
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
        )
        if len(citations) == limit:
            break
    return tuple(citations)


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must not be empty or blank")  # noqa: TRY003


def _validate_document_sha256(document_sha256: str) -> None:
    if not isinstance(document_sha256, str) or not _SHA256_PATTERN.fullmatch(document_sha256):
        raise ValueError("document_sha256 must be a lowercase hex sha256 digest")  # noqa: TRY003


def _read_pages(content: bytes) -> list[str]:
    """Extract the text of every physical page, or raise ValueError without partial output."""

    if not isinstance(content, (bytes, bytearray)):
        raise ValueError("content must be bytes")  # noqa: TRY003
    from pypdf import PasswordType, PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(bytes(content)))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise ValueError("pdf requires a non-empty password")  # noqa: TRY003
        texts = [(page.extract_text() or "") for page in reader.pages]
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - one contract for every parse failure
        raise ValueError(f"pdf document cannot be parsed: {error}") from error
    return texts


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
