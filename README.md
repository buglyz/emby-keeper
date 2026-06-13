# Emby Keeper
[![Docker API (amd64)](https://github.com/buglyz/emby-keeper/actions/workflows/docker-api-amd64.yml/badge.svg)](https://github.com/buglyz/emby-keeper/actions/workflows/docker-api-amd64.yml)

一个美观、简洁、易用的图形化管理平台，用于管理和执行 Emby 服务器的模拟观看保活任务。

## 功能特性

- **图形化界面** — 现代化 Web UI，告别繁琐的命令行和 TOML 配置编辑
- **多服务器管理** — 轻松添加、编辑、删除多个 Emby 服务器
- **双认证模式** — 支持 AccessToken（推荐，更安全）和密码（一次性换取 Token，密码绝不保存）
- **一键操作** — 每个服务器独立的"登录测试"、"保活"按钮
- **自动调度** — 后台定时执行保活任务，支持自定义间隔和时间范围
- **加密存储** — Token 使用 Fernet 对称加密存储，密钥从环境变量读取
- **多平台部署** — 支持 HuggingFace Spaces、VPS Docker 一键部署

 **截图**
![截图](https://cfi.ryanvan.com/file/1781352841792_PixPin_2026-06-13_20-09-03.png)


## 快速开始

### 方式一：直接运行

```bash
# 安装依赖
pip install -e .

# 如需 Telegram 签到/抢注、OCR、MongoDB 缓存等完整功能
pip install -e ".[full]"

# 设置认证（至少配置一项）
export EK_TOKEN="your-pre-shared-token"    # 预共享 Token 登录
export EK_WEBPASS="your-admin-password"    # 密码登录

# 启动 API 服务器
embykeeperapi
```

打开浏览器访问 `http://localhost:1818` 即可使用。

### 方式二：Docker 部署

```bash
# 拉取镜像
docker pull ghcr.io/buglyz/emby-keeper/api:main

# 运行
docker run -d \
  -p 1818:1818 \
  -e EK_MODE=api \
  -e EK_TOKEN="your-token" \
  -e EK_SECRET="your-secret-key" \
  -v ./data:/app \
  ghcr.io/buglyz/emby-keeper/api:main
```

Docker 镜像默认安装完整功能依赖（Telegram 签到/抢注、OCR、MongoDB 缓存等）。如果只需要 Web UI 和 Emby 保活，可以自行构建轻量镜像：

```bash
docker build --build-arg EK_EXTRAS=none -t emby-keeper-api-core .
```

### 方式三：Docker Compose（VPS 推荐）

```bash
cd deploy
# 编辑 .env 文件设置 EK_TOKEN 和 EK_SECRET
docker compose up -d
```

数据目录挂载到宿主机 `./data`，确保持久化。

### 方式四：HuggingFace Spaces

将 `hf/` 目录推送到 HF Spaces 即可自动部署，设置 Space Secrets 中的 `EK_TOKEN`。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `EK_TOKEN` | 预共享认证 Token（用于 Web UI Token 登录） | 无 |
| `EK_WEBPASS` | 管理密码（用于 Web UI 密码登录） | 无 |
| `EK_SECRET` | Fernet 加密密钥 / JWT 签名密钥 | 自动生成 |
| `EK_BASEDIR` | 数据存储目录 | 系统默认 |
| `EK_MODE` | 运行模式：`api` / `cli`，默认启动新 Web UI | `api` |
| `EK_TRUST_PROXY` | 是否信任所有反向代理转发头（`1`/`true` 启用） | `false` |
| `EK_TRUSTED_PROXIES` | 可信反向代理 IP/CIDR，逗号分隔 | 仅本机 |
| `PORT` | API 服务端口 | `1818` |

## Web UI 使用说明

### 登录

打开页面后选择登录方式：
- **Token 登录**（推荐） — 输入 `EK_TOKEN` 环境变量设置的预共享 Token
- **密码登录** — 输入 `EK_WEBPASS` 环境变量设置的密码

### 添加服务器

点击"添加服务器"，选择认证方式：

- **Token 方式**（推荐） — 直接填写从 Emby 服务器获取的 AccessToken，全程无需密码
- **密码方式** — 填写用户名和密码，系统会一次性向 Emby 服务器换取 Token，**密码立即丢弃，绝不保存**

### 日常使用

- **仪表盘** — 查看所有服务器状态（在线/离线、Token 配置情况）
- **一键保活** — 点击"保活"按钮触发模拟观看
- **计划任务** — 查看调度状态，支持立即执行
- **抢注** — 选择 Telegram 账号、目标 Bot 和注册账号密码，发起一键抢注
- **运行历史** — 查看手动/自动任务的最近运行结果
- **全局配置** — 编辑代理、保活间隔等设置
- **自动化配置** — 管理 Telegram 自动签到站点和定时抢注 Bot
- **通知配置** — 支持 Apprise URI 和 Telegram 测试通知

### Telegram 通知

在 Web UI 的"配置 → 通知配置"中选择 Telegram，填写 Bot Token 和 Chat ID 后保存。系统会转换为 Apprise 的 Telegram URI 并加密/持久化在配置目录中。
项目不会向内置或固定的 Telegram 机器人发送通知；只有用户显式配置的 Apprise URI 或 Telegram Bot Token + Chat ID 会被使用。

也可以在 `config.toml` 中直接配置：

```toml
[notifier]
enabled = true
method = "apprise"
apprise_uri = "tgram://123456:ABCDEF/-1001234567890"
```

### 反向代理客户端 IP

登录限速依赖客户端 IP。出于安全考虑，默认只信任本机反向代理传入的 `X-Forwarded-For` / `X-Real-IP`。如果部署在外部反向代理后面，请优先配置可信代理网段：

```bash
export EK_TRUSTED_PROXIES="10.0.0.0/8,172.16.0.0/12"
```

仅在完全受控网络中才建议使用 `EK_TRUST_PROXY=1` 信任所有代理头。

## Emby API 认证说明

所有 API 请求仅使用 `X-Emby-Token` Header，**绝不包含用户密码**。

- Token 方式：用户直接提供 AccessToken，系统加密存储后使用
- 密码方式：密码仅用于向 Emby 服务器发起一次性 `/Users/AuthenticateByName` 请求换取 Token，成功后密码立即销毁，仅保存加密后的 Token

## 安全承诺

- 配置文件中不出现明文密码或 Token（Token 使用 Fernet 加密存储）
- 前端 API 响应不返回任何密码或 Token
- 后端日志不记录明文密码或 Token
- 前端密码输入框始终使用掩码显示

## 项目结构

```
emby-keeper/
├── embykeeper/          # Emby 保活核心与 CLI
├── embykeeperapi/       # FastAPI Web UI（图形化管理平台，默认入口）
│   ├── app.py           # FastAPI 应用入口
│   ├── auth.py          # JWT 认证
│   ├── config_service.py # 配置读写、备份、恢复和通知配置服务
│   ├── crypto.py        # Token 加密
│   ├── models.py        # API 数据模型
│   ├── scheduler_bridge.py  # 调度器桥接
│   ├── routers/         # API 路由
│   │   ├── auth_router.py
│   │   ├── servers.py   # 服务器 CRUD + 操作
│   │   ├── scheduler.py # 调度状态
│   │   ├── registrar.py # WebUI 一键抢注
│   │   └── config.py    # 全局配置与自动化配置
│   └── static/          # Vue 3 + Naive UI SPA（免构建，模块化）
│       ├── index.html   # 精简骨架：加载 vendor 运行时与应用模块
│       ├── css/app.css  # 前端样式
│       ├── js/
│       │   ├── util.js   # 通用工具（EK.util）
│       │   ├── api.js    # 前端 API 客户端（EK.API）
│       │   ├── router.js # AppLayout + 路由 + 启动
│       │   └── pages/    # 各页面组件（EK.pages.*）
│       └── vendor/      # Vue / Naive UI / Vue Router 本地包
├── deploy/              # 部署配置
│   └── docker-compose.yml
├── hf/                  # HuggingFace Spaces 配置
├── Dockerfile           # 多平台 Docker 构建
├── requirements.txt     # 完整运行依赖
├── requirements-core.txt # WebUI/Emby 保活核心依赖
└── requirements-*.txt   # Telegram/OCR/MongoDB 可选依赖
```

## 运行模式

| 模式 | `EK_MODE` | 入口 | 说明 |
|------|-----------|------|------|
| API | `api` | `embykeeperapi` | FastAPI 图形化管理平台，Docker 默认启动 |
| CLI | `cli` | `embykeeper` | 原始命令行工具 |

## 技术栈

- **后端**: Python + FastAPI + uvicorn
- **前端**: Vue 3 + Naive UI（CDN，无需构建）
- **认证**: JWT + Token/Password 双模式
- **加密**: cryptography (Fernet)
- **调度**: embykeeper 内置 Scheduler
- **HTTP**: curl_cffi (浏览器指纹模拟)

## HuggingFace Spaces 注意事项

HF Spaces 的文件系统在重启后会重置，配置数据会丢失。建议：
- 定期备份 `web_accounts.json`
- 需要长期保留数据时，优先使用带持久化卷的 VPS 或容器部署

VPS 部署时务必挂载卷持久化数据目录。

## 许可证

GPLv3
