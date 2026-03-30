# yarb (Yet Another Rss Bot)

一个方便获取每日安全资讯的爬虫和推送程序。支持导入 OPML 文件，因此也可以订阅其他任何 RSS 源。

**懒人福音，每日自动更新，点击右上角 Watch 即可：[每日安全资讯](./today.md)，[历史存档](./archive)**

- [yarb (Yet Another Rss Bot)](#yarb-yet-another-rss-bot)
  - [安装](#安装)
  - [运行](#运行)
    - [本地搭建](#本地搭建)
    - [定时任务](#定时任务)
    - [Github Actions](#github-actions)
  - [订阅源](#订阅源)
  - [关注我们](#关注我们)

## 安装

```sh
$ git clone https://github.com/VulnTotal-Team/yarb.git
$ cd yarb && ./install.sh
```

应用代码位于仓库根目录下的 **`src/`** 包中，安装依赖后从仓库根目录执行模块入口即可。

## 运行

### 本地搭建

编辑配置文件 `config.json`，启用所需的订阅源和机器人（密钥类字段可通过环境变量覆盖，见 `config.example.json` 与各 bot 的 `secrets`），按需配置代理。

```sh
$ python3 -m src --help
usage: python -m src [-h] [--update] [--config CONFIG] [--test]

YARB — 安全 RSS 聚合

options:
  -h, --help       show this help message and exit
  --update         Update RSS OPML files from remote
  --config CONFIG  Path to config.json
  --test           Test bot with fake data
```

```sh
# 单次抓取并推送（默认读取仓库根目录的 config.json）
$ python3 -m src

# 指定配置文件
$ python3 -m src --config /path/to/config.json

# 从远程更新 OPML 后再跑任务
$ python3 -m src --update

# 使用假数据测试机器人推送
$ python3 -m src --test
```

### 定时任务

程序本身不包含内置定时调度。需要每日固定时间运行时，请使用系统 **crontab**、**systemd timer** 或同类方式，在预定时间执行 `python3 -m src`（并确保工作目录为仓库根目录，或配合 `--config` 指定配置路径）。

**Cron 里环境变量很少**，且**不会加载 `~/.bashrc`**，请勿依赖 `~/.bashrc` 里的 `export`。请用下面任一方式（推荐 **仓库根目录的 `run-yarb.sh` + `.env`**）：

1. **`.env` + `run-yarb.sh`（推荐）**  
   复制 `.env.example` 为 `.env`，填入密钥（已在 `.gitignore` 中）。首次执行：`chmod +x run-yarb.sh`。定时任务示例：

```cron
0 10 * * * /path/to/yarb/run-yarb.sh >> /path/to/yarb/run.log 2>&1
```

若使用 **venv**，请把 `run-yarb.sh` 最后一行里的 `python3` 改成该 venv 下的解释器路径（或在 crontab 里设置 `PATH` 使 `python3` 指向 venv）。

2. **在 crontab 里写环境变量**（写在任务行上方，同一用户生效）：

```cron
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
LARK_KEY=你的token
DEEPSEEK_API_KEY=sk-xxxx
GITHUB_TOKEN=ghp_xxxx

0 10 * * * cd /path/to/yarb && /usr/bin/python3 -m src >> /path/to/yarb/run.log 2>&1
```

3. **systemd timer / service**：在 `Environment=` 或 `EnvironmentFile=-/path/to/yarb/.env` 里配置，比 cron 更易管理环境与日志。

`config.json` 里各 bot 的 `secrets` 字段（如 `LARK_KEY`、`FEISHU_KEY`）必须与你在环境中 **export 的变量名一致**；程序启动时会读这些环境变量覆盖配置中的 `key` 等字段。

### Github Actions

利用 Github Actions 提供的服务，你只需要 fork 本项目，在 Settings 中添加 secrets，即可完成部署。

目前支持的推送渠道及对应的 **secrets**（名称需与 `config.json` 里 `bot.*.secrets` 一致，可按需调整）：

- [飞书群机器人](https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN)：`FEISHU_KEY`
- [飞书/Lark 自定义机器人（摘要等）](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)：`LARK_KEY`（字段与上方飞书群相同：`secrets` + `key`；`key` 可为完整 Webhook URL，否则为 token 并与代码内建前缀拼接）
- [企业微信群机器人](https://developer.work.weixin.qq.com/document/path/91770)：`WECOM_KEY`
- [钉钉群机器人](https://open.dingtalk.com/document/robots/custom-robot-access)：`DINGTALK_KEY`（机器人安全设置可以使用「自定义关键词」，设置为「Yarb」）
- [Telegram 机器人](https://core.telegram.org/bots/api)：`TELEGRAM_KEY`（若需代理，请在 `config.json` 的 `proxy` 中配置）

## 订阅源

推荐订阅源：

- [CustomRSS](rss/CustomRSS.opml)

其他订阅源：

- [CyberSecurityRSS](https://github.com/zer0yu/CyberSecurityRSS)
- [Chinese-Security-RSS](https://github.com/zhengjim/Chinese-Security-RSS)
- [awesome-security-feed](https://github.com/mrtouch93/awesome-security-feed)
- [安全技术公众号](https://github.com/ttttmr/wechat2rss)
- [SecWiki 安全聚合](https://www.sec-wiki.com/opml/index)
- [Hacking8 安全信息流](https://i.hacking8.com/)

非安全订阅源：

- [中文独立博客列表](https://github.com/timqian/chinese-independent-blogs)

添加自定义订阅有两种方法：

1. 在 `config.json` 中添加本地或远程仓库：

```json
{
  "rss": {
      "CustomRSS": {
          "enabled": true,
          "filename": "CustomRSS.opml"
      },
      "CyberSecurityRSS": {
          "enabled": true,
          "url": "https://raw.githubusercontent.com/zer0yu/CyberSecurityRSS/master/CyberSecurityRSS.opml",
          "filename": "CyberSecurityRSS.opml"
      },
```

2. 在 `rss/CustomRSS.opml` 中添加链接：

```opml
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
<head><title>CustomRSS</title></head>
<body>
<outline type="rss" xmlUrl="https://forum.butian.net/Rss" text="奇安信攻防社区" title="奇安信攻防社区" htmlUrl="https://forum.butian.net" />
</body>
</opml>
```

## 关注我们

[VulnTotal安全](https://github.com/VulnTotal-Team)致力于分享高质量原创文章和开源工具，包括物联网/汽车安全、移动安全、网络攻防等。

GNU General Public License v3.0

[![Stargazers over time](https://starchart.cc/VulnTotal-Team/yarb.svg)](https://starchart.cc/VulnTotal-Team/yarb)
