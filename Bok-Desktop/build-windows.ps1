param(
    [string]$Python = "python",
    [string]$Cargo = "cargo",
    [string]$Tauri = ""
)

$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Workspace = Split-Path -Parent $Project
$Version = & $Python (Join-Path $Project "scripts\release_metadata.py") --workspace $Workspace
if ($LASTEXITCODE -ne 0) { throw "Bok 发布版本不一致。" }
$Version = $Version.Trim()
$Runtime = Join-Path $env:TEMP "bok-python-3.13.15-embed-amd64"
$Output = Join-Path $Workspace "_dist\Bok-Desktop-$Version-Windows"
$PrivacyDeny = if ($env:BOK_PRIVACY_DENY) { $env:BOK_PRIVACY_DENY } else { $env:USERNAME }

& $Python (Join-Path $Project "scripts\fetch_windows_python.py") $Runtime
if ($LASTEXITCODE -ne 0) { throw "Bok Windows Python 下载或校验失败。" }
& $Python (Join-Path $Project "scripts\prepare_share.py") `
    --workspace $Workspace `
    --windows-python $Runtime `
    --deny $PrivacyDeny
if ($LASTEXITCODE -ne 0) { throw "Bok 分享资源准备失败。" }

& $Python (Join-Path $Project "scripts\test_desktop_contracts.py")
if ($LASTEXITCODE -ne 0) { throw "Bok 桌面契约测试失败。" }

Push-Location $Project
try {
    $PreviousRustFlags = $env:RUSTFLAGS
    $env:RUSTFLAGS = "--remap-path-prefix=$Project=Bok-Desktop --remap-path-prefix=$env:USERPROFILE=LOCAL_BUILD_HOME"
    if ($Tauri) {
        & $Tauri build --bundles nsis
    } else {
        & $Cargo tauri build --bundles nsis
    }
    if ($LASTEXITCODE -ne 0) { throw "Bok Windows 构建失败。" }
} finally {
    $env:RUSTFLAGS = $PreviousRustFlags
    Pop-Location
}

$Installer = Get-ChildItem (Join-Path $Project "target\release\bundle\nsis\*.exe") |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $Installer) { throw "没有找到 NSIS 安装包。" }

if (Test-Path $Output) { Remove-Item $Output -Recurse -Force }
New-Item -ItemType Directory -Path $Output | Out-Null
$Target = Join-Path $Output "Bok_${Version}_x64-setup.exe"
Copy-Item $Installer.FullName $Target
& $Python (Join-Path $Project "scripts\privacy_audit.py") $Target --deny $PrivacyDeny
if ($LASTEXITCODE -ne 0) { throw "Bok Windows 安装包隐私扫描失败。" }

& $Python (Join-Path $Project "scripts\release_metadata.py") --workspace $Workspace --checksums $Output
if ($LASTEXITCODE -ne 0) { throw "Bok 安装包校验清单生成失败。" }

Write-Host "Bok Windows 分享版已生成：$Target"
