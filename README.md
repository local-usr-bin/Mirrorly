# Mirrorly

Mirrorly 是面向 Windows 个人用户的文件夹版本化备份助手：单向备份，整文件快照，使用 NTFS 硬链接复用未变文件。备份产物是普通、可浏览和复制的文件。

> 当前形态：**Windows CLI（GUI 尚未实现）**。已实现 `init / backup / list / verify / restore`，主要生产代码安全加固已完成。当前状态和限制见 [STATUS](docs/STATUS.md)，命令与退出码见 [CLI_SPEC](docs/CLI_SPEC.md)。

## 项目简介

Mirrorly 以备份安全和可解释性为先，目前支持：

- 本地/外置盘上的版本化目录备份、排除规则和 dry-run 预览。
- 未变文件的硬链接复用、变更文件的整文件复制；源中删除的文件仍可在保留的旧快照中找到。
- 按配置保留最近快照及月度代表、中断后以可用 incomplete 为只读基线创建新备份。
- 写入校验、按需 full/quick verify、整快照或指定文件/子树恢复。

当前没有 GUI、内置调度/后台服务、双向同步、云后端、加密/压缩或系统镜像功能。正式支持与验证平台为 Windows 10/11，目标文件系统为 NTFS；非 NTFS 默认拒绝，显式 `--filesystem-policy warn` 并确认后可用整文件复制模式，空间占用会增加。其他平台不作支持承诺。

## 安装与基本使用

从当前源码仓库根目录，在 Python 3.12+ 环境中安装：

```powershell
python -m pip install .
python -m mirrorly --help
```

也可使用安装生成的 `mirrorly` 命令。运行时依赖由 `pyproject.toml` 声明；开发安装使用 `python -m pip install -e ".[dev]"`。当前仍是公开前源码阶段，不以 PyPI 发布包作为安装前提。

以下路径为示例，请换成自己的源、备份盘和空恢复目录；源与备份仓库不得互相包含：

```powershell
mirrorly --config C:/MirrorlyConfig --task daily init --source D:/Data --target E:/Backups
mirrorly --config C:/MirrorlyConfig --task daily backup --dry-run
mirrorly --config C:/MirrorlyConfig --task daily backup
mirrorly --config C:/MirrorlyConfig --task daily list
mirrorly --config C:/MirrorlyConfig --task daily verify
mirrorly --config C:/MirrorlyConfig --task daily restore --to D:/Recovered
```

仓库创建在 `E:/Backups/MirrorlyRepo/`，任务配置在 `C:/MirrorlyConfig/config.d/daily.toml`。`verify` 和 `restore` 默认选择最新的 sequenced complete；可用 `--snapshot <id>` 指定历史版本，`verify --all` 校验所有 complete。`restore --path docs` 选择字面子树，默认 `--overwrite never`，已有目标不被覆盖。Restore 不自动执行 full verify，可先校验再恢复。

## 安全边界与兼容性

- **不要编辑快照目录中的文件**：硬链接可能让多个历史快照共享同一份内容。Mirrorly 只在新快照中写数据；也请保留独立副本，不把唯一副本放在同一块盘上。
- 默认变更检测信任相同的大小和 mtime；`backup --full-hash` 强制对与基线同大小的源文件作哈希复核，不再只凭 mtime 判未变。新增/大小已变文件直接进入复制流程。这不是 VSS 或源目录的时间点一致性快照。
- 配置 `[verify] on_write = true` 为默认值，成功 complete 的普通文件有可信哈希覆盖。关闭它后，新复制文件可没有哈希；full verify 对 `sha=None` 条目只检查存在性/类型/大小，并计入 `unhashed_entries`。`verify --quick` 完全不做哈希比对；full verify 比较快照与 manifest，不证明历史源数据本身正确。
- 当前新仓库为 repo format v2，新 manifest 为 v2；持久 `lifecycle_seq` 决定生命周期先后，时钟回拨不改变 latest/baseline/keep-last 选择。新 ID 为 `YYYY-MM-DD_HHMMSS-s<20位sequence>-<32位UUIDv4 hex>`，不要从文件名排序推断 latest。
- 合法 v1 manifest 仍可读取。首次正式 backup 会升级 v1 仓库；无 sequenced complete 时全量物化源，不复用 legacy 基线。多个 legacy complete 且无 sequenced complete 时，默认 verify/restore 要求显式选择；legacy complete 不被自动 retention 删除。升级后的仓库不能交给只支持 v1 的旧程序写入。
- Backup 同时使用任务锁和仓库写锁，锁占用返回 6。崩溃可能留下锁；只有确认该仓库没有备份进程后才可手工移除对应 lockfile，不自动按 PID 猜测或夺锁。详见 [CLI_SPEC](docs/CLI_SPEC.md)。
- Backup/verify 的 JSON report 是必需命令产物。Complete manifest 发布后，report 失败仍返回非零，并明确说明「快照已提交、报告失败」；不要仅凭命令失败推断快照不存在。Report I/O 已使用 Windows extended-length path，但并非整个控制面都保证支持任意长路径。

更多边界（legacy、残留、文件 symlink 环境性 skip）见 [STATUS](docs/STATUS.md)。MVP 历史验收保持 **MVP ACCEPTED — PASS WITH ENVIRONMENTAL SKIPS**，见冻结的 [MVP_ACCEPTANCE](docs/MVP_ACCEPTANCE.md)；该历史结论不等于所有后续加固审计均已完成。

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

## License

采用 [MIT License](LICENSE)。
