"""DeepSeek Chat API 分类。"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import requests

from .http_utils import format_requests_detail
from .prompts import DEEPSEEK_SYSTEM

from .utils import console

# 利用类标题：模型若标 vulnerability，在此校正为否（与业务定义一致）
EXPLOIT_OR_WRITEUP_RE = re.compile(
    r"EXPLOIT|POC|EXP\b|提权|PRIVILEGE\s+ESCALATION|渗透|漏洞利用|利用链|"
    r"公开利用|"
    r"ATTACK\s+GUIDE|WALKTHROUGH|STEP[-\s]?BY[-\s]?STEP|WEAPONIZ|"
    r"打穿|绕过|复现|实战|WRITEUP|WRITE-UP",
    re.I,
)


def _parse_json_array(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text)


def _deepseek_debug_enabled(conf: dict) -> bool:
    ds = conf.get("deepseek") or {}
    if "debug" in ds:
        return bool(ds["debug"])
    return os.environ.get("DEEPSEEK_DEBUG", "").strip().lower() in ("1", "true", "yes")


def _preview_for_log(text: str, max_len: int = 2500) -> str:
    if not text:
        return "(空)"
    t = text.replace("\r", " ").replace("\n", " ")
    if len(t) > max_len:
        return f"{t[:max_len]}…（共 {len(text)} 字符，已截断）"
    return t


def _deepseek_est_tokens_mixed(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) * 0.55))


def _deepseek_user_message_for_batch(batch: List[str]) -> str:
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(batch))
    return (
        f"共 {len(batch)} 条标题，请严格按顺序输出长度为 {len(batch)} 的 JSON 数组，"
        f'元素形如 {{"security":true,"vulnerability":false}}；vulnerability 仅表示「新漏洞情报」见系统说明，'
        f"漏洞利用/渗透/PoC 实战类须为 false。不要 markdown 代码块或其它文字。\n\n{lines}"
    )


def _deepseek_batch_context_tokens(batch: List[str], conf: dict) -> int:
    ds = conf.get("deepseek") or {}
    margin = int(ds.get("context_safety_margin") or 120)
    per_out = int(ds.get("output_tokens_per_title") or 32)
    sys_t = _deepseek_est_tokens_mixed(DEEPSEEK_SYSTEM)
    user_t = _deepseek_est_tokens_mixed(_deepseek_user_message_for_batch(batch))
    out_t = per_out * len(batch)
    return sys_t + user_t + out_t + margin


def _deepseek_split_title_batches(titles: List[str], conf: dict) -> List[List[str]]:
    ds = conf.get("deepseek") or {}
    max_ctx = int(ds.get("max_context_tokens") or 4096)
    max_n = int(ds.get("max_titles_per_batch") or 0)
    batches: List[List[str]] = []
    batch: List[str] = []

    def trial_ok(trial: List[str]) -> bool:
        if max_n > 0 and len(trial) > max_n:
            return False
        return _deepseek_batch_context_tokens(trial, conf) <= max_ctx

    for t in titles:
        if not batch:
            batch = [t]
            if not trial_ok(batch):
                console.print(
                    f"[-] DeepSeek 单条标题仍超上下文估算（约 {_deepseek_batch_context_tokens(batch, conf)} "
                    f"token / limit {max_ctx}，{len(t)} 字），仍尝试单条请求",
                    style="bold yellow",
                )
            continue
        trial = batch + [t]
        if trial_ok(trial):
            batch.append(t)
        else:
            batches.append(batch)
            batch = [t]
            if not trial_ok(batch):
                console.print(
                    f"[-] DeepSeek 单条标题仍超上下文估算（约 {_deepseek_batch_context_tokens(batch, conf)} "
                    f"token / limit {max_ctx}，{len(t)} 字），仍尝试单条请求",
                    style="bold yellow",
                )
    if batch:
        batches.append(batch)
    return batches


def _deepseek_classify_one_batch(
    titles: List[str],
    conf: dict,
    api_key: str,
    base_url: str,
    model: str,
    proxies: Optional[dict],
    max_ctx: int,
) -> Optional[List[Dict[str, Any]]]:
    user_msg = _deepseek_user_message_for_batch(titles)
    sys_t = _deepseek_est_tokens_mixed(DEEPSEEK_SYSTEM)
    user_t = _deepseek_est_tokens_mixed(user_msg)
    ds = conf.get("deepseek") or {}
    margin = int(ds.get("context_safety_margin") or 120)
    cap_out = int(ds.get("max_output_tokens_cap") or 2048)
    max_tokens = min(cap_out, max(256, max_ctx - sys_t - user_t - margin))
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": DEEPSEEK_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "stream": False,
        "max_tokens": max_tokens,
    }
    debug = _deepseek_debug_enabled(conf)
    if debug:
        safe = json.loads(json.dumps(payload))
        for m in safe.get("messages") or []:
            c = m.get("content")
            if isinstance(c, str) and len(c) > 4000:
                m["content"] = c[:4000] + f"\n…（省略 {len(c) - 4000} 字）"
        console.print(f"[debug] DeepSeek POST {url}", style="dim")
        console.print(
            "[debug] DeepSeek request headers: Authorization=Bearer ***\n"
            + json.dumps(safe, ensure_ascii=False, indent=2),
            style="dim",
        )
    try:
        r = requests.post(url, json=payload, headers=headers, proxies=proxies, timeout=120)
        r.raise_for_status()
        try:
            api_body = r.json()
        except json.JSONDecodeError as e:
            console.print(
                f"[-] DeepSeek HTTP 体不是合法 JSON，本批分类失败 | {e} | "
                f"{format_requests_detail(None, r, url)}",
                style="bold yellow",
            )
            return None
        if debug:
            raw_dbg = json.dumps(api_body, ensure_ascii=False, indent=2)
            cap = 12000
            n = len(raw_dbg)
            if n > cap:
                raw_dbg = raw_dbg[:cap] + f"\n…（共 {n} 字，已截断）"
            console.print(f"[debug] DeepSeek HTTP {r.status_code}", style="dim")
            console.print(f"[debug] DeepSeek response body:\n{raw_dbg}", style="dim")
        try:
            raw_assistant = api_body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            api_dump = _preview_for_log(json.dumps(api_body, ensure_ascii=False), max_len=2000)
            console.print(
                f"[-] DeepSeek API JSON 结构异常（缺 choices[0].message.content），本批分类失败 | "
                f"{type(e).__name__}: {e} | 响应预览={api_dump}",
                style="bold yellow",
            )
            return None
        try:
            arr = _parse_json_array(raw_assistant)
        except json.JSONDecodeError as e:
            line = getattr(e, "lineno", None)
            col = getattr(e, "colno", None)
            pos = getattr(e, "pos", None)
            loc = f"line={line} col={col} pos={pos}" if line is not None else f"pos={pos}"
            console.print(
                f"[-] DeepSeek 助手正文 JSON 解析失败，本批分类失败 | JSONDecodeError: {e} ({loc}) | "
                f"正文预览={_preview_for_log(raw_assistant)}",
                style="bold yellow",
            )
            return None
        if not isinstance(arr, list):
            console.print(
                f"[-] DeepSeek 解析结果类型错误，本批分类失败 | 期望 JSON 数组 list，实际为 {type(arr).__name__} | "
                f"正文预览={_preview_for_log(raw_assistant)}",
                style="bold yellow",
            )
            return None
        n_expect, n_got = len(titles), len(arr)
        if n_got != n_expect:
            console.print(
                f"[-] DeepSeek 数组长度与标题数不一致，本批分类失败 | "
                f"标题数={n_expect} 返回项数={n_got} | 正文预览={_preview_for_log(raw_assistant)}",
                style="bold yellow",
            )
            return None
        out = []
        for i, x in enumerate(arr):
            if not isinstance(x, dict):
                console.print(
                    f"[-] DeepSeek 第 {i + 1} 项不是 JSON 对象，本批分类失败 | "
                    f"类型={type(x).__name__} 值预览={_preview_for_log(repr(x), max_len=400)} | "
                    f"全文预览={_preview_for_log(raw_assistant)}",
                    style="bold yellow",
                )
                return None
            sec = bool(x.get("security"))
            vuln = bool(x.get("vulnerability"))
            if vuln and EXPLOIT_OR_WRITEUP_RE.search(titles[i]):
                vuln = False
            out.append({"security": sec, "vulnerability": vuln})
        return out
    except requests.RequestException as e:
        resp = getattr(e, "response", None)
        if debug and resp is not None:
            try:
                tb = resp.text
                if len(tb) > 4000:
                    tb = tb[:4000] + f"\n…（共 {len(resp.text)} 字，已截断）"
                console.print(f"[debug] DeepSeek 错误响应 HTTP {resp.status_code} body:\n{tb}", style="dim")
            except Exception:
                pass
        console.print(
            f"[-] DeepSeek 请求失败，本批分类失败 | {format_requests_detail(e, resp, url)}",
            style="bold red",
        )
        return None


def deepseek_classify_titles(titles: List[str], conf: dict) -> Optional[List[Dict[str, Any]]]:
    ds = conf.get("deepseek") or {}
    if not ds.get("enabled") or not titles:
        return None
    api_key = os.getenv(ds.get("api_key_env") or "DEEPSEEK_API_KEY") or ds.get("api_key") or ""
    if not api_key:
        console.print("[-] DeepSeek 未配置 API Key，跳过分类", style="bold yellow")
        return None
    base_url = (ds.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
    model = ds.get("model") or "deepseek-chat"
    max_ctx = int(ds.get("max_context_tokens") or 4096)
    proxy_url = ""
    if ds.get("use_proxy") and conf.get("proxy", {}).get("url"):
        proxy_url = conf["proxy"]["url"]
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    batches = _deepseek_split_title_batches(titles, conf)
    if len(batches) > 1:
        console.print(
            f"[+] DeepSeek 本请求拆为 {len(batches)} 批（共 {len(titles)} 条，max_context≈{max_ctx}）",
            style="bold cyan",
        )
    merged: List[Dict[str, Any]] = []
    for batch in batches:
        part = _deepseek_classify_one_batch(
            batch, conf, api_key, base_url, model, proxies, max_ctx
        )
        if part is None:
            return None
        merged.extend(part)
    return merged
