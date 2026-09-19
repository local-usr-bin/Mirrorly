# Worker IPC contract draft v1

> 2026-09-19 · 全文为 PROPOSED DESIGN，O-03 待 review；目前没有 worker 或可调用的 IPC API。
> 协议版本与 Python package `0.1.0`、repo/manifest format 版本互不替代。

## Transport 选择

**APPROVED**：版本化本地 IPC，独立 Python worker。具体 transport 推荐 **redirected stdio pipes + UTF-8 NDJSON**，只服务本用户桌面进程与其 child；不引入 local HTTP server。

| 候选 | 优点 | 成本/适用边界 |
| --- | --- | --- |
| Redirected stdin/stdout + stderr | 无端口、无公开 pipe name；父子进程现成流；少量代码即可测试双向请求/事件 | 需并发读取/背压；父进程结束不能自然重新 attach；stdout 必须协议专用 |
| Windows named pipe | 独立地址、双向连接，可为未来 broker/reconnect 服务 | 需用户 ACL、实例身份、连接认证/会话与孤儿处理；Python/Windows interop 增加工作 |

当前没有后台服务、多 client 或跨启动队列需求，stdio 足够；named pipe 留作运行时需求变化后的替换，不同时实现两个 transport。Microsoft 的 [pipe 概述](https://learn.microsoft.com/en-us/dotnet/standard/io/pipe-operations) 区分了匿名父子 pipe 与 named pipe；[Process 重定向文档](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo.redirectstandardoutput?view=net-10.0) 明确提醒 stdout/stderr 同步读取可能死锁。

## Session、framing 与流纪律

- Desktop 显式启动可信 Python 路径/模块，`UseShellExecute=false`，不经 shell 拼接命令；stdin/stdout 协议，stderr 有界诊断，双方异步持续 drain。路径作为 JSON 数据，绝不 eval/执行用户提供的字符串。
- 每行一个 UTF-8 JSON object，以 LF 结尾，无 BOM；换行在 JSON 字符串内转义。接收端支持拆包/合包；空行、非法 UTF-8、截断帧、超限帧视为 protocol fault，不当作业务成功。
- 建议最大单帧 1 MiB；分页/分批返回列表，禁止整份大 manifest 塞入 event。分页结果有 repo/snapshot identity、manifest revision 与 continuation token；源发生变化则要求重新读取，不合并不同版本分页。限额/页大小在实现 review 中锁定。
- 每个 session 一个 `session_id`，worker 先发 `hello`，client 回 `initialize`，worker 返回协商后的版本/capabilities。握手前不接收业务操作；major 不一致拒绝，minor 取共同支持版本。允许新增可选字段，但不能忽略未知必需字段/安全枚举或未协商的语义。
- `request_id` 标识一次协议交互，`operation_id` 标识一次用户操作/队列项；一个操作可以有多次 cancel/confirm 请求。时间戳 UTC；worker 发出的所有消息带单调递增 `seq`（session 内从 1 起），不拿时间或 snapshot 名排序。
- 只允许一个 Backup active。协议 reader/heartbeat 与 operation executor 分离，取消请求不能排在耗时函数返回之后才读取。协议 host responsive 不代表 executor IO 前进。
- 进度可合并为最新值；状态转换、commit facts、confirmation、errors 和 final response 不得静默丢弃。使用有界缓冲，慢消费者时降低 progress 频率；不能让无界事件积压或 stdout 背压把核心持锁 IO 永久卡住。断连按 O-03 处理。
- `request_id` 去重仅限当前 session；相同 ID 不同 payload 为错误。重复请求返回已有 ack/result，不再次执行。内存会话去重不提供崩溃后的 exactly-once 承诺。

### 协商示例

以下是**未来 worker 的示例**，不是当前 core 支持声明。详细进度与 cooperative cancel 在完成实现和验证前必须为 false。

```json
{
  "protocol": {"major": 1, "minor": 0},
  "kind": "hello",
  "session_id": "session-example",
  "seq": 1,
  "worker_version": "development",
  "capabilities": {
    "phase_events": false,
    "item_progress": false,
    "byte_progress": false,
    "current_item": false,
    "cooperative_cancel": false,
    "confirmation_requests": false,
    "restore_policies": ["never", "always", "older"]
  }
}
```

Capabilities 还应按 operation 返回动态支持状态，例如 cancel 在 publishing 后变为 too_late。UI 不能因字段已在 schema 定义就启用功能，也不能把 restore_policies 中的 older 自动提升为主选项。

## Identity 与请求

| 字段 | 契约 |
| --- | --- |
| `backup_id` | GUI registry 的稳定 opaque ID；不是 core task/repo identity |
| `task_ref` | 绝对 `config_dir` + `task_name` + 应用计算的 `config_revision`；worker 必须重新验证 |
| `repository` | 请求带 `expected_repo_id`（init 前可 null）；解析后结果给 actual repo_id、volume 身份及 resolved path；路径不是身份 |
| `snapshot` | repo_id 范围内的 snapshot_id；sequence 可空（legacy），uint64 用十进制字符串传输以免语言工具精度丢失 |
| `operation_id` | enqueue 时分配，dispatch 继续使用；retry 是新 operation，关联旧 ID |

`init` 不能伪造 expected repo identity；结果在创建完成后返回。identity 不匹配/歧义必须失败，不提示用户一键忽略。配置 revision 属 GUI/application metadata，不写进严格 task TOML。实际校验仍由 Python 现有身份解析承担。

建议 command 范围：`backup.start`、`repository.inspect`、`snapshot.list`、`snapshot.browse`、`verify.start`、`restore.plan`、`restore.apply`；后续 init/config service 经独立 review 后加入。这里是职责清单，不要求 Phase 1 一次实现全部。

```json
{
  "protocol": {"major": 1, "minor": 0},
  "kind": "request",
  "session_id": "session-example",
  "request_id": "request-example",
  "operation_id": "operation-example",
  "command": "backup.start",
  "backup_id": "backup-documents",
  "task_ref": {
    "config_dir": "C:\\Users\\Example\\AppData\\Local\\Mirrorly\\core-config",
    "task_name": "documents",
    "config_revision": "opaque-revision"
  },
  "repository": {"expected_repo_id": "opaque-core-repo-id"},
  "arguments": {"full_hash": false}
}
```

以上 ID/revision 值仅示例。Worker 不接受 GUI 提供的任意 Python 方法名、shell command、manifest 内容或可跳过安全检查的 flag。snapshot selector 是 backend 验证的字面相对路径；GUI 不把浏览返回的展示文本直接拼为可信写路径。

## 消息种类与最终结果

| kind | 语义 |
| --- | --- |
| `ack` | 请求已接受或拒绝；接受不表示锁已取得、操作已成功或取消已完成 |
| `event` | `state_changed / progress / heartbeat / snapshot_committed / confirmation_required / cancellation_changed`，内容遵守能力协商 |
| `response` | 对一个 request 的最终答复；start 的 response 承载 operation terminal result；control 的 response 仅确认控制请求处理结果 |

事件/结果带 `session_id`、worker `seq`、关联 `request_id`、`operation_id`。普通 inspect/list 的 response 没有 operation 可用 null。accepted start 最终应有一份 terminal result；重复传送按 ID 去重。全程没有 response 的进程 exit 0 仍不是成功；客户端 EOF/超时只能建立 unknown projection。

**Terminal result 必需信息**：operation kind/identity、开始/结束时间、[OPERATIONS](OPERATIONS.md) 定义的 outcome/commit/finalization、已知 snapshot identity、阶段末值、实际 counts/issues、report 状态、structured error；Restore 用副作用统计而非 snapshot commit。`legacy_cli_exit_code` 仅可选诊断，不能替代这些字段。

下面是假设未来已经支持 phase_events 时的 scanning event；没有计数能力就用 null。每帧实际写成一行，下例为可读性展开；不是用于模拟当前不存在的 observer。

```json
{
  "protocol": {"major": 1, "minor": 0},
  "kind": "event",
  "session_id": "session-example",
  "seq": 8,
  "request_id": "request-example",
  "operation_id": "operation-example",
  "event": "progress",
  "payload": {
    "phase": "scanning",
    "phase_instance": 2,
    "items": null,
    "bytes": null,
    "current_item": null,
    "elapsed_ms": 2400,
    "observed_at": "2026-09-19T01:00:02.400Z"
  }
}
```

已提交但报告失败的简化结果示例（数量仅用于示例；null 代表没有可提供的路径，report 不存在不影响已知写入统计）：

```json
{
  "protocol": {"major": 1, "minor": 0},
  "kind": "response",
  "session_id": "session-example",
  "seq": 27,
  "request_id": "request-example",
  "operation_id": "operation-example",
  "result": {
    "operation_kind": "backup",
    "started_at": "2026-09-19T01:00:00Z",
    "ended_at": "2026-09-19T01:02:00Z",
    "backup_id": "backup-documents",
    "repository": {"repo_id": "opaque-core-repo-id"},
    "outcome": "failed",
    "snapshot_commit": "committed",
    "snapshot": {"snapshot_id": "opaque-core-snapshot-id", "lifecycle_seq": "42"},
    "phase": "finalizing",
    "finalization": "failed",
    "finalization_steps": {"resume_cleanup": "not_applicable", "retention": "succeeded", "report": "failed"},
    "counts": {"copied_files": 20, "linked_files": 120, "skipped_items": 0, "bytes_written": 1048576},
    "report": {"status": "failed", "path": null},
    "issues": [],
    "error": {
      "code": "report_publication_failed",
      "category": "io",
      "phase": "finalizing",
      "certainty": "known",
      "retry_advice": "inspect_saved_result",
      "technical_detail": {"exception_type": "OSError", "native_code": null}
    }
  }
}
```

`snapshot_committed` event 是及时展示事实，terminal response 再次包含提交事实以允许漏事件后的正确收敛。即使看到 committed，未收到 final result 也只知道版本已保存，不能推断 retention/report 成功。完整 manifest 可帮助重连后的只读核对，但不能重建丢失的所有最终状态。

## 结构化错误与 confirmation

**CURRENT FACT**：现有异常按模块分组，许多原因只有人类字符串；通用 exit 1/5/6 不足以细分 GUI diagnosis。新 code 是 application 层待设计能力，不通过正则匹配 stderr“稳定实现”。无法分类时 `unknown_error` + 真实技术原因；不可从所有 OSError 推断“硬盘未连接”。

建议 error 字段：稳定 `code`、`category`（configuration/identity/locked/io/integrity/unsupported/unknown）、发生 `phase`、确认事实与 `certainty`、可验证的 context、`retry_advice`、technical detail（异常类型、原错误/native code、trace/detail reference）。用户文案由已审阅 code mapping 提供；猜测使用 may/might，未知保持未知。路径、用户名和文件名可能敏感，默认本地保存，不发送外部服务。

Confirmation 是未来能力，不是给 GUI 的任意执行授权。请求含 `confirmation_id`、operation/plan identity/revision、`reason_code`、后果、明确 choices；答复绑定同一 session/operation/plan。worker 验证选择有效和事实未过期，拒绝重放。GUI 不自动回答 yes。尽量在持锁前收集意图，必须锁内决策的 resume 等需有明确等待/放弃边界；断连不能默认为同意，不能承诺无限持锁。具体确认流程与等待期限属 O-08。

Restore 推荐先 `restore.plan`，worker 保有 plan，返回 opaque plan_id + 摘要；`restore.apply` 接收 plan_id 与绑定确认，不能接收 C# 重建的任意 plan。worker 重启使 plan 失效，现有 apply revalidation 必须保留。若安全检查降级为 skip/conflict，如实报告；需要更强动作则必须重新 plan/确认。

## Cancel、断连与重试

`operation.cancel` 为单独 control request，只包含目标 operation_id 与 request_id；回复 `unsupported / requested / waiting_for_checkpoint / too_late / already_ended` 等明确结果，并以事件/terminal result 跟进。pending queue Remove 不发 cancel 消息。只有合作取消实现且能力 true 时才发送；不用操作系统 terminate 兜底伪装取消。

客户端协议错误/失联时停止派发，保留最后可证事实与 unknown outcome；不得自动重发 mutating request、自动清锁或根据 GUI activity 接管 orphan。独立只读 reconciliation 需核对 repo、operation 的 snapshot identity 与报告；若证据不够仍 unknown。用户后续 Retry 必须是新 operation，先解决旧 worker 是否仍工作的问题。

**OPEN DECISION O-03**：EOF/父进程崩溃后 worker 是完成当前 operation 再退出，还是在未来 safe checkpoint 请求取消；以及下一次启动如何确认 orphan 已结束。推荐先验证“停止接收新任务、保留现有安全收尾”的方案，但本轮不批准实现、不保证可重连。不能把 stdio 关闭等价于用户 Cancel，也不能把 OS 强制终止描述为可靠正常路径。
