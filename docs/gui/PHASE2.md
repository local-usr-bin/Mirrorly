# Phase 2：共享 Python application 接入准备

## Phase 2B：已完成的基础设施

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
- Phase 2B 结束时，`cmd_init`、`_Baseline` / baseline selection、scan/detect、snapshot ID allocation、完整 backup、list/verify/restore 编排全部留在 `cli.py`；Phase 2C 只继续提取了 init（见下）。配置写入仍由既有 `config.py` 实现，init 的部分副作用语义未改。
- 既有不同 CLI tasks 可引用同一 repository 的语义不变；未把 GUI 的一 Backup 一 repository 产品规则加入 core。

## 测试与开发环境

继续使用 Phase 2A 的独立 worktree 环境，禁止以 cwd / PYTHONPATH 注入掩盖错误安装。`tests/checkout_qualification.py` 和 pytest session-start qualification 保持启用；未重指向旧 Conda 环境。

既有 CLI 测试只迁移真实 implementation seam：report publication、report clock/open/replace/long-path，resolver 的卷信息查询，以及 lock enter/exit。Phase 2A 的 JSON、exit code、init partial side effect、post-complete failure、锁范围和 persisted/stdout report 断言不变。

[test_application.py](../../tests/test_application.py) 补充小范围直接调用测试：隔离子进程禁止导入 CLI，并实际组合 task resolution、repo resolution、锁和报告发布；同时验证相对路径/任务身份、选择错误、排序诊断、CLI 默认 config root，以及 relocation fact 与 CLI 通知分离。既有仓库 identity/ambiguity/serial/containment 测试继续通过 CLI adapter 到达共享实现，不复制整套测试矩阵。

Phase 2B 验证（2026-09-20，Windows；均使用已 qualification 的独立 worktree interpreter）：

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

## 当前事实：Phase 2C setup/init

在 Phase 2B `a90a0c543f3211e2fc79105a132f258ed41a33a4` 上新增 [application/setup.py](../../src/mirrorly/application/setup.py)。它同步执行原 `cmd_init` 的业务序列，不调用 CLI、input 或 UI，也不执行 backup。CLI 继续负责默认值、原确认行为、原 JSON/human 输出与退出码映射。

### Python 接口与只读事实

- `SetupRequest(task_name, source, target, config_root, filesystem_policy="strict")`：不接受 argparse，不改 task/TOML/lock identity。
- `preflight_setup(request) -> SetupPreflight`：返回 prospective repository/config 路径、输入检查是否通过、首次 filesystem 查询、mount root、通过底层检查后的含 GUID 卷信息、copy-mode approval 是否必需、首个 blocking problem。字段为空表示未成功取得该事实；不承诺穷举全部问题。
- prospective repository path 始终来自 `Path(target) / repo.REPO_DIR_NAME`，即 `<target>/MirrorlyRepo`。保留原相对路径语义；未来 GUI 用绝对目标路径调用此 Python 接口，而不是继续自行拼接。**GUI 本轮未接入**。
- preflight 不创建目录、配置、仓库或锁，不试写，不修复/迁移；不预留资源，也不保证实际执行成功。
- [repo.inspect_init_target](../../src/mirrorly/repo.py) 是本轮唯一底层提取：把原 `init_repo` 的 policy、已有 repo.json、volume/GUID 和 strict 检查分成只读 helper，同时供 preflight 和 `init_repo` 复用。原写入顺序、文件格式、底层 warn confirmation 行为不变，不复制一套初始化算法。

### 执行、确认与重新检查

`create_backup(request, copy_mode_approved=False) -> SetupResult(task, repo, config_path)` 初始化仓库并写任务配置；成功返回既有 `TaskConfig` / `RepoInfo` 和真实配置路径，不返回 GUI 状态或新的 CLI JSON。

执行始终重新运行：task name → source → config collision → containment → filesystem/approval → mount → `init_repo`（再次运行底层目标检查）→ 构造含 anchors 的 TaskConfig → 写配置。不接收 preflight 结果作为执行凭证，不增加原 init 没有的 mutation lock。

唯一审批是既有 warn + 非 NTFS 的整文件复制模式。缺少明确同意时，执行在 mount 查询之前抛出 `CopyModeApprovalRequired(volume)`，尚未进入写入。CLI 输出原警告，调用原 `_confirm()`；同意后用 `copy_mode_approved=True` 再调用服务，从头重新检查。原 `--yes`、`--json`、quiet、拒绝/非交互语义保留，`_ask()` 未改变。确认期间 source 消失或配置冲突会在第二次检查被发现，这是明确要求的重新验证，不缓存批准前的检查结果。

**既有顺序细节**：非 NTFS 确认先于 mount/已有仓库检查。完整 preflight 可以同时报告“需要批准”和较晚的 blocker；CLI 不直接把完整 preflight 的 blocker 提前输出，否则会改变既有提示/exit 6 优先级。strict 的拒绝仍位于 mount 成功后的底层目标检查。

这些查询不是一个原子 filesystem/volume 视图；每次执行重新检查并不能保证后续 IO 不失败。没有新增跨查询的卷身份授权 token 或锁。

### 部分结果及 CLI 兼容性

`SetupFailure` 保留 `stage`、原始 `cause`、prospective repository/config 路径和已成功返回的 `RepoInfo`（如果有），并提供本次调用的完成事实：

| 失败位置 | `repository_initialized` | `config_written` |
| --- | --- | --- |
| 输入、卷、挂载点检查（或纯只读 preflight） | False：本次未执行初始化 | False：未执行配置写入 |
| `init_repo` 抛错，未取得成功返回 | None：未知，可能已有目录/metadata | False |
| repo 初始化成功，配置构造失败 | True，保留 RepoInfo | False |
| 配置写入抛错 | True，保留 RepoInfo | None：未知，可能已有部分/完整 TOML |

False/None 都不是“磁盘上没有历史数据”的声明。没有回滚或自动清理 repo/config。BaseException（例如 Ctrl+C）保持既有传播行为，本阶段不提供 cancellation contract。

CLI 对 task/source 用法错误保留 exit 2；其他 setup failure 重新交给原始异常/exit-code 映射处理。既有 init 的 JSON keys/types、stdout/stderr、提示文本、repo 成功/config 失败行为保持不变；未将 SetupResult/SetupFailure 直接序列化为 CLI JSON。

[test_setup.py](../../tests/test_setup.py) 覆盖只读 inspection、路径/volume facts、blocking/approval、相对路径与 anchors、stale preflight 后的检查、批准后的重检、部分 repo/config 写入及隔离进程禁止 CLI 导入。原 CLI 测试只把 init 的卷信息/config-write fault injection 移至 setup 实现；原断言不变。未修改 Phase 2A qualification 或 Phase 2B 测试。

Phase 2C 验证（2026-09-21，Windows；独立 worktree interpreter）：

| 检查 | 结果 |
| --- | --- |
| Checkout qualification：in-process、普通/isolated subprocess、console entry point | PASS，当前 GUI worktree |
| setup/application/CLI/repo/qualification 聚焦测试 | 251 passed |
| 完整 Python regression | 642 passed；无 skip/failure（Phase 2B 612 + 新增 30） |
| Windows E2E 单独运行 | 13 passed（也包含在完整 regression 中） |
| 默认 testpaths 外 fake-worker tests | 11 passed |
| Ruff check / format check / pip check | PASS，71 Python files 格式通过 |
| diff check / 更新文档相对链接 | PASS |

源码结构对比也确认：除 init adapter 外的 CLI 函数、init 成功输出块、既有 CLI 685 条断言保持不变；repo 初始化的写入序列和限制提示文本未变。没有 desktop 输入变化，未重建 WinUI。

## 尚未实现

共享 backup transaction、生产 Python worker、真实 GUI Create Backup / Back up now、progress、cancellation、queue 和 GUI 配置持久化均未实现。权威 setup preflight 目前只存在于 Python 侧；GUI Setup 仍是 Phase 1C prototype。Phase 0–1C 文档继续作为各阶段历史记录，不能将 setup 服务理解为完整 backup 事务已提取。

O-01～O-09 保持 [OPEN](README.md#待决事项登记)，尤其 O-09 最终 Python worker packaging/distribution 未作决定。后续事务提取需要独立任务与 review；本轮不开始 Phase 2D。
