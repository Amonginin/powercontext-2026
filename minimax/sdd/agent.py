"""The MiniMax development assistant.

The assistant is asked for a unified diff rather than for a rewritten file.
That keeps the change reviewable and lets the module apply it with
``git apply``, so what the reviewer sees in the repository is exactly what the
model produced.

MiniMax cannot turn its thinking off, so the thinking text arrives in
``reasoning_content`` and the answer in ``content``. Both are recorded: the
thinking shows how the model reached the change, the answer is the change.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import Settings

__all__ = ["MiniMaxAssistant", "MiniMaxError", "MiniMaxResponse", "build_patch_prompt"]

_SYSTEM_PROMPT = """你是一位资深 Python 工程师，正在按规格驱动开发（SDD）完成一项局部修改。

你会收到：
1. 开发任务描述；
2. 适用的规格条款（逐条列出，标注来源）；
3. 需要修改的代码文件；
4. 用于验证的测试文件。

请输出一个 unified diff 补丁，只包含实现规格所要求的那项修改，不要顺手重构、不要改动测试文件、
不要改动与任务无关的代码。

输出格式要求（必须严格遵守）：
- 先输出一行 ```diff 起始的代码块，块内是标准 unified diff；
- diff 的文件路径相对于项目根目录；
- 代码块之后不要再有任何解释文字。

如果没有需要修改的地方，输出一个空的 diff 代码块。"""


class MiniMaxError(RuntimeError):
    """Raised when the MiniMax request fails."""


@dataclass
class MiniMaxResponse:
    """What MiniMax answered, plus enough metadata to prove it did."""

    request_id: str
    model: str
    content: str
    reasoning_content: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "model": self.model,
            "content": self.content,
            "reasoning_content": self.reasoning_content,
            "usage": self.usage,
        }


def build_patch_prompt(context_prompt: str) -> str:
    """Combine the system prompt and the context into the user message."""
    return (
        "以下是本次任务的开发上下文。\n\n"
        f"{context_prompt}\n\n"
        "请按规格实现上述修改，并输出 unified diff 补丁。"
    )


def extract_diff(text: str) -> str:
    """Pull the unified diff out of the model's answer."""
    if not text:
        return ""
    if "```diff" in text:
        block = text.split("```diff", 1)[1]
        if "```" in block:
            block = block.split("```", 1)[0]
        return block.strip() + "\n"
    if "```" in text:
        block = text.split("```", 1)[1]
        if "```" in block:
            block = block.split("```", 1)[0]
        return block.strip() + "\n"
    # The model sometimes answers with a bare diff.
    if text.lstrip().startswith("---"):
        return text.strip() + "\n"
    return ""


class MiniMaxAssistant:
    """Calls the MiniMax chat completion endpoint."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def complete(self, system: str, user: str) -> MiniMaxResponse:
        """Send one chat completion request and return the answer."""
        if not self.settings.minimax_api_key:
            raise MiniMaxError(
                "缺少 MINIMAX_API_KEY。代码修改这一步需要真实的 MiniMax 密钥；"
                "其余步骤（导入、检索、测试、状态）不需要。"
            )
        url = f"{self.settings.minimax_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.minimax_model,
            "reasoning_effort": self.settings.minimax_effort,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.minimax_api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.minimax_timeout
            ) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise MiniMaxError(f"MiniMax 返回 HTTP {exc.code}: {detail[:600]}") from exc
        except urllib.error.URLError as exc:
            raise MiniMaxError(f"无法连接 MiniMax {url} —— {exc.reason}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MiniMaxError(f"MiniMax 返回的不是 JSON：{raw[:400]}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise MiniMaxError(f"MiniMax 没有返回 choices：{json.dumps(data)[:400]}")
        message = choices[0].get("message") or {}
        return MiniMaxResponse(
            request_id=str(data.get("id") or data.get("request_id") or ""),
            model=str(data.get("model") or self.settings.minimax_model),
            content=str(message.get("content") or ""),
            reasoning_content=str(message.get("reasoning_content") or ""),
            usage=data.get("usage") or {},
            raw=data,
        )

    def propose_patch(self, context_prompt: str) -> tuple[MiniMaxResponse, str]:
        """Ask for a patch and return both the response and the extracted diff."""
        response = self.complete(_SYSTEM_PROMPT, build_patch_prompt(context_prompt))
        return response, extract_diff(response.content)
