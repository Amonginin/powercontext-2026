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

"""Persist the scoped Source lifecycle graph beside the other runtime tables."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table, Text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import (
    MAX_MEMORY_ENTRY_ID_LENGTH,
    MAX_SCOPE_ID_LENGTH,
    identity_string,
)

SOURCE_LIFECYCLE_NODES_TABLE = Table(
    "pc_source_lifecycle_nodes",
    MetaData(),
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("node_id", identity_string(MAX_MEMORY_ENTRY_ID_LENGTH), primary_key=True),
    Column("payload", Text, nullable=False),
)


async def ensure_source_lifecycle_schema(connection: AsyncConnection, /) -> None:
    """Create the lifecycle table on a store that predates it."""

    await connection.run_sync(
        lambda sync: SOURCE_LIFECYCLE_NODES_TABLE.create(sync, checkfirst=True)
    )


__all__ = ["SOURCE_LIFECYCLE_NODES_TABLE", "ensure_source_lifecycle_schema"]
