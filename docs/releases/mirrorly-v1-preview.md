# Mirrorly v1 Preview

Tag：`v1.0.0-preview.1` · GitHub Release 分类：**Pre-release**

Mirrorly v1 Preview 为 Windows 文件夹版本化备份带来图形界面。你可以创建备份任务、保存历史版本，并把一个完整的已保存版本恢复到指定文件夹。

本次面向 Windows 10/11 x64，提供绿色 ZIP，无需安装程序，也不需要另行安装 Python、.NET 或 Windows App Runtime。不承诺覆盖所有 Windows 版本、硬件和存储设备组合。

## 可以做什么

- 通过 Setup 选择 Source 和 Backup location，创建持久备份任务。
- 使用 Back up now 保存文件夹版本；多个待运行 Backup 按顺序执行。
- 查看任务信息、已保存 Snapshots 和未完成的备份记录。
- 从 Restore 选择完整已保存版本，检查恢复计划后执行。
- 默认跳过目标已有文件，或明确选择 Replace；不删除目标额外文件。
- 浏览普通文件形式的备份数据；在适用的 NTFS 目标上复用未变化文件的硬链接。

## 下载与启动

从 [本次官方 Release](https://github.com/local-usr-bin/Mirrorly/releases/tag/v1.0.0-preview.1) 下载 **Mirrorly-v1.0.0-preview.1-win-x64.zip**，核验 SHA-256 后，将整个 ZIP 解压，运行根目录的 **Mirrorly.exe**。

`app/` 是内部运行文件，请完整保留。GitHub 的 `Source code` ZIP/tar.gz 不是绿色运行包。

完全退出后可以整体移动程序文件夹。本机任务配置与程序目录分开保存；复制程序到另一台电脑不会自动迁移已有任务状态。

## 使用前请了解

这是 Preview 版本，请为重要数据保留独立副本，并先试做一次恢复。

不要手动修改备份仓库内部内容：多个历史版本可能通过硬链接共享文件内容。

Backup 是单向文件夹备份，不是双向同步、云备份或系统镜像，也不提供正在使用文件的时间点一致性快照。Restore 默认 Skip；选择 Replace 后，已替换文件不会在后续失败时自动回滚。

操作期间保持相关设备连接。最小化或关闭到通知区域可继续运行；完整退出请使用托盘的 Exit Mirrorly。

## 已知限制

- 暂无精确 Backup / Restore 百分比或 ETA，后续可能完善状态反馈。
- 正在运行的 Backup / Restore 暂无取消操作。
- Source 可用性按配置路径判断；重新接入设备后可使用 Refresh，暂不自动识别变化后的盘符，也不能仅凭盘符确认设备身份。
- GUI Restore 恢复完整版本，暂不提供单文件选择或直接恢复到原 Source。
- Activity 和 Settings 目前是未来功能页面。

## 签名与安全提示

Mirrorly 自有的可执行文件当前未签名。SmartScreen 或第三方安全软件可能显示未知发布者、未识别应用或信誉提示。这类提示本身不等同于恶意软件检测，也不是安全保证。

请仅从本项目官方 GitHub Release 下载并核对 SHA-256；不要全局关闭杀毒软件或其他安全防护。具体恶意软件检测或隔离需另行核实。

## SHA-256

准确的 **Mirrorly-v1.0.0-preview.1-win-x64.zip** SHA-256 将与附件一同公布在 GitHub Release 页面。请将下载文件的 SHA-256 与页面所列值核对。

## 文档与许可证

- [本版本使用说明](https://github.com/local-usr-bin/Mirrorly/blob/v1.0.0-preview.1/README.md)
- Mirrorly 自有代码：包内 `LICENSE.txt`
- 第三方组件及源码可用性：包内 `THIRD-PARTY-NOTICES.txt`
- 相关第三方条款：包内 `THIRD-PARTY-SOFTWARE-TERMS.txt`

第三方软件保留各自许可证与条款。运行或再分发相关 Microsoft 组件前，请阅读对应条款。
