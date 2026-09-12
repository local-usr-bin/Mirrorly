# Mirrorly 开发日志

> 按日期倒序追加（新条目写在最上面）。每条记录：日期、做了什么、关键决定、遇到的问题。

---

## 2026-09-13（六）MVP T-06 完整性校验

### 做了什么

1. **新增 `src/mirrorly/verify.py`**
   - `verify_snapshot(repo, snapshot_id, *, quick=False)`：manifest 必须 complete；
     全量模式做存在性/类型/大小 + 逐文件哈希比对；quick 模式不调用 hash_file。
   - `VerifyIssue`（missing / type_mismatch / size_mismatch / corrupt）与
     `VerifyReport`（含 hashed_files / unhashed_entries / extras / ok）。
   - extras（快照中多出清单未记录的文件）只报告不影响 ok——避免旧流程残留误报。
2. **snapshot.py 追加写入即校验**（写入模型不变）
   - `write_snapshot(..., verify_writes=False)`：默认 False，T-03/T-05 行为零变化。
   - 开启后每个 copied 文件在原子改名后重算目标端哈希与源端比对；不一致仅重试
     该文件一次（完整重走临时文件+原子改名流程），仍失败抛 SnapshotError。
   - `SnapshotResult.hashes`：copied 文件哈希，供 create_manifest 持久化。
3. **新增 `tests/test_verify.py`**（15 用例）：9 项 verify（篡改/删除文件/删除目录/
   大小不符/quick 零哈希调用/sha=None 跳过/extras/Unicode+长路径）+ 5 项写入校验
   （哈希正确返回、仅重试一次、重试成功、hashes 入 manifest 后 verify 通过、默认关闭
   回归）+ 1 项 T-09 预演（linked 文件 sha 从旧 manifest 结转，merged 后全量 verify 通过）。

### 关键决定

- **职责边界**：VerifyReport/manifest 解析只在 verify.py；snapshot.py 只做单文件
  哈希校验 hook，不感知 previous manifest；linked 文件 sha 由调用方结转合并。
- **quick 语义边界明确**：同大小内容篡改在 quick 模式不检出（测试固化该语义）。
- **sha=None 条目**（T-06 前的旧快照）跳过哈希、计入 unhashed_entries、不影响 ok。

### 遇到的问题

- 测试断言 tuple/list 类型笔误（result.linked 为 tuple），已修正。

### 下一步

T-07 保留策略（keep_last/keep_monthly、dry-run、incomplete 不删）——等待确认后启动。

---

## 2026-09-13（五）MVP T-05 中断恢复

### 做了什么

1. **新增 `src/mirrorly/recovery.py`**
   - `scan_recovery`：发现 incomplete 清单、孤儿快照目录（有目录无 manifest）、`.mrtmp` 与 `manifests.tmp/` 残留；只报告不修改。
   - `build_resume_baseline`：以 incomplete 快照构建续传基线。校验：manifest 存在且 status=incomplete、快照目录存在、目录中已存在条目与清单大小/类型一致——任一矛盾显式 `RecoveryError`，不静默继续；清单中尚未复制的条目（正常中断态）显式收入 `missing` 报告，不纳入信任基线。
   - `clean_tmp_residue`：只删 `.mrtmp` 与 `manifests.tmp/` 残留，支持按快照 scoped 清理。
   - `discard_incomplete`：显式删除 incomplete 目录+manifest，complete 快照拒绝；快照 id 防路径穿越校验。
2. **新增 `tests/test_recovery.py`**（25 用例）：按文件计数注入中断（monkeypatch `_copy_file_atomic`），覆盖 4 个中断点、双重中断收敛、inode 复用（未从头复制）、中断期间源变更、Unicode+长路径、基线校验各矛盾分支、discard 边界。
3. **T-03 规范一致性修复**：`snapshot.py` 三处 `mkdir`（快照根/目录重建/文件父目录）未走 `to_long_path`，导致 >260 字符目标路径写入失败。按 T-03 自身规范（"所有文件操作走 to_long_path"）做最小修复，无行为变更；全套件 100 项复跑无回归。

### 关键决定

- **续传产出新 snapshot id**（用户确认）：incomplete 目录作为只读硬链接基线，不引入第二套写入逻辑，完成后显式 discard。
- **检测基线用 incomplete manifest**：已完整复制的文件 mtime 一致被信任为未变 → 直接硬链接复用，满足 TR-5"不从头复制"；sha 为 None 的条目复核时保守判 modified（安全方向）。
- **"清单-目录一致性"的解释**：missing（未复制）= 正常中断态，显式报告+重传；矛盾（目录缺失/大小或类型不符）= RecoveryError。此解释已记录，因字面"条目必须全部存在"会使正常中断永远无法续传，与 TR-5 冲突。

### 遇到的问题

- `os.walk` 用 `\\?\` 前缀遍历后 `relative_to` 普通路径报 ValueError：recovery.py 增加 `_unprefix` 辅助。
- 上轮 T-04 期间 5 个 T-03 用例因宿主沙箱删除守卫无法复跑，本轮计数器重置后全套件 100 项全绿，确认非代码回归。

### 下一步

T-06 完整性校验（写入即校验 + verify 报告，退出码 0/4）——等待确认后启动。

---

## 2026-09-13（四）MVP T-04 Manifest 管理

### 执行内容

1. **新增 `src/mirrorly/manifest.py`**：
   - `create_manifest`：从 T-02 扫描结果构建 incomplete 清单（条目含 path/size/mtime_ns/sha/type；sha 由调用方提供，缺失记 None；统计 files/dirs/total_bytes）；
   - `mark_complete`：不可变对象状态流转 incomplete → complete；
   - `write_manifest`：原子提交（manifests.tmp/ 临时文件 → flush+fsync → 关闭 → os.replace）；失败清理临时文件且无"半个 complete manifest"；
   - `load_manifest`：格式版本校验；`require_complete=True` 拒绝把 incomplete 误读为完整备份；
   - `list_manifests`：轻量摘要（list 命令的数据源）；
   - 稳定序列化：同一对象多次序列化逐字节一致（可 diff 可校验）；目录条目只记 path+type。
2. **新增 `tests/test_manifest.py`**：13 个用例，覆盖创建字段、原子提交无残留、写失败无完整清单残留、incomplete 防误读、状态流转、Unicode、JSON 稳定性、格式版本校验、2 万条目读写冒烟。

### 遇到的问题

- 宿主沙箱 safe-delete 守卫按"轮次"累计删除计数（阈值 50）：本轮多次运行 pytest，测试内临时文件删除累计触发 SystemExit 拦截，导致删除密集型测试（test_snapshot 的 5 个用例）在本轮后段无法复跑。**T-04 全部 13 个测试通过、ruff 通过**；全套件最后一轮绿跑为 62 项（T-03 提交时）+ T-04 13 项单独通过。下轮会话（计数重置）应做一次全套件复跑确认。

### 后续计划

T-05 中断恢复（incomplete 基线续传、tmp 清理）——等待确认后启动；启动前先全套件复跑 pytest。

---

## 2026-09-13（三）MVP T-03 快照写入引擎

### 执行内容

1. **计划先行**：输出实现计划（接口、三条安全纪律、测试矩阵、边界声明）经产品负责人确认后编码。
2. **新增 `src/mirrorly/snapshot.py`**：
   - `write_snapshot(source, repo, current, changes, snapshot_id, previous_snapshot)` → SnapshotResult（linked/copied/skipped/bytes_written/dirs_created）。
   - 目录重建含空目录；deleted 文件自然缺席，旧快照零写入（结构性保证）。
   - 未变文件（含 suspected_modified）`os.link` 硬链接复用；link 失败显式 `SnapshotError`（TR-2 不静默降级）；`repo.hardlinks=False` 显式降级整文件复制。
   - 变更文件：`.mrtmp` 临时文件 → flush+fsync → 关闭 → 复测源 size/mtime（TR-4 变动中删临时文件记 skipped）→ os.replace 原子改名 → mtime 回写保真。
   - 快照 id 冲突拒绝（防覆盖半成品）；所有文件操作走 `to_long_path`。
3. **新增 `tests/test_snapshot.py`**：13 个用例，含核心回归（改写源文件后旧快照字节不变）、inode/nlink 断言、变动注入跳过、link 失败显式报错、崩溃不污染旧快照（整树哈希比对）。

### 遇到的问题

- Windows FILETIME 粒度 100ns，os.utime 截断纳秒尾数 → mtime 保真断言改 100ns 容差并在代码注释说明（平台限制，不影响检测逻辑：manifest 记录的是扫描时的源 mtime）。
- frozen dataclass / 中文标识符等编码规范问题在提交前清理。

### 边界确认（未扩大范围）

manifest 落盘（T-04）、中断续传（T-05）、写入即哈希校验（T-06）均未实现；目录级 fsync 省略记为已知简化（极端断电由 T-05 续传兜底）。

### 后续计划

T-04 Manifest 管理（每快照独立 JSON、tmp 原子提交、incomplete→complete）——等待确认后启动。

---

## 2026-09-13（二）MVP T-02 源扫描与变更检测

### 执行内容

1. **新增 `src/mirrorly/scan.py`**（只读模块，不做任何写入）：
   - `scan_source`：os.scandir 迭代遍历，产出文件/目录元数据（相对 POSIX 路径、size、mtime_ns）；stat 失败记入 skipped 不中断（TR-4）；符号链接等特殊条目跳过并记录。
   - 长路径：Windows 下文件系统调用统一 `\\?\` 前缀（to_long_path）；Unicode 文件名原生支持。
   - `Excluder`：尾斜杠模式只匹配目录并整树剪枝，其余模式只匹配文件；含 `/` 匹配相对路径，否则匹配任意层级名称。
   - `detect_changes`：ADR-006 判定——新增 / 大小变→modified（不哈希）/ mtime 一致→信任未变 / mtime 变+大小同→BLAKE3 流式复核（hash 同→suspected_modified，异→modified，上清单无哈希→保守 modified）；产出 ChangeSet（含 added_dirs/deleted_dirs/hashed_files）。
   - `PreviousEntry` 定义为 T-02 消费上一清单的最小接口，不预设 T-04 的 manifest 落盘格式。
2. **新增 `tests/test_scan.py`**：18 个用例，覆盖空基线、新增、删除、大小变化、内容变+mtime 变（复核判修改）、仅 mtime 变（复核排除）、Unicode 文件名、>260 字符长路径、文件/目录排除区分、路径模式排除、哈希复核仅对疑似项触发（hashed_files 断言）。

### 遇到的问题

- ChangeSet 误用 frozen dataclass 直接属性赋值（FrozenInstanceError），改为局部列表构建后一次性构造。
- pytest 默认 basetemp 在系统 Temp 触发沙箱删除拦截，改用 `--basetemp=P:\DevProjects\Mirrorly\.pytest_tmp`（已加入 .gitignore）。

### 后续计划

T-03 快照写入引擎（硬链接复用、临时文件+原子改名、历史快照不可变性回归）——等待产品负责人确认后启动。

---

## 2026-09-13 开发前整理 + MVP T-01

### 执行内容

1. **Task 0 文档一致性（commit b2a9a3b）**：TECH_RISKS.md TR-3 中哈希算法旧描述（"xxHash64/BLAKE3 可选、基准测试后定"）修正为与 ADR-013 冻结决策一致（BLAKE3 主 / SHA-256 兜底 / xxHash 不用于校验）。其余文档（DESIGN_DECISIONS、ARCHITECTURE、CLI_SPEC、MVP_TASKS）核对一致，无需修改。
2. **Task 1 Git 身份**：仓库级 user.name=local-usr-bin、user.email=242546610+local-usr-bin@users.noreply.github.com（GitHub noreply）；历史占位身份 commit 不重写。
3. **Task 2 UI 方向（commit 9b8a91e）**：docs/UI_DIRECTION.md——clean/lightweight/trustworthy、mint green、首屏五要素（最近备份状态/源目录/目标盘/快照数/完整性）、默认不展示底层技术细节；仅设计约束，不开发不引依赖。
4. **Task 3 = MVP T-01 仓库初始化与配置加载**：
   - 依赖：pyproject.toml 增加 `blake3>=0.3`，mirrorly 环境 `pip install -e ".[dev]"`（清华 PyPI 镜像）成功，pytest 9.1.1 / ruff 0.16.7 / blake3 1.0.9。
   - `mirrorly/hashing.py`：BLAKE3/SHA-256 抽象（new_hasher/hash_file 流式分块），blake3 不可用自动降级。
   - `mirrorly/repo.py`：init_repo（MirrorlyRepo 目录结构、repo.json 原子写入、GetVolumeInformationW 卷标识、strict 拒绝非 NTFS / warn 确认后降级 hardlinks=False）、load_repo（格式版本校验）。
   - `mirrorly/config.py`：TOML 严格模式加载（未知节/键报错、必填校验、类型校验、保留策略正整数校验）、配置生成（config.d/<task>.toml）。
   - 测试 31 项全过（含 mock 卷信息的三策略分支、严格模式矩阵、原子写入无残留）；ruff check/format 通过；真实 NTFS 卷冒烟通过。

### 遇到的问题

- 测试 glob("*.tmp") 误匹配 `manifests.tmp` 目录导致一次断言失败，修正为只统计文件——布局中目录名带 .tmp 后缀是刻意设计（表明临时区），测试需按 is_file() 过滤。

### 后续计划

T-02 源扫描与变更检测（目录遍历、元数据初筛+哈希复核、排除规则、长路径/Unicode）。

---

## 2026-09-12（深夜·二）架构细化：MVP 前最终设计

### 执行内容

1. **Manifest 选型（ADR-010）**：对比单一 JSON / SQLite / 每快照独立 JSON → 选定每快照独立 manifest（`manifests/<id>.json`，tmp 原子提交，incomplete→complete 流转）。决定因素：损坏域最小、与快照只读不变量一致、人类可读、GUI 可用派生索引扩展。仓库布局随之确定（repo.json + snapshots/ + manifests/ + locks/）。
2. **配置格式选型（ADR-011）**：TOML 胜出（`tomllib` 零依赖、手写友好、严格无歧义）；多任务结构定为 `config.toml` + `config.d/<task>.toml`，MVP 单任务但目录约定现在锁定。
3. **CLI 规范（ADR-012 + docs/CLI_SPEC.md v1.0）**：`init/backup/verify/restore/list` 五命令的参数与行为契约；退出码 0/1/2/3/4/5/6/130，其中 3（部分完成有跳过）与 5（卷标识不符）是脚本化场景的关键区分。
4. **哈希算法（ADR-013）**：BLAKE3 主用（备份负载下 5–10 倍于 SHA-256），SHA-256 标准库兜底；xxHash 因非密码学性质被排除出校验用途；算法 init 时锁定写入 repo.json，仓库内不混用。
5. **MVP 任务拆分（docs/MVP_TASKS.md v1.0）**：T-01~T-10，依赖序 T-01→{T-02→T-03→{T-04~T-08}}→T-09→T-10；每任务含输入/输出/验收标准/测试要求。

### 关键决定

| 决定 | 理由 |
| --- | --- |
| 每快照独立 manifest 而非 SQLite | 单点损坏不可接受；GUI 全局索引可派生重建，manifest 是唯一事实来源 |
| manifest 与快照数据分离存放 | 快照目录保持纯数据，用户可直接浏览拷贝（产品差异点） |
| 配置严格模式（未知键报错） | 拼写错误静默失效是备份工具不能接受的行为 |
| BLAKE3 + 算法锁仓 | 写入即校验在外置机械盘上不能成为瓶颈；混用算法会破坏 verify 语义 |

### 后续计划

设计阶段全部结束。待产品负责人指示后启动 MVP 开发（T-01 开始，先 `pip install -e ".[dev]"` + blake3 依赖 + 测试基建）。

---

## 2026-09-12（深夜）PRD 确认 + 技术风险分析 + 架构 ADR

### 执行内容

1. **PRD v0.1 → v0.2**（产品负责人确认 5 项决策）
   - D1 备份优先：通过。
   - D2 快照+硬链接：NTFS 为 v1 正式支持；exFAT 不静默降级，明确提示能力限制，用户决定继续或转换 NTFS。
   - 加密/压缩暂不加入 v1：通过。
   - 多备份任务：架构预留，MVP 单任务流程实现。
   - 保留策略：默认（最近 30 + 每月至少 1）接受，必须配置化。
   - PRD 第 8 节改为"决策确认记录"，风险 R1 缓解措施同步修订。

2. **技术风险分析**：输出 `docs/TECH_RISKS.md` v1.0
   - TR-1 变化检测（中）、TR-2 增量/硬链接（中）、TR-3 完整性（中）、TR-4 冲突（低）、TR-5 中断恢复（高）、TR-6 大文件（中）、TR-7 外置盘（高）。
   - 核心结论：高风险集中在 TR-5/TR-7，由"原子写入 + 不变量清单 + 卷标识寻址"三条设计纪律系统性化解，无阻塞性风险。
   - 特别标记的最危险 bug 类：对已硬链接文件原地写会污染所有历史快照——必须以"临时文件+原子替换"和"旧快照只读"不变量防范。

3. **架构 ADR**：ARCHITECTURE.md 新增 ADR-005（存储模型）、ADR-006（变更检测）、ADR-007（任务模型）、ADR-008（保留策略）、ADR-009（完整性机制）；"待决策"收敛为架构细化项（manifest 格式、配置存储、CLI 命令集、哈希选型基准、大文件续传粒度）。

### 关键决定

| 决定 | 理由 |
| --- | --- |
| exFAT 明示不降级 | 静默降级会偷改空间/可靠性预期，备份工具必须行为可预期 |
| 多任务只预留不实现 | 避免为未验证需求提前做调度编排；但数据模型不留单任务假设 |
| 三条设计纪律（原子写入/不变量清单/卷标识） | 一次性化解 TR-5、TR-7 两类高风险，无需重量级架构 |

### 后续计划

架构细化（manifest 格式、CLI 命令集、哈希基准）→ MVP 任务拆分 → 开发启动。当前不写代码。

---

## 2026-09-12（晚）产品定义阶段：PRD v0.1

### 执行内容

1. **市场调研**（web 检索综述）
   - 工具格局：restic/borg（CLI、去重加密、专有格式）、FreeFileSync/GoodSync（文件夹同步、版本弱）、Syncthing（P2P 实时同步）、Time Machine/File History（OS 绑定）、云同步（Dropbox/OneDrive，**同步 ≠ 备份**）。
   - 原则：3-2-1 备份规则；版本化是防误删/勒索的关键；备份必须测试恢复。
   - 结论：Mirrorly 的机会 = **本地版本化备份 + 备份产物透明可读**（普通文件快照，恢复不依赖工具本身）。

2. **产品定义**
   - 用户画像：P1 技术型个人用户（Windows + 外置盘，主力）；P2 内容创作者（大文件场景，次要）。
   - MVP 范围：必须支持 M1–M10（单向备份、增量、版本快照、删除保护、排除规则、完整性校验、中断恢复、dry-run、报告、外置盘卷标识）；明确不支持 W1–W10（同步、云、块级去重、加密压缩、实时监控、GUI、系统镜像、P2P、移动端、非 Windows）。
   - 关键决策 D1–D7 及推荐理由：备份优先（D1）、整文件快照+硬链接（D2）、Windows 优先（D3）、CLI 优先（D4）、元数据初筛+哈希复核（D5）、直接拷贝恢复（D6）、写入即校验（D7）。
   - 输出 `docs/PRD.md` v0.1 草案，含用户故事、使用流程、产品风险 R1–R7。

3. **待确认问题**（PRD 第 8 节，共 5 项）
   - 定位（备份优先）、存储模型与 exFAT 策略、加密是否提前到 v1、多任务支持、快照保留策略默认值。
   - 确认后修订 PRD v0.2，再进入技术风险分析（docs/TECH_RISKS.md）。

### 关键决定

| 决定 | 理由 |
| --- | --- |
| 备份优先，v1 不做双向同步 | 冲突合并最难做对；同步不带历史无法防误删/勒索 |
| 快照+硬链接模型（非 restic 分块） | 备份产物透明、恢复零工具依赖；实现简单；NTFS 原生支持 |
| 关键假设以 ⚠️ 标注，不擅自定案 | 产品负责人要求：不明确处列问题而非假设 |

---

## 2026-09-12 项目初始化

### 执行内容

1. **环境检查**
   - 工作目录 `P:\DevProjects\Mirrorly`（外置盘）确认为空目录。
   - Git 2.55.0.windows.3 可用（通过宿主提供的 PortableGit）。
   - Conda 25.5.1（Anaconda3）位于 `C:\Users\sakur\anaconda3`，仅存在 base 环境。
   - 系统 Python 3.13.x 为宿主沙箱自带，仅作工具链，不用于项目。

2. **Conda 环境创建**
   - 创建独立环境 `mirrorly`，Python 3.12。
   - 位置：本机默认环境目录 `C:\Users\sakur\anaconda3\envs\mirrorly`（按约定不放入外置工作空间）。
   - 环境定义导出至仓库根目录 `environment.yml`，可复现。

3. **Git 仓库初始化**
   - `git init`，默认分支 `main`。
   - 设置仓库级用户身份占位（未改动全局配置）：
     `user.name = Mirrorly Dev`，`user.email = dev@mirrorly.local`。
     **→ 正式开发前需替换为真实身份（`git config user.name "..."` / `user.email "..."`，仓库级即可）。**
   - 提交 `.gitignore` / `.gitattributes`，统一 LF 换行策略。
   - 完成初始提交（全部基础文件）。

4. **目录结构与工程文件**
   - 采用 Python `src-layout`：`src/mirrorly/`（主包）、`tests/`、`docs/`、`scripts/`。
   - `pyproject.toml`：setuptools 构建后端、`mirrorly` CLI 入口占位、dev 依赖（pytest、ruff）。
   - `.editorconfig`：UTF-8 / LF / Python 4 空格。
   - `.gitignore` 额外约定：**备份产物目录（backup/、sync_data/ 等）与日志永不入库**。

5. **文档体系**
   - `README.md`：项目简介、结构、环境搭建。
   - `docs/STATUS.md`：当前状态、环境快照、待办。
   - `docs/ARCHITECTURE.md`：架构决策记录（ADR 起点）。
   - `docs/DEVELOPMENT_LOG.md`：本文件。

### 关键决定

| 决定 | 理由 |
| --- | --- |
| Python 3.12（而非系统 3.13） | 生态兼容性更稳；与沙箱工具链解耦，环境独立可控 |
| src-layout | 防止测试时误导入仓库根目录的同名包，长期维护更规范 |
| Conda 环境放本机默认位置 | 环境体积大且含二进制缓存，不应放在外置/同步盘；外置盘有断连风险 |
| 运行时依赖暂不添加 | 当前阶段不实现产品功能，避免过早锁定技术选型 |
| Git 身份用仓库级占位 | 不动全局配置；提交历史可追溯；正式开发前替换 |

### 遇到的问题

- 宿主 shell（Git Bash shim）PATH 异常（`ls`/`git`/`conda` 不在 PATH），通过手动 `export PATH` 解决；后续如遇同样问题可参考本条。
- Conda 官方源 `repo.anaconda.com` 在当前网络不可达（HTTP 000 连接失败），首次 `conda create` 失败。
  解决：改用清华 TUNA 镜像并加 `--override-channels` 禁用默认频道后创建成功。
  注意：若直接 `conda env create -f environment.yml`（channels 含 defaults）在本网络下同样会失败，
  可临时执行 `conda config --add channels <镜像>` 或改用命令行 `-c` 方式（见 environment.yml 注释）。
  为不改写用户全局 `.condarc`，本次未持久化镜像配置——如后续频繁使用，建议与用户确认后写入。

### 后续计划

见 [docs/STATUS.md](STATUS.md) 待办清单：需求确认 → 技术调研 → 架构设计 → MVP。
