# Operation、queue、progress 与 cancellation

> 2026-09-19 · 产品约束 APPROVED；具体字段、转换和 checkpoint 为 PROPOSED DESIGN，待 review。
> 当前没有 worker/progress/cancellation API；不得把以下事件当作已有能力。

## 运行状态、结果与长期健康分开

一个枚举不足以表达 Mirrorly。建议至少保留以下互相独立的事实：

| 维度 | PROPOSED DESIGN | 说明 |
| --- | --- | --- |
| `execution_state` | `queued / preparing / running / awaiting_confirmation / finalizing / ended / outcome_unknown` | 一次用户请求的生命周期；unknown 不是正常结束 |
| `phase` | 下文阶段；未知时 `null` | 来自真实 backend 边界，不靠计时器推演 |
| `outcome` | `succeeded / completed_with_issues / failed / cancelled / not_started / unknown` | 已知结果由 terminal result 或明确的队列移除产生；运行中为 `null`，失联 projection 用 unknown |
| `snapshot_commit` | `not_started / not_committed / committed / unknown / not_applicable` | Backup 特有事实；Restore 不发布 snapshot，使用 not_applicable |
| `finalization` | `not_started / in_progress / succeeded / failed / unknown / not_applicable` | Backup 的提交后必要收尾；report/retention 各保留子结果 |
| `cancellation_state` | `unsupported / available / requested / waiting_for_checkpoint / cancelling / too_late / settled` | 与 execution_state 正交；unsupported 是当前能力 |
| `availability` | `unchecked / available / unavailable / unknown` + `observed_at` | 当前/最近一次 destination 检查，不能推翻历史结果 |
| Backup projection | `last_attempt`、`last_clean_completion`、`last_saved_snapshot`、`unresolved_issues` | 有时间/身份/来源；不提供无依据的 source up-to-date 布尔值 |

`not_started` 用于确认未 dispatch 的请求；已执行操作的 `not_committed` 必须有 backend 证据。失联时不能把“没有看到 committed event”当作“尚未提交”。Restore 保留真实写入/跳过/冲突/错误/残留统计；当前 `RestoreResult.restored` 合并 create/overwrite，不可从它捏造两者各自的精确数量。失联后部分写入范围也可能 unknown。

常规转换为 `queued → preparing → running → finalizing → ended`；仅 Backup 在已 committed 后进入此处的 finalizing。确认可暂时进入 awaiting_confirmation，答复后回到实际阶段；任何阶段都可能失败或失联。已提交事实单调保留，不能因后续错误/取消请求退回 not_committed。Terminal result 结束 operation；晚到/重复事件不能把它重新变成 running。

### 必须通过的展示场景

| 真实事实 | 用户主文案示例 | 持久记录 |
| --- | --- | --- |
| complete，必要收尾/report 成功，无报告问题 | Last backup completed successfully + 时间 | 成功；不称当前 source Up to date |
| complete，有 skipped items，收尾成功 | Backup completed with issues | 显示未纳入内容与原因，不只绿色勾 |
| 已发布 complete，之后 retention/report 等失败 | A backup version was saved, but finalization encountered a problem | `failed + committed + finalization failed`，保留 snapshot 和失败事实 |
| backend 确认未发布，操作失败 | Backup failed before a new version was saved | `failed + not_committed`；incomplete 是否存在另报 |
| 本次解析/访问确认目标不可用 | Destination currently unavailable | 本次检查时间；历史成功/版本标记为 cached |
| 已加入队列未开始 | Waiting in queue | 不算备份失败，展示位置/Remove |
| 正在准备/写入/收尾 | Preparing / Backing up / Finalizing | 持续 activity indicator，不先庆祝成功 |
| worker 失联、结果未确认 | Backup result couldn't be confirmed | `unknown`，保留已有证据，提供查看/重新检查；不能自动 Retry 写操作 |
| Restore 按 Skip 完成但有 skipped | Restore completed with skipped items | 保留 skips/conflicts，不改成所有文件已恢复 |

**CURRENT FACT**：[cli.py](../../src/mirrorly/cli.py) 在 complete publication 后仍执行 retention/report；[test_cli.py](../../tests/test_cli.py) 有 report 失败但 complete 仍可验证的用例。complete 允许 skips；exit code 不是 GUI success 的唯一证据。最终 result 必须同时表达这些事实。Warning/needs attention 是展示映射，不是新增 manifest status。

成功不清空其他未解决问题；queued/running 也不覆盖旧错误。Home 展示最新操作、上次结果和仍有效的问题摘要。持续问题的消除规则见 [ACTIVITY_SETTINGS](ACTIVITY_SETTINGS.md)。

## Backup queue

**APPROVED**：single execution slot、FIFO、自动 enqueue、同 Backup Running/Queued 去重、可移除未开始项；Close/Minimize 不停止队列；true Exit 清空未开始项，不跨退出持久化。

**PROPOSED DESIGN**：coordinator 为应用级对象，独立于页面/ViewModel。最小模型：

- `QueueEntry(operation_id, backup_id, enqueued_at, enqueue_sequence, config_revision)`；revision 是应用侧配置指纹，不新增 task TOML 字段。
- 有序 pending 集合 + 一个 active operation + 按 `backup_id` 的去重索引；所有 enqueue/remove/dispatch/exit 变化串行处理。
- 点击同一 Running/Queued Backup 返回已有 `operation_id`，引导到真实状态，不产生第二次执行。不同 Backup 自动排到末尾，显示 “Photos has been added to the queue”。
- dispatch 时重新加载配置、解析 repository 并验证 identity/revision。排队期间配置变化不能悄悄按新目标执行；该请求以 `not_started` 和明确原因结束，等待用户重新发起。离线/锁占用是该次操作真实失败，不无限重试阻塞后续队列。
- 只有服务确认 active 已结束且没有仍在执行的 operation 才释放 slot。明确失败后可继续下一项；outcome unknown 时内部禁止 dispatch，先收敛 worker 存活/结果，不能让用户看到“暂停队列功能已实现”。
- Remove 与 dispatch 竞争必须原子裁决：仍 pending 则移除并记 `not_started`；已 active 则拒绝 Remove，显示实际状态；不能偷偷转成 Cancel。
- 全部 queue 仅在内存。Activity 可以记录历史排队/移除，但启动时绝不从 Activity 重建 pending jobs。

**DEFERRED**：Back up all、reorder、priority、Pause queue、持久化队列、多 worker 并行。保持调度选择与列表展示分离，未来可替换选择策略/增加显式批量 enqueue；不预建优先级体系、磁盘队列数据库或通用工作流引擎。

**OPEN DECISION O-04**：Backup 串行的批准不等于已决定 Restore/verify 的并发。建议真实 IO 接入初期统一门禁，Restore/verify 忙时给出明确选择，不擅自加入 Backup FIFO。现有 verify/restore/list 不进入 backup writer 锁；GUI 内的串行也不能消除外部 CLI retention 与读取的竞争。需要单独 review，不能以 UI 限制宣称解决 core 并发安全。

## 真实流程与 progress contract

**CURRENT FACT**：`cmd_backup` 的顺序为解析 → 锁 → 必要 repo migration/基线选择 → scan → compare/hash → temp cleanup/sequence reserve/incomplete manifest → snapshot write → hash coverage guard → complete publication → resume 善后/retention/report。写入中的校验属于 write_snapshot 的工作，不是结束时自动 full verify。当前只有同步函数的最终统计，无 item/current-file/byte callback。

| 建议 phase key | 真实工作边界 | 当前可观测性与呈现 |
| --- | --- | --- |
| `queued` | GUI pending list | GUI authoritative，无百分比 |
| `preparing` | 解析配置/身份、获取锁、迁移、基线/恢复选择 | 提取 application service 后可发边界；indeterminate |
| `scanning` | `scan_source` | 需未来 observer；不能捏造总文件数 |
| `planning` | compare 与疑似项 hash；cleanup/reserve/write preparation | 需未来边界；hash 时间不算复制百分比 |
| `backing_up` | dirs、hardlink/copy、on-write check/retry | 当前只有最终 SnapshotResult，实时值 unavailable |
| `publishing` | coverage guard 与 complete manifest publication | 短阶段也必须真实；indeterminate，无假 100% |
| `finalizing` | resume 善后、retention、mandatory report | 提交后继续占用 slot；允许失败 |
| `verifying` | **独立** verify operation | 仅真实执行 verify 时使用，不插入每次 backup |
| `restoring` | restore plan/apply 中的 apply | plan 属 preparing/planning；不假装有 snapshot commit |

未接入阶段边界时，只能在真实调用前显示 Preparing，在同步调用期间显示 Working，返回后展示结果。不能模拟上述内部阶段流转。Completed 是 terminal outcome 的展示，不是用于补满 progress bar 的工作阶段。

**PROPOSED DESIGN progress payload**：

| 字段 | 约束 |
| --- | --- |
| `phase` / `phase_instance` | 稳定 key + 本次进入阶段的序号；phase 改变时不能沿用旧 denominator |
| `items` / `bytes` | 可空对象：`completed`、`total`（可 null）、`unit`、`scope`、`measurement="authoritative"`；计数非负整数 |
| `current_item` | 可 null，真实正在处理的相对路径/安全展示名；不能由 UI 猜“下一个” |
| `elapsed_ms` | worker monotonic elapsed；排队等待时间另算，不受系统时钟调整影响 |
| `observed_at` | UTC 时间，用于新鲜度；不是排序/剩余时间依据 |

scope 必须命名计数对象，例如 `snapshot_entries_finished` 或 `copy_payload_bytes`；items 包括何种 entry/是否含 skips 必须在能力定义中声明。重试统计不能重复累计到伪造的 100%，hash/hardlink 与 copy bytes 分开。同一 phase_instance/scope 的 completed 单调，已知 total 时须满足 completed ≤ total；若分母不再可信则退回 indeterminate 并说明范围变化，不裁成假 100%。只有稳定、真实 denominator 才可在**该阶段** determinate；0/未知总量都不做除法，不把 unavailable 序列化为 0。计数可含 skips，但 issues 独立存在。整个 operation 不用 copied bytes / total bytes 计算百分比。

未来可用计数不代表当前能力：最终 `SnapshotResult` 可以形成 result summary，不能倒推出运行中采样。`estimated` ETA/推算总量首版不使用；无数据时省略指标，用 indeterminate、真实阶段及 elapsed 表达等待。

heartbeat 为独立事件，建议每 2 秒一次、超过 10 秒未见显示“状态暂未更新”（阈值为提案，需负载验证）。heartbeat 只说明协议/worker host responsive，不能声称文件 IO 有进展、没有阻塞或 operation 健康。UI 动画也只证明 UI responsive。持续大文件 IO 无 byte callback 时不得显示伪造 byte 增量。

## Cooperative cancellation（capability gap；PROPOSED DESIGN）

**CURRENT FACT**：没有 cancel token。`KeyboardInterrupt` 的退出/解锁测试和 kill recovery 测试不等于完整取消契约。强杀可能遗留锁/incomplete；现有 early incomplete 无目录树的恢复限制继续存在，不能拿它当正常取消路径。详情见 [STATUS](../STATUS.md) 与 [test_e2e.py](../../tests/test_e2e.py)。

Queued Remove 只改内存集合。Running Cancel 必须经 service 验证能力/阶段；capability 不支持时 UI 不提供可点击的虚假 Cancel。请求/确认接收不是操作已结束：

```text
available -> requested -> waiting_for_checkpoint -> cancelling -> settled
                  |                 |
                  +------ too_late --+-> 继续真实 publication/finalization -> ended
unsupported -> 返回 unsupported；operation 继续，不能显示 Cancelled
```

只有 backend terminal result 可将 outcome 标为 cancelled；无须取消、已结束、已提交等都须明确回复。重复 cancel 使用同一 operation identity，幂等返回最新取消状态。取消等待期间 slot 仍占用。

### Checkpoint 提案（O-02；实施前单独安全 review）

| 边界 | 推荐契约；尚未实现 |
| --- | --- |
| scan/hash/copy 等工作单元间 | 检查 token；正在进行的系统调用可继续，不保证立即停止 IO |
| temp/file materialization 内 | 只在确认 temp ownership、文件句柄释放/原子替换安全的点退出；不得半写成为 complete 内容 |
| sequence reserve / incomplete bootstrap | 作为受保护区完成既有发布纪律；不能增加一个落在已知不可恢复窗口的“安全 checkpoint” |
| complete publication 前最后检查点 | 在此实际接受取消后不得发布 complete（协议收到请求的 ack 不等于此裁决）；按既有 ownership/recovery 规则保留或清理本次 incomplete，结果明确残留与 repo lock 状态 |
| complete publication 临界区 | 建议不可取消；取消与 publish 由 service 单点裁决，不能同时回复 cancelled 又实际发布成功 |
| 已 committed / Finalizing | 建议不接受取消，回复 too_late/committed，继续必要善后、retention/report；保留已提交 snapshot，不撤销发布 |
| Restore | 在既有 plan/apply 安全边界接受；已创建/替换的目标文件保留，不回滚/删除；最终显示部分恢复及数量，未知则明说未知 |

终止取消路径还要完成必要诊断/结果与释放自己拥有的锁。必要清理/report 本身失败则 outcome 为 failed，并保留“请求过取消”和副作用，不能只用 Cancelled 掩盖。不可抢夺他人锁或依据文件名后缀清理无所有权证据的文件。具体 incomplete/report 处理是 O-02 的实现设计，不在本轮修改 core。

## Close / Minimize / Exit 状态机

**APPROVED**：`visible -> minimized` 留 taskbar；`visible -> hidden_to_tray` 保持 desktop process；两种状态都保持 active 与 pending。首次 Close 的轻量提示及 tray 见 [DESIGN_RESOURCES](DESIGN_RESOURCES.md)。

**PROPOSED DESIGN**：区分退出意图和确认真正退出，避免先清队列再发现用户改变主意：

```text
normal -> exit_intent (暂不 dispatch 新项，等待 Running 策略裁决)
exit_intent -> normal (用户撤回，queue 保留)
exit_intent -> exit_committed (关闭 admission，清空未开始项)
exit_committed -> 等待 active 按已批准策略结束 -> flush GUI store -> exited
```

无 active 时可直接接受 Exit 并清 pending；已移除的项记 `not_started / app_exit`，不是 worker cancelled。不跨真正退出保存待运行请求。最终窗口关闭/销毁只能在该流程结束后进行，不能等同 X。

**OPEN DECISION O-01**：有 Running 时的选择尚未批准：

| 候选 | 用户体验与约束 |
| --- | --- |
| 完成当前任务后退出 | 清 pending 后等待当前完整收尾；可在没有 cooperative cancel 时工作，但大文件/IO 阻塞可能久等 |
| 请求安全取消后退出 | 只在 O-02 能力完成后可用；等待 checkpoint；已 committed/finalizing 时仍需等待，不强杀 |
| 退出 UI、worker 独立继续 | 需要 broker/重连/通知所有权与 orphan 方案；超出当前最小 child-session 设计，建议 DEFERRED |

以上是候选，不替用户选择。“立即强制终止”不作为正常 Exit/Cancel。Windows 注销、关机、应用崩溃是异常中断场景，不承诺能阻止系统终止；下次按现有锁/recovery 纪律和 unknown result 处理，不自动清锁/重放队列。
