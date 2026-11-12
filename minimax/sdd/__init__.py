"""The SDD context module.

Given a development task, this module finds the applicable specification
clauses, the code that has to change, and the tests that verify it. Every
piece it reports carries the identifier of the record it came from, so a
reviewer can read the source back and check that it says what the context
claims it says.

The module talks to two stores:

* **PowerContext** holds the task state and the memory entries, so a run can
  be resumed after the module restarts.
* **seekdb / OceanBase** holds the engineering material itself — the
  specification files, the source files and the test files.

Both are required by the topic, and neither is optional.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
