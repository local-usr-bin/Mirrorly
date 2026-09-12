# Mirrorly 项目状态

> 本文件维护项目当前状态与环境快照。每次重大变更后更新。
> 最后更新：2026-09-12（PRD v0.2 确认 + 技术风险分析 + 架构 ADR 完成）

## 当前阶段

**设计阶段收尾** —— PRD v0.2 已确认，技术风险分析（TECH_RISKS v1.0）与产品架构决策（ADR-005~009）已完成。下一步：架构细化（清单格式、CLI 命令集等）→ MVP 开发。

## 关键文档

- [docs/PRD.md](PRD.md)：产品需求文档 v0.2（已确认，决策 D1–D7 定案）
- [docs/TECH_RISKS.md](TECH_RISKS.md)：技术风险分析 v1.0（TR-1~TR-7，高风险集中于中断恢复与外置盘）
- [docs/ARCHITECTURE.md](ARCHITECTURE.md)：架构决策记录（ADR-001~009）

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

## 待办（建议优先级从高到低）

1. **架构细化**：清单（manifest）格式、配置存储、CLI 命令集与退出码、哈希算法基准测试（见 ARCHITECTURE.md 待决策）
2. **MVP 任务拆分**：将 M1–M10 拆为可验收的开发任务清单
3. **开发启动**：搭建核心模块骨架（扫描/清单/快照/校验），仍按"不写过度复杂架构"原则小步迭代
4. **测试基建**：pytest 用例随模块并行建立；外置盘端到端测试方案

## 风险与注意事项

- 工作区位于外置盘（`P:\`），注意断盘风险；建议尽早配置远程仓库并定期推送
- Git 身份当前为仓库级占位配置，正式使用前应替换为真实姓名/邮箱（见 DEVELOPMENT_LOG 2026-09-12 条目）
- Mirrorly 是备份工具，务必坚持「备份产物永不入库」的 .gitignore 约定
