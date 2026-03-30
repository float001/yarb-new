"""任务编排：RSS → 分类 → 输出 → 机器人。"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyfiglet

from .config import load_config
from .feeds import init_rss, parseThread
from .output import update_today
from .paths import project_root, run_heading_label

from .bot import *
from .utils import Pattern, console


def init_bot(conf: dict, proxy_url: str = "") -> list:
    bots = []
    for name, v in conf.items():
        if v["enabled"]:
            if name == "lark":
                key = resolve_lark_webhook(v)
                if key:
                    bots.append(larkBot(key, proxy_url))
                continue

            key = os.getenv(v["secrets"]) or v["key"]

            if name == "telegram":
                bot = telegramBot(key, v["chat_id"], proxy_url)
                if bot.test_connect():
                    bots.append(bot)
            else:
                bot = globals()[f"{name}Bot"](key, proxy_url)
                bots.append(bot)
    return bots


def job(args: argparse.Namespace) -> None:
    print(f'{pyfiglet.figlet_format("yarb")}\n{run_heading_label()}')

    root_path = project_root()
    if args.config:
        config_path = Path(args.config).expanduser().absolute()
    else:
        config_path = root_path.joinpath("config.json")
    conf = load_config(config_path)

    proxy_rss = conf["proxy"]["url"] if conf["proxy"]["rss"] else ""
    feeds = init_rss(conf["rss"], root_path, args.update, proxy_rss)

    results = []
    if args.test:
        results.extend({f"test{i}": {Pattern.create(i * 500): "test"}} for i in range(1, 20))
    else:
        numb = 0
        tasks = []
        with ThreadPoolExecutor(100) as executor:
            tasks.extend(
                executor.submit(parseThread, conf["keywords"], url, proxy_rss) for url in feeds
            )
            for task in as_completed(tasks):
                title, result = task.result()
                if result:
                    numb += len(result.values())
                    results.append({title: result})
        console.print(f"[+] {len(results)} feeds, {numb} articles", style="bold yellow")

        update_today(results, conf)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YARB — 安全 RSS 聚合")
    parser.add_argument("--update", help="Update RSS OPML files from remote", action="store_true", required=False)
    parser.add_argument("--config", help="Path to config.json", type=str, required=False)
    parser.add_argument("--test", help="Test bot with fake data", action="store_true", required=False)
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    job(args)
