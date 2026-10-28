# STARPICK

GitHub 上有几十万个免费开源的好工具，但英语界面、命令行、真假难辨的质量，把大多数人挡在了门外。**STARPICK 替你逛 GitHub**——自动发现、筛选、翻译、标注，你像刷应用商店一样点开就能用。

> **想直接用？** 去 [GitHub Releases 下载安装包](https://github.com/Geoff-WSX/starpick/releases/latest)（Windows / macOS 双平台，双击安装即可）。

## 这是给谁用的

**普通用户（不逛 GitHub、不装命令行）**
不用知道什么是仓库、release、README。打开 STARPICK 按分类浏览，点「下载」就完成——一个视频下载器、一个屏幕录制、一个音乐播放器，就这么简单。

**中文用户（受语言门槛困扰）**
项目简介、README、安装提示全部处理成中文可读；「中文」筛选一键只看中文友好的项目（约 430 个已入库）；项目自带的中/英/日等多语言 README（71 个项目、366 个语言版本）在详情页一键切换。

**访问 GitHub 不稳定的用户**
README、图片、下载直链自动走国内镜像加速（gh-proxy.com / ghproxy.net failover），大多数环节不用翻墙。

**怕下到假货的用户**
每个项目都是真实开源仓库，星级、活跃时间、更新历史一目了然，杜绝下载站捆绑安装器的套路；「清除分类项目」随时把误采的分类整体清掉。

**装机 / 家长**
电影、动漫、游戏（含汉化）、学习工具一栏排开，装机选软件直接照单挑；Windows / macOS / Linux / Android / iOS 按平台筛选，装哪台就筛哪台。

**效率党 / 极客**
在采集中心一键触发发现轮或全量搜索（可选 Star 门槛、活跃天数），新项目落库 30 秒内自动上架；支持自定义分类与关键词，调成你自己的「开源雷达」。

一句话：**你不需要会 GitHub，只需要会用「下载」这个按钮。**

## 你能用它做什么

- **按分类逛榜单**：影视 / 视频工具 / 游戏 / 游戏开发 / 开发 / AI工具 / 效率工具 / 加速工具 / 设计 / 创作 / 音乐 / 阅读 十二大类，190 个中英关键词持续召回，跨分类项目自动多标签
- **四维筛选**：分类、语言（中文/英文）、平台（Windows/macOS/Linux/Android/iOS/Web/Docker）、新增（72 小时新入库）自由叠加；输入即搜（300ms 防抖 + 回车直搜）；排序支持 Star / 最近活跃 / 最近入库
- **看得懂再下载**：每个项目标注平台支持、系统要求、中文友好度；点击「下载版本」按当前系统优先列出全部安装包（如 Kazumi 的 Windows msix/zip、macOS dmg、Android apk 共 9 个版本），镜像加速 + 原地址双通道
- **站内看详情**：完整 README 原文不跳转 GitHub，多语言一键切换，图片走镜像；安装包检测进度实时可见（顶栏 N/M + 卡片「检测中」状态）
- **收藏与推荐**：一键收藏自动按分类归档；每日精选榜单；Star 翻倍自动再推荐
- **自己掌控采集**：手动触发、按分类采、可选阈值；列表/筛选/搜索状态全部同步 URL，刷新分享不丢

## 桌面版安装包（推荐）

**下载地址：[GitHub Releases 最新版](https://github.com/Geoff-WSX/starpick/releases/latest)**

| 平台 | 下载文件 | 说明 |
|------|----------|------|
| Windows 10/11 (x64) | `StarPick-1.1-setup.exe` | 双击安装，Per-user 无需管理员 |
| macOS (Apple Silicon, 11+) | `StarPick-1.1-arm64.dmg` | 拖入 Applications |

- **数据与日志**：macOS `~/Library/Application Support/StarPick/`；Windows `%APPDATA%\StarPick\`。卸载不删数据
- **升级**：设置 → 关于 →「立即检查」，有新版本自动提示（每 6 小时检查一次）；原地升级数据保留
- **首次打开的安全提示**（未签名公证属预期）：
  - macOS：右键 → 打开，或 系统设置 → 隐私与安全性 → 仍要打开
  - Windows：SmartScreen 点「更多信息 → 仍要运行」
- **GITHUB_TOKEN**：设置里填入即热生效（存数据目录，不落盘、不入库）。未配置时限流较严（一轮采集 15-30 分钟），配置后约 3-5 分钟

## 快速开始（macOS 开发模式）

```bash
./start.sh   # 自动建 venv、装依赖、构建前端、启动并打开浏览器
```

手动启动：`backend` 装依赖 → `frontend` 构建 → `python -m uvicorn app.main:app`，访问 http://127.0.0.1:8000。

> 本仓库为 macOS 分支（与 Windows 旧电脑上的分支独立演进，基线见 FORK.md）。

## 配置

集中在 `backend/config.yaml`（修改后重启生效，非法配置拒绝启动）：

| 配置块 | 关键项 | 说明 |
| --- | --- | --- |
| `server` | `host` / `port` | 仅本机访问默认 127.0.0.1:8000 |
| `schedule` | `discovery_created_hours` / `timezone` | 发现轮回看窗口与时区（采集均为手动触发） |
| `github` | `token_env` / `search_rate_per_minute` / `request_timeout` / `retry_max` / `mirror_enabled` / `mirror_prefixes` | 配额、超时、重试、镜像列表 |
| `filter` | `min_stars`(200) / `max_inactive_days`(90) / `zh_weight` / `require_zh` | 筛选阈值 |
| `dedup` | `re_recommend_on_major_update` / `rounds_retention` | 去重与再推荐策略 |
| `categories` | 分类名 → 关键词列表 | 决定搜索范围，可自由增删 |

Token 只放 `backend/.env`（已 gitignore）或设置页保存，绝不写配置文件、绝不入库。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（服务状态 / 版本 / 最近轮次 / 是否配置 Token） |
| GET | `/api/projects` | 项目列表（`category` / `keyword` / `lang=zh|en` / `new=new` / `zh=1` / `platform=Windows` / `page` / `page_size` / `sort=stars|recent|new`） |
| GET | `/api/projects/{id}` | 项目详情（README 原文、多语言变体、安装包 assets） |
| GET | `/api/categories` | 分类列表 + 各分类项目数 |
| GET | `/api/collect/status` | 采集进度快照（前端轮询） |
| GET | `/api/rounds` / `/api/update-rounds` | 采集 / 更新检查轮次记录 |
| GET | `/api/stats` | 统计（总数 / 安装包就绪 / 待检测数 / 轮次状态） |
| POST | `/api/run` | 手动触发一轮采集（防重入） |
| POST | `/api/updates/run` | 手动触发增量更新检查 |
| POST | `/api/categories/cleanup` | 按分类清除存量项目（收藏保留，采集运行中拒绝） |

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 页面打开但无项目 | 功能菜单 → 采集中心 →「立即采集」触发首轮（首次 15-30 分钟属正常，配 Token 后 3-5 分钟） |
| 卡片只有「复制安装提示词」 | 安装包还在后台检测中——顶栏看「安装包检测 N/M」进度，完成会自动出现「下载版本」；未配 Token 时限速较慢 |
| 日志 `403 rate limit` / `触发配额上限` | 未认证配额耗尽；限流器自动排队，等整点重置或配置 GITHUB_TOKEN |
| 详情页 README 为空 | 首次采集后自动全库回填补抓，重启后等几分钟 |
| 采集失败但服务正常 | 单轮失败不影响服务，失败原因见「采集中心」轮次表 |
| 端口被占用 | 桌面壳自动顺延端口；开发模式改 `config.yaml` 的 `port` |

日志：`logs/kazumi.log`（5MB × 5 轮转，单行 JSON 结构化）。

## 架构

```
┌─────────────┐    JSON API    ┌──────────────┐
│  Vue3 前端   │ ────────────▶ │  FastAPI     │
│  (dist 静态) │ ◀──────────── │  (uvicorn)   │
└─────────────┘                └──────┬───────┘
                                      │
                    ┌─────────────────┼───────────────────┐
                    │                 │                   │
              ┌─────▼─────┐    ┌──────▼──────┐    ┌───────▼──────┐
              │  采集中心   │───▶│  Collector  │───▶│   SQLite     │
              │ (手动触发)  │    │ 采集流水线   │    │ (WAL 模式)   │
              └───────────┘    └──────┬──────┘    └──────────────┘
                                      │
                            ┌─────────▼─────────┐
                            │  GitHub API 客户端 │
                            │ 限流/重试/镜像failover│
                            └───────────────────┘
```

三层单向依赖：采集只写库 → API 只读库 → 前端只调 API。语言标记（zh/en）入库时按简介判定；180 项单元/契约测试。

## 目录结构

```
QuanWangSouSuo/
├── start.sh                # 一键启动（macOS）
├── backend/
│   ├── config.yaml         # 全部可调配置
│   ├── requirements.txt    # 生产依赖
│   ├── app/
│   │   ├── main.py         # FastAPI 入口 + 路由 + 调度 + 静态托管
│   │   ├── config.py       # 配置加载与强校验
│   │   ├── db.py           # SQLite（WAL + 迁移）
│   │   ├── github_api.py   # GitHub 客户端（限流/重试/镜像 failover）
│   │   ├── requirements.py # 平台推断/系统要求/中文检测
│   │   ├── collector.py    # 采集流水线（防重入/去重/并发/进度）
│   │   ├── desktop.py      # 桌面壳（pywebview + 程序化 uvicorn）
│   │   └── paths.py        # 数据/资源路径层（冻结/开发双模式）
│   └── tests/              # 180 项单元/契约测试
├── frontend/
│   ├── src/                # Vue3 + TS 源码（列表 + 详情）
│   └── dist/               # 构建产物（由后端托管）
├── packaging/              # 桌面打包（macOS 构建脚本 / Windows CI / Inno 配置）
└── logs/                   # 运行日志
```

## 开发命令

```bash
# 后端测试（180 项）
cd backend && python -m pytest tests -q

# 前端构建（改完前端必须 build，后端托管的是 dist/）
cd frontend && npm run build

# 前端开发模式（热更新，代理 API 到 8000）
cd frontend && npm run dev   # http://localhost:5173
```

## License

MIT
