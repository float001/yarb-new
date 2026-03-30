"""写入 today / archive 与飞书摘要。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .bot import build_digest_senders

from .classify import classify_feed_titles_with_prefilter
from .github_api import github_sync_local_archive_dir, github_upload_today_md
from .paths import format_output_path, path_format_context, project_root, run_heading_label, run_stamp


def _digest_mode_line(conf: Optional[dict], stats: dict) -> str:
    """根据本Run DeepSeek 实际调用情况生成一行说明。"""
    enabled = bool((conf or {}).get("deepseek", {}).get("enabled"))
    ok = int(stats.get("ai_ok", 0))
    fail = int(stats.get("ai_fail", 0))
    if not enabled:
        return "分类：AI 未启用（条目不按 AI 分类）"
    if ok == 0 and fail == 0:
        return "分类：本Run 无待分类标题"
    if fail == 0:
        return f"分类：AI 成功 {ok} 条"
    if ok == 0:
        return f"分类：AI 失败 {fail} 条（条目不纳入）"
    return f"分类：AI 成功 {ok} 条、失败 {fail} 条"


def update_today(data: list = None, conf: Optional[dict] = None) -> None:
    if data is None:
        data = []
    root_path = project_root()
    data_path = root_path.joinpath("temp_data.json")
    stamp = run_stamp()
    heading = run_heading_label()
    ctx = path_format_context(stamp)
    gh = (conf or {}).get("github") or {}
    today_rel = format_output_path(gh.get("path"), "today-{hour}.md", ctx)
    arch_rel = format_output_path(
        gh.get("archive_path_pattern"),
        "archive/{year}/{stamp}.md",
        ctx,
    )
    today_path = root_path.joinpath(today_rel)
    archive_path = root_path.joinpath(arch_rel)

    if not data and data_path.exists():
        with open(data_path, "r", encoding="utf-8") as f1:
            data = json.load(f1)

    proxy_bot = conf["proxy"]["url"] if (conf or {}).get("proxy", {}).get("bot") else ""
    digest_senders = build_digest_senders(conf or {}, proxy_bot)

    def _push_digest(text: str) -> None:
        if not digest_senders or not (text or "").strip():
            return
        for sender in digest_senders:
            sender.send(text)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with open(today_path, "w+", encoding="utf-8") as f1, open(archive_path, "w+", encoding="utf-8") as f2:
        content = f"# 每日安全资讯（{heading}）\n\n"
        cc = content
        vti_cc = f"# 每日漏洞情报（{heading}）\n\n"
        classify_stats: dict = {"ai_ok": 0, "ai_fail": 0}
        for item in data:
            (feed, value), = item.items()
            content += f"- {feed}\n"
            cc += f"{feed}\n"
            titles_urls = list(value.items())
            ai_flags = classify_feed_titles_with_prefilter(
                [t for t, _ in titles_urls],
                conf,
                stats_out=classify_stats,
            )
            for idx, (title, url) in enumerate(titles_urls):
                if ai_flags is not None:
                    vuln = ai_flags[idx]["vulnerability"]
                    sec = ai_flags[idx]["security"]
                else:
                    vuln, sec = False, False
                if sec:
                    content += f"  - [{title}]({url})\n"
                if vuln:
                    vti_cc += f"- {title} ({url})\n"
                elif sec:
                    cc += f"    - {title} ({url})\n"

            if len(cc) > 19000:
                _push_digest(cc)
                cc = ""

        _push_digest(_digest_mode_line(conf, classify_stats))
        _push_digest(cc)
        _push_digest(vti_cc)
        f1.write(content)
        f2.write(content)

    github_upload_today_md(content, conf, stamp=stamp)
    github_sync_local_archive_dir(conf, root_path)
