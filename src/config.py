"""加载配置，环境变量覆盖密钥（工程化：秘钥不进仓库，仅用 env）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def apply_env_overrides(cfg: Dict[str, Any]) -> None:
    """环境变量优先覆盖配置文件中的密钥类字段。"""
    ds = cfg.setdefault("deepseek", {})
    env_n = (ds.get("api_key_env") or "DEEPSEEK_API_KEY").strip()
    v = os.environ.get(env_n)
    if v and str(v).strip():
        ds["api_key"] = str(v).strip()

    gh = cfg.setdefault("github", {})
    env_n = (gh.get("token_env") or "GITHUB_TOKEN").strip()
    v = os.environ.get(env_n)
    if v and str(v).strip():
        gh["token"] = str(v).strip()

    bot = cfg.get("bot")
    if isinstance(bot, dict):
        for _name, section in bot.items():
            if not isinstance(section, dict):
                continue
            sec_env = section.get("secrets")
            if sec_env:
                ev = os.environ.get(sec_env)
                if ev and str(ev).strip():
                    section["key"] = str(ev).strip()


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    p = path or root / "config.json"
    with open(p, encoding="utf-8") as f:
        cfg: Dict[str, Any] = json.load(f)
    apply_env_overrides(cfg)
    return cfg
