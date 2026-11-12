"""Running the tests and deciding whether the change is real.

The topic is strict about this: the same target test set has to fail before
the change because the behaviour is missing, and pass afterwards, and the
regression suite has to keep passing. An environment problem must not be
mistaken for a missing feature.

pytest reports the difference in its exit code and in the shape of the
failure:

* ``0`` — everything passed;
* ``1`` — tests were collected and at least one assertion failed;
* ``2`` — the run was interrupted;
* ``3`` — an internal error;
* ``4`` — a usage error;
* ``5`` — nothing was collected.

Only exit code 1 with assertion failures counts as "the feature is missing".
Anything else is an environment problem and is reported as such, so it can
never be used as the before-state evidence.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["PytestRunResult", "run_pytest", "classify_failure"]

_ASSERTION_MARKERS = (
    "AssertionError",
    "assert ",
    "DID NOT RAISE",
    "Failed: DID NOT RAISE",
)

_ENVIRONMENT_MARKERS = (
    "ModuleNotFoundError",
    "ImportError",
    "SyntaxError",
    "IndentationError",
    "FileNotFoundError",
    "PermissionError",
    "No module named",
    "command not found",
    "is not recognized as an internal",
)


@dataclass
class PytestRunResult:
    """The outcome of one pytest run."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    collected: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    category: str = "unknown"
    detail: str = ""
    revision: str = ""

    @property
    def is_missing_feature(self) -> bool:
        """True when the run failed because a behaviour is not implemented."""
        return self.category == "missing_feature"

    @property
    def is_environment_error(self) -> bool:
        """True when the run failed for a reason unrelated to the feature."""
        return self.category == "environment_error"

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "collected": self.collected,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "category": self.category,
            "detail": self.detail,
            "revision": self.revision,
        }


def _count(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def classify_failure(result: PytestRunResult) -> PytestRunResult:
    """Work out whether the failure is a missing feature or a broken environment."""
    combined = result.stdout + "\n" + result.stderr

    if result.exit_code == 0:
        result.category = "passed"
        result.detail = "全部通过"
        return result

    if result.collected == 0 and result.exit_code == 5:
        result.category = "environment_error"
        result.detail = "没有收集到任何测试 —— 环境问题，不是功能缺失"
        return result

    if any(marker in combined for marker in _ENVIRONMENT_MARKERS):
        result.category = "environment_error"
        result.detail = "输出里出现导入/语法/路径错误 —— 环境问题，不能当作改前的功能缺失"
        return result

    if result.exit_code == 1 and result.failed > 0:
        if any(marker in combined for marker in _ASSERTION_MARKERS):
            result.category = "missing_feature"
            result.detail = f"{result.failed} 个断言失败，目标行为尚未实现"
            return result
        result.category = "environment_error"
        result.detail = "退出码为 1，但失败形态不是断言失败"
        return result

    result.category = "environment_error"
    result.detail = (
        f"退出码 {result.exit_code} 不代表测试断言失败"
        "（2=中断 3=内部错误 4=用法错误 5=未收集到测试）"
    )
    return result


def run_pytest(
    project_root: Path,
    targets: list[str] | None = None,
    revision: str = "",
    python: str | None = None,
) -> PytestRunResult:
    """Run pytest and classify the outcome."""
    interpreter = python or sys.executable
    args = [interpreter, "-m", "pytest"]
    args += targets or ["tests"]
    args += ["-v", "--no-header", "-p", "no:cacheprovider"]
    command = " ".join(args)

    completed = subprocess.run(
        args,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = stdout + "\n" + stderr

    result = PytestRunResult(
        command=command,
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        collected=_count(r"(\d+) tests? collected", combined)
        or _count(r"collected (\d+) items?", combined),
        passed=_count(r"(\d+) passed", combined),
        failed=_count(r"(\d+) failed", combined),
        errors=_count(r"(\d+) error", combined),
        revision=revision,
    )
    return classify_failure(result)


def render_log(result: PytestRunResult, heading: str) -> str:
    """Render a test run into the log file that is submitted as evidence."""
    lines = [
        f"# {heading}",
        "",
        f"command: {result.command}",
        f"exit_code: {result.exit_code}",
        f"revision: {result.revision or '(uncommitted working tree)'}",
        f"collected: {result.collected}",
        f"passed: {result.passed}",
        f"failed: {result.failed}",
        f"errors: {result.errors}",
        f"category: {result.category}",
        f"detail: {result.detail}",
        "",
        "--- stdout ---",
        result.stdout.rstrip(),
    ]
    if result.stderr.strip():
        lines += ["", "--- stderr ---", result.stderr.rstrip()]
    return "\n".join(lines) + "\n"
