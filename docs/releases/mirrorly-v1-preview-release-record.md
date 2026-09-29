# Mirrorly v1 Preview — 发布后记录

这是在公开发布完成后创建的 **POST-RELEASE** 历史记录。`v1.0.0-preview.1` 始终固定在 `c17cb030b97d347fac3462ea00d052f923b5401d`；承载本记录的后续文档提交不属于 tagged release source 或已发布 payload，不移动 tag，也不重建或替换发布资产。

以下验证和人工验收结果汇总已完成的 release qualification 及项目负责人确认的验收证据；本轮仅重新核验公开发布身份、元数据和文档，不重新执行产品测试。发布说明保留在 [Release Notes](mirrorly-v1-preview.md)。

## 发布身份

| 项目 | 已发布值 |
| --- | --- |
| 产品 / Release 标题 | Mirrorly v1 Preview |
| 版本 | `1.0.0-preview.1` |
| Tag | `v1.0.0-preview.1` |
| Release URL | [GitHub Release](https://github.com/local-usr-bin/Mirrorly/releases/tag/v1.0.0-preview.1) |
| Release ID | `399232567` |
| Published at | `2026-09-29T14:32:53Z` |
| Tagged source commit | `c17cb030b97d347fac3462ea00d052f923b5401d` |
| Annotated tag object | `7b3cc671adf05e3de20f345a922b314e77a02213` |
| 分类 / 状态 | GitHub Pre-release；`draft = false`，`prerelease = true` |

历史 CLI `v0.1.0` Release 保留不变。历史 tag object `v0.1.0` = `4a9e62e664208d6aa68dd68ca884b934afa4bacc`，`v0.1.0-mvp` = `b0d4c807875bfa2c272b37a0ee74cf7ca0761219`，均未移动。

## 最终资产

| 项目 | 已验证值 |
| --- | --- |
| 文件名 | `Mirrorly-v1.0.0-preview.1-win-x64.zip` |
| 大小 | `107832050 bytes` |
| ZIP SHA-256 | `A18461F46BF39D9E8B2E5C3822DA975AAE93F34763EF37772B5DF26B7F4F80AF` |
| GitHub asset digest | `sha256:a18461f46bf39d9e8b2e5c3822da975aae93f34763ef37772b5df26b7f4f80af` |
| 根目录 `Mirrorly.exe` SHA-256 | `DAB780A2C31AB45DE0D677948D769F7E2D496B1BC03B9D8EB48F6549174A95D1` |
| Artifact `source_commit` | `c17cb030b97d347fac3462ea00d052f923b5401d` |
| Artifact `source_dirty` | `false` |

最终资产通过 staging/ZIP 文件清单与逐文件内容等价检查。最终 qualification 后没有重建；上传、发布及本次记录均保持同一 ZIP 字节和 launcher 身份。

## 自动化验证

完整产品验证在冻结产品基线 `66ca9038b6f0af7e09367514e6e4fed4f802cc12` 上完成；后续 launcher、分发材料和文档工作采用相应 focused release validation，未改变 Backup/Restore/worker 产品行为。

| 完整产品验证 | 结果 |
| --- | --- |
| Python full suite | 869 passed / 0 skipped / 0 failed |
| C# harness | 138 passed / 0 skipped / 0 failed |
| Ruff | PASS |
| Release x64 build | PASS；0 build warnings / 0 build errors |

| 最终 focused release validation | 结果 |
| --- | --- |
| Portable regression | 7 passed |
| Launcher tests | 5 passed |
| Redistribution artifact tests | 12 passed |
| Packaging validators | PASS |
| SHA256SUMS | 647 entries PASS |
| ZIP / staging integrity | missing = 0；extra = 0；duplicate = 0；per-file hash mismatches = 0 |
| ZIP CRC | PASS |

## Heavy-I/O 审计

Backup 最大逻辑 heavy-I/O 并发数 = **1**；Restore 最大逻辑 heavy-I/O 并发数 = **1**。

冻结生产路径审计未发现失控的多文件并发复制、并行 hash、并行 verify、Robocopy `/MT` 或无界 heavy-I/O worker pool。这是架构层面的逻辑并发结论，不保证所有存储设备在运行时都不会繁忙。

## 代表性人工验收

以下验收均为 PASS：

- Setup backup、Repository 创建、首次 Back up now 和首个 Snapshot。
- Source 修改 / 添加 / 删除后，第二次 Back up now 和第二个 Snapshot。
- Restore 首个历史 Snapshot，人工核对恢复内容；首版包含、后来删除的文件正确恢复，后来新增的文件不出现在首版 Restore 中。
- 重启与状态持久化、tray 生命周期、中文及空格路径。
- 已使用后的程序目录整体移动 / 重命名，以及迁移后重新启动。

此前还完成过较大规模的真实数据验收；此处保留结论，不展开全部测试日志或截图，也不表示已覆盖所有 Windows、硬件或存储组合。

## 许可证与再分发

Gate 6 最终审计 **PASS**。发布 ZIP 包含 Mirrorly license、第三方 notices、第三方 software terms、组件 / 版本映射、Eigen/MPL 源码可用性信息、Python incorporated notices 以及 BLAKE3/Rust runtime notices。

Focused redistribution validation 覆盖 **20 份 redistribution materials、26 个 component fingerprints、16 个 runtime crates**，全部通过；包装测试保护对应版本和材料映射。已识别义务中没有要求将 Mirrorly 自有代码改为其他许可证的 copyleft 结论。本记录汇总具体分发包的已完成审计，不提供法律意见或普遍合规保证。

## 签名、杀毒软件与信誉

Mirrorly 自有可执行文件未签名；本 Preview 有意不以取得公开代码签名证书作为发布前置条件。

| 代表性安全软件 | 验收结果 |
| --- | --- |
| Microsoft Defender Antivirus | PASS |
| 火绒 Huorong 6.0 | PASS |
| 360 | 厂商误报处理后，正常解压与 Mirrorly 使用 PASS |

360 曾将未签名 native root launcher 检测为 `Win64/Heur.Generic.H8oAYKkA`。Source/PE 审查未发现意外的恶意类产品行为；误报样本提交给 360 后，厂商回复已移除木马弹窗警告。复测确认正常使用时根 launcher 不再被阻止或隔离。

仍保留一项上游非阻塞观察：360 手动扫描可能标记 CPython 3.13.15 的 `python313.zip -> email/mime/message.pyc`。同一检测已在 python.org 官方 3.13.15 embeddable package 上复现，Mirrorly 的 `python313.zip` 与官方副本字节一致。正常解压和 Mirrorly 使用未因该项触发阻止 / 隔离，分类为已知上游手动扫描误报、non-blocking。以上仅代表已测试产品和环境，不代表所有杀毒软件兼容性。

## 真实浏览器下载与 SmartScreen 验收

发布后从官方 GitHub Release 通过浏览器下载最终资产：

- 下载 ZIP SHA-256 = `A18461F46BF39D9E8B2E5C3822DA975AAE93F34763EF37772B5DF26B7F4F80AF`。
- 解压后根 launcher SHA-256 = `DAB780A2C31AB45DE0D677948D769F7E2D496B1BC03B9D8EB48F6549174A95D1`。
- 首次执行出现 Microsoft Defender SmartScreen 未知 / 未识别应用信誉提示；用户选择 **Run anyway** 后 GUI 正常启动，tray Exit 成功。
- 同一机器随后再次启动未出现提示。这只是本次验收观察，不保证所有用户 / 系统具有相同行为。

首次 SmartScreen 提示归类为未签名新应用的预期信誉行为，而非恶意软件检测；不表示今后不会出现信誉提示，也不替代对具体安全检测的独立判断。

## 已公开的 Preview 限制

- 无精确 Backup / Restore 百分比或 ETA。
- 正在运行的 Backup / Restore 无 Cancel。
- Source / 可移动设备识别仍以配置路径为基础。
- GUI Restore 恢复完整 saved version。
- Activity / Settings 仍是未来功能页面。
- 未签名可执行文件可能触发信誉 / 未知发布者提示。

这些边界保持延期，不在本次收尾中重开。P2 visual corruption 保持 MONITORING / non-blocking；上述上游手动扫描误报同为非阻塞记录。

## 发布结论

**Mirrorly v1 Preview / v1.0.0-preview.1 已成功公开发布。** Release source commit、annotated tag、二进制资产身份、自动化验证、代表性人工验收、heavy-I/O 审计、再分发 / 许可证审计、代表性杀毒软件检查以及真实浏览器下载 / SmartScreen 验收均已记录。

没有已知剩余问题达到既定 v1 Preview release-blocker 标准。此结论不宣称软件无缺陷、不宣称穷尽所有硬件或杀毒软件组合，也不保证不会出现 SmartScreen 提示。
