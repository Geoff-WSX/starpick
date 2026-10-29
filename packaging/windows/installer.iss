; STARPICK Windows 安装包（Inno Setup 6）
; 产物：packaging/dist/windows/StarPick-1.0.0-setup.exe
; 构建：packaging/windows/build_windows.ps1 或 CI（.github/workflows/build-windows.yml）
; 前置：packaging/dist/windows/StarPick/（PyInstaller onedir，见 spec 构建步骤）
; 注意：Inno 指令行的行内注释不被剥离（Value 取到行尾），所有注释必须独立成行；
; 行内写 "Key=Value  ; 注释" 会把注释并进 Value（CI 实测 PrivilegesRequired 因此报
; "Values invalid"，见 Comp.SetupCompiler.SeparateDirective 的实现）。

#define MyAppName "STARPICK"
#define MyAppExeName "StarPick.exe"
#define MyAppVersion "1.3"

[Setup]
; AppId 双写 {{ 是 Inno 脚本的转义写法：落盘为 {8A7C...}，改版升级/卸载靠它匹配
AppId={{8A7C2E51-3F0B-4C9A-9B7D-6E15A2F04D3B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
; lowest 权限下 {autopf} 自动映射到 %LOCALAPPDATA%\Programs\StarPick（Per-user 无 UAC）
DefaultDirName={autopf}\StarPick
DefaultGroupName=STARPICK
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputBaseFilename=StarPick-{#MyAppVersion}-setup
OutputDir=..\dist\windows
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern
SetupIconFile=..\icon.ico
; 一键自动更新：/VERYSILENT 静默安装时先关旧实例再覆盖（AppId 相同、数据保留）
CloseApplications=yes
; 静默模式下不显示重启提示（应用无重启需求，下次启动即新版）
RestartApplications=no
VersionInfoCopyright=MIT License
VersionInfoDescription=GitHub 公开项目自动发现与下载引导工具

[Languages]
; 中文语言包不在 Inno Setup 默认安装里（官方 Unofficial 翻译），随仓库提供：
Name: "chs"; MessagesFile: "lang\ChineseSimplified.isl"

[Files]
Source: "..\dist\windows\StarPick\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前先停掉可能残留的进程（用户不关窗口直接卸载时）
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /F /T"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
; 数据目录（%APPDATA%\StarPick）默认保留：项目库属于用户数据，不随程序删除
Type: filesandordirs; Name: "{app}"
