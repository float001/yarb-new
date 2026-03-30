"""GitHub Contents API 上传。"""

from __future__ import annotations

import base64
import datetime
import os
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from .http_utils import format_requests_detail
from .paths import format_output_path, path_format_context, run_stamp

from .utils import console


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
    gh = (conf or {}).get("github") or {}
    if not gh.get("enabled"):
        return True
    token_env = gh.get("token_env") or "GITHUB_TOKEN"
    token = os.getenv(token_env) or gh.get("token") or ""
    if not token:
        console.print("[-] GitHub 未配置 token，跳过上传", style="bold yellow")
        return False
    owner = (gh.get("owner") or "").strip()
    repo = (gh.get("repo") or "").strip()
    if not owner or not repo:
        console.print("[-] GitHub 未配置 owner / repo", style="bold yellow")
        return False
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
) -> None:
    gh = (conf or {}).get("github") or {}
    if not gh.get("enabled"):
        return
    stamp = stamp or run_stamp()
    ctx = path_format_context(stamp)
    repo_today = format_output_path(gh.get("path"), "today-{hour}.md", ctx)
    github_put_repo_file(conf, repo_today, content)
    if gh.get("sync_archive_full"):
        return
    if gh.get("sync_archive", True):
        pattern = gh.get("archive_path_pattern") or "archive/{year}/{stamp}.md"
        archive_rel = format_output_path(pattern, "archive/{year}/{stamp}.md", ctx)
        github_put_repo_file(
            conf,
            archive_rel,
            content,
            message=f"chore: update {archive_rel} ({stamp})",
        )


def github_sync_local_archive_dir(conf: Optional[dict], project_root: Path) -> None:
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
