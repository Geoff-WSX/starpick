# AGENTS.md — STARPICK（QuanWangSouSuo）

GitHub 公开项目手动发现与下载引导工具：用户在采集中心手动触发搜索 GitHub 高质量中文友好项目，按分类入库（SQLite），FastAPI 提供 JSON API 并托管 Vue3 前端构建产物。**采集与项目更新检查均为手动触发（2026-10-24 起），调度器只留数据库备份与应用自更新两个后台任务。**

## 技术栈

- 后端：Python 3.10+ / FastAPI / uvicorn / APScheduler / SQLite（WAL 模式，`backend/history.db`）/ requests
- 前端：Vue 3 + TypeScript + Vite（无 UI 组件库，自定义 CSS 主题变量，深空暗色为默认 + 日/夜切换）
- 前后端仅通过 JSON API 交互；生产模式下前端 `dist/` 由 FastAPI 静态托管（SPA fallback）

## 常用命令

```bash
# 一键启动（自动建 backend/.venv、装依赖、构建前端、开浏览器）
./start.sh                      # 服务地址 http://127.0.0.1:8000，停止用 pkill -f 'uvicorn app.main:app'

# 后端测试（181 项，2026-10-28 实测全绿；必须用 venv 里的 python）
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

- 仓库 `https://github.com/Geoff-WSX/starpick`（分支 `main`，已公开）。**2026-10-24 版本重置：v1.0.0~v1.3.0 均为测试版，其 Release（含资产）与 tag 已全部删除，正式版从 v1.0 起步；v1.1（2026-10-28）已发布，当前 Latest=v1.1**；tag 触发 Windows CI 自动产 setup.exe 上 Release，macOS dmg 手动补传。Windows 分支不在同一仓库（见 FORK.md），不要往这个仓库里塞 Windows 专有内容（start.bat 已删）。
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
│   ├── desktop.py       # 桌面壳：pywebview 窗口 + 程序化 uvicorn
│   ├── paths.py         # 数据/资源路径层（冻结/开发双模式）
│   └── picker.py        # 每日精选
└── tests/               # pytest 单元/契约测试（181 项）
frontend/src/
├── views/               # ProjectList（榜单+采集中心入口）、ProjectDetail（详情）
├── components/          # AppHeader/BaseSelect/CollectModal/CategoryCleanupModal/DownloadPicker/FavoritesModal/RoundsModal/SettingsModal/UpdatesModal/ProjectCard
├── composables/         # 组合式复用逻辑
├── api.ts               # 所有后端调用集中在此
└── router.ts
packaging/               # 桌面打包：spec/双平台构建脚本/Inno 配置
```

## 前端样式约定

- 全部样式集中在 `frontend/src/style.css`（组件不写 scoped style）；颜色一律走主题变量（`--accent` / `--accent-deep` / `--accent-weak` / `--on-accent` 等），日/夜两套在 `[data-theme="light"]` 下覆盖，禁止硬编码色值。
- 按钮统一 `.btn` 基底（含透明 1px 边框，保证同排按钮高度一致）+ 变体：`.dl` 实底主操作、`.ghost` 描边次级、`.prompt` 浅橙底次级（复制安装提示词专用）、`.small` 小号。
- 弹窗统一 `.modal-overlay` + `.modal` 骨架（`DownloadPicker` 用 `.picker-mask/.picker` 类名，但遮罩/圆角/动画等视觉参数已与 modal 对齐——改一处要对照另一处）。

## 分类清除（2026-10-23 落地）

「功能菜单 → 清除分类项目」批量删除存量项目（用户误采不想要的分类），动这块前先读约束：

- **匹配规则**：主分类 `category=?` **或** 附加分类数组命中——`db._category_tag_blob()` 把 JSON 数组剥成 `,A,B,` 标签串后 Python 侧精确比对（`json.dumps` 默认分隔符带空格，`LIKE` 粗筛 `%,名,%` 落空的历史 bug 就出在这；LIKE 只做预筛，**前缀同名分类（音乐 vs 音乐与阅读）靠标签串边界防误伤**）。
- **收藏永不删**：命中项目里收藏的只把命中分类从附加字段摘除（`_cleanup_category_refs`），主分类不动。
- **采集互斥**：`rounds` 表有 running → `POST /api/categories/cleanup` 返 409；预览接口 `preview-cleanup` 始终可用并带 `running` 标志（弹窗据此显示警告+禁选）。
- **不做删除前备份**：目前没有恢复入口，备份无法回滚（已试过快照剔除方案，因 `_backup_clean_snapshot` 在 `Database` 非重入锁内调 `self.backup()` 死锁而废弃）。若重做该功能，备份必须**在锁外**取快照。日常备份仍由启动/每日 3:05 定时任务维护。
- 级联清理无外键的 `health_snapshots`/`daily_picks`/`notifications` 中被删项目的行（其中 `daily_picks` 时间列叫 `created_at` 不叫 `picked_at`）。
- 测试 `tests/test_category_cleanup.py`（8 项；夹具沿用 test_favorites.py 的 monkeypatch 模式）。

## 列表自动刷新与安装包检测进度（2026-10-24 落地）

- 采集落库/安装包探测回填后列表 30s 内自动更新：`AppHeader` 的既有轮询附带 `GET /api/stats`，**三个信号任一变化**即 `window.dispatchEvent('starpick:projects-changed')` → `ProjectList.silentReload()` 静默重拉：`total_projects`（采集增删）、`assets_ready`（探测出安装包）、`assets_pending`（探测结论为「确无 release」——前两者都不动，只有它减一，漏了它卡片「检测中」态翻不掉）。
- **silentReload 的守卫**：仅第一页且无分类/关键词/新增/平台筛选时生效（翻页深处/带筛选不打扰，避免跳页）；无变化判定用签名 `id:assets数:probed位` 逐项拼接比对——**别改成 Set 包含判定**（新项目 star 少排尾页时第一页 id 完全一致但 total 已变，Set 判定会误判"无变化"，实测踩过）。
- **安装包三态**：`assets 非空`→「下载版本 · N 个」；`assets 空且 assets_probed`→只有复制提示词；`assets 空且未探测`→「安装包检测中…」标签（`.probe-chip` 样式，点击经 `starpick:open-collect` 事件由 AppHeader 打开采集中心）。`assets_probed` 已进 `_LIST_COLS` 与 `ProjectOut`，详情页主按钮同三态。
- 顶栏状态行在有未探测项目时显示「安装包检测 已完成/总数」，未配 Token 追加限速提示；全部探测完自动隐藏。
- 两个事件名 `starpick:projects-changed` / `starpick:open-collect` 是 window 级全局契约，广播端（AppHeader）与监听端要同时改。

## 平台筛选（2026-10-27 落地）

- 筛选区「平台」组：Windows/macOS/Linux/Android/iOS/Web/Docker，与分类平级可叠加，URL 参数 `platform`，再点同平台取消。
- 后端 `search_projects(platform=...)` 用 **带引号 LIKE `%"Windows"%`** 精确匹配 platforms JSON 数组——裸 LIKE 会让 Web 命中 Windows，测试锁定了该行为。
- 语言组「中文」是**中文友好口径**（`zh=1`，含简介英文但中文友好的项目，约 430 个），不是简介语言判定；「英文」才是 `lang=en`。组内严格单选（`isLangActive`）。

## 设置「关于」分组与应用自更新（2026-10-20 落地，2026-10-28 扩展）

- `SettingsModal.vue` 是**选项卡**结构（`tab`/`about`/`models` 三页，`.settings-tabs/.settings-tab` 样式在 style.css）：配置页=Token+限流，关于页=版本/检查更新/一键更新，模型中转站页=推荐列表（芯谷 AI 国内/国外，注册跳转 + 整行可点，数据硬编码前端常量 `MODEL_SOURCE_LINKS`）。新增设置项先进对应选项卡。
- `GET /api/settings` 返回 `version` / `repo_url` / `app_update` / `deployment`（`desktop`=打包壳 / `web`=源码网页服务，按 `paths.is_frozen()` 判定）；`POST /api/app/update-check` 手动触发（后台线程 + `app_update_checking` 防重入）。结果缓存在 `state.app_update`（内存态，不入库），status 取值 `none/up_to_date/newer/unknown`。
- **部署形态区分更新引导**：`deployment=web` 时「发现新版本」不显示下载安装包链接，只提示 git pull 重启；`deployment=desktop` 时显示「下载并自动更新」+「手动下载」降级链接。
- **Windows 一键自动更新（2026-10-28）**：`POST /api/app/update-download`（仅 frozen+win32 可用，返回最新 setup.exe 直链，走独立 requests 不走采集限流器）→ 前端调桌面壳桥 `pywebview.api.update_and_exit(url, name)` → `desktop.py` 下载到临时目录 → 停内嵌服务（`_stop_uvicorn`）→ `subprocess` 以 `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` 启动安装器 → 进程退出。installer.iss 已加 `CloseApplications=yes` 支持静默覆盖。**当前线上 Latest=v1.1 仍为旧安装器配置，一键更新自下版起生效。**
- `GITHUB_REPO = "Geoff-WSX/starpick"` 常量在 `backend/app/main.py`（设置页「关于」与更新检查共用）；仓库换了要同步改。
- **检查更新绝不能走 `GithubClient`/`get_latest_release`**：那个走采集限流器（匿名 60 次/小时），全量扫荡期间会排队数分钟卡死手动检查——必须保持 `_check_app_update()` 的独立 requests 请求（超时 8s，任何失败归 `unknown`）。仓库公开后匿名即可查到 Release；当前 token（`github_pat_` fine-grained）反而读不到私有仓库 Release 的历史遗留问题，公开后无所谓。
- 版本比较用 `_parse_tag_version`（v 前缀剥离 + 数字元组比较；非语义化 tag → `unknown` 并原样显示 latest_tag）。改 `APP_VERSION` 时注意：本地版本低于线上 latest Release 时，旧版安装包会真弹「发现新版本」。
- 对应测试 `backend/tests/test_app_update.py`（13 项：fake requests 模块自带异常体系给 `_check_app_update` 的 except 捕获，勿换成真 requests 异常类）。

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
- **语言切换链接三种形态都要覆盖（2026-10-28 三次踩坑）**：① Markdown `[文字](path)`；② **徽章嵌套** `[![xx](badge)](path)`（旧正则 `[^\]]*` 遇嵌套 `](` 截断，须用 `\[((?:[^\[\]]|\[[^\]]*\])*)\]`）；③ **HTML `<a href>`**（transformers 等）。三种都必须在 `resolveReadme` 转成 `data-lang` 内链，否则点击跳相对路由 `/project/i18n/...` 变空白页。标签文本要清洗（徽章/HTML 标签剥成纯文字）。
- **语言码表要含区域双码**：`README.pt-BR.md`→`ptbr`、`ja-JP`→`jajp`、`en-US`→`enus` 及非标准码（`hd` 印地语）必须进 `OTHER_LANG_CODES`，否则识别不了 → 点击跳原页面。
- **详情页默认中文**：有 `readme_zh` 的项目进入详情默认选「中文」tab（`readmeLangs` watch 中 `!userPickedLang && readmeLang==='default' && 有zh` 才切）；用户手动切换后 `userPickedLang=true` 尊重选择，重进页面恢复默认中文。
- 现状量级（2026-10 全库）：1425 个有 README 的项目里 71 个声明了中/英之外的语言，366 个语言版本入库、JSON 共约 5.5 MB。

## 配置与密钥

- 所有配置集中在 `backend/config.yaml`，改后需重启；启动时强校验，报错会定位具体配置键。
- **定时采集配置键已删除（2026-10-24）**：`interval_hours`/`full_sweep_hour`/`update_interval_hours`/`catchup_threshold_rounds` 不再存在；升级用户的旧 config.yaml 里残留这些键会被静默忽略，不要因为"键还在旧文件里"就恢复定时逻辑。现存 schedule 键仅 `discovery_created_hours`/`timezone`/`misfire_grace_seconds`。
- `GITHUB_TOKEN` 只允许两个来源：`backend/.env`（已 gitignore）或前端设置页保存的 runtime token（`backend/.runtime_token`，热生效）。**绝不写入 config.yaml、绝不入库、绝不提交**。
- 无 Token 时为匿名限流（搜索 10 次/分钟、core 60 次/小时），手动全量一轮 15-30 分钟属正常，限流器自动排队，不要为此"修复"。

## 已知陷阱

- **改前端后忘记 build**：`start.sh` 只在 `dist/index.html` 缺失时才构建；已构建过则改源码后必须手动 `npm run build`（或用 dev 模式调试）。
- **端口 8000 已占用**：`start.sh` 检测到会直接打开浏览器而不重复启动；重启服务先 `pkill -f 'uvicorn app.main:app'`。
- GitHub raw 直连常被重置：README/图片/下载链接走镜像 failover（gh-proxy.com → ghproxy.net → 直连/API 兜底），镜像前缀列表在 config.yaml `github.mirror_prefixes`，不要硬编码。
- 采集有防重入原子锁 + 启动时自动清理遗留 running 轮次；调试采集卡死先看 `logs/kazumi.log`（单行 JSON 结构化日志，5MB×5 轮转）和 `backend/uvicorn.log`。
- **笔记本休眠会让后台任务延迟**（2026-10-24 起仅剩数据库备份/应用自更新两个定时任务，采集已全手动）：macOS 休眠时 `time.monotonic()` 冻结，APScheduler 的等待线程据此计时，睡眠期间连维护性 DarkWake 也不检查任务。`_run_sleep_watchdog`（挂钟/单调钟差值判休眠）+ 剩余任务 `misfire_grace_time=None`（唤醒后 coalesce 补跑一次）兜底，改动前先读 `backend/tests/test_scheduler.py`。
- 历史上的定时采集轮次（discovery/full/full-startup 等 source）仍存在于 rounds 表与采集历史弹窗里，`RoundsModal` 的来源标签是给旧数据渲染的，不要删。
- `backend/history.db*`、`logs/`、`frontend/dist/`、`node_modules/` 均已 gitignore，不要提交或手改。
- **详情页直开/刷新必定是深色**：已修（2026-10-18 定位，2026-10-23 在 `main.ts` 挂载前读 `localStorage` 主题落地）。`index.html` 仍硬编码 `data-theme="dark"` 作为首帧兜底，属预期勿改。
- **8000 端口的服务身份要先确认再操作**：`lsof -ti tcp:8000` + `ps` 看是 `uvicorn app.main:app`（开发服务，库 `backend/history.db`）还是 `/Applications/StarPick.app/Contents/MacOS/StarPick`（桌面壳，库 `~/Library/Application Support/StarPick/history.db`）。**桌面壳检测到 8000 被占会直接连占端口的服务开窗口**——验证安装包时如果开发服务还在跑，看到的"应用"其实是开发服务的页面；测试库里插数据前先确认服务用的是哪个库文件。
- **空白库的桌面壳 ≠ 坏了**：安装包首次启动用全新空库（`~/Library/Application Support/StarPick/`），项目列表为空是正常的，第一轮采集后才有数据。
- **详情接口响应会随语言数变大**：`/api/projects/{id}` 会带上全部语言正文（supabase 41 种语言约 600 KB 未压缩；列表接口走 `_LIST_COLS` 不返回这些大字段）。改详情接口时别顺手把语言字段塞进列表接口。
- **浏览器自动化点长详情页会超时**：README 很长时 Playwright 的 `click()` 常超时（即使 `force: true`），改用 `locator.evaluate((el) => el.click())` 派发真实 DOM 点击触发 Vue 事件委托；页面里 `.markdown-body` 有多个（release 说明 + README），README 是最后一个。
- **会话顶部注入的约束可能来自另一个项目**：`~/AGENTS.md` 写的是「街头美食社交平台」（React + Ant Design + Express + Prisma + MySQL），会被当作 workspace 约束注入到本仓库的会话里。那套规则（如「后端只允许改 backend/src/」）与本仓库布局不符，**一律以本文件为准**。
- **push 失败先查代理**：`git push` 报 `SSL_ERROR_SYSCALL`/`Failed to connect to 127.0.0.1 port 7890` 时，先 `lsof -iTCP:7890 -sTCP:LISTEN` 确认代理进程、`curl -x http://127.0.0.1:7890 https://api.github.com` 探活；代理挂了就等恢复再推（提交在本地不丢），别反复重试或改代理配置。直连 `api.github.com` 始终可用，可作远端状态核验。
- **浏览器自动化点击失效**：页面内 `evaluate("el.click()")` 派发的 DOM click 偶发不触发 Vue 事件（弹窗按钮尤其），改用 Playwright 原生 `getByRole(...).click()`；弹窗里「设置」按钮只在首页 AppHeader 存在，详情页没有。
- **仓库顶层不该再出现 Windows 专有内容**：start.bat 已于 2026-10-28 删除；立项文档移入 docs/。品牌统一为 **STARPICK/starpick**，旧名「GitHub 好项目发现器」「拾星」已全仓清除，不要再引入。

## 双机分叉约定（见 FORK.md）

本机（Mac）与 Windows 旧电脑的项目**独立演进、互不同步**。不要把 Windows 分支（v13）的功能描述当作本机现状，也不要主动"对齐"两边；合并只能由用户显式发起。

## 桌面打包（packaging/，2026-10 落地）

桌面壳 = pywebview 窗口 + 程序化 uvicorn（`backend/app/desktop.py`），窗口只是本地服务的浏览器；动打包前先读这节的契约：

- **路径层是唯一入口**：所有数据/资源路径必须走 `backend/app/paths.py`（数据目录、日志、备份、config/token/.env、静态目录）。`is_frozen()` 区分双模式：开发模式的行为常量与旧版字节级一致；冻结模式数据在 macOS `~/Library/Application Support/StarPick`、Windows `%APPDATA%\StarPick`（可 `STARPICK_DATA_DIR` 覆盖）。不要再写 `Path(__file__)` 推路径（Windows 不兼容），也不要在 paths.py 之外 pack 资源探测。
- **随包资源布局与探测标记**：PyInstaller spec（`packaging/starpick.spec`，双平台共用）把前端产物放 `frontend_dist/`、默认配置放 `config.yaml`；`paths.resource_dir()` 靠这两个标记探资源根（macOS 实测在 `Contents/Frameworks`，勿硬编码）。Windows 的 zoneinfo 依赖 tzdata 数据文件（已在 requirements.txt），漏打包会在采集启动时崩。
- **自检机制（CI 冒烟与打包验收依赖）**：`--selftest` 无窗口起服务→探活→校验静态页→退出。`STARPICK_SELFTEST=1` 会关闭启动补跑采集（空库 + 真 token 的组合绝不能发真实网络请求）；`STARPICK_SELFTEST_REPORT=<path>` 把输出写文件（Windows `console=False` 的 exe 在 CI 里 stdout 是 NUL，不写文件什么都看不到）；`STARPICK_SELFTEST_WEBVIEW=1` 额外校验 GUI 栈可导入。改 desktop/main/config 三者之一要复跑 `test_desktop.py` + `test_paths.py`。
- **回环请求绝不走系统代理**：本机探测（/health、自检 GET）必须用 `desktop._urlopen_local()`（`ProxyHandler({})`）——macOS urllib 会读系统代理设置（Clash/表哥云），127.0.0.1 请求被转给代理后桌面壳会误报"启动失败"。WKWebView 自身直连回环，不受影响。
- **构建入口与产物**：macOS `./packaging/macos/build_macos.sh [版本号]` → `packaging/dist/StarPick.app` + `StarPick-<版本>-arm64.dmg`（本地构建，构建用 `packaging/.venv-build`，勿用 Anaconda base——PyInstaller 不支持）；Windows `packaging/windows/build_windows.ps1` + `installer.iss`（Inno Setup，Per-user 无 UAC）→ CI `.github/workflows/build-windows.yml`（windows-latest 自带 Inno Setup 6，产物 `StarPick-*-setup.exe` + `selftest-report.txt`；tag `v*` 触发自动传 Release；要拿 `selftest-report.txt`/setup.exe 复盘用 `gh run download <run-id>`）。本机 PyInstaller 无法交叉编译 Windows 包，Windows 产物只能 CI 出；**验证 CI 包别解 Inno**——`gh run view --log` 的 `Compressing:` 行就是完整文件清单，版本读 setup.exe 内 UTF-16 版本资源（本机 innoextract 装不上，别再折腾）。
- **版本号三处同步**：spec `VERSION`、`installer.iss` `MyAppVersion`、`app.main.APP_VERSION`（+ git tag 共四处）；改一处要同步其余（v1.0/v1.1 已两次走通全流程）。桌面壳窗口标题由 `desktop.py _window_title()` 生成（`STARPICK vX.Y.Z`）。
- **macOS dmg 要手动补传 Release**：CI 只产 Windows setup.exe；macOS 包打完要 `gh release upload vX.Y.Z packaging/dist/StarPick-<版本>-arm64.dmg`（v1.0 起一直如此，v1.1 也补传过）。用户"从 GitHub 下载安装"的前提是线上资产齐全。
- **卸载/升级保留数据**：Inno `[UninstallDelete]` 特意不删数据目录；v1.0.0→v1.1.0 实测可原地升级。

## 采集分类匹配契约（2026-10-25 修复后）

- **英文多词关键词必须短语化**：`build_category_queries` 对多词英文加引号——不加引号会被 GitHub 拆成裸词 OR（"game framework"→game OR framework），裸词 framework/engine 曾把 40% 的库错分类（tensorflow 进游戏开发、storybook 进加速工具）。
- **全量扫荡有本地复检**：搜索结果逐条过 `_category_patterns`（名称+简介+topics），不命中被搜分类的关键词即丢弃（`_run` 内 `_match_categories`）——GitHub 词干匹配的误伤（"streamed from disk" 的 MoE 引擎进影视）被这层拦下。
- **本地匹配规则**（`_english_word_pattern`）：≥5 字母英文词带词形变体（agent→agents）；≤4 字母严格词边界（rag 不得命中 storage/rags）；域代码（m3u8/yt-dlp/tvbox）子串匹配；中文关键词子串。**改动前先跑 test_collector 的复检用例**。
- **关键词表红线**：影视不放裸词 `streaming`（词干误伤 ML 项目），用 `video streaming` 短语；`开发` 收 framework/runtime/sdk 等通用开发词是有意为之（开发框架类归开发）。
- 存量库 2026-10-25 做过一次手术：327 个按关键词证据迁移 + 57 个高星人工认领（colibri→AI工具 等），无关键词证据但语义合理的保留原类——不要按「关键词不命中=删」清库。

## 延伸阅读

- `README.md` — 功能全貌、API 一览、故障排查表（2026-10-28 已重写为「适用人群优先」视角，同步现状）
- `FORK.md` — 双机分叉基线与约定
- `docs/下拉框排查经验库.md` — 前端下拉框问题排查经验
- `docs/Windows-1.1.0-安装包实测清单.md` — Windows 包人工实测验收清单（含原地升级路径与错误场景判读；文件名版本号随发布更新，留意实际存在哪个版本）
- `docs/可行性分析报告.md` / `docs/生产级优化方案.md` — 立项分析与优化路线（2026-10-28 移入 docs/；历史文档，标题仍为旧名「starpick」，含定时采集/7 分类等过时内容，仅供参考勿当真实现依据）
