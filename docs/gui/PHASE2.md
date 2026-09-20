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

## Phase 2B 兼容性边界（该阶段记录）

- CLI 的 `_resolve_task_config` 仍提供 `./.mirrorly` 默认值，按调用时 cwd 解释；application 不将相对路径改成配置文件相对路径，也不重新定义任务身份。选择错误仍映射到 CLI exit 2，配置错误仍映射到 exit 1。
- application 保留原异常诊断字符串以保护 CLI 兼容性，包括其中已有的 CLI 操作提示；这不是新 GUI error taxonomy，也不是面向 GUI 的展示 API。
- 仓库解析只返回事实。GUID、repo id、serial、containment、候选去重与拒绝规则保持不变，不自动修复配置或初始化仓库。CLI 的 `_resolve_repo` adapter 根据 `relocated` 调用原 `_notify_relocation`，原文仍输出到 stderr（包括 quiet/JSON 模式）。
- `cmd_backup` 仍在原位置按 task → repository writer 的顺序取锁，覆盖原有完整事务及 finalization/report 路径；application 锁类不决定事务范围。不新增 list/verify/restore 锁，不增加等待、重试或 queue。
- 报告 payload 仍由原命令构造。persisted report 不自动增加 `report_path`，CLI 在原位置加入 stdout JSON 的 `report_path`。complete 已发布后报告失败仍是提交后的错误，不回滚 snapshot。
- Phase 2B 结束时，`cmd_init`、`_Baseline` / baseline selection、scan/detect、snapshot ID allocation、完整 backup、list/verify/restore 编排全部留在 `cli.py`；后续 Phase 2C/2D 的范围见下。配置写入仍由既有 `config.py` 实现，init 的部分副作用语义未改。
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

## Phase 2C setup/init

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

## Phase 2D 共享 Backup 事务

以 Phase 2C `37efaa0050821fa5eb112816dbec333d5f65465e` 为起点，新增 [application/backup.py](../../src/mirrorly/application/backup.py)，移入原 `cmd_backup` 的完整业务事务。它是 CLI 与未来 worker 共用的唯一 Backup 编排；底层算法没有复制或改写。没有新增依赖。

### 移动范围与 Python API

| 原 `cli.py` 实现 | 当前所有者 |
| --- | --- |
| `_Baseline`、`_select_baseline` | `application.backup` 同名私有 helper；原生命周期/续传选择规则 |
| `_scan_and_detect` | `application.backup` 同名 helper；只将 Namespace 换为普通 request |
| `_snapshot_id_free`、`_new_snapshot_id` | `application.backup` 同名 helper；原 clock/sequence/UUID/collision 行为 |
| `cmd_backup` dry-run / real transaction | `application.backup.run_backup`；CLI 仅适配输入、交互、输出、exit mapping |

`BackupRequest(config_root, task=None, dry_run=False, full_hash=False, exclude=())` 使用普通 Python 值。config root 必须由调用者提供；CLI 仍传原 `./.mirrorly` 默认值。相对路径、配置文件选择身份与 `TaskConfig.name`/锁身份的区别不变；不同 CLI task 可以指向同一 repo。

`run_backup(request, *, decide_resume=None, on_relocation=None, on_resume=None)` 同步返回 `DryRunResult` 或 `BackupResult`，普通异常包装为 `BackupFailure(stage, cause, facts)`。`BaseException`（含 KeyboardInterrupt）直接传播并按原 context manager 释放锁，不被解释为取消 API。

- `DryRunResult(facts, changes, scan_skipped)`：只读分析；无 task/repo 锁、migration、sequence reservation、cleanup、manifest/snapshot 写入、retention 或 report publication。真实执行重新加载配置、解析仓库和扫描，不接收该结果作为执行依据。
- `BackupResult(facts, report, duration_seconds)`：返回成功完成整个事务的事实与原 report payload。`has_issues` 基于真实 skipped items，不能把 complete 当成无问题或 source 当前 up to date。report 不含 CLI 的 `report_path`；CLI 复制 payload 后加入该字段，保持原 JSON keys/types/order。duration 保存原未取整值供 CLI human output；persisted report 仍保留原三位小数。
- `BackupFacts`：已解析的 TaskConfig/RepoInfo、relocation、成功返回的 lifecycle sequence、snapshot id、commit state、resumed-from、ChangeSet、scan skipped、SnapshotResult、构造出的 Manifest、成功返回的 retention deleted 列表和 report path。未取得的字段为 None；不通过解析异常文本猜测事实。
- `BackupFailure`：保留原异常对象与失败阶段及上述已知事实；不是项目级 error taxonomy，也不直接成为 CLI/IPC JSON schema。

### Commit 边界与失败事实

| `facts.commit_state` | 本次调用已知的事实 |
| --- | --- |
| `not_published` | 尚未调用 complete publisher；可能已有迁移、sequence、incomplete 或 snapshot 文件等副作用，不代表 rollback |
| `unknown` | complete `write_manifest` 已进入但未成功返回；即使磁盘可能已发布，也不猜测提交结果 |
| `published` | complete `write_manifest(repo, complete_manifest)` 已成功返回；不等于后续 finalization/report 成功 |

`mark_complete` 是构造候选值；完成构造后、进入 publisher 前才设 unknown，publisher 返回后立即设 published。facts 中 complete candidate 的 `status` 本身不能作为 commit 证据。

已 published 后，resumed-incomplete cleanup、retention plan/apply、mandatory report 仍可能失败。服务保留实际 snapshot identity、manifest stats、scan/materialization 结果和原始异常，不回滚 complete。retention apply 未返回时 `retention_deleted=None`，不声称删除了零个或精确猜测部分删除数；report 未返回时 `report_path=None` 同样不是“肯定没有落盘”的断言。

CLI 保留原异常/退出码映射。在 report stage 收到 `ReportPublicationError` 时，仍输出原“快照已成功提交，但必需报告发布失败”的错误文本。complete publication 本身抛错仍沿用原错误文本/exit 1，不向 CLI 用户虚构未提交或新增 JSON 格式。

### 原事务顺序和锁范围

task/config resolution → repository resolution → 原 relocation notice（如有）

真实执行：TaskLock → RepoWriterLock → migration → baseline/resume selection → scan/change detection → owned temp residue cleanup → durable lifecycle sequence reservation → unused snapshot ID allocation → incomplete manifest publication → snapshot materialization → final manifest construction → hash-coverage guard → complete manifest publication → resumed-incomplete discard → retention plan → retention apply → mandatory report publication → release RepoWriterLock → release TaskLock。

两把锁仍覆盖原有全部 mutation 和 finalization/report 路径。sequence 可以在后续失败时形成 gap；不复用、不回滚。baseline/legacy/resume 信任规则、verify-on-write 切换和 complete hash-coverage guard 仍由原代码和 core primitive 执行。list/verify/restore 的编排与锁策略均未移动。

### 仅限原有 resume/relocation 的同步适配

已有 resume 询问发生在 migration 之后、scan 之前，并且位于两把锁内。`ResumeDecision(snapshot_id, created_at)` 交给同步 `decide_resume`；CLI 在该位置调用原 `_ask()`，保留 prompt、`--yes`、JSON/非交互及默认回答语义。不调用 `_confirm()` 替代它。不提供 callback 时抛出 `ResumeDecisionRequired`（作为 BackupFailure 的 cause），不会默默决定续传或拒绝。

`on_resume(ResumeNotice)` 仅携带原 available/selected/declined 及已物化/缺失/不可信/未认证数量；CLI 将其转换为原文本，通过 `_info` 保持 quiet/JSON/stdout/stderr 规则。`on_relocation(cfg, repo)` 仅把原 relocation fact 交给 CLI `_notify_relocation`。这两个窄通知接口是为了保留诊断在后续错误之前的原输出时机，**不是 phase/file/byte progress 或 worker event stream**。

已有拒绝续传文案说“从头开始”，实际代码仍可能选用 sequenced complete baseline；本轮保留这一既有语义和文案，不做 CLI UX 清理。未来 worker 必须另行处理锁内同步 resume 决策和断连；本轮未设计 IPC 交互或 cancellation。

### 验证范围

[test_backup_application.py](../../tests/test_backup_application.py) 直接调用 dry-run/real transaction，覆盖只读预览和重新扫描、success/issues、pre-complete 副作用与 sequence gap、complete publisher 在落盘前/后抛错均 unknown、known post-commit failure facts、完整事务/锁顺序、resume decision、relocation timing、Ctrl+C、文件名/锁身份以及 isolated subprocess 禁止 CLI import。

既有 CLI tests 的 patch 移至 `application.backup` 的真实调用位置，包括跨进程 writer-lock barrier 内的 `write_snapshot`，clock/ID、resume/scan/detect、manifest、retention。原 verify/setup/report/lock 底层 patch 保持其真实所有者。原 CLI **685 条 assert 的 AST 完全不变**；其余现存 CLI 函数（含 init/list/verify/restore/main/_ask/_confirm）AST 未变。snapshot ID helpers、baseline type、scan/detect 算法及 report payload 的结构对比也通过。

Phase 2D 验证（2026-09-21，Windows；所有 Python 命令显式使用已 qualification 的 `mirrorly-gui-dev` interpreter）：

| 检查 | 结果 |
| --- | --- |
| Checkout qualification：in-process、普通/isolated subprocess、console entry point | PASS；均为当前 GUI worktree，editable origin 一致 |
| backup/setup/application/CLI/qualification 聚焦测试 | 260 passed |
| 完整 Python regression | 662 passed；无 skip/failure（Phase 2C 642 + 新增 20） |
| Windows E2E 单独运行 | 13 passed（也包含在完整 regression 中） |
| 默认 testpaths 外 fake-worker tests | 11 passed |
| Ruff check / format check / pip check | PASS；73 Python files 格式通过 |
| diff check / 更新文档相对链接检查 | PASS |

测试开发中发现两个多行 snapshot-ID monkeypatch 仍指向旧 CLI 符号，已迁到真实 application 调用点；没有改变预期行为或弱化断言。没有修改底层 core 算法、格式、版本、desktop/fake-worker 或环境依赖；未重建 WinUI。

## 当前事实：Phase 2E 共享 list / verify / restore 编排

以 Phase 2D `2c7fee2d509860d3fea29291d5ab88af82aa17a6` 为起点，补齐三个同步模块；CLI 五个命令均通过共享 application 层执行业务。原 setup/Backup 服务及底层 core 未修改，不增加依赖、锁、并发或格式变化。

| 原 CLI 职责 | 当前 Python API |
| --- | --- |
| list/verify/restore 开头的 task → repo resolution | [queries.resolve_task_repository](../../src/mirrorly/application/queries.py)`(config_root, task=None) -> TaskRepository` |
| `cmd_list` 的 snapshot discovery | `queries.list_snapshots(context) -> tuple[ManifestSummary, ...]` |
| `_latest_complete` | `queries.latest_complete(repo) -> ManifestSummary \| None` |
| `cmd_verify` 的选目标、校验与必需报告 | [verification.verify_snapshots](../../src/mirrorly/application/verification.py)`(context, *, snapshot=None, all_snapshots=False, quick=False, on_verified=None)` |
| `cmd_restore` 的选 snapshot、规划和统计 | [restoration.prepare_restore](../../src/mirrorly/application/restoration.py)`(context, destination, *, snapshot=None, paths=(), overwrite="never", in_place=False)` |
| `cmd_restore` 的获准执行和 partial interpretation | `restoration.execute_restore(prepared, *, overwrite_approved=False)` |

### 显式解析边界与查询

三个操作均先调用共享 `resolve_task_repository`，得到 `TaskRepository(task, repo, relocated)`，随后进入相应服务。把解析独立成第一步，允许 CLI 在**原顺序**输出 relocation，并在 restore snapshot selection 之前执行 `--in-place` 必须配 `--yes` 的 CLI 用法检查；无需增加新的 presentation callback。context 是本次解析事实，不是持久缓存、卷固定句柄或授权 token；新操作应重新解析。

CLI `_resolve_query_context` 仅提供原 `./.mirrorly` 默认值、`TaskSelectionError` → usage mapping、`_notify_relocation` 原 stderr 文本。实际 task/repo 逻辑继续复用 Phase 2B 模块。旧 CLI 私有 resolution/default-selection helper 已移除，未保留供旧 monkeypatch 使用的空 alias。

list 返回现有 `ManifestSummary`，不创建 CLI dictionary：完整保留 core 的 **manifest 文件名顺序**、complete/incomplete、stats、sequence/legacy facts 与校验/拒绝规则；不是按 lifecycle latest 排序。默认 verify/restore selection 则继续复用 core `select_default_complete`：最大 sequenced complete，或唯一 legacy complete；多个 legacy complete 不猜测。无可用默认目标用 `NoCompleteSnapshot` 标记，CLI 输出原各命令的中文提示与 exit 1。

### Verify：完成事实与必需报告分离

顺序保持：解析 → 选择显式/默认/all complete 目标 → 按既有顺序完成全部 `verify_snapshot` 调用 → 构造各 report entry 并输出原逐项结果 → 构造总报告 → `reports.write_report`。`--all` 沿用 list 顺序，不额外排序；单个目标仍使用 `verify-<id>`，多个目标使用 `verify-all`。无新增 mutation lock、repair 或历史数据库。

`VerificationResult(facts, report)` 返回原 persisted payload 与 `VerificationFacts`：context、targets、已成功返回的 `VerifyReport`、`verification_completed`、report path。`facts.ok` 只在全部目标返回后提供 bool，否则为 None。它沿用 core `VerifyReport.ok`，**不代表所有文件都有 hash**；unhashed_entries、hashed_files、quick 和 extras 都保留。CLI 复制原 payload 后附加 `report_path`，不把 dataclass 序列化成新 CLI schema。

普通异常用 `VerificationFailure(stage, cause, facts)` 保留原对象和已知事实。中途某个 target 抛错时，可保留已返回的前缀结果，但不生成整体 integrity 结论，也不提前打印逐项结果或发布报告。全部 verify 已返回、report publication 再失败时，`verification_completed=True` 和结果/coverage facts 保留；`report_path=None` 只表示未取得成功返回，不能断言磁盘没有部分或已发布报告。CLI 继续输出原“校验已完成（通过/失败），但必需报告发布失败”并返回 1，覆盖原本可能的 0/4。

`on_verified(VerifyReport)` 只保留既有 terminal 输出在 mandatory report 之前的时机，并且全部校验完成后才调用；不是运行中 progress API。`_present_verification` 和 quiet/verbose/JSON channel 仍由 CLI 管理。KeyboardInterrupt 保持传播，不提供 cancellation。

### Restore：意图、确认和 apply 重验

`prepare_restore` 只读返回 `RestorePlanResult(context, plan)`，`.summary` 提供 create/overwrite/skip/conflict/dirs 数量。复用原 `plan_restore`，不自行复制文件或生成第二份恢复计划算法。

CLI 显示原 plan/entry 信息；存在 overwrite 时在原位置调用 `_confirm()`，成功返回后才传 `overwrite_approved=True`。应用层无 input、terminal 或 WinUI 依赖；未批准已规划的覆盖时抛出 `OverwriteApprovalRequired`。`in_place=True` 是调用者明确表达的原地恢复意图；CLI 额外要求显式 `--yes` 的规则仍由 CLI 执行，且优先级没有前移到 task/repo resolution 之前。

`execute_restore` 将**同一份计划**交给原 core `apply_restore`。审批不是“忽略 stale plan”的权限，也不在应用层重新规划成更危险的动作。底层继续执行：

1. 校验 snapshot ID、overwrite、canonical selectors、合法 action、冻结的绝对 destination。
2. 重载 complete manifest，全量验证路径，比较 manifest digest，确认 snapshot 目录存在。
3. 复核 repo/source/destination 的实际路径边界与双侧 reparse，重跑条目分类；整批 no-upgrade 拒绝任何破坏性升级。
4. 每条写入前再分类、检查 no-upgrade；文件 staging 完成后、`os.replace` 前再执行 final check，否决则清理本次 owned temp。

保留原 final-check → replace 的残余 TOCTOU 窗口；未新增 repo writer lock，也未在 plan/apply 间重新解析 task/volume identity。未来 worker 不得把任意客户端 plan DTO 当成可信服务端授权，或在 stale 后静默重规划/升级；如何管理 plan reference 属下一阶段。

`RestorationResult(result)` 保留原 core `RestoreResult` 的 restored、dirs_created、skipped、conflicts、errors、leftovers、bytes_written；`has_issues` 仍由 skipped/conflicts/errors/leftovers 判定。apply 未返回而抛错时，`RestoreExecutionFailure(cause, prepared)` 只保留原异常及准备的意图，不制造 partial counts 或 rollback/零副作用保证。core 正常返回的 partial restore 继续保留已恢复文件。

Restore v1 不变：默认 never/Skip；显式批准的 always/Replace；原 older 语义保留。类型冲突不删除用户数据，目标额外文件不删除，无 Keep both、内容相同自动跳过、逐文件 policy、exact mirror 或自动 full verify。restore 仍不新增 report publication。

### 兼容性验证与未来 worker 约束

[test_query_operations.py](../../tests/test_query_operations.py) 覆盖只读 query/plan、filename vs lifecycle ordering、verify 顺序/coverage/报告失败/中途失败、restore approval/重验/partial/未知副作用、原 CLI 确认顺序与 fault seam、relocation/error 优先级、无新增锁，以及 isolated subprocess 下的 list/verify/report failure/restore/partial 调用（禁止 CLI import）。

既有 CLI fault injection 的 `verify_snapshot` 已移到 verification 模块；default-selection 引用移到 queries，resolver 单元测试直接调用真实 repositories 实现；原 relocation stdout/stderr integration 断言保留。Phase 2B CLI 默认路径测试转向新的 CLI context adapter。无测试预期放宽，未修改 Phase 2A qualification。

**生产 worker 的硬约束（仅记录，未实现）**：Backup 的 relocation/resume presentation hooks，以及类似 verify notice 的传递，不得让 IPC event delivery failure 改变业务事务结果。worker 必须在传输适配边界隔离通知发送异常，不能直接把可能抛错的 IPC send 接到 Backup hook。需要用户决策的 resume callback 是另一条安全边界，不能伪造默认同意。Phase 2D API/commit state/锁范围保持冻结；本轮没有实现传输、断连策略、progress 或 cancellation。

Phase 2E 验证（2026-09-21，Windows；全部 Python 命令显式使用 `mirrorly-gui-dev` interpreter）：

| 检查 | 结果 |
| --- | --- |
| Checkout qualification：普通/isolated import、editable origin、CLI console entry | PASS，均指向当前 GUI worktree |
| query operations / CLI / application / qualification 聚焦测试 | 238 passed |
| 完整 Python regression | 690 passed；无 skip/failure（Phase 2D 662 + 新增 28） |
| Windows E2E 单独运行 | 13 passed（也包含在完整 regression 中） |
| 默认 testpaths 外 fake-worker tests | 11 passed |
| Ruff / format / pip check | PASS；77 Python files 格式通过 |
| diff check / 更新文档相对链接 | PASS |

结构对比确认：存续 CLI 函数仅 cmd_list/cmd_verify/cmd_restore 的 AST 改变；init/Backup、parser、main、prompt helpers 不变。既有 CLI 685 条断言仅有四处 latest helper 引用迁移，预期值/条件不变；list/restore JSON 构造和 verify report entry 序列化不变。application 无 CLI import，隔离子进程实际完成 query/verify/report failure/restore/partial 操作。底层 core、setup/Backup application、desktop/fake-worker、依赖/版本均未修改；未重建 WinUI。

## Phase 2E 结束时的边界与后续状态

生产 Python worker、真实 GUI Create Backup / Back up now、progress、cooperative cancellation、queue 和 GUI 配置持久化均未实现。权威 setup preflight 与五类共享 application 操作目前只存在于 Python 侧；GUI 不执行真实 Mirrorly 操作，Setup 仍是 Phase 1C prototype，Home 按钮仍使用 fixture。Phase 0–1C 文档继续作为各阶段历史记录。

上述是 Phase 2E 结束时的实现范围。后续 Phase 3B 已通过独立任务实现
[production bootstrap / 只读 IPC](PRODUCTION_WORKER.md)，仅开放真实 setup.preflight；
production mutation、Resume、progress/cancel 和 GUI Create Backup/Back up now 仍未实现。
O-01/O-03/O-04 及 Resume deadline 的批准状态见 [当前登记](README.md#待决事项登记)；
O-07/O-09 继续 OPEN，稳定 worker lifecycle gate 必须先于首个 mutation method。
