# Mirrorly 项目状态

> 本文件维护项目当前状态与环境快照。每次重大变更后更新。
> 最后更新：2026-09-19（`0.1.0` final release preparation；基于已提交基线 `554bde9f760154268a9ea60b0a7c974119cdea92`）

## 当前阶段

**Final Pre-Public Gate：PASS；GitHub Public source readiness：PASS。** Repository 已为 **Public**；主要生产代码安全加固、living documentation 与 package metadata 同步已完成。本次 release preparation 开始时 `HEAD = main = origin/main = 554bde9`（Record public source transition）。

当前 source/package 已收口为 **`0.1.0` / Alpha**，不表示 production-ready、stable 或 `1.0` ready。**`0.1.0` final release preparation 正在进行，等待 Architecture / Release Review**；尚未宣布 release，未创建 `v0.1.0` tag / GitHub Release，未发布 PyPI。GUI 尚未实现且本阶段未开始开发；GUI development 在 `0.1.0` release 收口之后开始。内置调度、云备份、双向同步均未实现。

### 当前验证记录与历史验收分开记录

| 范围 | 已完成验证结果 | 说明 |
| --- | --- | --- |
| 当前 release-preparation 工作树（source `0.1.0`） | **570 passed / 4 skipped** | 完整回归；focused version / entry-point tests **5 passed** |
| 已提交基线 `c75e1ad`（source `0.1.0.dev0`） | **570 passed / 4 skipped** | 保留 Final Pre-Public Gate 完整回归记录 |
| 上一生产基线 `1fb0cde` | **569 passed / 4 skipped** | 保留此前已批准的 regression 记录 |
| 当前 Windows E2E | **13 passed** | 本次 release preparation 独立运行 `tests/test_e2e.py` |
| 当前静态/格式检查 | Ruff check PASS；format check：44 files already formatted | 本次 release preparation 记录；`git diff --check` 通过 |
| 冻结 MVP acceptance | **452 passed / 4 skipped / 0 failed**；T-10 E2E **13 passed** | 不以当前数字覆盖历史验收 |

当前 4 个 skip 为 Windows 文件 symlink 创建权限 / Developer Mode 环境限制，不能描述为文件 reparse 验证 PASS。历史目录 junction 的通过记录与文件 symlink 的 skip 分开保留。冻结 tag `v0.1.0-mvp` 指向提交 `999ceb88c7d4c73c3062eb1927fd1c513d9e0234`（验收文档提交）；历史验收所记录的生产 HEAD 仍为 `60ed124`。

## 当前实现与边界（以源码和 committed tests 为准）

### 仓库写入、格式与身份

- 正式 backup 按 **task lock → repo writer lock** 顺序获取跨进程锁；仓库写锁为 `locks/repo-writer/active.lock`，覆盖迁移、基线选择、扫描/写入、complete publication、续传善后、retention 和 report。不同 task 指向同一 repo 也互斥；不同 repo 独立。退出只释放自己创建的 lockfile，`locks/repo-writer/` 命名空间保留。锁存在即 fail closed（exit 6），无 PID 猜测、自动 stale 删除或超时夺锁。Dry-run 不取锁、不迁移、不 reserve sequence；verify/restore/list 不进入 backup writer 临界区。
- 新仓库 `repo.json` 为 **format v2**，必须有 `lifecycle.json`：`format_version=1`、匹配的 `repo_id`、`next_sequence`。每个 fresh/resume physical attempt 在锁内 durable reserve 一个新的 uint64 sequence；先持久发布 `next_sequence=N+1` 再向调用方返回 N。Gap 允许，retention/discard 不回退 high-water；状态缺失、损坏、与可见 artifacts 矛盾或耗尽时 fail closed，不自动修正。
- Windows durable state/capability publication 使用同目录 temp、`flush()`、`os.fsync()`（Windows CRT `_commit()`）、`MoveFileExW(REPLACE_EXISTING | WRITE_THROUGH)`。这项持久化协议属于 repo/lifecycle 控制状态，不意味着所有文件写入都具有相同协议。
- 新 manifest 只写 **v2**，必需 `lifecycle_seq`；合法 v1 只保留读取兼容。ID 格式为 `YYYY-MM-DD_HHMMSS-s<20位sequence>-<32位小写UUIDv4 hex>`：本地时间便于阅读，完整 UUIDv4 提供 lifecycle identity，ID 中 sequence 是诊断冗余，必须与 manifest 一致。分配时检查正式/staging namespace，冲突重 mint UUID，不扫描旧 timestamp 空槽；legacy ID 不重命名。
- v1 仓库在正式 backup 锁内升级：先写 initial state（next=0），再 durable 发布 repo v2；中断后 v1 + 有效 initial state 可继续升级，矛盾状态拒绝。现有 v1 manifests 不重写、不猜 sequence；只支持 repo v1 的旧程序会拒绝 v2。

证据：[cli.py](../src/mirrorly/cli.py) `_TaskLock` / `_RepoWriterLock` / `cmd_backup`；[repo.py](../src/mirrorly/repo.py)、[lifecycle.py](../src/mirrorly/lifecycle.py)、[durable.py](../src/mirrorly/durable.py)；`tests/test_cli.py::TestBackupLockScope`、`tests/test_lifecycle.py`、`tests/test_repo.py`、`tests/test_snapshot.py::TestHelpers`。

### Authoritative lifecycle ordering 与 legacy

| Consumer | 当前规则 |
| --- | --- |
| latest complete / 默认 verify、restore | 有 v2 complete 时取最大 `lifecycle_seq`；无 v2 时唯一 legacy complete 可默认选择，多个 legacy complete 则要求 `--snapshot`（verify 也可用 `--all`） |
| 自动 backup baseline / dry-run 预览 | 只取最新 sequenced complete；无此 complete 时用空基线，首次 sequenced backup 全量物化当前源，不从 legacy unchanged 文件硬链接或结转哈希 |
| 自动 incomplete selection | 只考虑 sequence 大于最新 sequenced complete 的 v2 incomplete，取最大 sequence；无 sequenced complete 时取最大 v2 incomplete；legacy/stale incomplete 保留但不自动选 |
| 接受 resume | 旧 incomplete 是只读基线；新 physical attempt 使用新 ID、新 sequence。拒绝 resume 则用 latest sequenced complete 或空基线，旧 incomplete 保留 |
| retention `keep_last` | 保留 sequence 最大的 N 个 v2 complete；legacy complete 全部自动保护，实际数量可超过 N |
| retention `keep_monthly` | 从当前 UTC 月回溯配置月份数；`created_at` 决定所属年月，同月代表取最大 sequence 的 v2 complete；与 keep-last 取并集 |

重复 v2 sequence 不作时间/ID tie-break，直接拒绝。`created_at` 仍用于显示、审计及日历分组，snapshot prefix 是本地 wall clock；二者都不作为生命周期先后依据。时钟错误造成的月份标签偏差不在该修复范围。`list` 仍按 manifest 文件名列出，展示顺序不等于 authoritative latest。

证据：[manifest.py](../src/mirrorly/manifest.py) `latest_sequenced_complete` / `select_default_complete` / `newest_eligible_incomplete`；[retention.py](../src/mirrorly/retention.py) `build_retention_plan`；`tests/test_cli.py::TestAuthoritativeLifecycleOrdering`、`tests/test_manifest.py`、`tests/test_retention.py`。

### 内容、校验与 publication

- `[verify] on_write=true` 时，正常无哈希 incomplete 中准备复用的文件须与当前源作内容等价认证；不可信/认证未完成的副本重新物化。False→True 时，无可信 sha 的基线文件也重新复制并校验，不能仅给旧副本补算哈希。True-mode complete publication 前检查普通文件哈希覆盖；复用的可信 sha 从基线结转，copied 文件使用写入校验结果。
- `on_write=false` 不承诺完整 hash coverage，新复制文件可为 `sha=None`。Full verify 检查快照与 manifest 的存在性/类型/大小及已有 sha；无 sha 条目跳过哈希并计入 `unhashed_entries`，extras 只报告，均不单独令 `ok` 失败。Quick 不重算哈希。Full verify 不证明快照等于历史源，也不能替代 source-side change detection；默认相同 size/mtime 仍信任元数据，`backup --full-hash` 可强制对与基线同大小的源文件作哈希复核。
- 普通文件复制先写 staging temp、flush/fsync、源 size/mtime 复测，再在 temp 设置所需 mtime，最后 `os.replace`。启用写入校验时，文件 publication 后比对源/目标哈希，失败仅重试一次；整体快照仍为 incomplete，直到 complete manifest 原子发布。Recovered mtime 不符时清除信任并重物化，不对可能共享历史 inode 的 recovered 文件原地 `utime`。
- **Complete manifest 的原子发布仍是 backup safety commit point**，随后才做 resumed incomplete discard、retention 和 report。它不等于所有后续操作都成功；普通 complete 内容不被改写，但保留策略可以删除到期快照。
- Temp cleanup 必须依据 manifest 证明 ownership，`.mrtmp` 后缀本身不是删除授权；complete 快照不进入 temp cleanup。Retention 先移除 manifest 再删除 tree；tree 删除失败可留下可识别的孤儿目录，不把残缺 tree 留作可信 complete。

证据：[recovery.py](../src/mirrorly/recovery.py)、[snapshot.py](../src/mirrorly/snapshot.py)、[verify.py](../src/mirrorly/verify.py)、`cli.cmd_backup`；`TestB12ResumeHashCoverage` / `TestVerifyOnWriteHashCoverageTransition`（test_cli）、`TestSafetyInvariants`（test_snapshot）、`tests/test_recovery.py`、`tests/test_verify.py`。

### Shared manifest path boundary 与报告

- `load_manifest()`、`write_manifest()`、`list_manifests()` 共用 canonical lexical entry-path validation：拒绝非法/non-canonical 路径、重复与 Windows 大小写冲突；不自动修复输入。规则适用于合法 v1/v2 读及 v2 写，保护 Verify、Recovery、baseline、retention 等正常 loader consumers。Restore 复用规则并转译路径错误为 `RestoreError`，保留独立的 filesystem/reparse/destination 与 commit-time 检查。Lexical validation 不等于完整 filesystem alias/reparse 安全证明。
- Backup/verify report 仍为必需命令产物，位于 `logs/`；logical filename/schema 不变。`_write_report` 的 mkdir、撞名查询、exclusive sibling temp 写入、atomic replace 和 owned-temp 失败清理统一使用 `scan.to_long_path()`。清理仅尝试删除本次拥有的 temp，失败不覆盖原 publication error；不保证所有控制面路径任意长度均可用。
- Backup 已提交后 report I/O publication 失败仍 **exit 1**，错误明确给出 snapshot ID、「已成功提交」和「必需报告发布失败」。Verify report 失败同样 exit 1，但说明校验已完成及结果，不声称提交快照。报告原子可见性不等于 lifecycle state 的 durable reservation 协议。

证据：`manifest.validate_manifest_entry_paths`、`restore._load_restore_manifest`、`cli._write_report` / `cmd_backup` / `cmd_verify`；`tests/test_manifest.py`、各 consumer path-boundary tests、`tests/test_cli.py::TestReportPublication` / `TestPaths::test_long_repository_backup_and_verify_reports`。

## 已提交的公开前加固

| Commit | 已实现范围 |
| --- | --- |
| `2977497` | B1-1：temp residue ownership 与 complete 只读保护 |
| `f44795c` | B1-2：resumed snapshot hash certification / coverage |
| `196ce9f` | repository agent instructions |
| `3313566` | False→True write-verification hash coverage |
| `b22e87b` | staging mtime 与 recovered metadata 安全 |
| `438d92f` | CONCURRENCY-1：同 repo 跨 task writer serialization |
| `c5259b3` | SNAPSHOT-ID-REUSE-1：UUID lifecycle identity；当时的局部 ordinal 已由后续 durable sequence 取代 |
| `0b224c5` + `6ae831a` | WALL-CLOCK-ORDER-1：durable sequence foundation + 全部安全 ordering consumer 迁移 |
| `aef6648` | shared manifest canonical entry-path boundary（含 lone-surrogate error contract） |
| `1fb0cde` | report long-path transaction / owned-temp cleanup / post-commit error semantics |

## MVP 历史验收（冻结记录）

**MVP 开发与验收全部完成（10/10），已最终验收通过** —— T-01~T-09 已最终验收；T-10 端到端验收完成且 M10「盘符漂移」blocker 已修复（卷锚自动重定位：`[target]` 三键 all-or-none 锚 + `cli._resolve_repo` 七态 fail-closed 状态机 + `volume.py` Windows 官方 GUID API；真实 GUID 链路 E2E 验证「配置 path 失联不改配置自动定位原卷原仓库」）。完整 pytest 全套 **452 passed / 4 skipped / 0 failed**（4 skip 均为文件级 symlink 权限限制——当前 Windows 环境无 SeCreateSymbolicLinkPrivilege / Developer Mode，目录级 junction 对应防护已真实 Windows 通过，已被产品负责人裁定允许为 MVP environmental skip）；E2E **13 passed / 0 failed**；ruff 全绿。验收报告 `docs/MVP_ACCEPTANCE.md`。

**最终结论（产品负责人，2026-09-13）：MVP ACCEPTED — PASS WITH ENVIRONMENTAL SKIPS**（final accepted HEAD = `60ed124` feat(repo): add volume-anchored target relocation；T-10 initial baseline = `a31126b`，性能基线亦在 a31126b 测得）。**MVP 开发阶段结束，生产代码冻结。**

## 关键文档

- [docs/PRD.md](PRD.md)：产品需求文档 v0.2（已确认，决策 D1–D7 定案）
- [docs/TECH_RISKS.md](TECH_RISKS.md)：技术风险分析 v1.0（TR-1~TR-7，高风险集中于中断恢复与外置盘）
- [docs/ARCHITECTURE.md](ARCHITECTURE.md)：架构决策记录（ADR-001~013）
- [docs/DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)：manifest/配置/哈希三项选型分析 v1.0
- [docs/CLI_SPEC.md](CLI_SPEC.md)：当前 CLI 契约（五命令 + 退出码；MVP v1.0 历史版本保留于 tag）
- [docs/MVP_TASKS.md](MVP_TASKS.md)：MVP 开发任务拆分 v1.0（T-01~T-10，含验收标准与测试要求）
- [docs/UI_DIRECTION.md](UI_DIRECTION.md)：未来 GUI 设计约束 v1.0（clean/lightweight/trustworthy，mint green）

## 开发环境快照（2026-09-12）

| 项目 | 值 | 备注 |
| --- | --- | --- |
| 操作系统 | Windows（工作区位于外置盘 `P:\`） | |
| Git | 2.55.0.windows.3 | 仓库已初始化，main 分支 |
| Conda | 25.5.1（Anaconda3，`C:\Users\sakur\anaconda3`） | |
| Python（项目环境） | 3.12.14（Conda 环境 `mirrorly`） | 位于本机默认环境目录 `C:\Users\sakur\anaconda3\envs\mirrorly`；官方源不可达，经清华 TUNA 镜像创建 |
| Python（系统/工具链） | 3.13.x | 宿主沙箱自带，不用于项目开发 |
| 编辑器格式约定 | `.editorconfig`（UTF-8、LF、Python 4 空格） | |

## MVP 已完成记录（截至 2026-09-13，保留当时实现与验证数字）

以下 T-01~T-10 是历史记录；其中 timestamp 排序、旧 ID 分配、仅 task lock 等描述已被上方当前实现取代，不作为现行行为说明。

- [x] 确认工作目录与开发环境（Git / Conda / Python）
- [x] 创建独立 Conda 环境 `mirrorly`（Python 3.12，本机默认环境位置）
- [x] 初始化 Git 仓库（main 分支，含初始提交）
- [x] 建立项目目录结构（src-layout：`src/mirrorly`、`tests/`、`docs/`、`scripts/`）
- [x] 建立工程基础文件（README、pyproject.toml、environment.yml、.gitignore、.gitattributes、.editorconfig）
- [x] 建立文档体系（STATUS.md、DEVELOPMENT_LOG.md、ARCHITECTURE.md）
- [x] 市场调研：个人备份/同步工具格局、3-2-1 原则、版本化 vs 同步、主流工具优劣
- [x] 产品定义：目标用户画像（P1 技术型个人 / P2 内容创作者）、MVP 范围（M1–M10 / W1–W10）、关键决策 D1–D7 推荐方案、PRD v0.1
- [x] PRD 决策确认（2026-09-12）：D1 备份优先通过；D2 NTFS 正式支持 + exFAT 明示不降级；加密压缩延后；多任务架构预留 MVP 单任务；保留策略默认+配置化 → PRD v0.2
- [x] 技术风险分析：docs/TECH_RISKS.md（TR-1~TR-7）
- [x] 产品架构 ADR：ADR-005 存储模型 / ADR-006 变更检测 / ADR-007 任务模型 / ADR-008 保留策略 / ADR-009 完整性机制
- [x] 架构细化（2026-09-12）：manifest 选型（每快照独立 JSON，ADR-010）、配置格式（TOML + config.d/，ADR-011）、CLI 规范（五命令+退出码，ADR-012/CLI_SPEC）、哈希算法（BLAKE3 主用 SHA-256 兜底，ADR-013）
- [x] MVP 任务拆分：docs/MVP_TASKS.md（T-01~T-10，依赖关系、输入输出、验收标准、测试要求）
- [x] 开发前整理（2026-09-13）：TECH_RISKS 哈希描述与 ADR-013 对齐（b2a9a3b）；Git 仓库级身份设为 GitHub 用户 local-usr-bin + noreply 邮箱；docs/UI_DIRECTION.md 未来 GUI 设计约束
- [x] **T-01 仓库初始化与配置加载**：blake3 依赖安装；mirrorly.repo（MirrorlyRepo 目录结构、repo.json 原子写入、卷标识、strict/warn 文件系统策略）、mirrorly.config（TOML 严格模式加载/生成）、mirrorly.hashing（BLAKE3/SHA-256 抽象）；31 项测试全过，ruff 通过
- [x] **T-02 源扫描与变更检测**：mirrorly.scan（scan_source 目录遍历 + Excluder glob 排除[文件/目录区分] + detect_changes 元数据初筛与 BLAKE3 哈希复核）；长路径（\\?\ 前缀）与 Unicode 支持；49 项测试全过，ruff 通过
- [x] **T-03 快照写入引擎**：mirrorly.snapshot（write_snapshot：目录重建[空目录保留]、未变文件硬链接复用、变更文件 .mrtmp 临时文件+fsync+复测+原子改名、TR-4 变动中文件跳过、os.link 失败显式报错、hardlinks=False 显式降级复制）；62 项测试全过，ruff 通过
- [x] **T-04 Manifest 管理**：mirrorly.manifest（create_manifest/mark_complete/write_manifest[manifests.tmp+fsync+os.replace 原子提交]/load_manifest[require_complete 防误读]/list_manifests）；13 项新测试全过，ruff 通过
- [x] **T-05 中断恢复**：mirrorly.recovery（scan_recovery 发现 incomplete/孤儿目录/tmp 残留、build_resume_baseline[基线一致性校验：目录存在+大小/类型矛盾即 RecoveryError，缺失条目显式收入 missing]、clean_tmp_residue、discard_incomplete[拒绝 complete]）；续传产出新 snapshot id，incomplete 目录只读基线；修复 snapshot.py 三处 mkdir 未走长路径前缀的缺口（T-03 规范一致性修复，无行为变更）；100 项测试全过（含 4 个中断点注入、双重中断收敛、inode 复用断言），ruff 通过
- [x] **T-06 完整性校验**：mirrorly.verify（verify_snapshot 全量/quick 模式、VerifyReport[issues/extras/unhashed_entries]、四种 issue 类型）；snapshot.py 追加 verify_writes 写入即校验（改名后重算目标端哈希比对、仅重试该文件一次、hashes 供 manifest 持久化，默认关闭保持 T-03/T-05 行为不变）；linked 文件 sha 由调用方从旧 manifest 结转（T-09 组合流程已预演）；115 项测试全过，ruff 通过
- [x] **T-07 保留策略 + safety hardening**：mirrorly.retention（build_retention_plan[keep_last/keep_monthly/union、按 manifest created_at 排序、只读]、apply_retention_plan[dry-run、快照 id 路径安全校验、失败显式 RetentionError]）；**安全加固**：两阶段执行——阶段 1 对每个待删快照重新 load_manifest(require_complete=True) 复核状态（不信任传入 plan，非法即整体拒绝零删除），阶段 2 按「先 manifest 后目录」失败安全顺序删除（manifest 删失败→数据零触碰；目录删失败→残留为孤儿可由 scan_recovery 发现，不留虚假 complete 记录）；整体删除目录+manifest，数据生命周期靠 NTFS link count 自然管理；139 项测试全过（含 6 项加固专项、硬链接数据存活断言、长路径删除、孤儿/incomplete 保护），ruff 通过
- [x] **T-08 恢复引擎**：mirrorly.restore（plan/apply 两阶段；apply 不信任 plan——重载 complete manifest、digest 指纹比对、重验安全边界、重跑规划 + no-upgrade 对账[破坏性升级一律 stale 整体拒绝，降级按重算执行]；`--path` 字面 snapshot-relative 路径无 glob 语义；canonical Windows 路径校验[保留设备名含 COM¹-³/LPT¹-³ 及带扩展名形式、UTF-16 code unit 计长]；目标边界[仓库内拒绝/同一实际位置须 in_place/普通子目录允许]；覆盖策略 never/older[严格更旧]/always，file↔dir 冲突任何策略不删用户数据；snapshot 读侧+destination 写侧 reparse 防护，check-then-open 残余 TOCTOU 明确接受；mkstemp 短固定前缀独占临时文件+fsync+os.replace，cleanup 只删精确记录路径、失败进 leftovers；partial restore 无整体回滚；不自动 full verify）；**safety hardening follow-up**：单条目分类器 `_classify_entry` 供 plan/apply 共享，每个写操作紧邻 I/O 前逐条重验；plan 冻结 destination 绝对路径；mtime 在 temp 上设置后再 replace（replace 为单文件唯一 commit point）；load 后 selector 前全量 manifest 路径校验+拒绝重复路径；同一实际位置按 samefile/realpath 身份判定；属性查询 fail closed；apply 重验 plan 参数；**hardening 第二轮**：canonical validator 显式拒绝反斜杠、_restore_one_file 修复 mkstemp fd 泄漏窗口、manifest casefold 大小写冲突拒绝、snapshot id 复用单组件 canonical 规则（任何冒号/ADS 形式一律拒绝）；**hardening 第三轮**：commit point 前最终复核——temp staging 完成后、os.replace 前以 fresh action 为基线再跑共享分类器（_final_recheck），TOCTOU 收敛为 final check→replace 小窗口；261 项测试通过（8 项 reparse/junction 用例因沙箱屏蔽 reparse 属性跳过，真实 Windows 执行）；小修 T-02 长路径测试的 basetemp 长度敏感性；ruff 通过
- [x] **T-09 CLI 集成与报告**：mirrorly.cli（argparse 五命令 init/backup/verify/restore/list，纯编排不重复业务逻辑；退出码严格按 CLI_SPEC §6[0/1/2/3/4/5/6/130 全部有触发路径]；破坏性操作确认与 --yes[restore 覆盖、init warn 降级]，--in-place 必须显式 --yes，多任务未指定 --task→用法错误 2；任务锁 O_EXCL[占用→6、dry-run 不取锁]；backup 编排[配置解析→卷校验→incomplete 提示续传→扫描→变更检测→--full-hash 基线 mtime 置 -1 强制全量复核→锁内 incomplete manifest→write_snapshot→sha 结转合并→complete 原子提交→续传善后→retention]；backup/verify 报告 JSON 落盘 logs/[tmp+原子改名]+终端摘要；--json 机器可读输出；全局选项 parents+SUPPRESS 支持子命令后书写）；__main__ 委托 cli.main（entry point 不变，mirrorly.exe 与 python -m mirrorly 真实验证）；OQ-1 澄清：CLI_SPEC `--path <pattern>`→`--path <path>` 注明字面路径非 glob；集成暴露小修：config.py dump_task_config TOML 反斜杠转义（T-01 潜伏 bug）、repo.py 新增 RepoFormatError 子类（退出码 5 精确映射）；**integration hardening follow-up**：①snapshot id collision 修复——`_new_snapshot_id` 锁内先选定空闲 id（冲突追加 -01/-02 canonical 后缀、100 候选占满安全失败）再落盘 incomplete manifest，任何撞车下既有快照目录/manifest 字节/complete 状态零触碰；②任务锁前移至确定 repo/task 后（覆盖 recovery/扫描/检测/写入/善后/retention，dry-run 有意例外）；③--json stdout 严格单一 JSON 文档——人类文本走 stderr、交互场景无 --yes 直接 exit 6 且 stdout 为空、dry-run --json 结构化输出；④init 预检前移（task config 已存在/source 与 prospective repo 互相包含检查均在 init_repo 前，零写入拒绝）+ `validate_task_name` 防锁路径逃逸（load_task_config 与 CLI --task/init 强制执行）；⑤verify --snapshot/--all 互斥（argparse exit 2）、报告文件名微秒+撞名后缀不再同秒覆盖；**list --verbose「增量大小」经裁定 MVP 不实现**（冻结数据模型无权威口径，CLI_SPEC 已澄清为文件数/目录数/总大小/状态，列为 MVP 后候选指标——未来实现须在 backup 创建时持久化权威值）；**最终收尾**：write_task_config 写入边界自身校验 task name（library API 不依赖调用者提前验证，非法 name 零写入拒绝）、init preflight 回归补强（已有 task config 时零仓库写入逐字节固化）；全套 **383 项测试通过 / 8 项沙箱跳过 / 0 失败**（safe-delete 配额刷新后完整回归一次通过，上轮 20 项护栏受阻用例全部转绿），CLI 专项 119 项通过，ruff 通过；**源码验收 blocker 修复**：`_path_within` 字符串 startswith 在 Windows 卷根上前缀失配（`C:\` + os.sep），`init --source C:\` 可漏过「repo 位于源内」保护——改为 realpath+normcase 后 commonpath 判断（跨盘 ValueError→False，UNC 兼容），新增卷根/跨盘/UNC 纯单元与盘根 source 零写入共 7 项测试；最终完整回归 **390 项测试通过 / 8 项沙箱跳过 / 0 失败**——T-09 正式最终验收
- [x] **T-10 端到端验收 + M10 blocker 修复**：`tests/test_e2e.py`（13 用例，真实 CLI 子进程 + 真实 NTFS：主流程 A–F[init/backup#1/verify/变更/dry-run 零写入/backup#2/list]、restore[整快照字节级/--path 文件与子树/never/always]、损坏检测 exit 4、TR-5 真实 kill 续传、M10 身份[错误 serial exit 5 零写入 + **真实 GUID 链路盘符漂移自动重定位** + GUID 未挂载零写入 + 仓库缺失不自动 init]、retention 多快照清理后 verify 全过）；`scripts/t10_perf_baseline.py` 性能基线两 workload（9.6 GB 混合/4 GB 大文件；unchanged 二次备份 0 字节写入、硬链接复用 10,006 文件、空间仅 +0.01 GB）；**M10 修复**：`src/mirrorly/volume.py`（GetVolumePathNameW / GetVolumeNameForVolumeMountPointW / GetVolumePathNamesForVolumeNameW，FindFirstVolumeW 仅诊断）、repo.json `volume.guid` 增量可选（format_version 不变双向兼容）、TaskConfig `[target] volume_guid/repo_id/repo_dir` all-or-none 锚（三重校验 + join 后 containment 复验）、`cli._resolve_repo` 七态 fail-closed 状态机（GUID 主锚 + repo_id/serial 确认，无 serial 降级 fallback，重定位不自动改写 config）；测试 +34（test_volume 7 / TestM10Resolver 11 / config 锚 16）；reparse 真机补验：4 个目录级 junction 用例通过、4 个文件级用例因无 symlink 特权（ERROR_PRIVILEGE_NOT_HELD）如实 skip；完整回归 **452 passed / 4 skipped / 0 failed**，ruff 全绿；`docs/MVP_ACCEPTANCE.md` 结论 **PASS WITH ENVIRONMENTAL SKIPS（M10 blocker 已修复并验证）**

## 文档分类与后续边界

- Living/current：`README.md`、本文件、`CLI_SPEC.md`；`DEVELOPMENT_LOG.md` 只追加新记录，原有日期条目保留。
- 历史设计/验收：`PRD.md` v0.2、`ARCHITECTURE.md` ADR-001~013、`DESIGN_DECISIONS.md` v1.0、`TECH_RISKS.md` v1.0、`MVP_TASKS.md`、`MVP_ACCEPTANCE.md`。它们记载当时需求、设计与验收，不将每一项设想自动视为当前已实现保证；当前格式、锁、排序和校验以本文件及源码为准。`UI_DIRECTION.md` 是后续 GUI 设计约束，不代表 GUI 已实现。
- Developer instructions：repo-root `AGENTS.md`。另外 `LICENSE` 为 MIT；`pyproject.toml` / `environment.yml` 是安装与环境配置，本次 release preparation 仅将 `pyproject.toml` 的版本收口为 `0.1.0`，其他 metadata 与 `environment.yml` 不变。
- 远程 push 与历史 MVP tag 已存在，不再列为待创建。是否发布新的 GitHub release 是独立任务；本次不创建 release 或移动 tag。
- 待独立处理/评估：CP-KILL-A1（early incomplete 已落盘但 tree 尚未建立时，接受 resume 会拒绝目录缺失）；post-commit discard/retention 失败的状态说明和残留恢复；MVP_ACCEPTANCE §11 的卷兼容/诊断 hardening。Report long-path P2 已提交，不继续列为开放修复。
- 上一 Control Metadata / Path Containment 综合审计仍是 **AUDIT INCOMPLETE / SAFETY-BLOCKED**；未完成项保持 **NOT TESTED — blocked by Codex cyber safety control**。Shared lexical boundary 的实现与回归不等于 duplicate JSON keys、numeric/parser ambiguity、filename/payload identity、结构冲突及 alias/reparse 等完整动态 matrix 已通过。
- 当前 source/package version 为 **`0.1.0`**，Development Status 继续为 **Alpha**；版本收口不等于 tag / GitHub Release 已发布。`pyproject.toml` 与 `mirrorly.__version__` 保持手工双源，由 smoke regression 检查一致。历史 `v0.1.0-mvp` 只是 Git MVP milestone，当时 package metadata 为 `0.0.1`，不是已发布的 Python package `0.1.0`；未来 `v0.1.0` 将表示经过安全加固与公开前审计的正式 CLI source release，与旧 tag 并存，不移动旧 tag。**`Private :: Do Not Upload` 有意保留**，只表示当前不发布 PyPI，不限制 GitHub Public 源码。正式 release/tag 仍需独立批准，GUI development 在 release 收口之后开始。

## 风险与注意事项

- 工作区位于外置盘（`P:\`），注意断盘风险；远程已配置，后续推送仍需明确授权
- Git 仓库级身份已设为 GitHub 用户 `local-usr-bin` + noreply 邮箱（2026-09-13）；历史 commit 的占位身份保留不重写
- Mirrorly 是备份工具，务必坚持「备份产物永不入库」的 .gitignore 约定
