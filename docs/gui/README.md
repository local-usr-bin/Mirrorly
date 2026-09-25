# Mirrorly GUI Phase 0：架构与契约入口

> Phase 0 历史快照：2026-09-19 · 源码基线 `bc8e54f54b6db2e2b3163b7c1060dc44edf1f7eb`
> 当时 GUI、worker、共享 application service 均尚未实现；下文保留该阶段的产品边界与契约提案。当前实施状态见下方更新。

**当前实施状态（2026-09-25）**：[Phase 4C GUI FIFO queue](PHASE4C.md) 已通过真实 A/B/C FIFO、托盘继续执行及 true Exit 人工验收；app-scoped coordinator 实现自动单槽 FIFO、去重与移除未开始项，worker 仍是单槽且没有队列。[Phase 4B](PHASE4B.md) 已通过人工验收：真实 Backup、Resume、重启后从 Python 发现已保存版本及准确快照目录。队列不持久化；进度、取消和持久 Activity 仍未实现；O-02 和 O-09 仍待决。

Phase 4C 后的 [Backups collection](BACKUPS_COLLECTION.md) 使 Home 的小型预览通向完整、真实的 GUI Backup 列表；Home 的最近项按已保存版本时间展示，不改变 Python 的权威选择规则。

Phase 1A–1C documents preserve their reviewed historical prototype results. [Phase 2](PHASE2.md) completed shared application extraction; the current [production contract](PRODUCTION_WORKER.md) records the implemented worker/GUI bridge. Fake worker and fixtures remain test/design infrastructure, not normal runtime data.

## 阅读顺序与状态标记

| 文档 | 内容 |
| --- | --- |
| [PRODUCTION_WORKER](PRODUCTION_WORKER.md) | 当前生产 v1 contract、gate、setup.create、backup.run、Resume 与 saved-version query |
| [PHASE4B](PHASE4B.md) | 首次真实 GUI 单 Backup 执行、结果呈现及验收 |
| [PHASE4C](PHASE4C.md) | GUI 自动 FIFO、移除、失联暂停与 Exit 队列语义 |
| [BACKUPS_COLLECTION](BACKUPS_COLLECTION.md) | Home 预览与完整 Backups 列表的当前实现边界 |
| [CONFIGURATION](CONFIGURATION.md) | O-07 已批准归属、集中路径 provider、Python task truth 与 CLI 共存 |
| [ARCHITECTURE](ARCHITECTURE.md) | GUI-ADR-001、分层、运行时、Backup/repository、Restore v1、未来目录 |
| [OPERATIONS](OPERATIONS.md) | operation/result、health、queue、progress、cancellation/Exit 状态机 |
| [IPC_CONTRACT](IPC_CONTRACT.md) | transport 推荐、版本、消息、identity、错误与故障处理草案 |
| [ACTIVITY_SETTINGS](ACTIVITY_SETTINGS.md) | 本地设置、历史、问题状态与 core truth 的关系 |
| [DESIGN_RESOURCES](DESIGN_RESOURCES.md) | 导航、视觉资源、组件、DPI/无障碍、tray/notifications |
| [MOTION](MOTION.md) | 未来真实任务开始时的一次性花瓣规范；尚未实现 |
| [DEVELOPMENT](DEVELOPMENT.md) | 稳定工具链计划、分期、验证与实施门槛 |
| [PHASE2](PHASE2.md) | 当前 Python application 提取进度、边界与兼容性验证 |

本文档统一使用以下标记；没有标成 CURRENT FACT 的接口/字段不能视为现有 API。

- **CURRENT FACT**：当前源码及已提交测试证明的行为；事实改变时先核对源码。
- **APPROVED**：本轮用户已批准的产品/架构约束，不表示已经实现。
- **PROPOSED DESIGN**：为实现已批准方向提出的具体契约，等待 review；不是新增行为的授权。
- **DEFERRED**：明确不进入 GUI 首版的功能。
- **OPEN DECISION**：尚未拍板；实现受影响部分前必须解决，不能由默认值暗中决定。

事实优先级：源码 → tests → 当前设计/项目文档 → 产品提示与历史讨论。若发生冲突，报告差异，不为迎合 GUI 修改 core。CLI release、frozen tags、版本与验收记录不受本目录改变。

## 已批准且应保持的边界

1. WinUI 3 / C# / XAML + 独立 Python worker；经版本化本地 IPC 接入未来共享 application service，再使用现有 core。WPF 只保留后备地位。
2. 一个 Backup = 一个 source root + 一个独立 repository；首版不支持多 source 或同 repo 下多个 task namespace。
3. Backup 自动 FIFO 串行队列；同一 Backup 不重复 Running/Queued，可移除未开始项。Close 到 tray、Minimize 均继续队列；真正 Exit 清空未开始项，不跨退出保存队列。
4. Restore 是 merge：默认 Skip，确认后 Replace；`older` 若保留只在 Advanced。不会删除目标额外文件；Keep both 等延期。
5. 快照 complete、CLI exit code、上次成功均不能单独推导“当前已是最新”。运行状态、提交事实、最终结果、长期问题分别表达。
6. 当前进度/取消是 capability gap；本轮仅设计契约。不得以强杀 worker 代替正常取消。
7. GUI 本地 settings/activity/issues 与 task TOML、manifest、report 分离。
8. 语义资源、独立装饰层、可调整组件和 DPI/无障碍从第一天建立；不构建通用工作流/主题插件系统。

## 待决事项登记

| ID | OPEN DECISION | 何时必须解决 |
| --- | --- | --- |
| O-01 | APPROVED policy：确认 Exit 后清空等待队列，监督当前 operation 完成再退出；v1 不支持撤回已确认 Exit | Phase 4C 已实现内存 FIFO 的清空与当前 Backup 监督，见 [production contract](PRODUCTION_WORKER.md) |
| O-02 | 取消 checkpoint、publication 不可取消区、finalizing 策略、restore 部分写入提示的详细提案 | 单独的安全关键 cancellation 任务前 |
| O-03 | APPROVED：stdio v1、无 reattach；idle 失联退出，active 非交互收尾；必需交互不可用则在该边界结束 | Phase 4A/4B 已实现 Backup Resume 的失联不可用语义与 GUI 决策入口 |
| O-04 | APPROVED：全部 application operation 共用 worker execution slot | Phase 3B 已实现 slot；不替代 GUI FIFO，不解决外部 CLI 竞争 |
| O-05 | GUI 最低 Windows build/edition、Windows 10 支持范围、首发 CPU 架构；开发工具版本组合 | 创建正式工程前确认开发目标，发布前验证完整支持矩阵 |
| O-06 | Packaged/MSIX 或 unpackaged；Python 分发形式、runtime 依赖、签名与更新方案 | Phase 1 明确开发形态；生产分发另行 review |
| O-07 | APPROVED v1：独立 per-user machine-local GUI config root；Python 独占 TOML 业务语义；不自动导入 CLI tasks | Phase 3C provider 已实现；registry/GUI preferences persistence 与显式 import 另行设计，见 [CONFIGURATION](CONFIGURATION.md) |
| O-08 | Resume 十分钟期限和 unavailable 中止已实现；`older`、legacy/非 NTFS 的正式入口仍 OPEN | 不自动同意，也不把失联转换为拒绝续传后继续 |
| O-09 | **最终 Python worker distribution / packaging strategy**：A. 保留 MSIX/package identity，使用 Windows Application Packaging Project 或经验证的其他官方多 executable 方案；B. unpackaged/self-contained WinUI + 传统 installer/deployment | 捆绑生产 Python worker、冻结 installer 或宣称可分发前；两候选均未批准，见 [架构约束](ARCHITECTURE.md#python-worker-分发与打包open-decision-o-09)；O-06 的 worker 分发问题在此具体登记 |

IPC 字段、进度阶段、活动存储限额和 source tree 都是 PROPOSED DESIGN，可在 review 中调整；这不重开已批准技术路线。Phase 0 时未附图；用户随后提供 v0，再提供 **Mirrorly GUI Visual Baseline v1**。v1 现为最高优先级视觉参考，取代 v0；不要求逐像素照抄。Phase 1B visual fidelity pass 的实际截图与回归记录见 [PHASE1B](PHASE1B.md#visual-fidelity-pass--baseline-v1)。

## Phase 0 历史范围

只新增/修改文档和入口链接；不创建 GUI 工程、C#/XAML、worker、依赖、schema 代码或 core callbacks。共享 application service 的提取、协议实现和 cooperative cancellation 均是后续独立任务。本文不授予 commit、安装、push 或发布许可。
