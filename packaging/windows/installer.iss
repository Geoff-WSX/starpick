; STARPICK 拾星 Windows 安装包（Inno Setup 6；随安装附带的 Languages/ChineseSimplified.isl 即可用）
; 产物：packaging/dist/windows/StarPick-1.0.0-setup.exe
; 构建：packaging/windows/build_windows.ps1 或 CI（.github/workflows/build-windows.yml）
; 前置：packaging/dist/windows/StarPick/（PyInstaller onedir，见 spec 构建步骤）

#define MyAppName "STARPICK 拾星"
#define MyAppExeName "StarPick.exe"
#define MyAppVersion "1.0.0"

[Setup]
; AppId 双写 {{ 是 Inno 脚本的转义写法：落盘为 {8A7C...}，改版升级/卸载靠它匹配
AppId={{8A7C2E51-3F0B-4C9A-9B7D-6E15A2F04D3B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\StarPick        ; lowest 权限下自动映射到 %LOCALAPPDATA%\Programs\StarPick
DefaultGroupName=STARPICK 拾星
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputBaseFilename=StarPick-{#MyAppVersion}-setup
OutputDir=..\dist\windows
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest               ; Per-user 安装：无 UAC 弹窗，卸载只删自己的文件
WizardStyle=modern
SetupIconFile=..\icon.ico
VersionInfoCopyright=MIT License
VersionInfoDescription=GitHub 公开项目自动发现与下载引导工具

[Languages]
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

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
