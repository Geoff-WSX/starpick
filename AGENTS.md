# AGENTS.md — STARPICK 拾星（QuanWangSouSuo）

GitHub 公开项目自动发现与下载引导工具：定时搜索 GitHub 高质量中文友好项目，按分类入库（SQLite），FastAPI 提供 JSON API 并托管 Vue3 前端构建产物。

## 技术栈

- 后端：Python 3.10+ / FastAPI / uvicorn / APScheduler / SQLite（WAL 模式，`backend/history.db`）/ requests
- 前端：Vue 3 + TypeScript + Vite（无 UI 组件库，自定义 CSS 主题变量，深空暗色为默认 + 日/夜切换）
- 前后端仅通过 JSON API 交互；生产模式下前端 `dist/` 由 FastAPI 静态托管（SPA fallback）

## 常用命令

```bash
# 一键启动（自动建 backend/.venv、装依赖、构建前端、开浏览器）
./start.sh                      # 服务地址 http://127.0.0.1:8000，停止用 pkill -f 'uvicorn app.main:app'

# 后端测试（139 项，2026-10-18 实测全绿；必须用 venv 里的 python）
cd backend && .venv/bin/python -m pytest tests -q
cd backend && .venv/bin/python -m pytest tests/test_collector.py -q   # 单文件更快迭代

# 前端构建（含 vue-tsc 类型检查）——改完前端必须重新 build，后端托管的是 dist/
cd frontend && npm run build

# 前端开发模式（热更新，API 代理到 127.0.0.1:8000）
cd frontend && npm run dev      # http://localhost:5173
```

## 改动约定

- 最小局部修改，不做顺手重构；一次会话解决一个明确问题；预计要动超过 3 个文件时先给方案再动手。
- 改前端后必须 `npm run build`；改后端后跑 `pytest`；连续 2 次测试失败即停下报告，不要继续叠加改动。

## Git 与远端

- 私有仓库 `https://github.com/Geoff-WSX/starpick-mac`（分支 `main`）。Windows 分支不在同一仓库（见 FORK.md），不要往这个仓库里塞 Windows 专有内容。
- **直连 `github.com` 会被墙，但 `api.github.com` 可以直连**——现象是 `gh` 建库/查 API 一切正常、`git push` 却超时（或报 HTTP2 framing error）。本仓库 `.git/config` 已写死 `http.proxy=http://127.0.0.1:7890` 与 `http.version=HTTP/1.1`，推送/拉取前确认本机代理在运行；凭据走 `credential.helper='!gh auth git-credential'`（Token 从 gh 钥匙串取，不落盘，不要改成把 Token 写进 URL）。
- 永不提交：`history.db*`、`backend/data/`（备份目录）、`logs/`、`*.log*`、`.runtime_token`、`frontend/dist/`、`node_modules/`、`.venv/`、`*.tsbuildinfo`、编译产物 `frontend/vite.config.js` 与其 `.d.ts`。推前自查：`git ls-files | grep -Ei '\.env$|runtime_token|\.db|node_modules|dist/|\.log'` 应无输出。

## 目录结构与分层规则

三层单向依赖：**采集只写库 → API 只读库 → 前端只调 API**。不要让 API 层直接触发采集逻辑（走 `POST /api/run` + 防重入锁），不要让前端绕过 API 访问其他来源。

```
backend/
├── config.yaml          # 全部可调配置（启动强校验，非法配置拒绝启动）
├── app/
│   ├── main.py          # FastAPI 入口 + 路由 + APScheduler 调度 + 静态托管
│   ├── config.py        # 配置加载/强校验 + runtime token 热更新
│   ├── db.py            # SQLite 连接、schema 迁移、全部查询方法
│   ├── github_api.py    # GitHub 客户端：限流令牌队列/分级重试/镜像 failover
│   ├── collector.py     # 采集流水线：防重入/去重/并发抓取/进度快照
│   ├── requirements.py  # 平台推断/系统要求提取/中文检测
│   └── picker.py        # 每日精选
└── tests/               # pytest 单元/契约测试
frontend/src/
├── views/               # ProjectList（榜单+采集中心入口）、ProjectDetail（详情）
├── components/          # AppHeader/BaseSelect/CollectModal/DownloadPicker/FavoritesModal/RoundsModal/SettingsModal/UpdatesModal/ProjectCard
├── composables/         # 组合式复用逻辑
├── api.ts               # 所有后端调用集中在此
└── router.ts
```

## 前端样式约定

- 全部样式集中在 `frontend/src/style.css`（组件不写 scoped style）；颜色一律走主题变量（`--accent` / `--accent-deep` / `--accent-weak` / `--on-accent` 等），日/夜两套在 `[data-theme="light"]` 下覆盖，禁止硬编码色值。
- 按钮统一 `.btn` 基底（含透明 1px 边框，保证同排按钮高度一致）+ 变体：`.dl` 实底主操作、`.ghost` 描边次级、`.prompt` 浅橙底次级（复制安装提示词专用）、`.small` 小号。
- 弹窗统一 `.modal-overlay` + `.modal` 骨架（`DownloadPicker` 用 `.picker-mask/.picker` 类名，但遮罩/圆角/动画等视觉参数已与 modal 对齐——改一处要对照另一处）。

## 数据库迁移规则（重要）

- Schema 变更**只能**在 `backend/app/db.py` 的 `_SCHEMAS` 列表**末尾追加**新条目；版本号自动按列表顺序递增，已应用的条目绝不能改动（`schema_version` 表记录了已执行版本）。
- 迁移逐条执行 DDL（故意不用 `executescript`，避免隐式 COMMIT 破坏回滚）；新迁移必须是幂等安全的追加（`ALTER TABLE ADD COLUMN` / `CREATE TABLE IF NOT EXISTS`）。
- `Database` 内部用 `threading.Lock`（不可重入）：`_migrate` 持锁期间调用的 backfill 方法（如 `_backfill_favorite_groups`）**不得再次加锁**。
- 列表查询只取 `_LIST_COLS` 列，避免拖读 readme 等大字段；新增展示字段时同步更新 `_LIST_COLS`。

## README 多语言变体（全语言采集）

详情页的语言版本不是「抓原文 + 中文」那么简单，动这块前先读这段：

- **列的含义**：`readme`（原文）、`readme_zh`、`readme_en` 各占一列；**其它语言全部塞在 `readme_variants`**（JSON：语言键 → 正文）。空串 = 还没做过全语言采集，`'{}'` = 跑过了但确实只有中/英。中/英不重复写进 JSON。
- **语言清单来自 README 自己**：`github_api.extract_variant_links` 从 README 正文的切换链接解析真实路径（含子目录、GitHub blob 绝对链接），`variant_lang` 归一到 `zh`/`zh-Hant`/`en`/其它语言代码（`README.dev.md` 之类非语言后缀不算）。探测走**镜像 HEAD**（`_VARIANT_PROBE_WORKERS=4` 并发、`_VARIANT_MAX_LANGS=64` 上限），不消耗 API 配额；一轮之后再从抓到的变体正文补一轮新语种（中文版常比英文版多列几个语种）。
- **入库取并集**：`db._merge_variant_jsons` 让同语言以新内容为准、库内已有而本轮没采到的语言保留。镜像会偶发连接重置，**不要**把这里改成整列覆盖，否则一次抖动就丢内容。单一语言正文截断 `_VARIANT_MAX_CHARS=400_000`。
- **补齐与重探**：启动时 `Collector.backfill_variants()` 跑两类——`list_projects_missing_all_variants()`（`readme_variants=''`，迁移上线前的存量）与 `list_projects_retry_variants(cutoff)`（超过 `_VARIANT_RETRY_DAYS=30` 天没探过，全库重扫一遍兜镜像抖动和上游新增语种）。探到就盖 `ml_variants_at`、失败留空下次重试。
- **前端**：README 里的语言切换链接由 `ProjectDetail.vue` 渲染成 `data-lang`/`data-variant` 的内链（事件委托在 `onReadmeClick`），**库内有内容就直接切（零请求）**，没有才调 `GET /api/projects/{id}/readme-variant` 按需抓（返回 `truncated` 标记）。`variantLang()` 是后端 `variant_lang()` 的镜像表，**两张表要一起改**。
- 现状量级（2026-10 全库）：1425 个有 README 的项目里 71 个声明了中/英之外的语言，366 个语言版本入库、JSON 共约 5.5 MB。

## 配置与密钥

- 所有配置集中在 `backend/config.yaml`，改后需重启；启动时强校验，报错会定位具体配置键。
- `GITHUB_TOKEN` 只允许两个来源：`backend/.env`（已 gitignore）或前端设置页保存的 runtime token（`backend/.runtime_token`，热生效）。**绝不写入 config.yaml、绝不入库、绝不提交**。
- 无 Token 时为匿名限流（搜索 10 次/分钟、core 60 次/小时），一轮采集 15-30 分钟属正常，限流器自动排队，不要为此"修复"。

## 已知陷阱

- **改前端后忘记 build**：`start.sh` 只在 `dist/index.html` 缺失时才构建；已构建过则改源码后必须手动 `npm run build`（或用 dev 模式调试）。
- **端口 8000 已占用**：`start.sh` 检测到会直接打开浏览器而不重复启动；重启服务先 `pkill -f 'uvicorn app.main:app'`。
- GitHub raw 直连常被重置：README/图片/下载链接走镜像 failover（gh-proxy.com → ghproxy.net → 直连/API 兜底），镜像前缀列表在 config.yaml `github.mirror_prefixes`，不要硬编码。
- 采集有防重入原子锁 + 启动时自动清理遗留 running 轮次；调试采集卡死先看 `logs/kazumi.log`（单行 JSON 结构化日志，5MB×5 轮转）和 `backend/uvicorn.log`。
- **笔记本休眠会让定时轮次消失**：macOS 休眠时 `time.monotonic()` 冻结，APScheduler 的等待线程据此计时，睡眠期间连维护性 DarkWake 也不检查任务；历史上 `misfire_grace_time=3600` 还会把迟到超 1h 的轮次静默丢弃。现由 `_run_sleep_watchdog`（挂钟/单调钟差值判休眠）+ 采集/更新任务 `misfire_grace_time=None`（唤醒后 coalesce 补跑一次）兜底，改动前先读 `backend/tests/test_scheduler.py`。
- 定时任务用单 worker 线程池串行：唤醒后多个任务同时到期时，并行提交会让后来者撞上"已有采集任务运行中"被整轮跳过（当日全量扫荡直接丢失）。
- 排查"定时轮次没跑"先看 `/health` 的 `next_collect_at`/`next_full_at`：停在过去时间说明调度线程尚未被唤醒（休眠场景），而不是任务失败。
- `backend/history.db*`、`logs/`、`frontend/dist/`、`node_modules/` 均已 gitignore，不要提交或手改。
- **详情页直开/刷新必定是深色**：`index.html` 硬编码 `data-theme="dark"`，而主题切换按钮所在的 `AppHeader` 只在 `ProjectList.vue` 挂载——直接打开/刷新详情页会丢失日间主题且页面上无处切换（2026-10-18 已定位、未修；最小修法是在 `main.ts` 挂载前读 `localStorage` 主题）。别把它当成新引入的 bug。
- **详情接口响应会随语言数变大**：`/api/projects/{id}` 会带上全部语言正文（supabase 41 种语言约 600 KB 未压缩；列表接口走 `_LIST_COLS` 不返回这些大字段）。改详情接口时别顺手把语言字段塞进列表接口。
- **浏览器自动化点长详情页会超时**：README 很长时 Playwright 的 `click()` 常超时（即使 `force: true`），改用 `locator.evaluate((el) => el.click())` 派发真实 DOM 点击触发 Vue 事件委托；页面里 `.markdown-body` 有多个（release 说明 + README），README 是最后一个。
- **会话顶部注入的约束可能来自另一个项目**：`~/AGENTS.md` 写的是「街头美食社交平台」（React + Ant Design + Express + Prisma + MySQL），会被当作 workspace 约束注入到本仓库的会话里。那套规则（如「后端只允许改 backend/src/」）与本仓库布局不符，**一律以本文件为准**。

## 双机分叉约定（见 FORK.md）

本机（Mac）与 Windows 旧电脑的项目**独立演进、互不同步**。不要把 Windows 分支（v13）的功能描述当作本机现状，也不要主动"对齐"两边；合并只能由用户显式发起。

## 延伸阅读

- `README.md` — 功能全貌、API 一览、故障排查表（注意：第 15/91 行关于多语言 README 的描述还是「中/英」时代的写法，现状看上面那节）
- `FORK.md` — 双机分叉基线与约定
- `docs/下拉框排查经验库.md` — 前端下拉框问题排查经验
- `可行性分析报告.md` / `生产级优化方案.md` — 立项分析与优化路线（历史文档，不一定反映现状）
