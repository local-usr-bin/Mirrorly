# Mirrorly 项目状态

> 本文件维护项目当前状态与环境快照。每次重大变更后更新。
> 最后更新：2026-09-13（T-08 hardening 第三轮完成）

## 当前阶段

**MVP 开发进行中（8/10）** —— T-01~T-08（仓库初始化、扫描检测、快照引擎、Manifest、中断恢复、完整性校验、保留策略+删除安全加固、恢复引擎+三轮安全加固）已完成，261 项测试通过（8 项 reparse/junction 用例因沙箱环境限制跳过）。下一任务：T-09 CLI 集成。

## 关键文档

- [docs/PRD.md](PRD.md)：产品需求文档 v0.2（已确认，决策 D1–D7 定案）
- [docs/TECH_RISKS.md](TECH_RISKS.md)：技术风险分析 v1.0（TR-1~TR-7，高风险集中于中断恢复与外置盘）
- [docs/ARCHITECTURE.md](ARCHITECTURE.md)：架构决策记录（ADR-001~013）
- [docs/DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)：manifest/配置/哈希三项选型分析 v1.0
- [docs/CLI_SPEC.md](CLI_SPEC.md)：CLI 契约 v1.0（五命令 + 退出码规范）
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

## 已完成

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
- [x] **T-08 恢复引擎**：mirrorly.restore（plan/apply 两阶段；apply 不信任 plan——重载 complete manifest、digest 指纹比对、重验安全边界、重跑规划 + no-upgrade 对账[破坏性升级一律 stale 整体拒绝，降级按重算执行]；`--path` 字面 snapshot-relative 路径无 glob 语义；canonical Windows 路径校验[保留设备名含 COM¹-³/LPT¹-³ 及带扩展名形式、UTF-16 code unit 计长]；目标边界[仓库内拒绝/同一实际位置须 in_place/普通子目录允许]；覆盖策略 never/older[严格更旧]/always，file↔dir 冲突任何策略不删用户数据；snapshot 读侧+destination 写侧 reparse 防护，check-then-open 残余 TOCTOU 明确接受；mkstemp 短固定前缀独占临时文件+fsync+os.replace，cleanup 只删精确记录路径、失败进 leftovers；partial restore 无整体回滚；不自动 full verify）；**safety hardening follow-up**：单条目分类器 `_classify_entry` 供 plan/apply 共享，每个写操作紧邻 I/O 前逐条重验（目标存在性/类型、当前覆盖决策、双侧 root/祖先/leaf reparse、单条 no-upgrade，升级即拒绝）；plan 冻结 destination 绝对路径（apply 拒绝相对路径）；mtime 在 temp 上设置后再 replace（replace 为单文件唯一 commit point）；load 后 selector 前全量 manifest 路径校验+拒绝重复路径；同一实际位置按 samefile/realpath 身份判定（别名不能绕过 repo 边界/in_place 要求）；属性查询 fail closed（仅 FILE/PATH_NOT_FOUND 视为不存在）；apply 重验 plan 参数（overwrite/paths/action 合法集）；**hardening 第二轮**：canonical validator 显式拒绝反斜杠（selector 归一便利性不变）、_restore_one_file 修复 mkstemp fd 泄漏窗口（所有权标记+异常路径显式关闭）、manifest 增加 casefold 大小写冲突拒绝、snapshot id 复用单组件 canonical 规则（任何冒号/ADS 形式一律拒绝）；**hardening 第三轮**：commit point 前最终复核——_restore_one_file 增加 pre_commit 回调，temp staging 完成后、os.replace 前以 fresh action 为基线再跑共享分类器（_final_recheck），升级/变 skip/变 conflict 一律不 commit 且 temp 精确清理，TOCTOU 收敛为 final check→replace 小窗口；261 项测试通过（8 项 reparse/junction 用例因沙箱屏蔽 reparse 属性跳过，真实 Windows 执行）；小修 T-02 长路径测试的 basetemp 长度敏感性；ruff 通过

## 待办（建议优先级从高到低）

1. **T-09 CLI 集成**：五命令接入、退出码、报告落盘；OQ-1（CLI_SPEC `--path` `<pattern>` 措辞澄清）随本任务处理
2. **远程仓库**：尽早配置并定期推送（工作区在外置盘）

## 风险与注意事项

- 工作区位于外置盘（`P:\`），注意断盘风险；建议尽早配置远程仓库并定期推送
- Git 仓库级身份已设为 GitHub 用户 `local-usr-bin` + noreply 邮箱（2026-09-13）；历史 commit 的占位身份保留不重写
- Mirrorly 是备份工具，务必坚持「备份产物永不入库」的 .gitignore 约定
