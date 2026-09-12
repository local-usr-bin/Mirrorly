# Mirrorly 开发日志

> 按日期倒序追加（新条目写在最上面）。每条记录：日期、做了什么、关键决定、遇到的问题。

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
