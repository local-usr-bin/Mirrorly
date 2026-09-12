# Mirrorly 项目状态

> 本文件维护项目当前状态与环境快照。每次重大变更后更新。
> 最后更新：2026-09-12

## 当前阶段

**项目初始化完成** —— 工程骨架、版本控制、开发环境已就绪。尚未开始产品功能开发。

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

## 待办（建议优先级从高到低）

1. **需求确认**：明确备份/同步的核心场景、目标平台、数据规模与冲突处理预期
2. **技术调研**：备份/同步引擎方案（自研 vs 基于成熟库，如 rsync 类算法、bup/restic 思路）、增量备份与去重策略、文件监控（watchdog）等
3. **架构设计**：模块划分、存储抽象、配置管理（写入 docs/ARCHITECTURE.md）
4. **MVP 定义**：划定第一个可用版本的最小功能集
5. **CI/质量基建**：测试框架（pytest 已在 dev 依赖中）、代码检查（ruff）本地跑通后再考虑自动化

## 风险与注意事项

- 工作区位于外置盘（`P:\`），注意断盘风险；建议尽早配置远程仓库并定期推送
- Git 身份当前为仓库级占位配置，正式使用前应替换为真实姓名/邮箱（见 DEVELOPMENT_LOG 2026-09-12 条目）
- Mirrorly 是备份工具，务必坚持「备份产物永不入库」的 .gitignore 约定
