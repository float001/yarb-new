"""项目路径与输出文件名时间戳。"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Dict, Optional


def project_root() -> Path:
    """仓库根目录（含 config.json、rss/）。"""
    return Path(__file__).resolve().parent.parent


def run_stamp() -> str:
    """输出文件名用时间戳：YYYY-MM-DD-H（24 小时制整点）。"""
    return datetime.datetime.now().strftime("%Y-%m-%d-%H")


def run_heading_label() -> str:
    """Markdown 标题中的运行时刻（整点）。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:00")


def path_format_context(stamp: str) -> Dict[str, str]:
    parts = stamp.split("-")
    year = parts[0] if parts else stamp
    date_only = "-".join(parts[:3]) if len(parts) >= 3 else stamp
    hour = parts[3] if len(parts) >= 4 else datetime.datetime.now().strftime("%H")
    return {"stamp": stamp, "year": year, "date": date_only, "hour": hour}


def format_output_path(template: Optional[str], default_tmpl: str, ctx: Dict[str, str]) -> str:
    t = (template or default_tmpl).strip() or default_tmpl
    keys = re.findall(r"\{(\w+)\}", t)
    if not keys:
        return t
    return t.format(**{k: ctx[k] for k in keys})
