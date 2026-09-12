# Mirrorly 项目状态

> 本文件维护项目当前状态与环境快照。每次重大变更后更新。
> 最后更新：2026-09-12（产品定义阶段）

## 当前阶段

**产品定义完成（PRD v0.1 草案，待确认）** —— 已完成市场调研、用户画像、MVP 范围与关键决策推荐。下一步：PRD 确认 → 技术风险分析 → 架构设计。

## 关键文档

- [docs/PRD.md](PRD.md)：产品需求文档 v0.1（草案，含 5 个待确认问题）
- [docs/ARCHITECTURE.md](ARCHITECTURE.md)：架构决策记录（ADR 001–004 为工程决策；产品 ADR 待 PRD 确认后补充）

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

## 待办（建议优先级从高到低）

1. **PRD v0.1 确认**：回答 PRD 第 8 节的 5 个待确认问题（定位、存储模型、exFAT 策略、加密是否提前、多任务/保留策略）
2. **技术风险分析**：变化检测、增量备份、完整性校验、冲突处理、中断恢复、大文件、外置盘七项（docs/TECH_RISKS.md）
3. **架构设计**：模块划分、快照/清单格式（写入 docs/ARCHITECTURE.md ADR）
4. **MVP 定义细化**：将 M1–M10 拆为可验收的开发任务
5. **CI/质量基建**：pytest、ruff 本地跑通后再考虑自动化

## 风险与注意事项

- 工作区位于外置盘（`P:\`），注意断盘风险；建议尽早配置远程仓库并定期推送
- Git 身份当前为仓库级占位配置，正式使用前应替换为真实姓名/邮箱（见 DEVELOPMENT_LOG 2026-09-12 条目）
- Mirrorly 是备份工具，务必坚持「备份产物永不入库」的 .gitignore 约定
