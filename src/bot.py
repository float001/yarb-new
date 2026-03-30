from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import List, Protocol

import requests
from pyrate_limiter import Duration, Rate, InMemoryBucket, Limiter

from .utils import *

__all__ = [
    "NotifyBot",
    "feishuBot",
    "wecomBot",
    "dingtalkBot",
    "telegramBot",
    "larkBot",
    "build_digest_senders",
]


class NotifyBot(Protocol):
    """统一发送接口：``send(text)`` 推送一段文本（摘要分段或 parse_results 的单条）。"""

    def send(self, text: str) -> None: ...


today = datetime.now().strftime("%Y-%m-%d")

# Lark 自定义机器人：配置与 feishu 相同（secrets + key）；`key` 可为完整 https URL，否则拼到内置前缀
DEFAULT_LARK_HOOK_BASE = "https://open.larksuite.com/open-apis/bot/v2/hook"

def _post_feishu_open_webhook_text(webhook_url: str, text: str, proxy: dict, ok_label: str) -> None:
    """飞书开放平台 webhook 文本（与 Lark 自定义机器人 JSON 一致）。"""
    if not (text or "").strip():
        return
    data = {"msg_type": "text", "content": {"text": text}}
    headers = {"Content-Type": "application/json"}
    try:
        r = requests.post(
            webhook_url,
            json=data,
            headers=headers,
            proxies=proxy,
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        code = body.get("code")
        if code is not None and code != 0:
            console.print(f"[-] {ok_label} 业务错误", style="bold red")
            print(body)
        else:
            console.print(f"[+] {ok_label} 发送成功", style="bold green")
    except requests.RequestException as e:
        console.print(f"[-] {ok_label} 发送失败: {e}", style="bold red")
    except Exception as e:
        console.print(f"[-] {ok_label} 发送失败: {e}", style="bold red")


class larkBot:
    """飞书/Lark 自定义机器人（文本消息）
    https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """

    def __init__(self, key, proxy_url="") -> None:
        self.key = key
        self.hook_base = DEFAULT_LARK_HOOK_BASE.rstrip("/")
        self.proxy = {"http": proxy_url, "https": proxy_url} if proxy_url else {"http": None, "https": None}

    def _webhook_url(self) -> str:
        k = (self.key or "").strip()
        if not k:
            return ""
        if k.startswith(("http://", "https://")):
            return k
        return f"{self.hook_base}/{k}"

    @staticmethod
    def parse_results(results: list):
        text_list = []
        for result in results:
            (feed, value), = result.items()
            text = f"[ {feed} ]\n\n"
            for title, link in value.items():
                text += f"{title}\n{link}\n\n"
            text_list.append(text.strip())
        return text_list

    def send(self, text: str) -> None:
        u = self._webhook_url()
        if not u:
            return
        _post_feishu_open_webhook_text(u, text, self.proxy, "larkBot")


class feishuBot:
    """飞书群机器人
    https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN
    """

    def __init__(self, key, proxy_url="") -> None:
        self.key = key
        self.proxy = {"http": proxy_url, "https": proxy_url} if proxy_url else {"http": None, "https": None}

    @staticmethod
    def parse_results(results: list):
        text_list = []
        for result in results:
            (feed, value), = result.items()
            text = f"[ {feed} ]\n\n"
            for title, link in value.items():
                text += f"{title}\n{link}\n\n"
            text_list.append(text.strip())
        return text_list

    def send(self, text: str) -> None:
        u = f"https://open.feishu.cn/open-apis/bot/v2/hook/{self.key}"
        _post_feishu_open_webhook_text(u, text, self.proxy, "feishuBot")


class wecomBot:
    """企业微信群机器人
    https://developer.work.weixin.qq.com/document/path/91770
    """

    def __init__(self, key, proxy_url="") -> None:
        self.key = key
        self.proxy = {"http": proxy_url, "https": proxy_url} if proxy_url else {"http": None, "https": None}

    @staticmethod
    def parse_results(results: list):
        text_list = []
        for result in results:
            (feed, value), = result.items()
            text = f"## {feed}\n"
            for title, link in value.items():
                text += f"- [{title}]({link})\n"
            text_list.append(text.strip())
        return text_list

    def send(self, text: str) -> None:
        if not (text or "").strip():
            return
        data = {"msgtype": "markdown", "markdown": {"content": text}}
        headers = {"Content-Type": "application/json"}
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={self.key}"
        try:
            r = requests.post(url=url, headers=headers, json=data, proxies=self.proxy, timeout=30)
            r.raise_for_status()
            j = r.json()
            if j.get("errcode") == 0:
                console.print("[+] wecomBot 发送成功", style="bold green")
            else:
                console.print("[-] wecomBot 发送失败", style="bold red")
                print(j)
        except requests.RequestException as e:
            console.print(f"[-] wecomBot 发送失败: {e}", style="bold red")
        except Exception as e:
            console.print(f"[-] wecomBot 发送失败: {e}", style="bold red")


class dingtalkBot:
    """钉钉群机器人
    https://open.dingtalk.com/document/robots/custom-robot-access
    """

    def __init__(self, key, proxy_url="") -> None:
        self.key = key
        self.proxy = {"http": proxy_url, "https": proxy_url} if proxy_url else {"http": None, "https": None}

    @staticmethod
    def parse_results(results: list) -> list:
        out = []
        for result in results:
            (feed, value), = result.items()
            body = "".join(f"- [{title}]({link})\n" for title, link in value.items())
            text = f"## {feed}\n{body}\n\n <!-- Powered by Yarb. -->"
            out.append(text.strip())
        return out

    def send(self, text: str) -> None:
        if not (text or "").strip():
            return
        title = "资讯"
        m = re.match(r"^##\s+(.+?)\s*\n", text)
        if m:
            title = m.group(1).strip()[:64] or title
        data = {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
        headers = {"Content-Type": "application/json"}
        url = f"https://oapi.dingtalk.com/robot/send?access_token={self.key}"
        try:
            r = requests.post(url=url, headers=headers, json=data, proxies=self.proxy, timeout=30)
            r.raise_for_status()
            j = r.json()
            if j.get("errcode") == 0:
                console.print("[+] dingtalkBot 发送成功", style="bold green")
            else:
                console.print("[-] dingtalkBot 发送失败", style="bold red")
                print(j)
        except requests.RequestException as e:
            console.print(f"[-] dingtalkBot 发送失败: {e}", style="bold red")
        except Exception as e:
            console.print(f"[-] dingtalkBot 发送失败: {e}", style="bold red")


class telegramBot:
    """Telegram 机器人（HTTP 同步）
    https://core.telegram.org/bots/api
    """

    def __init__(self, key, chat_id: list, proxy_url="") -> None:
        self.key = key
        self.chat_ids = chat_id if isinstance(chat_id, list) else [chat_id]
        self.proxy = {"http": proxy_url, "https": proxy_url} if proxy_url else {"http": None, "https": None}

    def test_connect(self) -> bool:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{self.key}/getMe",
                proxies=self.proxy,
                timeout=15,
            )
            ok = r.status_code == 200 and r.json().get("ok")
            if not ok:
                console.print("[-] telegramBot 连接失败", style="bold red")
            return bool(ok)
        except Exception:
            console.print("[-] telegramBot 连接失败", style="bold red")
            return False

    @staticmethod
    def parse_results(results: list):
        text_list = []
        for result in results:
            (feed, value), = result.items()
            text = f"<b>{feed}</b>\n"
            for idx, (title, link) in enumerate(value.items()):
                text += f'{idx + 1}. <a href="{link}">{title}</a>\n'
            text_list.append(text.strip())
        return text_list

    def send(self, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        use_html = bool(re.search(r"<\s*[a-zA-Z]", t))
        url = f"https://api.telegram.org/bot{self.key}/sendMessage"
        rates = [Rate(20, Duration.MINUTE)]
        bucket = InMemoryBucket(rates)
        limiter = Limiter(bucket, max_delay=Duration.MINUTE.value)
        max_len = 4000
        for i in range(0, len(t), max_len):
            chunk = t[i : i + max_len]
            for cid in self.chat_ids:
                limiter.try_acquire("identity")
                try:
                    payload = {"chat_id": cid, "text": chunk}
                    if use_html:
                        payload["parse_mode"] = "HTML"
                    r = requests.post(url, json=payload, proxies=self.proxy, timeout=90)
                    if r.status_code == 200 and r.json().get("ok"):
                        console.print(f"[+] telegramBot 发送成功 {cid}", style="bold green")
                    else:
                        console.print(f"[-] telegramBot 发送失败 {cid}", style="bold red")
                        print(r.text)
                except Exception as e:
                    console.print(f"[-] telegramBot 发送失败 {cid}", style="bold red")
                    print(e)


def _bot_wants_digest(name: str, v: dict) -> bool:
    """是否参与 update_today 流式摘要：显式 ``digest`` 优先；未配置时仅 ``lark`` 默认开启（兼容旧配置）。"""
    if not isinstance(v, dict) or not v.get("enabled"):
        return False
    if "digest" in v:
        return bool(v["digest"])
    return name == "lark"


def build_digest_senders(conf: dict, proxy_url: str = "") -> List[NotifyBot]:
    """根据 ``bot`` 中各渠道的 ``enabled`` / ``digest`` 构造摘要推送目标列表。"""
    out: List[NotifyBot] = []
    for name, v in (conf.get("bot") or {}).items():
        if not _bot_wants_digest(name, v):
            continue
        if name == "lark":
            key = os.getenv(v.get("secrets") or "") or v.get("key")
            if key:
                out.append(larkBot(key, proxy_url))
        elif name == "feishu":
            key = os.getenv(v.get("secrets") or "") or v.get("key")
            if key:
                out.append(feishuBot(key, proxy_url))
        elif name == "wecom":
            key = os.getenv(v.get("secrets") or "") or v.get("key")
            if key:
                out.append(wecomBot(key, proxy_url))
        elif name == "dingtalk":
            key = os.getenv(v.get("secrets") or "") or v.get("key")
            if key:
                out.append(dingtalkBot(key, proxy_url))
        elif name == "telegram":
            key = os.getenv(v.get("secrets") or "") or v.get("key")
            if key and v.get("chat_id"):
                out.append(telegramBot(key, v["chat_id"], proxy_url))
    return out
