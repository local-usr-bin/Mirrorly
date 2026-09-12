# Mirrorly 架构决策记录（ADR）

> 记录影响项目长期形态的技术决策。每条决策包含：背景、决定、理由、备选方案。
> 当前仅包含初始化阶段的工程决策；产品架构（备份引擎、同步策略、存储抽象等）待技术调研后补充。

## ADR-001：开发语言与运行时

- **决定**：Python 3.12，Conda 独立环境管理（环境名 `mirrorly`，位于本机默认 Conda 环境目录）。
- **理由**：备份/同步工具涉及大量文件系统操作、哈希计算与并发 IO，Python 生态（pathlib、hashlib、watchdog 等）成熟；3.12 兼容性稳定。Conda 环境与系统 Python、宿主沙箱隔离。
- **备选**：系统 Python 3.13（与工具链耦合，弃用）；Rust/Go（性能更优但迭代成本高，个人项目阶段不宜）。

## ADR-002：项目布局

- **决定**：src-layout（`src/mirrorly/`）+ `tests/` + `docs/` + `scripts/`。
- **理由**：防止测试误导入仓库根目录包；业界长期维护项目的通行做法。

## ADR-003：构建与打包

- **决定**：setuptools + pyproject.toml，`mirrorly` CLI 入口预留，dev 依赖 pytest + ruff。
- **理由**：标准、无额外学习成本；`pip install -e ".[dev]"` 即可开发。

## ADR-004：版本控制规范

- **决定**：Git，main 分支；文本统一 LF（`.gitattributes`）；备份产物目录一律不入库。
- **理由**：Mirrorly 本身是备份工具，其仓库绝不能反向成为备份存储；换行符统一避免 Windows/Linux 混合环境下 diff 噪音。

## 待决策（技术调研后）

- 备份引擎：全量/增量/差异策略、内容寻址与去重（类 restic/bup 模型 vs rsync 增量）
- 同步机制：单向镜像 vs 双向同步；冲突解决策略
- 变更检测：定时扫描 vs 文件系统事件（watchdog/ReadDirectoryChangesW）
- 存储目标抽象：本地目录 / 外置盘 / 网络位置 / 云端
- 配置与元数据：快照索引格式、加密与完整性校验
