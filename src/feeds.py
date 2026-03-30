"""RSS 拉取与 OPML 解析。"""

from __future__ import annotations

import calendar
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import feedparser
import listparser
import requests

from .http_utils import format_requests_detail

from .utils import console


def _entry_published_utc(entry) -> Optional[datetime.datetime]:
    d = entry.get("published_parsed") or entry.get("updated_parsed")
    if not d:
        return None
    try:
        ts = calendar.timegm(d)
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    except (OverflowError, ValueError, OSError, TypeError):
        return None


def update_rss(rss: dict, project_root: Path, proxy_url: str = "") -> Optional[dict]:
    proxy = {"http": proxy_url, "https": proxy_url} if proxy_url else {"http": None, "https": None}

    (key, value), = rss.items()
    rss_path = project_root.joinpath(f'rss/{value["filename"]}')

    result = None
    if url := value.get("url"):
        r = None
        try:
            r = requests.get(value["url"], proxies=proxy, timeout=60)
        except requests.RequestException as e:
            resp = getattr(e, "response", None)
            console.print(
                f"[-] RSS 拉取失败 {key} | {format_requests_detail(e, resp, value['url'])}",
                style="bold red",
            )
        if r is not None and r.status_code == 200:
            with open(rss_path, "w+", encoding="utf-8") as f:
                f.write(r.text)
            print(f"[+] 更新完成：{key}")
            result = {key: rss_path}
        else:
            if r is not None:
                console.print(
                    f"[-] RSS HTTP 错误 {key} | {format_requests_detail(None, r, value['url'])}",
                    style="bold red",
                )
            if rss_path.exists():
                print(f"[-] 更新失败，使用旧文件：{key}")
                result = {key: rss_path}
            else:
                print(f"[-] 更新失败，跳过：{key}")
    else:
        print(f"[+] 本地文件：{key}")
        result = {key: rss_path}

    return result


def parseThread(conf: dict, url: str, proxy_url: str = "", feed_headers: Optional[Dict[str, str]] = None) -> Tuple[str, dict]:
    def filter(title: str) -> bool:
        for i in conf["exclude"]:
            if i in title:
                return False
        return True

    try:
        within_h = float(conf.get("fetch_within_hours", 12))
    except (TypeError, ValueError):
        within_h = 12.0
    if within_h <= 0:
        within_h = 12.0
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now_utc - datetime.timedelta(hours=within_h)
    future_slack = datetime.timedelta(minutes=10)

    proxy = {"http": proxy_url, "https": proxy_url} if proxy_url else {"http": None, "https": None}
    headers = feed_headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    title = ""
    result = {}
    try:
        r = requests.get(url, timeout=10, headers=headers, proxies=proxy)
        if r.status_code != 200:
            console.print(
                f"[-] feed 非 200: {format_requests_detail(None, r, url)}",
                style="bold yellow",
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
        console.print(f"[+] {title}\t{url}\t{len(result.values())}/{len(parsed.entries)}", style="bold green")
    except requests.RequestException as e:
        resp = getattr(e, "response", None)
        console.print(f"[-] feed 请求失败: {format_requests_detail(e, resp, url)}", style="bold red")
    except Exception as e:
        console.print(f"[-] feed 解析失败: url={url} | {type(e).__name__}: {e}", style="bold red")
    return title, result


def init_rss(
    conf: dict,
    project_root: Path,
    update: bool = False,
    proxy_url: str = "",
) -> List[str]:
    rss_list = []
    enabled = [{k: v} for k, v in conf.items() if v["enabled"]]
    for rss in enabled:
        if update:
            if rss := update_rss(rss, project_root, proxy_url):
                rss_list.append(rss)
        else:
            (key, value), = rss.items()
            rss_list.append({key: project_root.joinpath(f'rss/{value["filename"]}')})

    feeds = []
    for rss in rss_list:
        (_, value), = rss.items()
        try:
            rss_parsed = listparser.parse(open(value, encoding="utf-8").read())
            for feed in rss_parsed.feeds:
                u = feed.url.strip().rstrip("/")
                short_url = u.split("://")[-1].split("www.")[-1]
                check = [f for f in feeds if short_url in f]
                if not check:
                    feeds.append(u)
        except Exception as e:
            console.print(f"[-] 解析失败：{value}", style="bold red")
            print(e)

    console.print(f"[+] {len(feeds)} feeds", style="bold yellow")
    return feeds
