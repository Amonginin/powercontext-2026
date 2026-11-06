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

"""Merge concurrent embedding calls into shared provider batches and cache results."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage


@dataclass
class _Batch:
    """One provider request that several calls may be waiting for."""

    batch_id: int
    texts: list[str] = field(default_factory=list)
    futures: dict[str, asyncio.Future[EmbeddingVector]] = field(default_factory=dict)
    closed: bool = False
    usage: InferenceUsage | None = None


class CoalescingEmbeddingModel:
    """Share one provider request between calls that need the same text.

    Concurrent calls hand their texts to the same open batch while the coalescing window
    is still open, later calls reuse a request that is still running, and successful
    vectors are kept in a bounded LRU cache. Every call still receives its vectors in its
    own input order, including repeated positions.
    """

    def __init__(
        self,
        model: Any,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._batches: dict[int, _Batch] = {}
        self._inflight: dict[str, _Batch] = {}
        self._next_batch_id = 0
        self._lock = asyncio.Lock()

    @property
    def profile(self) -> Any:
        """Expose the wrapped model's deployment profile unchanged."""

        return self._model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input text, in the input order."""

        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        unique = list(dict.fromkeys(ordered))

        resolved: dict[str, EmbeddingVector] = {}
        assignments: dict[str, _Batch | None] = {}
        created: list[_Batch] = []
        async with self._lock:
            for text in unique:
                if self._cache_size:
                    cached = self._cache.get(text)
                    if cached is not None:
                        self._cache.move_to_end(text)
                        resolved[text] = cached
                        assignments[text] = None
                        continue
                running = self._inflight.get(text)
                if running is not None:
                    assignments[text] = running
                    continue
                batch = self._open_batch()
                if batch is None:
                    self._next_batch_id += 1
                    batch = _Batch(batch_id=self._next_batch_id)
                    self._batches[batch.batch_id] = batch
                    created.append(batch)
                future: asyncio.Future[EmbeddingVector] = asyncio.get_running_loop().create_future()
                batch.texts.append(text)
                batch.futures[text] = future
                self._inflight[text] = batch
                assignments[text] = batch
            for batch in created:
                asyncio.create_task(self._run_batch(batch))

        vectors: list[EmbeddingVector] = []
        touched: list[_Batch] = []
        seen_batches: set[int] = set()
        for text in ordered:
            batch = assignments[text]
            if batch is None:
                vectors.append(resolved[text])
                continue
            # Shield the shared future: one caller giving up must not cancel the work the
            # other callers are waiting for.
            vectors.append(await asyncio.shield(batch.futures[text]))
            if batch.batch_id not in seen_batches:
                seen_batches.add(batch.batch_id)
                touched.append(batch)
        return EmbeddingResult(
            vectors=tuple(vectors),
            usage=_summarize(touched),
        )

    def _open_batch(self) -> _Batch | None:
        """Return an open batch that still has room, if any."""

        for batch in self._batches.values():
            if not batch.closed and len(batch.texts) < self._max_batch_size:
                return batch
        return None

    async def _run_batch(self, batch: _Batch) -> None:
        """Run one provider request and hand its vectors to every waiter."""

        if self._coalesce_seconds:
            await asyncio.sleep(self._coalesce_seconds)
        async with self._lock:
            batch.closed = True
            texts = tuple(batch.texts)
            self._batches.pop(batch.batch_id, None)
        try:
            result = await self._model.embed(texts)
            vectors = tuple(result.vectors)
            if len(vectors) != len(texts):
                raise ValueError(
                    f"embedding model returned {len(vectors)} vectors for {len(texts)} texts"
                )
        except BaseException as error:
            async with self._lock:
                for text in texts:
                    self._inflight.pop(text, None)
            for text in texts:
                _fail(batch.futures[text], error)
            return
        async with self._lock:
            for text, vector in zip(texts, vectors, strict=True):
                self._inflight.pop(text, None)
                if self._cache_size:
                    self._cache[text] = vector
                    self._cache.move_to_end(text)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
        batch.usage = result.usage
        for text, vector in zip(texts, vectors, strict=True):
            future = batch.futures[text]
            if not future.done():
                future.set_result(vector)


def _fail(future: asyncio.Future[EmbeddingVector], error: BaseException) -> None:
    """Publish one failure to its waiters without cancelling the shared work."""

    if not future.done():
        future.set_exception(error)


def _summarize(batches: list[_Batch]) -> InferenceUsage:
    """Summarize the provider usage of the batches one call waited for."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    for batch in batches:
        usage = batch.usage
        if usage is None:
            continue
        if usage.input_tokens is not None:
            input_tokens = (input_tokens or 0) + usage.input_tokens
        if usage.output_tokens is not None:
            output_tokens = (output_tokens or 0) + usage.output_tokens
    return InferenceUsage(
        requests=len(batches),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


__all__ = ["CoalescingEmbeddingModel"]
