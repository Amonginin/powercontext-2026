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

"""Batched HTTP cross-encoder reranking for coarse Memory candidates."""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

DEFAULT_HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Score every coarse candidate through one HTTP service and order globally."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_HTTP_RERANK_POLICY_ID,
    ) -> None:
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.endpoint = endpoint
        self.policy_id = policy_id
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Send every candidate once, then select by the global score order."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            ranks = tuple(range(start + 1, min(start + self._batch_size, len(candidates)) + 1))
            payload = {
                "query": query,
                "candidates": [{"rank": rank, "text": candidates[rank - 1].text} for rank in ranks],
            }
            response = await self._client.post(self.endpoint, json=payload)
            response.raise_for_status()
            requests += 1
            scores.update(_parse_scores(response, ranks))
        selected = sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]
        return MemoryRerankDecision(
            selected_ranks=tuple(selected),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(response: httpx.Response, ranks: tuple[int, ...]) -> dict[int, float]:
    try:
        payload: Any = response.json()
    except ValueError as error:
        raise ValueError("rerank response must be valid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("rerank response must contain exactly the scores field")  # noqa: TRY003
    entries = payload["scores"]
    if not isinstance(entries, list):
        raise ValueError("rerank scores must be a list")  # noqa: TRY003
    scores: dict[int, float] = {}
    expected = set(ranks)
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"rank", "score"}:
            raise ValueError("every score must contain exactly rank and score")  # noqa: TRY003
        rank = entry["rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or rank not in expected:
            raise ValueError(f"score rank {rank!r} is not part of this batch")  # noqa: TRY003
        if rank in scores:
            raise ValueError(f"score rank {rank!r} is duplicated")  # noqa: TRY003
        score = entry["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("score must be a number")  # noqa: TRY003
        value = float(score)
        if not math.isfinite(value):
            raise ValueError("score must be finite")  # noqa: TRY003
        scores[rank] = value
    if set(scores) != expected:
        raise ValueError("rerank scores must cover every rank of the batch exactly once")  # noqa: TRY003
    return scores


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


__all__ = ["DEFAULT_HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
