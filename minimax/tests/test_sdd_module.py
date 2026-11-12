"""Unit tests for the SDD context module.

These cover the parts that do not need a running database or a MiniMax key:
the keyword extraction, the specification clause parser, the patch extraction
and the test-result classification. The classification is the important one,
because it is what keeps an environment error from being presented as a
missing feature.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sdd.agent import extract_diff  # noqa: E402
from sdd.ingest import material_id_for, parse_spec_clauses  # noqa: E402
from sdd.retrieve import extract_keywords  # noqa: E402
from sdd.verify import PytestRunResult, classify_failure  # noqa: E402


class TestExtractKeywords:
    def test_english_identifiers_are_kept(self):
        terms = extract_keywords("add min_value filter")
        assert "min_value" in terms

    def test_identifier_parts_are_also_kept(self):
        terms = extract_keywords("add min_value filter")
        assert "min" in terms
        assert "value" in terms

    def test_stopwords_are_dropped(self):
        terms = extract_keywords("the quick brown fox")
        assert "the" not in terms

    def test_chinese_runs_are_kept(self):
        terms = extract_keywords("增加一个筛选条件")
        assert any("筛选" in term for term in terms)

    def test_empty_task_yields_no_terms(self):
        assert extract_keywords("") == []


class TestParseSpecClauses:
    def test_headings_become_clauses(self):
        text = "# SPEC-001 Title\n\n## 1. Scope\n\nSome scope text.\n\n## 2.1 Detail\n\nDetail body.\n"
        clauses = parse_spec_clauses("SPEC-001", text)
        assert len(clauses) == 2

    def test_clause_sections_are_captured(self):
        text = "# SPEC-001 Title\n\n## 2.1 min_value filter\n\nMust keep >= N.\n"
        clauses = parse_spec_clauses("SPEC-001", text)
        assert clauses[0].section == "2.1 min_value filter"

    def test_clause_body_is_the_text_under_the_heading(self):
        text = "# SPEC-001\n\n## 2.1 filter\n\n- keep >= N\n- reject negative\n"
        clauses = parse_spec_clauses("SPEC-001", text)
        assert "keep >= N" in clauses[0].body

    def test_clause_ids_are_stable_for_the_same_text(self):
        text = "# SPEC-001\n\n## 2.1 filter\n\nbody\n"
        first = parse_spec_clauses("SPEC-001", text)
        second = parse_spec_clauses("SPEC-001", text)
        assert first[0].clause_id == second[0].clause_id

    def test_clause_ids_change_when_the_body_changes(self):
        before = parse_spec_clauses("SPEC-001", "# SPEC-001\n\n## 2.1 filter\n\nkeep >= N\n")
        after = parse_spec_clauses("SPEC-001", "# SPEC-001\n\n## 2.1 filter\n\nkeep > N\n")
        assert before[0].clause_id != after[0].clause_id

    def test_empty_document_yields_no_clauses(self):
        assert parse_spec_clauses("SPEC-001", "") == []


class TestMaterialId:
    def test_same_content_gives_the_same_id(self):
        a = material_id_for("spec", "spec/SPEC-001.md", "body")
        b = material_id_for("spec", "spec/SPEC-001.md", "body")
        assert a == b

    def test_changed_content_gives_a_different_id(self):
        a = material_id_for("spec", "spec/SPEC-001.md", "body")
        b = material_id_for("spec", "spec/SPEC-001.md", "body changed")
        assert a != b

    def test_the_role_and_the_path_are_part_of_the_id(self):
        value = material_id_for("spec", "spec/SPEC-001.md", "body")
        assert value.startswith("spec:spec/SPEC-001.md:")


class TestExtractDiff:
    def test_a_fenced_diff_block_is_extracted(self):
        answer = "Sure, here you go:\n```diff\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```\n"
        diff = extract_diff(answer)
        assert diff.startswith("--- a/x.py")
        assert "+b" in diff

    def test_a_plain_fenced_block_is_extracted(self):
        answer = "```\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n```"
        diff = extract_diff(answer)
        assert "+++ b/x.py" in diff

    def test_a_bare_diff_is_kept(self):
        answer = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
        assert extract_diff(answer).startswith("--- a/x.py")

    def test_an_answer_without_a_diff_yields_nothing(self):
        assert extract_diff("I do not think any change is needed.") == ""

    def test_an_empty_answer_yields_nothing(self):
        assert extract_diff("") == ""


class TestClassifyFailure:
    def _result(self, exit_code, stdout="", stderr="", collected=0, failed=0):
        result = PytestRunResult(
            command="pytest",
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            collected=collected,
            failed=failed,
        )
        return classify_failure(result)

    def test_a_clean_run_is_a_pass(self):
        result = self._result(0, "5 passed", collected=5)
        assert result.category == "passed"

    def test_an_assertion_failure_is_a_missing_feature(self):
        result = self._result(
            1, "collected 5 items\nE AssertionError\n1 failed, 4 passed", collected=5, failed=1
        )
        assert result.is_missing_feature

    def test_did_not_raise_counts_as_a_missing_feature(self):
        result = self._result(
            1, "collected 5 items\nE Failed: DID NOT RAISE ValueError\n1 failed", collected=5, failed=1
        )
        assert result.is_missing_feature

    def test_an_import_error_is_an_environment_error(self):
        result = self._result(
            1, "collected 5 items\nE ModuleNotFoundError: no module named 'x'", collected=5
        )
        assert result.is_environment_error
        assert not result.is_missing_feature

    def test_a_collection_error_is_an_environment_error(self):
        result = self._result(2, "ERROR: interrupted", collected=0)
        assert result.is_environment_error

    def test_nothing_collected_is_an_environment_error(self):
        result = self._result(5, "no tests ran", collected=0)
        assert result.is_environment_error

    def test_a_usage_error_is_an_environment_error(self):
        result = self._result(4, "ERROR: file or directory not found", collected=0)
        assert result.is_environment_error

    def test_exit_code_one_without_an_assertion_is_an_environment_error(self):
        result = self._result(1, "collected 5 items\nE some other failure", collected=5)
        assert result.is_environment_error
