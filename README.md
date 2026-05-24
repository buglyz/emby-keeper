# Emby Keeper

一个美观、简洁、易用的图形化管理平台，用于管理和执行 Emby 服务器的自动签到与模拟观看保活任务。

## 功能特性

- **图形化界面** — 现代化 Web UI，告别繁琐的命令行和 TOML 配置编辑
- **多服务器管理** — 轻松添加、编辑、删除多个 Emby 服务器
- **双认证模式** — 支持 AccessToken（推荐，更安全）和密码（一次性换取 Token，密码绝不保存）
- **一键操作** — 每个服务器独立的"登录测试"、"保活"、"签到"按钮
- **自动调度** — 后台定时执行保活和签到任务，支持自定义间隔和时间范围
- **加密存储** — Token 使用 Fernet 对称加密存储，密钥从环境变量读取
- **多平台部署** — 支持 HuggingFace Spaces、VPS Docker 一键部署

## 快速开始

### 方式一：直接运行

```bash
# 安装依赖
pip install -e .

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

### 方式三：Docker Compose（VPS 推荐）

```bash
cd deploy
# 编辑 .env 文件设置 EK_TOKEN 和 EK_SECRET
docker compose up -d
```

数据目录挂载到宿主机 `./data`，确持久化。

### 方式四：HuggingFace Spaces

将 `hf/` 目录推送到 HF Spaces 即可自动部署，设置 Space Secrets 中的 `EK_TOKEN`。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `EK_TOKEN` | 预共享认证 Token（用于 Web UI Token 登录） | 无 |
| `EK_WEBPASS` | 管理密码（用于 Web UI 密码登录） | 无 |
| `EK_SECRET` | Fernet 加密密钥 / JWT 签名密钥 | 自动生成 |
| `EK_BASEDIR` | 数据存储目录 | 系统默认 |
| `EK_MODE` | 运行模式：`cli` / `web` / `api` | `cli` |
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
- **一键签到** — 配置签到插件 ID 后可触发签到
- **计划任务** — 查看调度状态，支持立即执行
- **全局配置** — 编辑代理、保活间隔等设置

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
├── embykeeper/          # 核心 CLI 工具（签到、保活、Telegram 机器人）
├── embykeeperweb/       # Flask Web UI（终端控制台 + TOML 编辑器）
├── embykeeperapi/       # FastAPI Web UI（图形化管理平台，新增）
│   ├── app.py           # FastAPI 应用入口
│   ├── auth.py          # JWT 认证
│   ├── crypto.py        # Token 加密
│   ├── models.py        # API 数据模型
│   ├── scheduler_bridge.py  # 调度器桥接
│   ├── routers/         # API 路由
│   │   ├── auth_router.py
│   │   ├── servers.py   # 服务器 CRUD + 操作
│   │   ├── scheduler.py # 调度状态
│   │   └── config.py    # 全局配置
│   └── static/
│       └── index.html   # Vue 3 + Naive UI SPA
├── deploy/              # 部署配置
│   └── docker-compose.yml
├── hf/                  # HuggingFace Spaces 配置
├── Dockerfile           # 多平台 Docker 构建
└── requirements.txt
```

## 三种运行模式

| 模式 | `EK_MODE` | 入口 | 说明 |
|------|-----------|------|------|
| CLI | `cli` | `embykeeper` | 原始命令行工具 |
| Web | `web` | `embykeeperweb` | Flask 终端 + TOML 编辑器 |
| API | `api` | `embykeeperapi` | FastAPI 图形化管理平台 |

## 技术栈

- **后端**: Python + FastAPI + uvicorn
- **前端**: Vue 3 + Naive UI（CDN，无需构建）
- **认证**: JWT + Token/Password 双模式
- **加密**: cryptography (Fernet)
- **调度**: APScheduler + embykeeper Scheduler
- **HTTP**: curl_cffi (浏览器指纹模拟)

## HuggingFace Spaces 注意事项

HF Spaces 的文件系统在重启后会重置，配置数据会丢失。建议：
- 使用 MongoDB 外部存储（设置 `EK_MONGODB` 环境变量）
- 定期备份 `web_accounts.json`

VPS 部署时务必挂载卷持久化数据目录。

## 许可证

GPLv3