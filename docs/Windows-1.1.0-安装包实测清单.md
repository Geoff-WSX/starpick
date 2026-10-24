# STARPICK v1.1.0 Windows 安装包实测验收清单

安装包：`StarPick-1.1.0-setup.exe`（https://github.com/Geoff-WSX/starpick/releases/tag/v1.1.0）
本清单按测试顺序排列，每项后面标注了「如果失败意味着什么」，方便在那台 Windows 电脑上直接照做。

## 一、全新安装（干净机器，未装过 StarPick）

1. 下载后双击 `StarPick-1.1.0-setup.exe`
   - SmartScreen 蓝框提示 → 点「更多信息」→「仍要运行」（未签名，正常）
   - 安装向导应为简体中文界面（语言包已内置）
   - Per-user 安装：不需要管理员权限，默认装到 `C:\Users\<你>\AppData\Local\Programs\StarPick`
2. 完成后启动
   - 窗口标题应为「STARPICK v1.1.0」
   - WebView2 运行时：Win10/11 一般已自带；若空白窗口，需装一次 WebView2 Runtime（微软官网 "Evergreen Bootstrapper"）
3. 首次打开基本功能
   - 榜单加载出项目（首次会自动采集，无 Token 时等 10-20 分钟属正常）
   - 设置 → 「配置」页：可粘贴 GitHub Token 保存（30 次/分钟、5000/小时立即生效）
   - 设置 → 「关于」页：**版本 v1.1.0；「立即检查」点下去应显示「已是最新（v1.1.0）」** ← 本次新功能的核心验收点
   - 项目地址链接点击 → 系统浏览器打开 github.com/Geoff-WSX/starpick（新窗口/标签）
   - 任一项目的「下载版本」→ 弹下载选择器 → 点「加速下载」/「原地址」→ **系统浏览器开始下载**（js_api 桥验收）
4. 数据目录
   - `C:\Users\<你>\AppData\Roaming\StarPick`（README/setup.exe 版本资源都已确认该路径保留数据）

## 二、v1.0.0 → v1.1.0 升级路径（如果那台电脑装过旧包）

1. 直接双击新 setup.exe（AppId 相同，原地升级）
2. 装完启动：
   - 标题「STARPICK v1.1.0」
   - 旧收藏/已采集项目还在（数据目录不删）
   - 设置 → 关于 → 「立即检查」→ 因为线上最新恰好是 v1.1.0，应显示「已是最新（v1.1.0）」
     （如果显示「发现新版本 1.1.0」或「检查中…」超过 15 秒再点一次，属缓存时序，无害）

## 三、错误场景快速判读

| 现象 | 含义 | 处理 |
|---|---|---|
| 双击无反应 | WebView2 缺失或杀软拦截 | 装 WebView2 Runtime；看杀软隔离记录 |
| 白窗口 | WebView2 环境问题 | 同上 |
| 「关于」页显示「暂无法检查更新」 | 仓库短暂 404 或网络不通（github.com 直连问题不影响——检查走 api.github.com） | 点一次「立即检查」重试 |
| 采集一轮不结束 | 无 Token 匿名限流正常现象（15-30 分钟） | 配 Token 后 3-5 分钟 |
| 卸载后 `%APPDATA%\StarPick` 还在 | 设计行为（卸载不删数据） | 如需彻底清除手动删 |

## 四、CI 侧已验证项（不用再测）

- 冻结自检 `OK version=1.1.0 frozen=True`（真实 Windows 环境，Python 3.10.11 win32-x64 + PyInstaller 6.22.3）
- 调度器四任务注册含 `_app_update_job`；tzdata 1233 个文件已打包；SPA 静态托管路径正确
- 打包清单含：`frontend_dist\assets\index-CVblsoZe.css` / `index-DbPiOWLt.js`（与本机同提交构建产物逐字节同源）、`_internal\config.yaml`、Inno 简体中文语言包
- PE 版本资源：ProductName「STARPICK」/ ProductVersion 1.1.0（Windows「应用和功能」里可见此版本）
- Inno 打包日志 859 个文件全部压缩成功，无 error 级输出
