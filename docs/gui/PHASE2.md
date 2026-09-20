# Phase 2：共享 Python application 接入准备

## 当前事实：Phase 2B

以 Phase 2A `4d4fa2ff1905df11a382f9cd16dbf3505a08efcc` 为起点，仅提取四类基础设施。当前依赖方向为 CLI → application → 既有 core；application 不导入 CLI，不接受 argparse Namespace，也不输出终端消息。没有新增依赖。

| 当前模块 | 从 `cli.py` 移入的实现 | 适配 |
| --- | --- | --- |
| [tasks.py](../../src/mirrorly/application/tasks.py) | `_resolve_task_config` 的发现/加载逻辑 | `resolve_task_config(config_root, task=None)`；选择错误用 `TaskSelectionError` 保持与配置加载错误的区分 |
| [repositories.py](../../src/mirrorly/application/repositories.py) | `_resolve_repo`、`_search_anchored`、`_guid_matches`、`_path_within`、`_IdentityMismatch` | `resolve_repo(cfg)` 返回 `RepositoryResolution(repo, relocated)`；containment helper 为 `path_within`，异常为 `IdentityMismatch` |
| [locking.py](../../src/mirrorly/application/locking.py) | `_ExclusiveFileLock`、`_TaskLock`、`_RepoWriterLock`、`_LockBusy` | 共享 `TaskLock` / `RepoWriterLock` / `LockBusy`，内部基类保留；原锁路径与行为不变 |
| [reports.py](../../src/mirrorly/application/reports.py) | `_write_report`、`_ReportPublicationError` | `write_report` / `ReportPublicationError`；原文件名、temp ownership、长路径与异常清理保持不变 |

`application/__init__.py` 仅标明包边界，没有 service manager 或提前构造的事务框架。

## 兼容性边界

- CLI 的 `_resolve_task_config` 仍提供 `./.mirrorly` 默认值，按调用时 cwd 解释；application 不将相对路径改成配置文件相对路径，也不重新定义任务身份。选择错误仍映射到 CLI exit 2，配置错误仍映射到 exit 1。
- application 保留原异常诊断字符串以保护 CLI 兼容性，包括其中已有的 CLI 操作提示；这不是新 GUI error taxonomy，也不是面向 GUI 的展示 API。
- 仓库解析只返回事实。GUID、repo id、serial、containment、候选去重与拒绝规则保持不变，不自动修复配置或初始化仓库。CLI 的 `_resolve_repo` adapter 根据 `relocated` 调用原 `_notify_relocation`，原文仍输出到 stderr（包括 quiet/JSON 模式）。
- `cmd_backup` 仍在原位置按 task → repository writer 的顺序取锁，覆盖原有完整事务及 finalization/report 路径；application 锁类不决定事务范围。不新增 list/verify/restore 锁，不增加等待、重试或 queue。
- 报告 payload 仍由原命令构造。persisted report 不自动增加 `report_path`，CLI 在原位置加入 stdout JSON 的 `report_path`。complete 已发布后报告失败仍是提交后的错误，不回滚 snapshot。
- `cmd_init`、`_Baseline` / baseline selection、scan/detect、snapshot ID allocation、完整 backup、list/verify/restore 编排全部留在 `cli.py`。配置写入仍由既有 `config.py` 实现，init 的部分副作用语义未改。
- 既有不同 CLI tasks 可引用同一 repository 的语义不变；未把 GUI 的一 Backup 一 repository 产品规则加入 core。

## 测试与开发环境

继续使用 Phase 2A 的独立 worktree 环境，禁止以 cwd / PYTHONPATH 注入掩盖错误安装。`tests/checkout_qualification.py` 和 pytest session-start qualification 保持启用；未重指向旧 Conda 环境。

既有 CLI 测试只迁移真实 implementation seam：report publication、report clock/open/replace/long-path，resolver 的卷信息查询，以及 lock enter/exit。Phase 2A 的 JSON、exit code、init partial side effect、post-complete failure、锁范围和 persisted/stdout report 断言不变。

[test_application.py](../../tests/test_application.py) 补充小范围直接调用测试：隔离子进程禁止导入 CLI，并实际组合 task resolution、repo resolution、锁和报告发布；同时验证相对路径/任务身份、选择错误、排序诊断、CLI 默认 config root，以及 relocation fact 与 CLI 通知分离。既有仓库 identity/ambiguity/serial/containment 测试继续通过 CLI adapter 到达共享实现，不复制整套测试矩阵。

本轮验证（2026-09-20，Windows；均使用已 qualification 的独立 worktree interpreter）：

| 检查 | 结果 |
| --- | --- |
| Checkout qualification：in-process、普通/isolated subprocess、console entry point | PASS；均指向本 GUI worktree |
| `test_application.py` + `test_cli.py` + `test_checkout_qualification.py` | 210 passed |
| 完整 Python regression | 612 passed；无 skip/failure（Phase 2A 599 + 新增 13） |
| Windows E2E 单独运行 `tests/test_e2e.py` | 13 passed（也包含在完整 regression 中） |
| 默认 testpaths 外 `desktop/phase1a_worker/test_worker.py` | 11 passed |
| Ruff check / format check / pip check | PASS |
| `git diff --check`、更新文档相对链接检查 | PASS |

没有 C#/XAML 或 desktop project input 变化，因此未重建 WinUI。未重新设计或重新审计已发布 CLI 语义。

## 尚未实现

共享 init/backup application service、生产 Python worker、真实 GUI Create Backup / Back up now、progress、cancellation、queue 和 GUI 配置持久化均未实现。Phase 0–1C 文档继续作为各阶段历史记录，不能将本次基础设施提取理解为完整共享事务已完成。

O-01～O-09 保持 [OPEN](README.md#待决事项登记)，尤其 O-09 最终 Python worker packaging/distribution 未作决定。后续事务提取需要独立任务与 review；本轮不开始 Phase 2C。
