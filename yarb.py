#!/usr/bin/python3

import os
import json
import time
import calendar
import base64
from typing import Optional, List, Dict, Any
from urllib.parse import quote
import asyncio
import schedule
import pyfiglet
import argparse
import datetime
import listparser
import feedparser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from bot import *
from utils import *

import re
import requests
requests.packages.urllib3.disable_warnings()


def _run_stamp() -> str:
    """输出文件名用时间戳：YYYY-MM-DD-H（24 小时制整点）。"""
    return datetime.datetime.now().strftime("%Y-%m-%d-%H")


def _run_heading_label() -> str:
    """Markdown 标题中的运行时刻（整点）。"""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:00")


def _path_format_context(stamp: str) -> Dict[str, str]:
    parts = stamp.split("-")
    year = parts[0] if parts else stamp
    date_only = "-".join(parts[:3]) if len(parts) >= 3 else stamp
    # stamp 由 _run_stamp 生成时为 YYYY-MM-DD-H，末段为 24 小时制小时
    hour = parts[3] if len(parts) >= 4 else datetime.datetime.now().strftime("%H")
    return {"stamp": stamp, "year": year, "date": date_only, "hour": hour}


def _format_output_path(template: Optional[str], default_tmpl: str, ctx: Dict[str, str]) -> str:
    t = (template or default_tmpl).strip() or default_tmpl
    keys = re.findall(r"\{(\w+)\}", t)
    if not keys:
        return t
    return t.format(**{k: ctx[k] for k in keys})


DEEPSEEK_SYSTEM = (
    "你是安全资讯分类助手，仅根据标题判断，输出约定 JSON。\n"
    "security：是否属于广义网络安全/信息安全（攻防、恶意软件、合规与隐私、安全产品、数据泄露事件报道等）。"
    "招聘、广告、纯娱乐/生活与 IT 安全无关则为 false。\n"
    "vulnerability：仅当标题核心是「新漏洞情报」时为 true——"
    "如新分配的 CVE、厂商/安全机构首次公开的安全缺陷、紧急安全更新与补丁说明中的新漏洞、"
    "「某产品曝出/修复某漏洞」类披露。若为漏洞利用、攻击手法复盘、渗透/PoC 实战、提权 writeup、"
    "武器化利用、CTF 题解、仅讲如何打穿/绕过而无新漏洞披露，则 vulnerability 必须为 false（"
    "此类若仍属安全领域则 security 为 true，归入资讯）。\n"
    "只输出要求的 JSON，不要解释。"
)

# 无 DeepSeek 时的回退：新漏洞倾向 vs 利用/实战倾向
_CVE_ID_RE = re.compile(r"CVE-\d{4}-\d+", re.I)
_EXPLOIT_OR_WRITEUP_RE = re.compile(
    r"EXPLOIT|POC|EXP\b|提权|PRIVILEGE\s+ESCALATION|渗透|漏洞利用|利用链|"
    r"ATTACK\s+GUIDE|WALKTHROUGH|STEP[-\s]?BY[-\s]?STEP|WEAPONIZ|"
    r"打穿|绕过|复现|实战|WRITEUP|WRITE-UP",
    re.I,
)

# 标题含以下特征则仍送 DeepSeek（避免把安全新闻误杀）
_STATIC_SECURITY_HINT_RE = re.compile(
    r"CVE-\d{4}-\d+|\bRCE\b|\bXSS\b|SQL注入|漏洞|0\s*[Dd]ay|"
    r"恶意软件|勒索|钓鱼|木马|后门|网络攻击|数据泄露|信息泄露|安全公告|安全预警|安全动态|"
    r"未授权|越权|认证绕过|供应链|APT\b|病毒\b|蠕虫|僵尸网络|botnet|phishing|ransomware|"
    r"malware|data breach|cyber\s*attack|vulnerability|exploit\b|"
    r"patch|安全更新|security update|CISA|"
    r"渗透|攻防|红队|蓝队|威胁情报|IOC\b|TTP\b|"
    r"加密劫持|凭据|账号劫持|账户接管|身份盗用|DDoS|defacement|"
    r"漏洞挖掘|Bug Bounty|零信任|防火墙|入侵|入侵检测|"
    r"加密\s*货币.*盗|钱包.*盗|钓鱼邮件|垃圾邮件|社工|"
    r"OpenClaw|安全.*风险|风险提示",
    re.I,
)


def title_is_static_non_security(title: str, conf: dict) -> bool:
    """明显非安全向标题，可跳过 DeepSeek。若命中安全强特征则永不视为非安全。"""
    ds = conf.get("deepseek") or {}
    if not ds.get("static_prefilter", True):
        return False
    if _STATIC_SECURITY_HINT_RE.search(title):
        return False
    kw = conf.get("keywords") or {}
    for sub in kw.get("exclude") or []:
        if sub and sub in title:
            return True
    for sub in kw.get("static_non_security") or []:
        if sub and sub in title:
            return True
    return False


def title_is_new_vuln_intel_fallback(title: str) -> bool:
    """关键词回退：仅将「新漏洞披露」倾向的标题划入漏洞情报，利用类归资讯。"""
    if _EXPLOIT_OR_WRITEUP_RE.search(title):
        return False
    if _CVE_ID_RE.search(title):
        return True
    if "漏洞" in title and re.search(
        r"补丁|修复|预警|披露|公告|严重|紧急|远程代码|RCE|未授权|认证绕过|CVSS|安全更新",
        title,
    ):
        return True
    return False


def format_requests_detail(
    exc: Optional[BaseException] = None,
    response: Optional[requests.Response] = None,
    url: str = "",
    body_max: int = 1200,
) -> str:
    """汇总 HTTP/网络错误信息，便于排查。"""
    parts: List[str] = []
    if url:
        parts.append(f"url={url}")
    if exc is not None:
        parts.append(f"{type(exc).__name__}: {exc}")
    if response is not None:
        parts.append(f"status={response.status_code}")
        u = getattr(response, "url", None) or url
        if u and f"url={u}" not in " | ".join(parts):
            parts.append(f"final_url={u}")
        body = ""
        try:
            ct = response.headers.get("Content-Type") or ""
            if "json" in ct.lower():
                body = json.dumps(response.json(), ensure_ascii=False)
            else:
                body = response.text or ""
        except Exception:
            body = (response.text or "")[:body_max]
        body = (body or "")[:body_max].replace("\n", " ").replace("\r", " ")
        if body:
            parts.append(f"body={body}")
    return " | ".join(parts)


def _parse_json_array(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text)


def _preview_for_log(text: str, max_len: int = 2500) -> str:
    """将模型/响应正文压成单行并截断，便于终端查看。"""
    if not text:
        return "(空)"
    t = text.replace("\r", " ").replace("\n", " ")
    if len(t) > max_len:
        return f"{t[:max_len]}…（共 {len(text)} 字符，已截断）"
    return t


def _deepseek_est_tokens_mixed(text: str) -> int:
    """中英混合粗略折 token（偏保守，便于适配 4k 等小上下文）。"""
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
    """单批请求估算总上下文（system + user + 预留输出）。"""
    ds = conf.get("deepseek") or {}
    margin = int(ds.get("context_safety_margin") or 120)
    per_out = int(ds.get("output_tokens_per_title") or 32)
    sys_t = _deepseek_est_tokens_mixed(DEEPSEEK_SYSTEM)
    user_t = _deepseek_est_tokens_mixed(_deepseek_user_message_for_batch(batch))
    out_t = per_out * len(batch)
    return sys_t + user_t + out_t + margin


def _deepseek_split_title_batches(titles: List[str], conf: dict) -> List[List[str]]:
    """按 max_context_tokens（默认 4096）拆批，避免超出 DeepSeek 4k 等限制。"""
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
    """对一批标题调用 API 并解析为与 titles 等长的列表。"""
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
    try:
        r = requests.post(url, json=payload, headers=headers, proxies=proxies, timeout=120)
        r.raise_for_status()
        try:
            api_body = r.json()
        except json.JSONDecodeError as e:
            console.print(
                f"[-] DeepSeek HTTP 体不是合法 JSON，使用关键词回退 | {e} | "
                f"{format_requests_detail(None, r, url)}",
                style="bold yellow",
            )
            return None
        try:
            raw_assistant = api_body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            api_dump = _preview_for_log(json.dumps(api_body, ensure_ascii=False), max_len=2000)
            console.print(
                f"[-] DeepSeek API JSON 结构异常（缺 choices[0].message.content），使用关键词回退 | "
                f"{type(e).__name__}: {e} | 响应预览={api_dump}",
                style="bold yellow",
            )
            return None
        try:
            arr = _parse_json_array(raw_assistant)
        except json.JSONDecodeError as e:
            pos = getattr(e, "pos", None)
            line = getattr(e, "lineno", None)
            col = getattr(e, "colno", None)
            loc = f"line={line} col={col} pos={pos}" if line is not None else f"pos={pos}"
            console.print(
                f"[-] DeepSeek 助手正文 JSON 解析失败，使用关键词回退 | JSONDecodeError: {e} ({loc}) | "
                f"正文预览={_preview_for_log(raw_assistant)}",
                style="bold yellow",
            )
            return None
        if not isinstance(arr, list):
            console.print(
                f"[-] DeepSeek 解析结果类型错误，使用关键词回退 | 期望 JSON 数组 list，实际为 {type(arr).__name__} | "
                f"正文预览={_preview_for_log(raw_assistant)}",
                style="bold yellow",
            )
            return None
        n_expect, n_got = len(titles), len(arr)
        if n_got != n_expect:
            console.print(
                f"[-] DeepSeek 数组长度与标题数不一致，使用关键词回退 | "
                f"标题数={n_expect} 返回项数={n_got} | 正文预览={_preview_for_log(raw_assistant)}",
                style="bold yellow",
            )
            return None
        out = []
        for i, x in enumerate(arr):
            if not isinstance(x, dict):
                console.print(
                    f"[-] DeepSeek 第 {i + 1} 项不是 JSON 对象，使用关键词回退 | "
                    f"类型={type(x).__name__} 值预览={_preview_for_log(repr(x), max_len=400)} | "
                    f"全文预览={_preview_for_log(raw_assistant)}",
                    style="bold yellow",
                )
                return None
            sec = bool(x.get("security"))
            vuln = bool(x.get("vulnerability"))
            if vuln and _EXPLOIT_OR_WRITEUP_RE.search(titles[i]):
                vuln = False
            out.append({"security": sec, "vulnerability": vuln})
        return out
    except requests.RequestException as e:
        resp = getattr(e, "response", None)
        console.print(
            f"[-] DeepSeek 请求失败，使用关键词回退 | {format_requests_detail(e, resp, url)}",
            style="bold red",
        )
        return None


def deepseek_classify_titles(titles: List[str], conf: dict) -> Optional[List[Dict[str, Any]]]:
    """调用 DeepSeek API；自动按 4k 等上下文拆批；vulnerability 表示「新漏洞情报」。"""
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
    for bi, batch in enumerate(batches):
        part = _deepseek_classify_one_batch(
            batch, conf, api_key, base_url, model, proxies, max_ctx
        )
        if part is None:
            return None
        merged.extend(part)
    return merged


def classify_feed_titles_with_prefilter(
    titles: List[str],
    conf: Optional[dict],
) -> Optional[List[Dict[str, bool]]]:
    """
    先静态剔除明显非安全标题（不送 API），其余再调用 DeepSeek。
    未启用 DeepSeek 时返回 None，由调用方走原有关键词逻辑。
    DeepSeek 整次失败时，未走静态规则的条目记为 security=false（不再用关键词冒充 AI 结果）。
    """
    if not conf or not conf.get("deepseek", {}).get("enabled"):
        return None
    n = len(titles)
    if n == 0:
        return []
    result: List[Optional[Dict[str, bool]]] = [None] * n
    need_ai: List[int] = []
    static_n = 0
    for i, t in enumerate(titles):
        if title_is_static_non_security(t, conf):
            result[i] = {"security": False, "vulnerability": False}
            static_n += 1
        else:
            need_ai.append(i)
    if need_ai:
        sub_titles = [titles[i] for i in need_ai]
        ai_part = deepseek_classify_titles(sub_titles, conf)
        if ai_part is None:
            for i in need_ai:
                result[i] = {"security": False, "vulnerability": False}
        else:
            for j, i in enumerate(need_ai):
                result[i] = ai_part[j]
    if static_n and need_ai:
        console.print(
            f"[+] DeepSeek 静态过滤 {static_n} 条，API 分类 {len(need_ai)} 条（本组共 {n} 条）",
            style="bold cyan",
        )
    elif static_n and not need_ai:
        console.print(
            f"[+] DeepSeek 本组 {n} 条均由静态规则过滤，未调用 API",
            style="bold cyan",
        )
    out: List[Dict[str, bool]] = []
    for i in range(n):
        cell = result[i]
        if cell is None:
            cell = {"security": False, "vulnerability": False}
        out.append(cell)
    return out


def send_lark(data: str):
    json_data = {
        "msg_type":"text",
        "content": { 
            "text": data 
        }
    }
    post_data = json.dumps(json_data, ensure_ascii=False)
    url = 'https://open.larksuite.com/open-apis/bot/v2/hook/5dc6fa40-42fc-4359-8d7a-de6163604b9c'
    headers = {
        'Content-Type': 'application/json',
    }
    try:
        resp = requests.post(url, json=json_data, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        r = getattr(e, "response", None)
        console.print(f"[-] Lark 推送失败 | {format_requests_detail(e, r, url)}", style="bold red")
        raise
    response_dict = resp.json()
    code = response_dict.get("code", -1)
    if code != 0:
        print(response_dict)


def _github_contents_api_url(api_base: str, owner: str, repo: str, path: str) -> str:
    segs = [quote(s, safe="") for s in path.strip("/").split("/") if s]
    path_q = "/".join(segs)
    return f"{api_base.rstrip('/')}/repos/{owner}/{repo}/contents/{path_q}"


def github_put_repo_file(
    conf: Optional[dict],
    repo_path: str,
    content: str,
    message: Optional[str] = None,
) -> bool:
    """通过 Contents API 创建或更新仓库内单个文件。未启用 github 时返回 True（跳过）。"""
    gh = (conf or {}).get("github") or {}
    if not gh.get("enabled"):
        return True
    token_env = gh.get("token_env") or "GITHUB_TOKEN"
    token = os.getenv(token_env) or gh.get("token") or ""
    if not token:
        console.print("[-] GitHub 未配置 token，跳过上传", style="bold yellow")
        return False
    owner = gh.get("owner") or "float001"
    repo = gh.get("repo") or "yarb-new"
    branch = gh.get("branch") or "main"
    api_base = (gh.get("api_base") or "https://api.github.com").rstrip("/")
    proxy_url = ""
    if gh.get("use_proxy") and (conf or {}).get("proxy", {}).get("url"):
        proxy_url = (conf or {})["proxy"]["url"]
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    url = _github_contents_api_url(api_base, owner, repo, repo_path)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    sha = None
    try:
        gr = requests.get(url, headers=headers, params={"ref": branch}, proxies=proxies, timeout=30)
        if gr.status_code == 200:
            sha = gr.json().get("sha")
        elif gr.status_code == 404:
            sha = None
        else:
            console.print(
                f"[-] GitHub 获取 {repo_path} 失败 | {format_requests_detail(None, gr, url)}",
                style="bold red",
            )
            return False
    except requests.RequestException as e:
        r = getattr(e, "response", None)
        console.print(
            f"[-] GitHub 获取 {repo_path} 失败 | {format_requests_detail(e, r, url)}",
            style="bold red",
        )
        return False
    msg = message or gh.get("commit_message") or (
        f"chore: update {repo_path} ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})"
    )
    body = {
        "message": msg,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    try:
        pr = requests.put(url, headers=headers, json=body, proxies=proxies, timeout=60)
        if pr.status_code in (200, 201):
            console.print(f"[+] 已推送到 GitHub {owner}/{repo}/{repo_path} ({branch})", style="bold green")
            return True
        console.print(
            f"[-] GitHub 更新 {repo_path} 失败 | {format_requests_detail(None, pr, url)}",
            style="bold red",
        )
        return False
    except requests.RequestException as e:
        r = getattr(e, "response", None)
        console.print(
            f"[-] GitHub 上传 {repo_path} 异常 | {format_requests_detail(e, r, url)}",
            style="bold red",
        )
        return False


def github_upload_today_md(
    content: str,
    conf: Optional[dict] = None,
    stamp: Optional[str] = None,
):
    """推送 today 与 archive；若开启 sync_archive 则同时推送 archive 路径（与 sync_archive_full 互斥）。"""
    gh = (conf or {}).get("github") or {}
    if not gh.get("enabled"):
        return
    stamp = stamp or _run_stamp()
    ctx = _path_format_context(stamp)
    repo_today = _format_output_path(gh.get("path"), "today-{hour}.md", ctx)
    github_put_repo_file(conf, repo_today, content)
    if gh.get("sync_archive_full"):
        return
    if gh.get("sync_archive", True):
        pattern = gh.get("archive_path_pattern") or "archive/{year}/{stamp}.md"
        archive_rel = _format_output_path(pattern, "archive/{year}/{stamp}.md", ctx)
        github_put_repo_file(
            conf,
            archive_rel,
            content,
            message=f"chore: update {archive_rel} ({stamp})",
        )


def github_sync_local_archive_dir(conf: Optional[dict], project_root: Path):
    """将本地 archive/ 下全部 .md 同步到仓库（需 sync_archive_full）。"""
    gh = (conf or {}).get("github") or {}
    if not gh.get("enabled") or not gh.get("sync_archive_full"):
        return
    ar = project_root.joinpath("archive")
    if not ar.is_dir():
        return
    paths = sorted(ar.rglob("*.md"))
    console.print(f"[+] GitHub 全量同步 archive/ 共 {len(paths)} 个文件", style="bold yellow")
    for p in paths:
        try:
            body = p.read_text(encoding="utf-8")
        except OSError as e:
            console.print(f"[-] 读取 {p} 失败: {e}", style="bold red")
            continue
        repo_path = p.relative_to(project_root).as_posix()
        github_put_repo_file(
            conf,
            repo_path,
            body,
            message=f"chore: sync {repo_path}",
        )


def update_today(data: list=[], conf: Optional[dict] = None):
    """更新today"""
    root_path = Path(__file__).absolute().parent
    data_path = root_path.joinpath('temp_data.json')
    stamp = _run_stamp()
    heading = _run_heading_label()
    ctx = _path_format_context(stamp)
    gh = (conf or {}).get("github") or {}
    today_rel = _format_output_path(gh.get("path"), "today-{hour}.md", ctx)
    arch_rel = _format_output_path(
        gh.get("archive_path_pattern"),
        "archive/{year}/{stamp}.md",
        ctx,
    )
    today_path = root_path.joinpath(today_rel)
    archive_path = root_path.joinpath(arch_rel)

    if not data and data_path.exists():
        with open(data_path, 'r') as f1:
            data = json.load(f1)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with open(today_path, 'w+') as f1, open(archive_path, 'w+') as f2:
        content = f'# 每日安全资讯（{heading}）\n\n'
        cc =  content
        vti_cc = f'# 每日漏洞情报（{heading}）\n\n'
        for item in data:
            (feed, value), = item.items()
            content += f'- {feed}\n'
            cc += f'{feed}\n'
            titles_urls = list(value.items())
            ai_flags = classify_feed_titles_with_prefilter(
                [t for t, _ in titles_urls],
                conf,
            )
            for idx, (title, url) in enumerate(titles_urls):
                if ai_flags is not None:
                    vuln = ai_flags[idx]["vulnerability"]
                    sec = ai_flags[idx]["security"]
                    if vuln:
                        vti_cc += f'- {title} ({url})\n'
                        content += f'  - [{title}]({url})\n'
                    elif sec:
                        cc += f'    - {title} ({url})\n'
                        content += f'  - [{title}]({url})\n'

            if len(cc) > 19000:
                send_lark(cc)
                cc = ''

        send_lark(cc)
        send_lark(vti_cc)
        f1.write(content)
        f2.write(content)

    github_upload_today_md(content, conf, stamp=stamp)
    github_sync_local_archive_dir(conf, root_path)

def update_rss(rss: dict, proxy_url=''):
    """更新订阅源文件"""
    proxy = {'http': proxy_url, 'https': proxy_url} if proxy_url else {'http': None, 'https': None}

    (key, value), = rss.items()
    rss_path = root_path.joinpath(f'rss/{value["filename"]}')

    result = None
    if url := value.get('url'):
        r = None
        try:
            r = requests.get(value['url'], proxies=proxy, timeout=60)
        except requests.RequestException as e:
            resp = getattr(e, "response", None)
            console.print(
                f"[-] RSS 拉取失败 {key} | {format_requests_detail(e, resp, value['url'])}",
                style="bold red",
            )
        if r is not None and r.status_code == 200:
            with open(rss_path, 'w+') as f:
                f.write(r.text)
            print(f'[+] 更新完成：{key}')
            result = {key: rss_path}
        else:
            if r is not None:
                console.print(
                    f"[-] RSS HTTP 错误 {key} | {format_requests_detail(None, r, value['url'])}",
                    style="bold red",
                )
            if rss_path.exists():
                print(f'[-] 更新失败，使用旧文件：{key}')
                result = {key: rss_path}
            else:
                print(f'[-] 更新失败，跳过：{key}')
    else:
        print(f'[+] 本地文件：{key}')
        result = {key: rss_path}

    return result


def _entry_published_utc(entry) -> Optional[datetime.datetime]:
    """feedparser 的 published_parsed / updated_parsed 转 UTC 时间。"""
    d = entry.get('published_parsed') or entry.get('updated_parsed')
    if not d:
        return None
    try:
        ts = calendar.timegm(d)
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    except (OverflowError, ValueError, OSError, TypeError):
        return None


def parseThread(conf: dict, url: str, proxy_url=''):
    """获取文章线程"""
    def filter(title: str):
        """过滤文章"""
        for i in conf['exclude']:
            if i in title:
                return False
        return True

    try:
        within_h = float(conf.get('fetch_within_hours', 12))
    except (TypeError, ValueError):
        within_h = 12.0
    if within_h <= 0:
        within_h = 12.0
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now_utc - datetime.timedelta(hours=within_h)
    future_slack = datetime.timedelta(minutes=10)

    proxy = {'http': proxy_url, 'https': proxy_url} if proxy_url else {'http': None, 'https': None}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }

    title = ''
    result = {}
    try:
        r = requests.get(url, timeout=10, headers=headers, verify=False, proxies=proxy)
        if r.status_code != 200:
            console.print(
                f'[-] feed 非 200: {format_requests_detail(None, r, url)}',
                style='bold yellow',
            )
        parsed = feedparser.parse(r.content)
        title = parsed.feed.title
        for entry in parsed.entries:
            pub_dt = _entry_published_utc(entry)
            if pub_dt is None:
                continue
            if not (cutoff <= pub_dt <= now_utc + future_slack):
                continue
            if filter(entry.title):
                item = {entry.title: entry.link}
                result |= item
        console.print(f'[+] {title}\t{url}\t{len(result.values())}/{len(parsed.entries)}', style='bold green')
    except requests.RequestException as e:
        resp = getattr(e, "response", None)
        console.print(f'[-] feed 请求失败: {format_requests_detail(e, resp, url)}', style='bold red')
    except Exception as e:
        console.print(f'[-] feed 解析失败: url={url} | {type(e).__name__}: {e}', style='bold red')
    return title, result


async def init_bot(conf: dict, proxy_url=''):
    """初始化机器人"""
    bots = []
    for name, v in conf.items():
        if v['enabled']:
            key = os.getenv(v['secrets']) or v['key']

            if name == 'mail':
                receiver = os.getenv(v['secrets_receiver']) or v['receiver']
                bot = globals()[f'{name}Bot'](v['address'], key, receiver, v['from'], v['server'])
                bots.append(bot)
            elif name == 'qq':
                bot = globals()[f'{name}Bot'](v['group_id'])
                if await bot.start_server(v['qq_id'], key):
                    bots.append(bot)
            elif name == 'telegram':
                bot = globals()[f'{name}Bot'](key, v['chat_id'], proxy_url)
                if await bot.test_connect():
                    bots.append(bot)
            else:
                bot = globals()[f'{name}Bot'](key, proxy_url)
                bots.append(bot)
    return bots


def init_rss(conf: dict, update: bool=False, proxy_url=''):
    """初始化订阅源"""
    rss_list = []
    enabled = [{k: v} for k, v in conf.items() if v['enabled']]
    for rss in enabled:
        if update:
            if rss := update_rss(rss, proxy_url):
                rss_list.append(rss)
        else:
            (key, value), = rss.items()
            rss_list.append({key: root_path.joinpath(f'rss/{value["filename"]}')})

    # 合并相同链接
    feeds = []
    for rss in rss_list:
        (_, value), = rss.items()
        try:
            rss = listparser.parse(open(value).read())
            for feed in rss.feeds:
                url = feed.url.strip().rstrip('/')
                short_url = url.split('://')[-1].split('www.')[-1]
                check = [feed for feed in feeds if short_url in feed]
                if not check:
                    feeds.append(url)
        except Exception as e:
            console.print(f'[-] 解析失败：{value}', style='bold red')
            print(e)

    console.print(f'[+] {len(feeds)} feeds', style='bold yellow')
    return feeds


def cleanup():
    """结束清理"""
    qqBot.kill_server()


async def job(args):
    """定时任务"""
    print(f'{pyfiglet.figlet_format("yarb")}\n{_run_heading_label()}')

    global root_path
    root_path = Path(__file__).absolute().parent
    if args.config:
        config_path = Path(args.config).expanduser().absolute()
    else:
        config_path = root_path.joinpath('config.json')
    with open(config_path) as f:
        conf = json.load(f)

    proxy_rss = conf['proxy']['url'] if conf['proxy']['rss'] else ''
    feeds = init_rss(conf['rss'], args.update, proxy_rss)

    results = []
    if args.test:
        # 测试数据
        results.extend({f'test{i}': {Pattern.create(i*500): 'test'}} for i in range(1, 20))
    else:
        # 获取文章
        numb = 0
        tasks = []
        with ThreadPoolExecutor(100) as executor:
            tasks.extend(executor.submit(parseThread, conf['keywords'], url, proxy_rss) for url in feeds)
            for task in as_completed(tasks):
                title, result = task.result()            
                if result:
                    numb += len(result.values())
                    results.append({title: result})
        console.print(f'[+] {len(results)} feeds, {numb} articles', style='bold yellow')

        # temp_path = root_path.joinpath('temp_data.json')
        # with open(temp_path, 'w+') as f:
        #     f.write(json.dumps(results, indent=4, ensure_ascii=False))
        #     console.print(f'[+] temp data: {temp_path}', style='bold yellow')

        # 更新today
        update_today(results, conf)

    # 推送文章
    proxy_bot = conf['proxy']['url'] if conf['proxy']['bot'] else ''
    bots = await init_bot(conf['bot'], proxy_bot)
    for bot in bots:
        await bot.send(bot.parse_results(results))

    cleanup()


def argument():
    parser = argparse.ArgumentParser()
    parser.add_argument('--update', help='Update RSS config file', action='store_true', required=False)
    parser.add_argument('--cron', help='Execute scheduled tasks every day (eg:"11:00")', type=str, required=False)
    parser.add_argument('--config', help='Use specified config file', type=str, required=False)
    parser.add_argument('--test', help='Test bot', action='store_true', required=False)
    return parser.parse_args()

async def main():
    args = argument()
    if args.cron:
        schedule.every().day.at(args.cron).do(job, args)
        while True:
            schedule.run_pending()
            await asyncio.sleep(1)
    else:
        await job(args)

if __name__ == '__main__':
    asyncio.run(main())
