# Mirrorly

Mirrorly 是一款个人数据备份与同步工具（Personal Backup & Sync Tool）。

> 当前状态：**项目初始化阶段**——开发环境与工程骨架已就绪，尚未实现产品功能。
> 详细进度见 [docs/STATUS.md](docs/STATUS.md)，过程记录见 [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md)，产品需求见 [docs/PRD.md](docs/PRD.md)，技术风险见 [docs/TECH_RISKS.md](docs/TECH_RISKS.md)。

## 项目简介

Mirrorly 旨在为个人用户提供可靠的文件备份与多设备同步能力。核心目标（规划中）：

- 本地与外部存储之间的文件备份
- 可配置的同步策略与冲突处理
- 数据完整性与可恢复性保障

具体功能范围以需求文档为准，本仓库当前仅包含工程基础结构。

## 项目结构

```
Mirrorly/
├── README.md              # 项目说明（本文件）
├── pyproject.toml         # Python 项目元数据与构建配置
├── environment.yml         # Conda 环境定义
├── .gitignore              # Git 忽略规则
├── .gitattributes          # 跨平台换行符与 diff 策略
├── .editorconfig           # 编辑器统一格式约定
├── src/
│   └── mirrorly/           # 主源码包（src-layout）
├── tests/                  # 测试代码
├── scripts/                # 开发与运维脚本
└── docs/                   # 项目文档
    ├── STATUS.md           # 当前状态与环境快照
    ├── DEVELOPMENT_LOG.md  # 开发日志（按日期追加）
    └── ARCHITECTURE.md     # 架构决策记录
```

## 开发环境

| 项目 | 值 |
| --- | --- |
| 操作系统 | Windows |
| Python | 3.12（Conda 环境 `mirrorly`） |
| 包/环境管理 | Conda（Anaconda3，环境位于本机默认位置） |
| 版本控制 | Git |

### 环境搭建

```bash
# 创建/复现 Conda 环境（环境保存在本机 Conda 默认环境目录，不在项目仓库内）
conda env create -f environment.yml

# 激活环境
conda activate mirrorly
```

## 开发约定

- 源码采用 `src-layout`，主包位于 `src/mirrorly/`。
- 所有进度与环境信息更新必须同步记录到 `docs/STATUS.md` 与 `docs/DEVELOPMENT_LOG.md`。
- 架构层面的决定记录在 `docs/ARCHITECTURE.md`。
