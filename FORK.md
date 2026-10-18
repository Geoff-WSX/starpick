# 项目分叉记录（FORK）

> 2026-10-13 用户明确决定：两台电脑上的项目**分两个方向独立发展**，各自单独对话维护，互不同步（除非主动合并）。

## 基线状态

| 维度 | 🖥 Windows 旧电脑 | 💻 Mac 新电脑（本机） |
|---|---|---|
| 项目路径 | `D:\QuanWangSouSuo` | `~/Desktop/GitHubSearch/QuanWangSouSuo` |
| 代码版本 | **v13 最新版**（10/13） | **v8 迁移版**（约 10/8，由 zip 解压） |
| 数据库 schema | v13 | v8 |
| 数据 | 约 1032 项目 / 23 轮 | 634 项目 / 18 轮 |
| 品牌 | STARPICK 拾星 + logo | GitHub 好项目发现器 |
| UI | 深空星图暗色 + 日/夜切换 | 科技杂志风（浅色） |
| 特色功能 | 收藏/分组/健康度、通知中心、每日精选、更新检查+进度条、采集/更新历史独立页 | 采集中心、实时搜索、多语言 README、镜像下载、MyMemory 本地翻译（旧功能） |
| 启动脚本 | `start.bat` | `start.sh`（已建，chmod +x） |
| Python 环境 | 系统 python | `backend/.venv`（Python 3.10，FastAPI 0.143） |
| 服务 | —（旧电脑） | http://127.0.0.1:8000 运行中 |

## 约定

1. 两台电脑各自独立演进，本目录的改动**不回传** Windows，Windows 的改动**也不会自动同步**到这里。
2. 在任一电脑开新对话时，先说明用的是哪边（Windows 最新版 / Mac v8 版），避免误操作。
3. 唯一合并方式（可选）：从 Windows 重新打包 `tar` 覆盖本目录，数据库会自动从 v8 迁移到 v13（不丢数据），随后两边重新对齐；但合并后再次分叉。
4. 本机（Mac）升级到最新版的可选路径：旧电脑 PowerShell 执行
   `tar -a -c -f QuanWangSouSuo_latest.zip --exclude="node_modules" --exclude="dist" --exclude="__pycache__" --exclude=".pytest_cache" --exclude="logs" QuanWangSouSuo`

## 本机维护要点（Mac）

- 启动：`./start.sh`（或双击），停止 Ctrl+C
- 后端日志：`backend/uvicorn.log`
- 服务自动行为：距上次成功轮次 > 2 周期时启动补跑；存量 README 回填
- 无 GITHUB_TOKEN 时匿名限流（搜索 10 次/分钟），可配 `backend/.env`
