# Mirrorly 开发日志

> 按日期倒序追加（新条目写在最上面）。每条记录：日期、做了什么、关键决定、遇到的问题。

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
