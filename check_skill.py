#!/usr/bin/env python3
"""校验 SKILL.md 是否符合 Agent Skills / 魔搭 Skills 中心的格式要求。

用法:
    python3 check_skill.py <SKILL.md 路径> [...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
NAME_RE = re.compile(r"^name:\s*(.+)$", re.M)
DESC_RE = re.compile(r"^description:\s*(.+)$", re.M)
NAME_CHARS_RE = re.compile(r"^[a-z0-9-]+$")


def check(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    ok = True

    m = FM_RE.match(text)
    if not m:
        print(f"  ✗ {path}: 缺少 YAML frontmatter（文件应以 --- 开头）")
        return False
    fm = m.group(1)

    nm = NAME_RE.search(fm)
    if not nm:
        print(f"  ✗ {path}: frontmatter 缺少 name")
        ok = False
    else:
        name = nm.group(1).strip()
        if len(name) > 64:
            print(f"  ✗ {path}: name 超过 64 字符（{len(name)}）")
            ok = False
        elif not NAME_CHARS_RE.fullmatch(name):
            print(f"  ✗ {path}: name 只能含小写字母、数字、连字符（当前 {name!r}）")
            ok = False
        else:
            print(f"  ✓ name: {name}（{len(name)} 字符）")

    dm = DESC_RE.search(fm)
    if not dm:
        print(f"  ✗ {path}: frontmatter 缺少 description")
        ok = False
    else:
        desc = dm.group(1).strip()
        if not desc:
            print(f"  ✗ {path}: description 为空")
            ok = False
        elif len(desc) > 1024:
            print(f"  ✗ {path}: description 超过 1024 字符（{len(desc)}）")
            ok = False
        else:
            print(f"  ✓ description: {len(desc)} 字符")

    lines = len(text.splitlines())
    if lines >= 500:
        print(f"  ✗ {path}: 正文 {lines} 行，建议控制在 500 行内")
        ok = False
    else:
        print(f"  ✓ 正文 {lines} 行")

    bad = text.count("�")
    if bad:
        print(f"  ✗ {path}: 有 {bad} 个替换字符（编码损坏）")
        ok = False
    else:
        print("  ✓ 无编码损坏字符")

    # 引用了但可能不存在的相对文件
    refs = re.findall(r"\]\((?!https?://)([^)#]+)\)", text)
    for ref in refs:
        target = (path.parent / ref).resolve()
        if not target.exists():
            print(f"  ✗ {path}: 引用了不存在的文件 {ref}")
            ok = False

    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 check_skill.py <SKILL.md> [...]")
        return 2
    all_ok = True
    for arg in sys.argv[1:]:
        p = Path(arg)
        print(f"== {p} ==")
        all_ok = check(p) and all_ok
        print()
    if all_ok:
        print("全部通过")
        return 0
    print("存在问题")
    return 1


if __name__ == "__main__":
    sys.exit(main())
