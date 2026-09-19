# Mirrorly CLI 当前规范

> 当前行为同步：2026-09-19 · 生产基线：`1fb0cde`
> 本文件维护当前命令结构、参数、退出码及已批准的安全行为；本次同步不新增功能。冻结 MVP CLI v1.0（2026-09-12）保留在 `v0.1.0-mvp` 的 Git 历史中，MVP 验收结论与数字不变。

## 0. 总则

- 入口：`mirrorly <command> [options]`（`python -m mirrorly` 等价）。
- 全局选项：`--config <path>`（配置目录，默认 `./.mirrorly`）、`--task <name>`（指定任务；现有配置恰有一个任务时可省略，多个时必填）、`--quiet / --verbose`、`--no-color`、`--json`；可放在子命令前或后。`mirrorly --version` 显示 package version（当前 `0.0.1`，不等于历史 tag 名）。
- Restore 覆盖须显式确认或 `--yes`；重复 init 已有仓库始终拒绝，`--yes` 不授权重建它。
- 输出约定：人读输出到 stdout；`--json` 时输出机器可读 JSON（为 GUI 预留）；错误信息到 stderr。
- `--json` 的人读提示走 stderr，stdout 保持单一 JSON 文档；若需要确认却未给 `--yes`，拒绝交互并返回 6。
- 备份/校验报告（M9）除终端摘要外，必须写入目标仓库 `logs/` 目录；报告发布失败返回 1，见 §2/§3。Restore 的结果为终端/`--json` 输出，不额外写此类日志。

---

## 1. `mirrorly init`

初始化备份仓库（目标目录）与任务配置。

| 参数 | 说明 |
| --- | --- |
| `--source <dir>` | 备份源目录（必填） |
| `--target <dir>` | 备份目标根目录（必填，其下创建 `MirrorlyRepo/`） |
| `--filesystem-policy <strict\|warn>` | 非 NTFS 目标的策略，默认 `strict`（D2 确认：exFAT 明确提示，用户决定继续或转 NTFS；`warn` 模式下用户确认后以整文件复制运行，初始化摘要标明降级 / `hardlinks=false`） |
| `--yes` | 跳过交互确认 |

行为：源/目标与配置预检 → 检测目标文件系统及卷锚 → 建立 durable `lifecycle.json` → 发布 format v2 `repo.json`（格式版本、卷标识、哈希算法）→ 生成 `config.d/<task>.toml` → 输出摘要。未指定 `--task` 时新任务名为 `default`。
幂等：目标已是仓库或任务配置已存在则报错，`--yes` 不改变这一拒绝行为。Source 与 repository 互相包含时拒绝。

---

## 2. `mirrorly backup`

执行一次备份，生成新快照。

| 参数 | 说明 |
| --- | --- |
| `--dry-run` | 只输出变更预览（新增/修改/删除统计与列表），不写入任何内容（M8） |
| `--full-hash` | 对与基线同大小的源文件强制哈希复核，不再仅凭 mtime 判未变；新增/大小已变文件直接进入复制流程，不改变 write-verification 配置 |
| `--exclude <pattern>` | 追加排除规则（可重复），与配置合并 |
| `--yes` | 自动接受续传确认（用于外部计划任务/脚本；Mirrorly 无内置调度） |

正式执行：配置/仓库身份解析 → task lock → repo writer lock → 必要时 v1→v2 迁移 → 选择基线、扫描与变更检测 → ownership-aware temp cleanup → reserve 新 sequence / snapshot ID → incomplete manifest → 物化与写入校验 → complete manifest 原子发布 → resumed incomplete 善后 → retention → 必需报告。

锁覆盖整个正式 backup transaction（含 report）。`locks/<task>.lock` 保留同任务互斥，`locks/repo-writer/active.lock` 使不同 task 写同 repo 也互斥；占用返回 6。正常/异常退出释放本进程创建的锁，崩溃可能留 stale lock。只有人工确认无相关备份进程后才可移除对应 lockfile；不按 PID 猜测、不自动夺锁，`locks/repo-writer/` 目录保留。

基线与续传：

- 自动 complete 基线只取 v2 manifest 中最大 `lifecycle_seq`，无 sequenced complete 时使用空基线；即使有 legacy complete，也全量物化 current source。
- 自动续传仅考虑比最新 sequenced complete 更新的 v2 incomplete，选最大 sequence；无 sequenced complete 时选最大的 v2 incomplete。Legacy 与较旧 incomplete 不自动选。
- 接受续传：先认证旧 incomplete 的可用条目，将其作为只读基线；新 attempt 使用新 snapshot ID 和新 sequence，成功提交后 discard 被使用的 incomplete。拒绝续传：改用最新 sequenced complete 或空基线，旧 incomplete 不动。
- Dry-run 不获取 mutating locks、不迁移、不 reserve sequence、不写 report；它按相同的 sequenced-complete / 空基线规则预览，遇到可续传 incomplete 只提示正式运行时会询问，不预先接受续传。

配置 `[verify] on_write=true`（默认）时，copied 文件进行源/目标哈希比对；resumed 条目需有可信认证，缺少可信 sha 的基线（含 False→True）须重新物化。True-mode complete 发布前拒绝普通文件 hash coverage gap。关闭 on_write 后不保证每个文件都有 sha。普通文件所需 mtime 在 staging temp 上完成后再 replace，不修改历史 hardlink 的 metadata。

默认 retention 为 `keep_last=30` 与 `keep_monthly=12` 的保留集合并集。V2 keep-last 按 sequence；月窗口由当前 UTC 年月决定、month membership 来自 `created_at`，同月代表取最大 sequence。Legacy complete 全部自动保护，incomplete 不纳入自动 retention；实际保留数可能超过 keep-last。

**Complete manifest 原子发布是快照 safety commit point**，不是整个命令的最后一步。若后续 report I/O publication 失败，仍返回 **1**，错误明确给出 snapshot ID、「已成功提交」及「必需报告发布失败」。Discard/retention 等后续步骤也可能失败；非零退出本身不代表没有 complete，请检查实际仓库状态。报告长路径处理详见 [STATUS](STATUS.md)，不承诺全控制面任意长路径支持。

---

## 3. `mirrorly verify`

校验备份完整性。

| 参数 | 说明 |
| --- | --- |
| `--snapshot <id>` | 只校验指定 complete 快照（默认选择规则见下文） |
| `--all` | 校验所有 complete 快照；与 `--snapshot` 互斥 |
| `--quick` | 只检查存在性、类型、大小，不重算哈希 |

默认选择最大 `lifecycle_seq` 的 v2 complete；无 v2 时，唯一 legacy complete 可选，多个 legacy complete 则返回 1，要求 `--snapshot` 或 `--all`，不按时间猜 latest。无 complete 时返回 1。

Full 模式检查存在性/类型/大小，并按 manifest 已有 sha 重算比对；`sha=None` 条目跳过哈希、计入 `unhashed_entries`，本身不导致失败；清单外文件计入 `extras`，只报告。Full verify 校验的是 snapshot ↔ manifest，不证明 snapshot 等于备份当时的源。要解释完整性覆盖，应同时查看 `hashed_files` / `unhashed_entries`，不能把 False-mode 或旧无哈希 manifest 的成功结果当作全文件哈希校验。

计算结果与逐项差异写入必需 JSON report。若报告发布失败，返回 1，并明确「校验已完成（结果：通过/失败）、必需报告发布失败」，不使用 backup 的「快照已提交」措辞。报告正常发布时，完整性问题返回 4，无问题返回 0。

---

## 4. `mirrorly restore`

从快照恢复文件。

| 参数 | 说明 |
| --- | --- |
| `--snapshot <id>` | 指定 complete 快照；默认与 verify 相同，取 sequence latest，legacy-only 多快照时要求显式 ID |
| `--to <dir>` | 恢复目标目录（必填；不允许直接覆盖源目录，除非 `--in-place`） |
| `--in-place` | 允许恢复到记录的原始源位置（危险操作，必须显式 `--yes`，仍须提供 `--to`） |
| `--path <path>` | 只恢复指定的文件/子树（可重复；**字面 snapshot-relative 路径，非 glob**——T-08 冻结语义，`dir/file.txt` 命中文件自身、`dir` 命中整棵子树） |
| `--overwrite <never\|older\|always>` | 目标已存在时的策略，默认 `never`（TR-4：防覆盖用户新数据） |

行为：列出将恢复的文件与覆盖情况 → 必要时确认覆盖 → 重新核对 plan 与安全边界 → 复制恢复 → 终端/JSON 结果。恢复产物为普通文件；restore 不自动做 full verify，用户可先运行 verify 再恢复。

Manifest entries 使用 shared canonical lexical validator，路径错误仍以 Restore 的错误契约向外报告；filesystem/reparse/destination 与 commit-time protections 是独立边界，继续保留。恢复目录不得位于 repo 内；file/dir 冲突不通过删除用户数据解决。跳过、冲突、单文件错误或 temp leftovers 返回 3，partial restore 无整体回滚。

---

## 5. `mirrorly list`

列出快照。

| 参数 | 说明 |
| --- | --- |
| `--json` | JSON 输出 |
| `--verbose` | 显示每快照统计（文件数、目录数、总大小、状态） |

行为：读 `manifests/` 目录列出全部快照（id、时间、状态 complete/incomplete、统计），按 manifest 文件名顺序展示，**不是 lifecycle latest 排序接口**。列表读取也做 shared canonical entry-path validation 及 sequence 唯一性检查；incomplete 明确标注。`--verbose` 不提供未经持久化的「增量大小」。

---

## 6. 退出码规范

| 码 | 含义 | 典型场景 |
| --- | --- | --- |
| 0 | 成功 | 全部完成，无跳过项 |
| 1 | 一般错误 | IO 错误、磁盘满、manifest 拒绝、legacy latest 不明确、必需报告发布失败（包括快照已提交后的报告失败） |
| 2 | 用法错误 | 参数缺失/非法（argparse 约定） |
| 3 | 部分完成 | backup 有已记录的扫描/复制跳过项；restore 有跳过、冲突、单文件错误或 leftovers。其他致命 IO/校验错误仍可返回 1，不保证每类锁定/权限错误都能跳过 |
| 4 | 校验失败 | verify 发现哈希不匹配/文件缺失 |
| 5 | 目标身份不符 / 仓库格式错误 | 卷/仓库身份不符、锚解析失败或歧义；repo 格式不兼容、v2 lifecycle state 缺失/损坏。已登记卷可可靠定位时盘符漂移会自动重定位，不直接判失败 |
| 6 | 用户中止 / 锁占用 | 必要确认未获允许（backup 拒绝 resume 可继续 fresh attempt）；任务锁或 repo writer lock 被占用 |
| 130 | Ctrl+C | 遵循 Unix 约定 |

脚本约定：`backup` 用 0/3 区分"干净完成"与"有跳过"，计划任务场景应对 3 发提醒而非视为失败。

---

## 7. 明确不做的命令（v1）

- 不设 `sync`（W1）、不设 `mount`/`browse`（超出 MVP）、不设守护/监控类命令（W5）、不提供 `config edit` 等配置管理命令（手改 TOML 即可，保持 CLI 面最小）。
