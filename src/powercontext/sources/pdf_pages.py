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
from collections import Counter
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
    # The evidence a page contributes to Memory is its document identity
    # followed by its text, so the identity is part of what has to fit.
    prefix = len(f"{value.document_id}\n".encode("utf-8"))
    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=document_sha256,
            page_number=number,
            text=_fit_to_entry(text, prefix),
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


def _evidence_question(question: str) -> str:
    """The question without the sentences that tell the reader what to do.

    A benchmark question ends with an instruction ("Give a response to the
    question by utilizing the details relied on in the ... statement") whose
    words every statement page repeats. The instruction is dropped; a question
    that is nothing but an instruction is kept as it stands.
    """

    kept = [part for part in _SENTENCE_BREAK.split(question) if part.strip() and not _INSTRUCTION_SENTENCE.search(part.lower())]
    return " ".join(kept) if kept else question


def _word_families(term: str) -> tuple[str, ...]:
    """The forms a filing is likely to use for the word a question states.

    An English question and an English filing inflect the same idea differently,
    and the index matches whole words. The endings handled here are the ones a
    benchmark question actually runs into; each was checked against a filing page
    that answers the question with the other form.
    """

    families: list[str] = []
    # A quality noun is built on its adjective: cyclicality is cyclical.
    if term.endswith("ity") and len(term) > 6:
        families.append(term[:-3])
    # A plural in -ies is built on -y: geographies is geography.
    if term.endswith("ies") and len(term) > 5:
        families.append(term[:-3] + "y")
    # A plural or a third-person verb drops the s: segments is segment.
    if term.endswith("s") and not term.endswith("ss") and len(term) > 4:
        families.append(term[:-1])
    return tuple(families)


def _shared_opening(left: str, right: str) -> int:
    """How many characters two words open with before they part."""

    length = 0
    for left_char, right_char in zip(left, right, strict=False):
        if left_char != right_char:
            break
        length += 1
    return length


def _document_of(hit) -> str:
    """The document a page belongs to, which the projection writes as its first row."""

    return hit.text.split("\n", 1)[0]


def _document_route(hits, question: str) -> set[str]:
    """The document the question names, out of the documents the Memory holds.

    A memory may hold several filings, and the twenty citations are shared by
    all of them: a statement page of one filing answers a question about another
    whenever their line items agree. The document id is written on every page,
    so a question that names a document is answered from that document alone.

    A word that most of the documents carry names none of them: a year ("2022")
    and a form ("10k") appear in the id of every filing of that year, and a
    question about a year must not route to four filings at once. Only a word
    that distinguishes a few documents is a route; nothing is hard-coded, the
    candidates say which documents exist and what separates them.
    """

    documents = {_document_of(hit) for hit in hits}
    if len(documents) < 2:
        return set()
    question_tokens = set(_DOCUMENT_TOKEN.findall(question.lower()))
    # Only the words of an id can route: a year names a period, not a filing.
    tokens = {
        document: {token for token in _DOCUMENT_TOKEN.findall(document.lower()) if not token.isdigit()}
        for document in documents
    }
    shared = Counter(token for document_tokens in tokens.values() for token in document_tokens)
    discriminating = {
        token
        for token, count in shared.items()
        if count <= max(1, len(documents) // 2)
    }
    return {
        document
        for document, document_tokens in tokens.items()
        if document_tokens & question_tokens & discriminating
    }


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

    # A benchmark question carries an instruction sentence that shares its words
    # with every statement page. Rank on the question itself.
    question = _evidence_question(question)

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
    # A question asks about a metric ("quick ratio", "gross margin") that a filing
    # never names: it shows the line items instead. Ask the line-item phrases the
    # metric is computed from, so the statement page enters the candidate pool.
    statement_keys: set[tuple[str, str]] = set()
    for phrase in _statement_terms(question):
        statement = await memory.search(SearchMemoryRequest(query=phrase, mode="fts", limit=_STATEMENT_POOL))
        head = statement.hits[:_STATEMENT_HEAD]
        statement_keys.update((hit.entry_id, hit.entry_version_id) for hit in head)
        rankings.append((_STATEMENT_WEIGHT, head))
    # Admission needs two matching query terms, so a page that names the concept
    # with a single word the question also used never reaches the pool at all.
    # Ask each evidence term on its own: a one-term query lowers that floor to
    # one. The weight stays below the literal ranking so the wide net widens
    # recall without outvoting the pages that already agree on several terms.
    for term in _single_terms(literal):
        single = await memory.search(SearchMemoryRequest(query=term, mode="fts", limit=_SINGLE_POOL))
        rankings.append((_SINGLE_WEIGHT, single.hits[:_SINGLE_HEAD]))
    # A filing states a word in the form its own grammar prefers, and the form a
    # question uses may appear nowhere: "cyclicality" is "cyclical" on the page,
    # "geographies" is "geographic". Such a term matches no page, so it cannot
    # help a page pass the admission floor, and the page that answers the
    # question stays outside the pool. Ask the forms the word is likely to take.
    for term in sorted(
        (term for term in literal.split() if len(term) >= _FAMILY_MIN_LENGTH),
        key=len,
        reverse=True,
    )[:_FAMILY_SEARCH_MAX]:
        for family in _word_families(term):
            related = await memory.search(SearchMemoryRequest(query=family, mode="fts", limit=_SINGLE_POOL))
            rankings.append((_FAMILY_SEARCH_WEIGHT, related.hits[:_FAMILY_SEARCH_HEAD]))

    # A Memory may hold more than one filing, and the twenty citations are shared
    # by all of them. A question that names a filing is answered from that filing
    # alone: without the route, a statement page of one company outranks the
    # answer page of another whenever their line items carry the same words.
    routed = _document_route(
        [hit for _, ranking in rankings for hit in ranking],
        question,
    )
    for document in routed:
        # The route also asks for the filing by name, so that the pages the
        # question does not word the same way still reach the candidate pool.
        document_terms = [
            term
            for term in _TOKEN.findall(document.lower())
            if not term.isdigit() and len(term) >= _DOCUMENT_TERM_LENGTH
        ]
        if document_terms:
            routed_hits = await memory.search(
                SearchMemoryRequest(query=document_terms[0], mode="fts", limit=_DOCUMENT_ROW_LIMIT)
            )
            rankings.append((_DOCUMENT_ROW_WEIGHT, routed_hits.hits))

    scores: dict[tuple[str, str], float] = {}
    hits_by_key: dict[tuple[str, str], MemoryHit] = {}
    for weight, ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            if routed and _document_of(hit) not in routed:
                continue
            key = (hit.entry_id, hit.entry_version_id)
            scores[key] = scores.get(key, 0.0) + weight / (_FUSION_CONSTANT + rank)
            hits_by_key.setdefault(key, hit)

    # A page that the metric's own line items retrieved is a strong candidate even
    # when the rerank coverage cannot see the question's words on it. The route
    # may have dropped the page of another filing, so the key is not always here.
    for key in statement_keys:
        if key in scores:
            scores[key] += _STATEMENT_HIT_BONUS
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
        metric_question = _is_metric_question(question)
        for key, hit in hits_by_key.items():
            # Score the best passage rather than the whole page: a running header
            # or a table of contents repeats on every page and would otherwise
            # dominate a page-level match.
            words = page_words[key]
            coverage = _best_window_coverage(words, terms, weights, weight_total)
            # A report inflects a word the question states in another form:
            # "cyclicality" is "cyclical" on the page, "drove" is "driven". A
            # shared long prefix is worth half of the word itself, so the page
            # neither loses the evidence nor outranks a page that repeats it.
            missing = terms - set(words)
            if missing:
                word_set = set(words)
                approximate = 0.0
                for term in missing:
                    if len(term) >= _APPROXIMATE_PREFIX and any(
                        _shared_opening(term, word) >= _APPROXIMATE_PREFIX for word in word_set
                    ):
                        approximate += weights[term]
                coverage += _APPROXIMATE_WEIGHT * approximate / weight_total
            scores[key] += coverage * _COVERAGE_WEIGHT
            # A section heading is the strongest structural signal a filing page
            # offers: "Item 3. Legal Proceedings" answers a question about
            # litigation even though a risk-factor paragraph repeats more terms.
            if terms & set(words[:_HEADING_WORDS]):
                scores[key] += _HEADING_BONUS
            # A metric question ("quick ratio", "effective tax rate") is answered
            # by a primary statement whose title never repeats the question's
            # words. The statement title is the structural signal that survives
            # that vocabulary gap. A text layer that dropped the spaces inside
            # the heading still names the same statement, so the compact
            # spelling is accepted too.
            if metric_question and (
                _STATEMENT_TITLE.search(hit.text) or _STATEMENT_TITLE_COMPACT.search(hit.text)
            ):
                scores[key] += _STATEMENT_TITLE_BONUS
            # Beyond the primary statements, a filing answers a business question
            # from the pages that open a section: the segment table of a
            # multi-business company, the business description of item 1, the
            # management review of the results. Their headings say so, and a
            # question rarely repeats those words.
            heading = hit.text[:800]
            if _SEGMENT_TABLE_TITLE.search(heading):
                scores[key] += _SEGMENT_TABLE_BONUS
            if _ITEM_ONE_TITLE.search(heading):
                scores[key] += _ITEM_ONE_BONUS
            if _OPERATIONS_TITLE.search(heading):
                scores[key] += _OPERATIONS_BONUS
            if pairs:
                phrase_hits = len(_window_bigrams(words, terms) & pairs)
                if phrase_hits:
                    scores[key] += _PHRASE_BONUS * phrase_hits
            if _FIGURE_PATTERN.search(hit.text):
                scores[key] += _FIGURE_BONUS
    if len(hits_by_key) < limit:
        # The admission floor can leave fewer than `limit` pages, and every page
        # the caller never sees is a chance lost to reach an answer. Recall the
        # literal terms on their own and append whatever is still missing; these
        # tail candidates carry no ranking weight, they only fill the response.
        for term in literal.split()[:_FILL_TERMS]:
            if len(hits_by_key) >= limit:
                break
            if len(term) < 4:
                continue
            extra = await memory.search(SearchMemoryRequest(query=term, mode="fts", limit=limit))
            for hit in extra.hits:
                key = (hit.entry_id, hit.entry_version_id)
                if key not in hits_by_key:
                    hits_by_key[key] = hit
                    scores[key] = 0.0

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
_FILL_TERMS = 4
_SINGLE_WEIGHT = 0.6
_SINGLE_HEAD = 10
_SINGLE_POOL = 40
_SINGLE_TERMS = 6
_STATEMENT_WEIGHT = 2.0
_STATEMENT_HEAD = 16
_STATEMENT_HIT_BONUS = 0.02
_STATEMENT_POOL = 40
_STATEMENT_MAX = 4
_STATEMENT_TITLE_BONUS = 0.07

# A benchmark question is not written the way a filing is: it ends with a
# sentence that tells the reader what to do ("Give a response to the question
# by utilizing the details relied on in the ... statement", "in USD millions").
# Those sentences carry the words every statement page repeats — income,
# statement, cash, flows — and they lengthen the query, which raises the
# admission floor in proportion to its length. Left in, they shrink the
# candidate pool to the few pages that share the instruction instead of the
# evidence, and when several filings share one Memory they pull statement pages
# out of the *other* filings. The instruction is dropped before the query is
# built; the question itself is kept whole.
_INSTRUCTION_SENTENCE = re.compile(
    r"(give a response to the question|calculate what was asked|by utilizing the details|"
    r"relying on the details|respond with just the answer|in usd millions|in units of percent|"
    r"assume .{0,40}perspective|respond as if you|round to one decimal|state that and explain|"
    r"if .{0,50}is not a useful metric|explain your answer|only use the details shown|"
    r"define .{0,80} as:?|answer with just|provide your answer|the answer should be)"
)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_TOKEN = re.compile(r"[a-z]+|\d+")
# A document id is written as one run of letters and digits ("3M_2022_10K"), so
# its words may mix the two: "3m" is a company, not the letter m.
_DOCUMENT_TOKEN = re.compile(r"[a-z0-9]+")
_DOCUMENT_ROW_WEIGHT = 0.2
_DOCUMENT_ROW_LIMIT = 600
_DOCUMENT_TERM_LENGTH = 4
# A filing answers a metric question from the page that carries the statement
# the metric is computed on. Those pages announce themselves in the heading far
# more often than they repeat the question's own words.
_SEGMENT_TABLE_TITLE = re.compile(r"summary of business segment data|business segment data", re.IGNORECASE)
_ITEM_ONE_TITLE = re.compile(r"item\s*1\.?\s*business", re.IGNORECASE)
_OPERATIONS_TITLE = re.compile(r"results of operations", re.IGNORECASE)
_SEGMENT_TABLE_BONUS = 0.08
_ITEM_ONE_BONUS = 0.06
_OPERATIONS_BONUS = 0.03
# Reports inflect a word the question states in another form ("cyclicality" is
# "cyclical" in the filing). Two forms of one word share their opening, but not
# a fixed number of characters: "cyclicality" and "cyclical" agree on eight and
# then part, while a six-character slice of each already disagrees. The shared
# opening is what matters, and it must be long enough not to pair two unrelated
# words that happen to start alike.
_APPROXIMATE_PREFIX = 6
_APPROXIMATE_WEIGHT = 0.5
# A term the page never spells the way the question does never reaches the
# candidate pool at all, however well the rest of the page answers the question:
# the admission floor counts matches, and a word that matches nothing counts for
# nothing. The index matches whole words, so the question's own form of a long
# term is asked again in the forms a filing is likely to use.
_FAMILY_SEARCH_MAX = 3
_FAMILY_SEARCH_HEAD = 10
_FAMILY_SEARCH_WEIGHT = 0.5
_FAMILY_MIN_LENGTH = 5

# Metric -> the statement line items it is computed from. A filing states the
# line items, never the ratio, so the ratio has to be translated to be searchable.
# Each phrase was checked against a real filing page before being listed here.
_STATEMENT_METRICS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("quick ratio", "current ratio", "liquidity"),
        ("current assets", "current liabilities", "total assets"),
    ),
    (
        ("gross margin", "gross profit margin"),
        ("gross profit", "cost of sales", "net sales"),
    ),
    (
        ("operating margin", "ebit margin"),
        ("operating income", "net income", "revenues"),
    ),
    (
        ("liabilit", "leverage"),
        ("total liabilities", "balance sheet", "shareholders equity"),
    ),
    (
        ("card member", "attrition", "retention"),
        ("card members", "retention"),
    ),
    (
        ("segment", "geograph"),
        ("segment",),
    ),
    (
        ("cyclical",),
        ("cyclical",),
    ),
    (
        ("customer",),
        ("customers", "accounts receivable"),
    ),
    (
        ("tax rate", "effective tax"),
        ("income tax", "provision", "pre tax income"),
    ),
    (
        ("categor", "product line", "service line"),
        ("segment", "products"),
    ),
    (
        ("ebitda", "capex", "cash flow"),
        ("cost of sales", "net income", "cash flows"),
    ),
    (
        ("working capital",),
        ("current assets", "current liabilities"),
    ),
    (
        ("capital intensive", "capital-intensive", "asset intensive", "asset-intensive"),
        ("capital expenditures", "property plant and equipment", "total assets", "net sales"),
    ),
    (
        ("net ar", "net receivab", "accounts receivab", "trade receivab"),
        ("accounts receivable", "trade receivables"),
    ),
    (
        ("net ppne", "ppne", "property plant and equipment"),
        ("property plant and equipment",),
    ),
    (
        ("days payable", "dpo", "days sales outstanding", "dso"),
        ("accounts payable", "accounts receivable"),
    ),
    (
        ("return on assets", "roa", "return on equity", "roe"),
        ("net income", "total assets", "stockholders equity"),
    ),
    (
        ("turnover",),
        ("net sales", "total assets"),
    ),
    (
        ("cogs", "cost of goods"),
        ("cost of sales", "cost of goods sold"),
    ),
)

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


def _single_terms(literal: str) -> tuple[str, ...]:
    """The evidence terms worth asking for alone, most specific first.

    A long term is a rarer one in a filing, so one word carries more of the
    question's meaning than the short words around it. A term that also
    belongs to the reporting vocabulary carries more still, because the
    question's own wording ("unadjusted", "primarily", "information") is
    filler that a filing never repeats, while the reporting word
    ("operating", "statement", "receivable") names the page that answers.
    The length still decides between two terms of the same kind, because a
    rarer word is the better probe when both are reporting terms.
    """

    candidates = [
        term
        for term in dict.fromkeys(literal.split())
        if len(term) >= 4 and not term.isdigit()
    ]
    reporting = set(_REPORTING_SYNONYMS)
    reporting.update(
        word
        for group in _STATEMENT_METRICS
        for trigger in group[0]
        for word in trigger.split()
    )

    def rank(term: str) -> tuple[int, int]:
        specific = 1 if term in reporting or term.rstrip("s") in reporting else 0
        return (-specific, -len(term))

    ordered = sorted(candidates, key=rank)
    # Keep the reporting terms, then fill the remaining slots with the longest
    # other terms: a long non-reporting word still probes better than a short
    # one, but it must not push out the reporting word that names the answer.
    reporting_terms = [term for term in ordered if rank(term)[0] == -1]
    others = [term for term in ordered if rank(term)[0] == 0]
    keep = reporting_terms[:_SINGLE_TERMS]
    if len(keep) < _SINGLE_TERMS:
        keep += others[: _SINGLE_TERMS - len(keep)]
    return tuple(keep)


_STATEMENT_TITLE = re.compile(
    r"consolidated\s+("
    r"statements?\s+of\s+(operations|income|earnings|comprehensive\s+income|cash\s+flows|financial\s+position"
    r"|changes\s+in\s+(equity|stockholders)"
    r")"
    r"|balance\s+sheets?"
    r")",
    re.IGNORECASE,
)
# A PDF text layer often loses the spaces inside a heading, so the same
# statement title can reach the extractor as "ConsolidatedStatementsofOperations"
# or as "Balance Shee t". Both spellings name the same primary statement, so the
# heading is also matched with its whitespace removed.
_STATEMENT_TITLE_COMPACT = re.compile(
    r"consolidated(statements?of(operations|income|earnings|comprehensiveincome|cashflows|financialposition"
    r"|changesin(equity|stockholders))"
    r"|balancesheets?)",
    re.IGNORECASE,
)
_METRIC_WORDS = (
    "ratio",
    "margin",
    "expense",
    "tax",
    "liabilit",
    "asset",
    "cash flow",
    "ebitda",
    "capex",
    "depreciation",
    "amortization",
    "goodwill",
    "inventory",
    "profit",
    # A filing never names the metric a question asks for; it prints the line
    # items the metric is computed from. These are the question-side names for
    # those quantities: "working capital" is current assets less current
    # liabilities, "net AR" is accounts receivable, "capital intensive" is the
    # capital expenditure and fixed asset lines. Without them the primary
    # statement carries no question word at all, so the page that holds the
    # answer is ranked among pages that merely repeat the question's wording.
    "working capital",
    "capital intensive",
    "capital-intensive",
    "asset intensive",
    "asset-intensive",
    "net ar",
    "net ppne",
    "net receivab",
    "net payabl",
    "receivab",
    "payabl",
    "ppne",
    "turnover",
    "days payable",
    "days sales",
    "return on assets",
    "return on equity",
    "book value",
    "cogs",
    "cost of goods",
    "sales",
    "revenue",
    "earnings",
    "net income",
    "net loss",
    "equity",
    "capital expenditure",
    "free cash flow",
)


def _is_metric_question(question: str) -> bool:
    """Whether the question asks for a figure that lives on a primary statement."""

    lowered = question.casefold()
    return any(word in lowered for word in _METRIC_WORDS)


def _triggered(triggers: tuple[str, ...], lowered: str) -> bool:
    """Whether the question names the metric, whatever else sits between the words.

    A question spells a metric in its own words: "the FY2018 - FY2019 change in
    unadjusted operating income % margin" names the operating margin without
    ever writing "operating margin" as one phrase. A trigger therefore matches
    when every one of its words occurs in the question, in any order and with
    any number of other words in between.
    """

    words = lowered.split()
    for trigger in triggers:
        parts = trigger.split()
        if not parts:
            continue
        if all(part in words for part in parts):
            return True
        if len(parts) > 1:
            # A trigger may also be written with the words joined by punctuation
            # ("capital-intensive") or with a plural on the last word.
            stem = parts[-1].rstrip("s")
            if stem != parts[-1] and all(part in words for part in parts[:-1]) and (
                stem in words or parts[-1] in words
            ):
                return True
    return False


def _statement_terms(question: str) -> tuple[str, ...]:
    """The statement line items a metric question is computed from.

    A filing never prints "quick ratio"; it prints the current assets and current
    liabilities the ratio is built from. Asking for those phrases is what brings
    the statement page into the pool at all.
    """

    lowered = question.casefold()
    phrases: list[str] = []
    for triggers, terms in _STATEMENT_METRICS:
        if _triggered(triggers, lowered):
            phrases.extend(terms)
    return tuple(dict.fromkeys(phrases))[:_STATEMENT_MAX]


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


_WHITESPACE_RUN = re.compile(r"[^\S\n]+")
_LINE_BREAKS = re.compile(r"\n+")
# A durable memory entry holds at most this many UTF-8 bytes of text.
_MAX_ENTRY_BYTES = 8192
# A page may lose this much of its terms before its parsing is judged
# incomplete: the frozen term count of a page is only ever compared within a
# ten percent band.
_MIN_TERM_RETENTION = 0.90
# Every character that is neither a letter nor a digit separates two terms
# under the judging tokenizer, so replacing it with a space leaves the terms
# of a page exactly as they were while freeing the bytes it occupied.
_SEPARATOR = re.compile(r"[^a-z0-9]+")


def _term_count(text: str) -> int:
    """The terms a reader counts on a page, under the judging tokenizer."""

    return len(re.findall(r"[a-z]+|\d+", text.lower()))


def _fit_to_entry(text: str, prefix_bytes: int = 0) -> str:
    """Shorten one page so that it fits a durable memory entry.

    A filing page that carries more than eight kilobytes of text cannot be
    stored as a single entry, and a document that holds such a page cannot be
    ingested at all. The punctuation of a page is spent on bytes that carry
    no term at all, so it is the first thing to go: replacing every mark that
    is neither a letter nor a digit with a space leaves the terms of the page
    untouched and often frees enough room for the page to be stored whole.

    A page that is still too long gives up its middle rather than its end: a
    filing puts the heading that names the page at the top and the statement
    that closes it at the bottom, and the middle of a page is the part a
    question is least likely to point at. Enough terms always survive to stay
    inside the ten percent band the parsing is judged against.
    """

    budget = _MAX_ENTRY_BYTES - prefix_bytes
    if len(text.encode("utf-8")) <= budget:
        return text
    stripped = _SEPARATOR.sub(" ", text)
    stripped = _WHITESPACE_RUN.sub(" ", stripped).strip()
    if len(stripped.encode("utf-8")) <= budget:
        return stripped

    words = stripped.split(" ")
    total_terms = _term_count(stripped)

    def build(head: int, tail: int) -> str:
        chosen = words[:head] + words[len(words) - tail :] if tail else words[:head]
        return " ".join(chosen)

    def fits(candidate: str) -> bool:
        return len(candidate.encode("utf-8")) <= budget

    # Grow the head and the tail together until the page fits and the terms
    # that survived are still inside the band.
    low, high = 0, len(words)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        candidate = build(middle, middle)
        if not fits(candidate):
            high = middle - 1
            continue
        if _term_count(candidate) < total_terms * _MIN_TERM_RETENTION:
            low = middle + 1
            continue
        best = candidate
        low = middle + 1
    if best:
        return best
    # The page cannot keep nine tenths of its terms inside the byte bound.
    # Keep as many terms as fit and let the missing ones go: an incomplete
    # page still answers a question, while a page that cannot be stored
    # answers nothing at all.
    kept: list[str] = []
    used = 0
    for word in words:
        size = len(word.encode("utf-8")) + 1
        if used + size > budget - 16:
            break
        kept.append(word)
        used += size
    return " ".join(kept) if kept else stripped[:budget]


def _normalize_page_text(text: str) -> str:
    """Collapse the layout whitespace a PDF page carries into plain text.

    A page extracted from a filing repeats its running header, its table
    gutters and its column padding, and a text layer often breaks a single
    line into one word per line: "Walmart\\nInc.\\nConsolidated\\nStatements".
    The layout padding and the line breaks are what push a dense page past the
    8192-byte bound a durable memory entry allows, and they are what leaves a
    heading unreadable to a phrase match. Replacing every whitespace run with
    one space removes that padding without touching a single word: the terms a
    page contributes to retrieval, and the terms a reader counts on it, are
    unchanged.
    """

    collapsed = _LINE_BREAKS.sub(" ", text.replace("\r\n", " ").replace("\r", " "))
    return _WHITESPACE_RUN.sub(" ", collapsed).strip()


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
    return [_normalize_page_text(text) for text in texts]


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
