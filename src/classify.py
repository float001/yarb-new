"""DeepSeek 标题分类（全部走 API，无静态预筛与静态回退）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from .deepseek import deepseek_classify_titles

from .utils import console


def classify_feed_titles_with_prefilter(
    titles: List[str],
    conf: Optional[dict],
    stats_out: Optional[Dict[str, int]] = None,
) -> Optional[List[Dict[str, bool]]]:
    if not conf or not conf.get("deepseek", {}).get("enabled"):
        return None
    n = len(titles)
    if n == 0:
        return []
    ai_part = deepseek_classify_titles(titles, conf)
    if ai_part is None:
        console.print(
            "[-] DeepSeek 本批调用失败，条目标记为非安全",
            style="bold yellow",
        )
        if stats_out is not None:
            stats_out["ai_fail"] = stats_out.get("ai_fail", 0) + n
        return [{"security": False, "vulnerability": False} for _ in range(n)]
    if stats_out is not None:
        stats_out["ai_ok"] = stats_out.get("ai_ok", 0) + n
    console.print(f"[+] DeepSeek 本批分类完成（{n} 条）", style="bold cyan")
    return ai_part
