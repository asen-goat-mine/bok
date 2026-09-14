# Bok Desktop 分享版

Bok Desktop 把现有 Bok UI 和本地 Markdown 能力封装成原生桌面应用。最终用户不需要打开终端、不需要手动选择知识库，也不需要自己安装 Python。

## 用户拿到后怎么用

### macOS

1. 打开 `Bok.dmg`。
2. 把 Bok 拖进“应用程序”。
3. 双击 Bok。

### Windows

1. 双击 `Bok_*_x64-setup.exe`。
2. 按安装向导完成安装。
3. 从开始菜单打开 Bok。

第一次启动会自动生成：

- 一份干净的 Starter Vault；
- 一份独立且初始为空的 Personal Core；
- 本机索引与运行配置。

以后升级应用只替换程序文件，不覆盖用户的 Vault、Personal Core、随手记、备份和设置。

需要让 Codex 自动观察对话时，打开“记忆工作台 → 设置”，点击“一键连接 Codex”，然后新建一个 Codex 任务。仅打开 Markdown 文件夹不代表自动观察已经启用；应用会保留这条边界，不给出虚假的连接状态。

侧边栏中的“重新选择文件夹”会打开系统原生目录选择器。选择后 Bok 自动重启并记住该 Markdown Vault；原 Starter Vault 和 Personal Core 不会被删除。

## 数据位置

- macOS：系统的 Bok Application Support 目录。
- Windows：当前用户的 Bok Local App Data 目录。

应用只监听 `127.0.0.1` 的随机端口；正文、个人记忆和备份不随分享包分发。Bok 的本地服务随应用启动并在应用退出时关闭。

## 分享版内容边界

分享构建采用白名单：只复制 UI 必需文件、Bok Python 源码、干净 Starter Vault、字体和视觉资源。不会从当前私人 Vault 反向删除敏感文件后再打包。

构建前后都要运行隐私扫描，阻断以下内容：

- 本机用户绝对路径；
- 私钥与常见 API Key；
- 明确配置的用户名、GitHub 名和账号标识；
- `.bok`、Personal Core、日志、备份、缓存和现有知识卡。

## 维护者构建

- macOS：双击 `build-macos.command`。
- Windows：在 Windows PowerShell 运行 `build-windows.ps1`。
- 仅验证资源与本地服务：运行 `python3 scripts/prepare_share.py --workspace ..`，再运行 `python3 scripts/test_desktop_contracts.py`。

发布版本以 `src-tauri/tauri.conf.json` 为入口，构建前核对 Python 内核、Cargo 清单和锁文件版本一致。输出目录同时包含 `SHA256SUMS.txt`；在该目录运行 `shasum -a 256 -c SHA256SUMS.txt` 可验证文件。

GitHub 的 `Desktop packages` 工作流构建真实 macOS/Windows 安装包，并在全新运行器上验证首次启动、内核版本、后台设置、重装后数据保留和退出后服务关闭。Windows 还验证静默安装与卸载。只有这些检查成功才上传构建产物；不会自动创建公开 Release。安装测试拒绝已有 Bok 数据，只能在一次性的 GitHub 桌面运行器上执行。模型对话、原生文件选择器的交互和操作系统来源警告仍需人工验收。

macOS 对外无警告分发仍需要 Apple Developer ID 与公证；Windows 对外减少 SmartScreen 警告仍需要代码签名证书。没有证书不影响本地功能，但操作系统可能显示来源警告。
