# Mirrorly MVP 验收报告（T-10 端到端验收）

> 日期：2026-09-13 · **最终裁定：MVP ACCEPTED — PASS WITH ENVIRONMENTAL SKIPS**
> T-10 initial baseline：`a31126b`（fix(cli): handle Windows root containment safely，工作区 clean）
> Final accepted MVP HEAD：`60ed124`（feat(repo): add volume-anchored target relocation）
> 验收依据（冻结文档）：MVP_TASKS.md T-10、PRD.md M1–M10、CLI_SPEC.md v1.0、TECH_RISKS.md TR-5/TR-7、ARCHITECTURE.md（ADR-005~013）、DESIGN_DECISIONS.md
> 状态标记：PASS（真实执行通过）/ SKIP（环境限制，如实跳过）/ SIMULATED（真实组件 + 仿真构造的场景）/ NOT REQUIRED（冻结 MVP 未要求）

## 1. 冻结 T-10 验收标准对照

| # | MVP_TASKS T-10 冻结验收标准 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 完整流程 init → 多次 backup（含变更与删除）→ verify → restore → 保留清理，全部通过 | PASS | `tests/test_e2e.py` 全部通过（见 §2–§7） |
| 2 | 盘符变化后 backup 仍能识别目标卷（卷标识匹配） | PASS（卷锚自动重定位，真实 GUID API 链路） | 见 §8 |
| 3 | 插错盘（卷标识不符）→ 退出码 5，零写入 | PASS | E2E：篡改 repo.json serial → exit 5、仓库树逐字节不变 |
| 4 | 10 GB 级混合数据集（含 >4 GB 大文件、万级小文件）备份耗时与空间占用记录在案 | PASS（基线已归档） | 见 §10 性能基线（DEVELOPMENT_LOG 同步归档） |

产物要求：端到端测试报告（本文档）+ 已知限制清单（§11）+ 性能基线归档 DEVELOPMENT_LOG——均已交付。

## 2. 验收环境

- Windows 11（10.0.26200）、Python 3.12.14（Conda `mirrorly`）、Mirrorly 0.0.1、git `a31126b`
- 真实入口：`mirrorly.exe`（console script，`C:\Users\sakur\anaconda3\envs\mirrorly\Scripts\`）与 `python -m mirrorly`
- 文件系统：真实 NTFS（C: 卷标 OS、serial 4440B938；另有真实外置卷 P: NTFS serial 012D1774）
- 隔离：所有数据集/仓库在 OS 临时目录专用 scratch（`%TEMP%\mirrorly_t10_*` / pytest basetemp），不触碰用户数据、不改真实盘符/挂载、不动开发仓库无关数据
- 环境限制（如实声明）：
  - 早期验收运行曾受宿主 safe-delete 批量护栏（阈值 50/turn）拦截 `\\?\` 前缀删除，两个用例当时受阻（环境限制，非产品缺陷）；最终配额刷新后已重新执行并全部通过（§6/§7/§12），不影响本报告最终结论
  - 文件符号链接创建被拒绝（`CreateSymbolicLinkW` → ERROR_PRIVILEGE_NOT_HELD，无 SeCreateSymbolicLinkPrivilege）——影响 4 个文件级 reparse 用例（见 §9）
  - 沙箱内 reparse point 不可见 → reparse 专项在沙箱外（真实 Windows）执行

## 3. CLI E2E 主流程（`tests/test_e2e.py::TestMainLifecycle`，单一场景 A–F）— PASS

数据集：文本 / 1 MiB 二进制 / 多层目录 / 空目录（含 Unicode 空目录）/ 中文+emoji 文件名 / >260 字符长路径 / 4 MiB unchanged 观察位 / to_modify / to_delete。

- **A init**：exit 0；repo.json 卷身份 == 真实 `GetVolumeInformationW` serial（4440B938）；hash_algorithm=blake3；config.d TOML 解析核对 source/target；目录结构恰为六项（snapshots/manifests/manifests.tmp/locks/logs/repo.json），无意外文件。
- **B backup#1**：exit 0；manifest complete；文件条目数 == 数据集 == 快照实文件数（长路径文件计入，经 `\\?\` 遍历验证）；报告落盘 logs/ 且 status complete；`verify --all` exit 0。
- **C 变更**：新增 + 修改 + 删除 + 新子目录（moved.txt 复制）。
- **D dry-run**：人类模式统计行正确（新增 2 / 修改 1 / 删除 1）；`--json` 明细含 new_file.txt/moved.txt/bin/to_modify.bin/docs/to_delete.txt；**零持久写入**：整仓库树（含 mtime、sha256）指纹逐字节不变，无锁文件。
- **E backup#2**：exit 0；新旧 manifest 均 complete；**旧快照不可变**（文件树+manifest 逐字节不变）；删除文件不在新快照；新增文件在；**硬链接复用**：unchanged 文件 s1/s2 同 NTFS file index（st_ino）且 nlink≥2，修改文件 inode 不同（真实复制）；新快照与源逐文件字节一致；`verify --all` exit 0；报告 linked 含 unchanged 文件、bytes_written ≈ 350 KiB（仅新增/修改）。
- **F list**：普通输出含两个快照 id 与 complete；--verbose 输出 文件/目录/共 统计（符合最新 CLI_SPEC）；--json 可解析为单一 JSON 文档（snapshot_id/status/created_at/stats{files,dirs,total_bytes}）。

## 4. Snapshot immutability / hardlink 证据 — PASS

见 §3 E：旧快照树 sha256+mtime 指纹不变、manifest 逐字节不变、unchanged 文件 st_ino 跨快照相同且 nlink≥2。性能基线另证：10,006 文件 unchanged 二次备份 bytes_written=0、linked=10,006、物理占用仅 +0.01 GB。

## 5. verify / corruption detection — PASS

`TestVerifyCorruption`（专用仓库副本，不污染 golden 源）：翻转 snapshot 文件中间字节（尺寸不变）→ full verify **exit 4**，输出与落盘报告均准确定位 `bin/blob.bin`（issue kind 哈希不匹配）；`--quick` 对同尺寸损坏按冻结语义不重算哈希（CLI_SPEC §3）通过——如实记录为设计行为，非误报。

## 6. restore 验收 — PASS

- 整快照恢复到空目录：与源**字节级一致**（递归 sha256 对账，含 Unicode/emoji、深层目录、空目录、>260 长路径）。
- `--path` 单文件：只出现 docs 子树；`--path photos` 子树：整棵子树含 Unicode 空目录，docs 不出现。
- `--overwrite never`：已有用户文件字节不动，exit 3（partial，skip 列明）。
- `--overwrite always`：用户文件被快照内容覆盖（已存在目录计入 skip → exit 3 属冻结 partial 语义，文件全部按计划覆盖）。
- 恢复后源快照树指纹不变（restore 不修改仓库）。

## 7. 中断 / incomplete / resume（TR-5）— PASS（REAL E2E）

`TestInterruptedBackupResume`：真实 `python -m mirrorly backup` 子进程物化 480 MiB（60×8 MiB）中途 `TerminateProcess` 杀进程：

- 中断后 manifest 为 **incomplete**（非虚假 complete）；残留锁 → 下一实例 **exit 6**（冻结语义：崩溃锁由用户确认后手工删除）；按文档删除锁文件后 `--yes` 续传；
- 续传产出**新 snapshot id**，resumed_from == 中断 id；旧 incomplete 目录+manifest 善后删除；无 .mrtmp 残留；`verify` exit 0；
- **复用证据**：bytes_written < 数据集总量（仅补缺失文件），linked 非空。

注：早期运行中该用例曾两次被宿主 safe-delete 护栏拦截（count 53–61 > 50，环境限制，非断言失败）；最终配额刷新后已重新执行并**全部通过**（E2E 最终 13 passed / 0 failed，见 §12），无遗留待复跑项。

## 8. M10 卷身份 / 盘符漂移 — 卷锚自动重定位（M10 blocker 修复后 PASS）

**语义最终裁定（产品负责人，架构 review 后）**：盘符漂移要求 **A + B 同时成立**——A：已登记目标卷仍在线、只是 mount point / drive letter 改变 → 自动识别同一卷并定位原仓库继续工作（**不要求用户修改 target.path**）；B：找不到已登记卷、身份不匹配、定位结果不唯一 → fail closed，绝不写替代位置。CLI_SPEC §6 exit 5 描述的是 B 的失败行为，不否定 A。

**实现（volume-anchored relocation）**：

- 信任锚 = **Volume GUID + volume serial + repo_id**（安全识别链）；volume label 仅诊断输出，不参与匹配（裁定：label 用户可改、跨卷可重复，GUID 优于字面「卷标」）。
- `init` 登记三字段：`[target] volume_guid / repo_id / repo_dir`（all-or-none：半套配置 ConfigError，拒绝从 GUID 安全模式静默降级）；repo.json 增量可选 `volume.guid`（format_version 不变，旧文件缺键兼容）。
- resolver 七态 fail-closed 状态机（`cli._resolve_repo`）：Case 1 全匹配正常；Case 2/3 path 失联或被其他卷占用 → `GetVolumePathNamesForVolumeNameW` 直查 expected GUID 当前挂载点 → repo_id + serial 全确认后重定位（**运行时 resolution，不自动改写 config**；stderr 提示）；Case 4 repo_id 不匹配 → exit 5；Case 5 多候选（samefile 去重后仍 >1）→ exit 5 不猜；Case 6 GUID 无挂载点 → exit 5「目标备份卷未连接或卷锚已失效」；Case 7 卷在但仓库缺失 → exit 1 不自动 init；legacy 配置（无锚）：path 有效走旧行为，失联 exit 1 + 重新登记提示，**不自动猜卷**。
- **无身份降级 fallback**：GUID 解析失败时绝不用 serial+repo_id 自动认领另一个卷（`GetVolumePathNamesForVolumeNameW` 为主定位器；`FindFirstVolume` 枚举仅诊断用途）。
- `repo_dir` 安全边界：config load / write / resolver 入口三重校验 + join 后 containment 复验（canonical 卷内相对路径，禁绝对/UNC/`..`/盘符限定；卷根为 `.`）。
- 重定位成功不改写 TaskConfig（最少可变状态）；`--json` stdout 纯度不受影响。

**E2E 结果**（全部真实 CLI 子进程）：

1. 正确卷 → 全部主流程正常（§3 隐含）。
2. **错误 volume serial → exit 5、零写入**（「插错盘」经篡改 repo.json 记录 serial 构造，与「同一路径出现另一卷」在身份校验边界完全等价）：整仓库树含 mtime 逐字节不变、无锁、无快照、无报告。
3. **盘符漂移自动重定位（REAL API）**：配置 path 失联（仿真旧盘符消失；真实盘符漂移正是此效果）+ 锚三字段不动 → 真实 `GetVolumePathNamesForVolumeNameW` 链路自动定位原仓库继续累积（不新建错位置仓库、配置未被自动改写、verify --all 通过）——满足冻结 T-10 #2 与 US-3 字面语义。
4. **GUID 未挂载 → exit 5、零写入**（REAL API，随机 GUID）：系统上同时存在 repo_id/repo_dir/serial 完全匹配的真实仓库——证明无 serial 降级认领。
5. **卷在但仓库缺失 → exit 1、不自动 init**（仓库搬迁走 rename，原位置不被重建）。
6. 旧盘符被另一卷占用（unit 级 fake provider）：旧路径零写入、GUID 定位 expected 卷后继续。
7. 真实双卷（C:/P:）存在但未做真实盘符改动实验（用户明确禁止）——该项 NOT REQUIRED BY FROZEN MVP（MVP_TASKS 允许「本地 NTFS 分区仿真」）；真实外置盘换口（TR-7 验证方式原文）保留为真机手工验收项。

**Volume GUID 语义说明**（避免过强宣传）：Volume GUID path 是 Windows 安装/挂载管理器级的卷锚（首次安装与格式化时由 OS 分配），适合本 MVP「同一台 Windows 机器上的 drive-letter 漂移」场景；若因重新格式化、系统迁移/重装、挂载管理状态变化等原因登记 GUID 不再可解析，Mirrorly fail closed，需要显式重新登记（重新 init）。它不是跨系统、跨重装、硬件级永久身份。

## 9. reparse / junction 真机补验 — 4 PASS + 4 SKIP（环境限制）

环境探测（真实 Windows）：junction 可创建、`FILE_ATTRIBUTE_REPARSE_POINT` 可见（lstat 与 GetFileAttributesW 均 0x410）；目录/文件 symlink 创建被拒（`CreateSymbolicLinkW` ERROR_PRIVILEGE_NOT_HELD；沙箱内 os.symlink 更是静默失败）。

`_make_symlink` 测试辅助增加目录场景 junction 回退（junction 是**真实 reparse point**，产品 `_check_no_reparse_chain`/`_file_attributes` 走完全相同防护路径——非 mock）：

| 用例 | 类型 | 结果 |
| --- | --- | --- |
| test_destination_root_reparse_rejected | 目录 | **PASS**（真实 junction） |
| test_mid_batch_dest_ancestor_becomes_reparse | 目录 | **PASS**（真实 junction） |
| test_repo_alias_via_junction_rejected | 目录 | **PASS**（真实 junction） |
| test_source_alias_via_junction_requires_in_place | 目录 | **PASS**（真实 junction） |
| test_snapshot_side_reparse_rejected | 文件 | SKIP：无法创建文件 symlink（需管理员/开发者模式） |
| test_destination_side_reparse_is_conflict | 文件 | SKIP：同上 |
| test_mid_batch_snapshot_leaf_becomes_reparse | 文件 | SKIP：同上 |
| test_leaf_becomes_reparse_during_copy_not_traversed | 文件 | SKIP：同上 |

未覆盖风险（如实声明）：文件级 symlink 的 reparse 防护未在真机验证；缓解：与目录 reparse 共享同一产品防护代码路径（已真机通过），且相关单元语义已有 mock 级回归。建议在有管理员权限/开发者模式的机器补验一次。

## 10. 性能基线（已归档 DEVELOPMENT_LOG；脚本 `scripts/t10_perf_baseline.py`）

环境：Windows 11、Python 3.12.14、C: NTFS（卷标 OS）、Mirrorly 0.0.1 @ a31126b。介质类型未能可靠探测（未识别 SSD/HDD，不猜）。

| 指标 | A：混合（10,006 文件 / 9.6 GB，含 4.5 GB 大文件 + 10,000×256 KiB + 4×512 MiB） | B：少而大（16×256 MiB / 4 GB） |
| --- | --- | --- |
| 首次 backup | 51.7 s，写入 9.6 GB | 15.9 s，写入 4.3 GB |
| 二次 unchanged backup | 6.5 s，写入 0 B，硬链接复用 10,006 | 2.5 s，写入 0 B，复用 16 |
| 小变更 backup | 12.0 s，写入 563 MB（+101 文件/改 1 中型） | 1.0 s，写入 268 MB（改 1 文件） |
| verify full（--all，3 快照） | 33.4 s | 5.9 s |
| verify quick（--all） | 3.0 s | 0.6 s |
| restore 整快照 | 59.7 s | 4.3 s |
| 空间占用（卷剩余变化） | 数据集 −8.95 GB；backup#1 后再 −8.94 GB；unchanged #2 仅 −0.01 GB（硬链接生效）；#3 −0.56 GB；restore −8.97 GB | 数据集 −4.0 GB；backup −4.0 GB；unchanged 0；#3 −0.25 GB；restore −4.0 GB |

（完整 JSON 备份于验收记录；数字为单次测量基线，不设通过阈值——冻结要求如此。）

## 11. 已知限制清单（MVP）

1. **M10 自动重定位限「同一 Windows 安装内」**：Volume GUID 为 OS 级卷锚（格式化/系统重装/挂载管理状态变化会失效）→ fail closed 后需显式重新登记；仓库跨卷拷贝/迁移（repo_id 匹配但卷锚不匹配）不接受，未来如有需要走显式 migration 工作流（裁定）。
2. **真实外置盘换口（真盘符漂移）未真机执行**（禁止改真实盘符；重定位以真实 GUID API 链路 + 配置 path 失联仿真验证，TR-7 原文验证方式保留为手工验收项）。
3. **崩溃后任务锁需手工删除**（有意取舍：不自动清理防误判活锁；E2E 验证 exit 6 + 文档化手工步骤）。
4. **verify --quick 不重算哈希**：同尺寸静默损坏不被 quick 模式发现（冻结设计，full verify 覆盖）。
5. **文件级 symlink reparse 防护未真机验证**（本机无 SeCreateSymbolicLinkPrivilege；目录级 junction 已真机通过，代码路径相同）。
6. **restore --overwrite always 到已存在目录**：已存在目录计入 skip → exit 3（partial）而非 0（冻结 partial 语义，明细列明「目录已存在」，无数据风险）。
7. **list --verbose 无「增量大小」**（T-09 裁定：MVP 后候选，须 backup 时持久化权威值）。
8. exFAT 整文件复制模式（D2 降级）未做外置盘真机验收（沙箱无真实 exFAT 卷；strict/warn 逻辑有单元覆盖）——NOT REQUIRED BY FROZEN MVP（可用本地 NTFS 仿真口径）。
9. **旧 anchored 配置被更旧版本代码读取**：新 `[target]` 三键对旧版本严格模式是「未知配置键」→ 报错拒绝（向前不兼容，如实接受；MVP 未发布）。

Post-MVP hardening 项（非阻塞，非当前数据安全缺陷；本轮不修改代码）：

10. **`repo.get_volume_info()` 的卷根获取方式**：当前按 `Path.anchor` 推导 `GetVolumeInformationW` 查询根。普通 drive-letter 外置盘场景正确；若未来正式支持 mounted-folder-only volume，应改用 `GetVolumePathNameW` 获取实际卷挂载根。冻结 MVP 非 blocker——post-MVP compatibility hardening。
11. **`volume.list_mounted_volumes()` 枚举终止语义**：当前 `FindNextVolumeW` 失败即结束枚举，未区分 `ERROR_NO_MORE_FILES` 与真实枚举错误。该函数目前仅诊断用途，不参与 resolver 身份接受路径——post-MVP diagnostic hardening。

## 12. 测试与工具链结果

- 新增 `tests/test_e2e.py`（13 用例，真实 CLI 子进程 E2E，含 M10 重定位/GUID 未挂载/仓库缺失三用例）
- 新增 `scripts/t10_perf_baseline.py`（性能基线）
- 修改 `tests/test_restore.py`：`_make_symlink` 目录场景 junction 回退（真机补验）
- M10 修复（架构 review 后）：新增 `src/mirrorly/volume.py`（Windows 卷定位 API）+ `tests/test_volume.py`（7 用例真实 API smoke）；`tests/test_cli.py` 增 `TestM10Resolver`（11 用例状态机）；`tests/test_config.py` 增锚校验（16 用例）
- E2E 最终确认运行：**13 passed / 0 failed**（含此前被宿主 safe-delete 护栏拦截的中断/retention 用例，本轮全部转绿）
- reparse 专项：4 passed / 4 skipped（4 个目录级 junction 用例在本轮沙箱内亦通过）
- ruff check：All checks passed；ruff format --check：40 files already formatted
- **完整 pytest 全套回归：452 passed / 4 skipped / 0 failed**（43.5 s，basetemp 用 OS 临时目录；4 skip 均为文件级 symlink 权限限制——`无法创建文件符号链接：无管理员/开发者模式`，与 §9 一致）

## 13. 结论

**PASS WITH ENVIRONMENTAL SKIPS（M10 blocker 已修复并验证）**

- 冻结 T-10 四项验收标准均有真实证据（§1）；主流程 / 不可变性 / 硬链接 / 字节级恢复 / 损坏检测 / TR-5 中断续传 / M10 身份安全（含卷锚自动重定位）/ retention / 性能基线全部通过
- 环境性 skip：仅 4 个文件级 symlink 用例（权限限制，§9）；完整 pytest 全套 452 passed / 4 skipped / 0 failed 已复跑确认（§12）
- M10 blocker（盘符漂移无自动重定位）已按产品负责人裁定修复并验证（§8）；无其他生产代码缺陷；无冻结规范冲突
- **最终裁定（产品负责人，2026-09-13）：MVP ACCEPTED — PASS WITH ENVIRONMENTAL SKIPS**；允许的环境性 skip 仅限 4 个文件级 symlink 用例（§9）；MVP 开发阶段就此结束
