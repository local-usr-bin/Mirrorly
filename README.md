# Mirrorly v1 Preview

Mirrorly 是面向 Windows 的文件夹版本化备份软件。通过简单的图形界面，你可以创建备份任务、保存文件夹的历史版本，并将一个完整的已保存版本恢复到指定文件夹。

备份数据保持为普通、可浏览的文件。在适用的 NTFS 备份盘上，未变化文件可以通过硬链接复用存储空间。

这是 **Preview** 版本。请先用可替代的数据熟悉备份和恢复流程，并为重要文件保留独立副本。

## 平台与下载

- 面向 Windows 10/11 x64，当前提供 Windows x64 绿色 ZIP。
- 本地或外置 NTFS 备份盘是主要支持路径。非 NTFS 位置如经提示确认继续，会使用完整文件复制，不能获得硬链接的空间复用。
- 不承诺覆盖所有 Windows 版本、硬件和存储设备组合。

公开版本/tag 为 **`v1.0.0-preview.1`**，GitHub Release 标题为 **Mirrorly v1 Preview**，分类为 **Pre-release**。

从 [Mirrorly v1 Preview 官方 Release](https://github.com/local-usr-bin/Mirrorly/releases/tag/v1.0.0-preview.1) 下载 **`Mirrorly-v1.0.0-preview.1-win-x64.zip`**。

下载后：

1. 对照 Release 页面公布的 SHA-256 核验 ZIP。
2. 将整个 ZIP 解压到一个文件夹。
3. 运行根目录的 **Mirrorly.exe**。

不要只提取 EXE，也不要拆散或单独移动 `app/` 内的文件。本 Preview 无需安装程序，也不需要另行安装 Python、.NET 或 Windows App Runtime。

可使用 PowerShell 查看 ZIP 的 SHA-256：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '.\Mirrorly-v1.0.0-preview.1-win-x64.zip'
```

准确的 ZIP SHA-256 随附件公布在 GitHub Release 页面。GitHub 自动提供的 `Source code` ZIP/tar.gz 是源码，不是可直接运行的绿色版。

## 快速开始

1. 打开 Mirrorly，选择 **Set up backup**。
2. 在 **Source** 中选择需要备份的文件夹。
3. 在 **Backup location** 中选择备份存放位置。不要让源文件夹与备份仓库互相包含。
4. 点击 **Continue**，检查名称和位置，然后选择 **Create backup**。
5. 返回 Home 或 Backups，点击 **Back up now**。创建任务本身不会完成首次文件备份。
6. 打开任务的 **View backup → Snapshots** 查看历史记录。只有 complete 的版本是完整保存版本；Incomplete 表示未完成的工作。
7. 要恢复文件，打开顶层 **Restore**，选择 Backup 和已保存版本，再选择恢复位置。点击 **Review restore**，核对后点击 **Restore**。

首次尝试 Restore 时，建议先在 Source 和备份仓库之外准备一个空文件夹。

Restore 恢复整个已保存版本。默认 **Skip existing** 保留目标中的同名文件；只有明确选择 **Replace existing** 才允许替换匹配的普通文件。目标中的额外文件不会被删除。当前 GUI 不提供直接恢复到原 Source 的操作。

## 三个基本概念

- **Backup（备份任务）**：保存 Source 与备份位置等配置，可重复执行。
- **Snapshot / saved version（快照／已保存版本）**：一次备份保存的历史版本。源中后来删除或修改的文件，仍可能在保留的旧版本中找到。
- **Repository（备份仓库）**：Mirrorly 管理的备份存储，包含版本数据及管理信息。

仓库中的普通文件可以浏览，也可以复制到仓库之外使用。**不要手动修改、删除或重命名仓库内部内容。** 硬链接可能让多个历史版本共享同一文件内容，直接编辑其中一份可能影响其他版本。

## 使用与安全边界

Mirrorly 做单向版本化备份，不提供双向同步、云备份或系统镜像备份。

- 不要把 Mirrorly 中的数据当作唯一副本；重要文件应另有独立备份。
- 备份不是正在运行的应用或整个文件系统的时间点一致性快照。备份重要数据库或持续写入的文件前，先让相关应用完成保存并停止写入。
- 默认变更判断会使用文件大小和修改时间，不应将其理解为每次都会重新核验所有源文件内容。
- 操作结束后检查结果及问题提示，并定期试做恢复。
- Backup / Restore 运行期间保持相关设备连接，避免关机、重启或强制结束进程。已写入或替换的恢复文件不会因后续失败自动回滚。

最小化或关闭到通知区域不会停止正在运行的操作。需要完全退出时，使用托盘菜单 **Exit Mirrorly**。

外置 Source 重新连接后，点击任务页面的 **Refresh** 更新可用状态。Mirrorly 不会在后台持续轮询 Source；若 Source 在开始备份前已不可用，本次备份会失败，不会将其当成空目录发布完整版本。

## 绿色版与本机状态

完全退出 Mirrorly 后，可以整体移动或重命名程序文件夹。请保持根目录文件与 `app/` 在一起。

任务配置保存在当前 Windows 用户的：

```text
%LOCALAPPDATA%\Mirrorly\Gui\Tasks
```

它与程序文件夹分开。移动程序不会移动 Source 或备份仓库，也不会自动改写任务中的路径。

“绿色版”表示无需安装，不代表把程序复制到另一台电脑就会自动迁移已有任务配置和历史关联。

## Preview 限制

- 长时间 Backup / Restore 暂无精确百分比或 ETA；后续可能完善状态反馈。
- 正在运行的 Backup / Restore 暂不提供取消操作。
- Source 识别以配置路径为基础，不会自动寻找变化后的盘符，也不能仅凭同一盘符确认是原来的设备。
- GUI Restore 当前按完整已保存版本恢复，不提供单文件选择。
- Activity 和 Settings 当前是未来功能页面，不提供持久操作历史或全局设置。

## 签名与安全提示

Mirrorly v1 Preview 自有的可执行文件目前未签名。Windows SmartScreen 或第三方安全软件可能显示“未知发布者”“无法识别的应用”或信誉相关提示。

这类提示本身不等同于检测到恶意软件，也不能据此证明文件安全。请仅从本项目的官方 GitHub Release 下载，并核对该 Release 公布的 SHA-256。

具体恶意软件检测或隔离应单独核实，不能一概当成普通信誉提示。不要为运行 Mirrorly 全局关闭杀毒软件或其他安全防护。

## 许可证与第三方软件

Mirrorly 自有代码采用 [MIT License](LICENSE)。

绿色包同时包含按各自许可证或条款提供的第三方软件。请阅读包内：

- `LICENSE.txt`：Mirrorly 自有代码许可证。
- `THIRD-PARTY-NOTICES.txt`：组件许可证、声明和源码可用性索引。
- `THIRD-PARTY-SOFTWARE-TERMS.txt`：相关第三方软件条款范围说明。

运行或再分发包内相关 Microsoft 组件前，请阅读对应条款。Mirrorly 的 MIT License 不会替代第三方组件的许可证。

## 历史 CLI 与开发资料

早期的 [Mirrorly CLI v0.1.0](https://github.com/local-usr-bin/Mirrorly/releases/tag/v0.1.0) 是独立的历史命令行版本，其 tag 和 Release 保持保留。本页介绍 GUI v1 Preview（`v1.0.0-preview.1`）；内部 Python/CLI package version 仍为 `0.1.0`，不与 GUI 发布版本强制同步。

CLI 命令说明见 [CLI_SPEC](docs/CLI_SPEC.md)；GUI 开发与构建资料见 [desktop README](desktop/README.md) 和 [Portable Release build](docs/gui/PORTABLE_RELEASE.md)。历史 CLI／设计文档中的能力清单不代表本 Preview 的 GUI 功能入口。
