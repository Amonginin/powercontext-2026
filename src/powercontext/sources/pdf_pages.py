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
import math
import re
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.artifacts.memory.models import MemoryHit

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
    to distinct physical pages. The literal query is reduced to its evidence terms first,
    because question function words would otherwise dilute the ranking of every page.
    One expanded query adds morphological neighbours of those terms — reports inflect
    words differently than questions do — and both rankings are fused so either spelling
    of an answer page can surface.
    """

    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.artifacts.search import analyze_fts_query
    from powercontext.builtin.runtime import SearchMemoryRequest

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")  # noqa: TRY003
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003

    pool = min(max(limit * 15, limit), _MAX_CANDIDATE_POOL)
    literal = analyze_fts_query(question) or question
    primary = await memory.search(SearchMemoryRequest(query=literal, mode="fts", limit=pool))
    rankings: list[tuple[float, tuple[MemoryHit, ...]]] = [(1.0, primary.hits)]
    expanded = _expanded_query(question)
    if expanded is not None:
        secondary = await memory.search(SearchMemoryRequest(query=expanded, mode="fts", limit=pool))
        rankings.append((0.5, secondary.hits))
    # Long questions carry weak words ("has", "any", the company name) that every
    # page matches, and the question's own noun may not be the filing's word at
    # all ("legal battles" is "litigation" in a report). Ask again with the few
    # long reporting terms alone: they are rare enough that the section page
    # which names the concept ranks at the top instead of below a hundred pages
    # that merely repeat the common words.
    sharp = _sharp_terms(expanded, literal)
    if sharp:
        focused = await memory.search(SearchMemoryRequest(query=sharp, mode="fts", limit=pool))
        # Only the head of this ranking carries the precise concept; its tail is
        # every page that happens to repeat one long word.
        rankings.append((3.0, focused.hits[:_FOCUSED_HEAD]))

    scores: dict[tuple[str, str], float] = {}
    hits_by_key: dict[tuple[str, str], MemoryHit] = {}
    for weight, ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            key = (hit.entry_id, hit.entry_version_id)
            scores[key] = scores.get(key, 0.0) + weight / (_FUSION_CONSTANT + rank)
            hits_by_key.setdefault(key, hit)

    # A report page that answers a question usually carries several of the asked
    # terms and the figures they describe. Reward that shape on top of the coarse
    # ranking so a dense statement page can outrank a short page that merely
    # repeats one word. Terms that appear on nearly every page — a running header,
    # the company name — carry almost no weight in that reward.
    from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text

    literal_terms = (analyze_fts_query(question) or "").split()
    scoring_terms = _expanded_query(question) or " ".join(literal_terms)
    terms = {term for term in scoring_terms.split()}
    literal_set = set(literal_terms)
    page_words: dict[tuple[str, str], list[str]] = {}
    if terms:
        for key, hit in hits_by_key.items():
            page_words[key] = analyze_text(hit.text).split()
        document_frequency = {term: 0 for term in terms}
        for words in page_words.values():
            present = terms & set(words)
            for term in present:
                document_frequency[term] += 1
        total = len(page_words)
        # An expansion term is evidence only when the literal terms already agree:
        # otherwise a rare synonym could outrank the words the question used.
        weights = {
            term: (
                math.log((total + 1) / (document_frequency[term] + 1)) + 1.0
                if term in literal_set
                else (math.log((total + 1) / (document_frequency[term] + 1)) + 1.0) * _EXPANSION_WEIGHT
            )
            for term in terms
        }
        weight_total = sum(weights.values())
        pairs = set(zip(literal_terms, literal_terms[1:], strict=False))
        for key, hit in hits_by_key.items():
            # Score the best passage rather than the whole page: a running header
            # or a table of contents repeats on every page and would otherwise
            # dominate a page-level match.
            words = page_words[key]
            coverage = _best_window_coverage(words, terms, weights, weight_total)
            scores[key] += coverage * _COVERAGE_WEIGHT
            # A section heading is the strongest structural signal a filing page
            # offers: "Item 3. Legal Proceedings" answers a question about
            # litigation even though a risk-factor paragraph repeats more terms.
            if terms & set(words[:_HEADING_WORDS]):
                scores[key] += _HEADING_BONUS
            if pairs:
                phrase_hits = len(_window_bigrams(words, terms) & pairs)
                if phrase_hits:
                    scores[key] += _PHRASE_BONUS * phrase_hits
            if _FIGURE_PATTERN.search(hit.text):
                scores[key] += _FIGURE_BONUS
    ranked_keys = sorted(scores, key=lambda key: (-scores[key], key))

    citations: list[MemoryCitation] = []
    for key in ranked_keys[:limit]:
        hit = hits_by_key[key]
        citations.append(
            MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
        )
    return tuple(citations)


_FUSION_CONSTANT = 60
_MAX_CANDIDATE_POOL = 300
_COVERAGE_WEIGHT = 0.05
_FIGURE_BONUS = 0.01
_PHRASE_BONUS = 0.015
_EXPANSION_WEIGHT = 0.4
_FIGURE_PATTERN = re.compile(r"\d")
_WINDOW_WORDS = 90
_WINDOW_STRIDE = 45
_HEADING_WORDS = 32
_HEADING_BONUS = 0.02
_SHARP_TERM_COUNT = 2
_FOCUSED_HEAD = 5

# How a question names a thing versus how a filing labels it. These are generic
# reporting terms, not answers: they only widen the lexical evidence terms so a
# page headed "Our Divisions" can answer a question about geographies.
_REPORTING_SYNONYMS: dict[str, tuple[str, ...]] = {
    "geograph": ("geographic", "geography", "regions", "divisions", "segment", "countries"),
    "region": ("geographic", "division", "segment", "countries", "markets"),
    "legal": ("litigation", "proceedings", "lawsuit", "claims", "contingencies", "matters"),
    "battle": ("litigation", "proceedings", "lawsuit", "claims", "disputes"),
    "lawsuit": ("litigation", "proceedings", "claims", "contingencies"),
    "ebitda": ("operating", "income", "depreciation", "amortization", "cash", "flow"),
    "capex": ("capital", "expenditure", "purchases", "property", "plant", "equipment"),
    "restructuring": ("severance", "charges", "productivity", "cost"),
    "employee": ("employees", "workforce", "personnel", "headcount"),
    "shareholder": ("stockholders", "shares", "dividends", "equity"),
    "debt": ("borrowings", "notes", "long-term", "interest", "obligations"),
    "tax": ("income", "taxes", "effective", "rate", "provision"),
    "revenue": ("net", "sales", "growth"),
    "profit": ("operating", "income", "margin", "earnings"),
    "margin": ("operating", "gross", "percent"),
    "cash": ("flow", "operating", "activities", "equivalents"),
    "dividend": ("dividends", "per", "share", "payments"),
    "acquisition": ("acquisitions", "divestitures", "business", "purchase"),
    "risk": ("risks", "factors", "market", "exposure"),
    "competition": ("competitors", "competitive", "market"),
    "regulation": ("regulatory", "laws", "compliance", "government"),
    "supply": ("chain", "suppliers", "ingredients", "materials"),
    "brand": ("brands", "trademarks", "portfolio"),
    "pension": ("retirement", "benefits", "plans", "obligations"),
    "lease": ("leases", "rent", "obligations", "operating"),
    "goodwill": ("impairment", "intangible", "reporting", "unit"),
}


def _sharp_terms(expanded: str | None, literal: str) -> str | None:
    """The long reporting terms that name the concept precisely.

    A long word in this domain ("litigation", "proceedings", "restructuring",
    "depreciation") is specific enough to rank its own section page, while the
    question's phrasing may never appear in the filing at all.
    """

    if expanded is None:
        return None
    literal_terms = set(literal.split())
    sharp = [
        term
        for term in dict.fromkeys(expanded.split())
        if len(term) >= 9 and term not in literal_terms and not term.isdigit()
    ]
    return " ".join(sharp[:_SHARP_TERM_COUNT]) or None


def _window_bigrams(words: list[str], terms: set[str]) -> set[tuple[str, str]]:
    """Adjacent evidence-term pairs that appear next to each other on the page."""

    return {
        (first, second)
        for first, second in zip(words, words[1:])
        if first in terms and second in terms
    }


def _best_window_coverage(
    words: list[str],
    terms: set[str],
    weights: dict[str, float],
    weight_total: float,
) -> float:
    """The strongest IDF-weighted term coverage of any bounded passage on the page."""

    if not words or weight_total <= 0:
        return 0.0
    window = _WINDOW_WORDS if len(words) > _WINDOW_WORDS else len(words)
    stride = max(_WINDOW_STRIDE, 1)
    best = 0.0
    for start in range(0, len(words) - window + 1, stride):
        present = terms & set(words[start : start + window])
        if present:
            score = sum(weights[term] for term in present) / weight_total
            if score > best:
                best = score
    return best


def _expanded_query(question: str) -> str | None:
    """One extra query that adds spelling and reporting-term neighbours."""

    from powercontext.builtin.artifacts.search import analyze_fts_query

    base_terms = analyze_fts_query(question).split()
    expanded: list[str] = []
    seen = set(base_terms)
    for term in base_terms:
        for neighbour in _morphological_neighbours(term):
            if neighbour not in seen:
                seen.add(neighbour)
                expanded.append(neighbour)
        for synonym in _reporting_synonyms(term):
            if synonym not in seen:
                seen.add(synonym)
                expanded.append(synonym)
    if not expanded:
        return None
    return " ".join([*base_terms, *expanded])


def _reporting_synonyms(term: str) -> tuple[str, ...]:
    """Reporting terms a filing uses for the concept the question names."""

    direct = _REPORTING_SYNONYMS.get(term)
    if direct is not None:
        return direct
    for stem, synonyms in _REPORTING_SYNONYMS.items():
        if len(term) >= len(stem) and term.startswith(stem):
            return synonyms
    return ()


def _morphological_neighbours(term: str) -> tuple[str, ...]:
    """Spelling variants a report may use where the question uses one form."""

    if len(term) < 4 or term.isdigit():
        return ()
    neighbours: list[str] = []
    if term.endswith("ies") and len(term) > 4:
        neighbours.append(f"{term[:-3]}y")
    if term.endswith("es") and len(term) > 4:
        neighbours.append(term[:-2])
    if term.endswith("s") and not term.endswith("ss"):
        neighbours.append(term[:-1])
    if term.endswith("ing") and len(term) > 5:
        neighbours.append(term[:-3])
    if term.endswith("ed") and len(term) > 4:
        neighbours.append(term[:-2])
    return tuple(dict.fromkeys(neighbours))


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
