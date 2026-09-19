# GUI settings、Activity 与 unresolved issues

> 2026-09-19 · 独立本地存储与 truth 边界 APPROVED；schema、限额、消除规则为 PROPOSED DESIGN（O-07）。

## 存储与事实来源

**CURRENT FACT**：[TaskConfig](../../src/mirrorly/config.py) 使用严格 TOML。Backup/verify 有 core report，restore 目前没有同等持久 report；并非每个失败都能产生 report。当前没有跨 Backup 的 GUI registry/history/settings store。

**APPROVED**：GUI settings、activity、unresolved attention 使用用户本地应用数据位置，不能塞入 task TOML，也不能代替 snapshot manifest/report。Activity 的“曾经保存”不保证对应版本现在仍存在；retention 后也不篡改历史事件。

**PROPOSED DESIGN**：通过 Windows Known Folder / 应用存储 API 定位用户 Local App Data 下的 Mirrorly 目录；unpackaged 逻辑位置 `%LOCALAPPDATA%\Mirrorly`。最终 packaged LocalState 映射、升级路径及 GUI-owned core config 根目录随 O-06/O-07 确认，不能把打包/未打包切换变成两套静默丢失的数据。

| 数据 | 建议 owner / 最小持久形式 | 不是 |
| --- | --- | --- |
| Settings | C# service，schema-versioned `settings.json` | task/retention 配置 |
| Backup registry | C# service，`backups.json`：backup_id、display_name、task_ref、预期 repo identity | 新 task namespace 或 repo 真相 |
| Activity | C# service，有界 `activity.jsonl`；记录事实和引用 | manifest 镜像或可执行任务队列 |
| Issue/latest projection | C# service，`state.json`；保存未解决摘要/最新观测 | 自动证明 repo 可用/完整的数据库 |
| task TOML / manifests / reports | Python application/core 通过原有格式管理 | GUI 自行编辑/修复的 JSON |

首版无需新增数据库依赖。GUI 单 writer、串行提交；小 JSON 文件用同目录 temp + atomic replace，journal 在启动时检测截断尾部。损坏保留可诊断证据并显示本地历史不完整，不能拿缓存“修复”core。活动写入失败只产生 GUI history 问题，不能将已知 core 成功改写为备份未保存。Settings 损坏时采用可解释默认值，禁止自动重新执行历史操作。

上表文件名/格式是提案，不是本轮新增文件。导入已有 CLI task 不复制/移动其 repo；编辑/删除归属与冲突须 O-07 明确。移除 GUI Backup 配置的确认应说明会移除哪些配置/关联，已有 backup files 不删除；不能顺带递归删除 repository。

## Activity event

建议字段：`schema_version`、`event_id`、UTC `occurred_at`、`event_type`、`operation_id`、`backup_id`、`task_ref/config_revision`、`repo_id`、可选 `snapshot_id/lifecycle_seq`、outcome/commit/finalization 摘要、issues、report reference、technical detail reference、`retry_of`、证据来源/检查时间。

面向用户的类型包括 Backup completed / completed with issues / failed / version saved but finalization failed、Restore completed/partial/failed、Verify result，以及 Queue added/removed 等低优先级记录。heartbeat/每个文件进度不写成长久 Activity。文案使用稳定 message key 与参数，避免把用户语言作为机器判定依据。

开始真正 operation 时记录 started；terminal result 后记录 finished；启动发现 started 无可信结束记录则展示 result unknown，尝试只读核对。可能在 start 记录与真实请求发送之间崩溃，因此不得自动认定已执行，也不重放。Queue 历史不是待运行队列，true Exit 的未开始项记 removed/app_exit；崩溃重启同样不恢复待运行请求。

**建议限额**：普通历史同时限制最近 90 天、最多 1,000 条用户事件，任一超限即先清旧记录；技术详情单独有界。未解决 issue 的最小证据/引用及每个 Backup 的 last-known summary 独立保留，不随历史裁剪丢失。裁剪不删除 core reports/manifests/backup files，不给 core retention 加另一套规则。限额待 review，可集中修改，不先做用户可配复杂清理 UI。

## 未解决问题与后续成功

Issue 至少有 `issue_id`、`scope`（backup/repo/snapshot/operation/config revision）、reason_code、first/last observed、evidence、status（active/resolved）、resolution evidence。acknowledged/dismissed 只是显示偏好，不等于 resolved。Home 即使错过 toast 也保留尚未解决的 backup 问题。

| 问题 | 何时可以消除 | 不能作为消除证据 |
| --- | --- | --- |
| destination unavailable | 同 identity 的新鲜解析/访问成功 | 重开窗口、dismiss toast、别的 Backup 成功 |
| 某次 backup 的暂时失败/skips | 同 Backup/source/config scope 后续无相应问题的完成，可标“后续运行已恢复”；旧 Activity 保留 | 仅有 complete manifest、换了 source/repo 后成功 |
| 某旧 snapshot 的 integrity issue | 对该 snapshot 的对应检查确证解决；已不存在则标不可用/已删除，不能称验证通过 | 新 snapshot 备份成功 |
| committed 后 report 失败 | 对应报告/故障有明确核对结果或用户确认处理；新报告不补成旧报告 | 新一次 backup 成功 |
| leftover/cleanup/identity 问题 | application/core 对相同对象确认问题已处理 | GUI 猜 stale lock、自动删残留 |
| Restore 部分失败 | 显式关联重试的实际结果与同范围核对；历史部分写入事实保留 | 任意 backup 成功 |

Retry 创建新的 operation，并关联原 issue；点击 Retry 不先清除问题，成功后按 scope 决定。用户“我已处理”可以记录人工处置，必须区别 verified resolution；不能改变 manifest 或完整性结论。

## 离线与 Settings

目标离线时保留 last-known 时间、最后成功/版本信息，明确标为上次观测。cached 路径不当作当前可打开路径，cached snapshot list 不保证仍存在。新的身份解析失败时显示已知事实，不擅自诊断为磁盘损坏；排队开始/Restore apply 前重新验证。

全局 Settings 仅收纳 Appearance/Theme、Notifications、首次 Close 提示等全局偏好，Advanced 按需展开。Backup 自身的 source/excludes/retention/verify 设置放 `Backup → Settings`，通过 Python 配置服务处理。没有 Enable Advanced Mode 切换整应用；没有新增调度/startup 默认行为。开机启动若后续提供，独立批准与配置，不能由 tray 常驻推出。
