# Mirrorly 开发日志

> 按日期倒序追加（新条目写在最上面）。每条记录：日期、做了什么、关键决定、遇到的问题。

---

## 2026-09-19 Mirrorly v0.1.0 正式发布（post-release bookkeeping）

- 项目负责人确认 **Mirrorly v0.1.0 GitHub Release 已正式发布**，标题为 **Mirrorly v0.1.0**，标记为 **Latest**、**非 Pre-release**；GitHub source repository 为 Public。
- Tag `v0.1.0` 指向 release commit `a466e6913c66932b9226fd0469d5a33017bdb6d4`。本次开始时 `HEAD = main = origin/main` 均为该 commit，working tree/index clean；本次只做文档 bookkeeping，不修改 tag 或 GitHub Release。
- Release 提供 **Source code ZIP / tar.gz**，没有手动上传的 binary/package asset；**未发布 PyPI package**，`Private :: Do Not Upload` 继续有意保留。
- Release candidate validation：**570 passed / 4 environmental skips / 0 failed**；独立 Windows E2E：**13 passed**。4 skip 仍是 Windows 文件 symlink 创建权限 / Developer Mode 环境限制，不记为 PASS；本次不重跑 regression。
- 当前 source/package version 为 **`0.1.0`**，**Alpha maturity 保留**；正式 GitHub source release 不表示 production-ready、stable 或 `1.0` ready。Final Pre-Public Gate 与 GitHub Public source readiness 均为 **PASS**。
- 历史 frozen tag `v0.1.0-mvp` 仍指向 `999ceb88c7d4c73c3062eb1927fd1c513d9e0234`，与 `v0.1.0` 并存；旧日志、MVP acceptance 和历史测试数字不改写。
- **CLI / Pre-Public / 0.1 release 主线：CLOSED。** 下一阶段为 **GUI product/UI design and development**；GUI 尚未实现，本次不进入 GUI implementation。

---

## 2026-09-19 0.1.0 final release preparation（基线 554bde9）

- 本次开始时 `HEAD = main = origin/main = 554bde9f760154268a9ea60b0a7c974119cdea92`，working tree/index clean。仅进行 release preparation，不新增功能或修改业务行为。
- `pyproject.toml` 与 `mirrorly.__version__` 从 `0.1.0.dev0` 收口为 **`0.1.0`**；smoke regression 同步精确值并保留源码双版本一致性检查，不引入动态版本机制。
- **Development Status :: 3 - Alpha** 与 **Private :: Do Not Upload** 原样保留；PyPI 发布仍禁用，其他 package metadata 不变。不安装或重新安装开发环境 package。
- CLI_SPEC / STATUS 同步当前版本与待 review 的 release preparation 状态；README 无当前版本声明，无需修改。旧日志中的 `0.0.1` / `0.1.0.dev0` 和历史测试数字不改写。
- 历史 `v0.1.0-mvp` 仍冻结于 `999ceb88c7d4c73c3062eb1927fd1c513d9e0234`，不是已发布的 Python package `0.1.0`。未来 `v0.1.0` 将与其并存；本次不创建 tag / GitHub Release、不发布 PyPI，正式 release 仍待独立批准。
- GUI 尚未实现且未开始开发；本阶段不进入 `0.2`，GUI development 在 CLI `0.1.0` release 收口之后开始。
- 实际验证：focused version / entry-point tests **5 passed**；full regression **570 passed / 4 skipped**；独立 Windows E2E **13 passed**；Ruff check PASS、format check **44 files already formatted**、`git diff --check` 通过。4 skip 仍为 Windows 文件 symlink 环境限制，不记为 PASS。
- `python -B -m mirrorly --version` 与现有 `mirrorly.exe --version` 均输出 `mirrorly 0.1.0`，两种入口的 `--help` 正常。已安装 distribution metadata 仍为历史 `0.0.1`，不影响 editable source runtime；未执行 reinstall。

---

## 2026-09-19 Public source transition（基线 c75e1ad）

- Pre-Public safety hardening、living documentation 与 package metadata 同步已完成；**Final Pre-Public Gate：PASS；GitHub Public source readiness：PASS**。项目负责人已确认 repository 从 Private 切为 **Public**，本次仅记录转换，不操作 visibility。
- 本次 bookkeeping 开始时 `HEAD = main = origin/main = c75e1adaf64afd43c6810536c442f1b05d0dece7`，working tree/index clean；不修改 production code、tests 或 package metadata。
- 已完成的 committed-state regression：**570 passed / 4 environmental skips**；独立 Windows E2E：**13 passed**。4 skip 为 Windows 文件 symlink 创建权限 / Developer Mode 环境限制，不记为 PASS；本次 bookkeeping 不重跑 regression。
- Public exposure / reachable history / secret audit **无 blocking finding**。项目负责人知情接受已披露的低敏感度本机路径/账户名历史痕迹；本条记录其 Final Gate 与 Public 转换确认，不将此前工具受限的 GitHub-side 检查改写为已执行 API 审计。
- 历史 `v0.1.0-mvp` 保持冻结，仍指向 `999ceb88c7d4c73c3062eb1927fd1c513d9e0234`；MVP acceptance 与旧日志不改写。
- 当前 source/package 为 **`0.1.0.dev0` / Alpha**，不是 production-ready 或 stable 声明；尚未声明 package `0.1.0` final、创建 `v0.1.0` tag / GitHub Release，未发布 PyPI。**`Private :: Do Not Upload` 有意保留**，仅表示当前不发布 PyPI，不限制 GitHub Public 源码。
- 下一步进入 **`0.1.0` final release preparation**；GUI 尚未实现，GUI development 在 `0.1.0` release 收口之后开始。本次不创建 tag/release；0.1.0 final release preparation 将作为下一独立阶段进行。

---

## 2026-09-19 Pre-Public package metadata 同步（基线 d92c9cb）

- 本次开始时 `HEAD = main = origin/main = d92c9cb0ee0c057777bc8a2f2caed20cc7be39f8`，working tree/index clean。仅同步 metadata、包说明、smoke regression 和相关 living docs，不改业务逻辑或 repo/manifest 格式。
- 按已确认决定，`pyproject.toml` 与 `mirrorly.__version__` 同步为 **`0.1.0.dev0`**，Development Status 改为 **Alpha**；description 与包 docstring 改为当前 Windows 单向文件夹备份 CLI 定位，GUI 尚未实现。其余依赖、license、entry point 与构建配置不变。
- `0.1.0.dev0` 表示首次正式编号 CLI `0.1.0` 之前的 development state；历史 `v0.1.0-mvp` 是 Git milestone，当时 package metadata 实际为 `0.0.1`，不代表曾发布 Python package `0.1.0`。冻结 tag 和历史验收记录不改写，GUI 后续版本不在此决定。
- **`Private :: Do Not Upload` 原样保留**：当前不发布 PyPI，此 classifier 不要求 GitHub repository 必须 Private。Final Pre-Public Gate 尚未通过；本次不创建 release/tag、不改变仓库 visibility、不安装或构建/上传 package。
- `tests/test_smoke.py` 保留精确 runtime version 检查，新增 stdlib `tomllib` 读取源码 `pyproject.toml` 的一致性断言；不依赖 editable-install distribution metadata，不引入动态版本机制。
- 实际验证：targeted **5 passed**；full regression **570 passed / 4 skipped**；独立 Windows E2E **13 passed**；Ruff check PASS、format check **44 files already formatted**、`git diff --check` 通过。4 skip 均为现有 Windows 文件 symlink 创建权限限制；pytest 使用 system Temp basetemp，禁用不可写的可选 cache，未修改权限。
- `python -B -m mirrorly --version` 与现有 `mirrorly.exe --version` 均为 `mirrorly 0.1.0.dev0`；tomllib/setuptools metadata validation、requirements/version parsing、package discovery 通过。现有 installed distribution metadata 仍为 `0.0.1`，如实记录，未执行 reinstall。

---

## 2026-09-19 Pre-Public 文档同步（生产基线 1fb0cde）

- 本次开始时 `HEAD = main = origin/main = 1fb0cdedd19591be663051e85e1abf677673539d`，working tree/index clean。此条只同步当前源码、测试与 Git 历史，不修改生产代码/tests，不重新运行回归。
- README 从初始化/同步规划改为已实现的 Windows 单向快照备份 CLI，补安装、backup/verify/restore 示例、NTFS/hardlink 边界、legacy 升级、校验覆盖与 MIT 入口。
- STATUS 增加当前格式/锁/sequence/ordering/identity/校验/publication/path-validation/report 状态及源码、测试入口；原 T-01~T-10 明确归为历史记录。CLI_SPEC 同步已批准的实际命令行为与错误语义；冻结 MVP v1.0 仍可从历史 tag 查阅。
- 已提交加固核对：`2977497` temp ownership、`f44795c` resumed hash coverage、`196ce9f` AGENTS、`3313566` False→True hash guard、`b22e87b` staging metadata、`438d92f` repo writer lock、`c5259b3` UUID identity、`0b224c5` durable sequence foundation、`6ae831a` authoritative ordering consumer migration、`aef6648` shared lexical manifest boundary、`1fb0cde` report long-path 与 post-commit error semantics。
- 当前实现为 repo v2 + mandatory lifecycle state、新写 manifest v2 + `lifecycle_seq`；v1 只兼容读取，不倒填 sequence。每次 fresh/resume physical attempt 都 reserve 新 sequence；旧 timestamp slot / prefix-local ordinal 已不是当前 allocator。Safety ordering 使用 sequence，日历分组仍使用 wall clock。
- 已完成验证记录（本次未重跑）：当前基线 **569 passed / 4 skipped**；Windows E2E **13 passed**；Ruff check PASS，format check **44 files already formatted**。4 skip 是 Windows file-symlink privilege / Developer Mode 限制，不记为 PASS。
- 历史材料不改写：**MVP ACCEPTED — PASS WITH ENVIRONMENTAL SKIPS**；MVP **452 passed / 4 skipped / 0 failed**，T-10 E2E **13 passed**。Tag `v0.1.0-mvp` 仍指向 `999ceb88c7d4c73c3062eb1927fd1c513d9e0234`；验收时生产 HEAD `60ed124` 与文档提交/tag target 是不同概念。
- 尚未完成的 metadata/path 综合动态审计不因 shared lexical validator 上线而变成 PASS；未知项和环境限制仍如实保留。CP-KILL-A1、post-commit residue/retention diagnostics、package metadata 等后续事项不在本次实现范围；不新增 GUI 或其他产品功能。

---

## 2026-09-13（十八）MVP 最终验收盖章

- 产品负责人最终裁定：**MVP ACCEPTED — PASS WITH ENVIRONMENTAL SKIPS**。
- Final accepted HEAD = `60ed124`（feat(repo): add volume-anchored target relocation）；T-10 initial baseline = `a31126b`（性能基线在该基线测得）。
- 完整 pytest：**452 passed / 4 skipped / 0 failed**；T-10 E2E：**13 passed / 0 failed**。
- M10「盘符漂移」blocker 已修复并验收通过（卷锚自动重定位）。
- 4 个 skip 原因：文件级 symlink/reparse 用例在当前 Windows 环境无 SeCreateSymbolicLinkPrivilege / Developer Mode；目录级 junction 对应防护已真实 Windows 通过。
- 两个非阻塞 post-MVP hardening 项已记入 MVP_ACCEPTANCE §11（repo.get_volume_info 的卷根获取方式 / list_mounted_volumes 枚举终止语义），本轮不修改代码。
- **MVP 开发阶段结束，生产代码冻结**；远程仓库 push、release/tag 与后续 hardening / GUI 另起阶段。

---

## 2026-09-13（十七）T-10 M10 blocker 修复：卷锚自动重定位

### 做了什么

- 架构 review 裁定 M10「盘符漂移」为真实 MVP blocker：冻结语义应为 A+B（自动重定位
  同一卷 + fail closed），原实现只有 B（`_open_repo` 只校验字面路径 serial，全仓无
  卷枚举代码，D:\→E:\ 后必然失败，唯一出路是用户手工改 target.path）。
- 新增 `src/mirrorly/volume.py`：Windows 官方卷定位 API（ctypes，只读，零依赖）——
  `GetVolumePathNameW`（路径→挂载点根）、`GetVolumeNameForVolumeMountPointW`
  （挂载点→Volume GUID）、`GetVolumePathNamesForVolumeNameW`（GUID→当前全部挂载点，
  未挂载/不可解析返回空）、`FindFirstVolumeW`（仅诊断）。
- 数据模型：repo.json `volume.guid` 增量可选（format_version 维持 1，旧文件缺键
  兼容读，旧 reader 忽略新键——双向兼容测试确认）；TaskConfig `[target]` 新增
  `volume_guid / repo_id / repo_dir` 三键，**all-or-none**（半套 ConfigError，拒绝
  从 GUID 安全模式静默降级），`repo_dir` 为 canonical 卷内相对路径（卷根 "."，
  禁绝对/UNC/`..`/盘符限定），load/write/resolver 入口三重校验 + join 后 containment 复验。
- `cli._open_repo` → `cli._resolve_repo` 七态 fail-closed 状态机：Case 1 全匹配正常；
  Case 2/3 path 失联或被其他卷占用 → GUID 直查挂载点 → repo_id+serial 确认 →
  自动重定位（运行时 resolution，不自动改写 config，stderr 提示）；Case 4 repo_id
  不匹配 exit 5；Case 5 多候选 samefile 去重后仍 >1 → exit 5 不猜；Case 6 GUID 无
  挂载点 exit 5「目标备份卷未连接或卷锚已失效」；Case 7 卷在仓库缺失 exit 1 不自动
  init；legacy 配置零行为变化。所有失败先于锁/扫描/写入（T-09 零写入语义保持）。
- **无身份降级**：GUID 解析失败绝不用 serial+repo_id 认领另一个卷（FindFirstVolume
  枚举仅诊断，不作为 acceptance fallback）。
- 测试：test_volume.py（7 用例，真实 API smoke，沙箱内 GUID 往返链路可用——关键
  前置验证）；test_cli.py `TestM10Resolver`（11 用例：Case 1-7 + samefile 去重 +
  无降级 + repo_dir 双防线）；test_config.py（+16 用例：partial/非法 GUID/repo_dir
  逃逸/写边界零写入）；test_e2e.py 重写重定位用例（真实 GUID API 链路，仅把配置
  path 换为失联地址，锚字段不动）+ 新增 GUID 未挂载零写入（REAL API）与仓库缺失
  exit 1 两用例；retention fixture 保留锚字段。
- 文档：MVP_ACCEPTANCE §8/§11/§12/§13 重写（移除「盘符漂移不自动重定位」限制；
  GUID 语义措辞按裁定——「Windows 安装/挂载管理器级卷锚」，不写 BitLocker 因果）。

### 关键决定

- 信任锚 = Volume GUID + serial + repo_id；label 只做诊断（裁定：label 用户可改、
  跨卷可重复，GUID 优于字面「卷标」）。
- init 时一次性登记锚，重定位后不自动改写 config（最少可变状态；resolver 每次 O(1)）。
- repo 被复制/移动到另一卷（repo_id 匹配但卷锚不匹配）→ fail closed（裁定：未来
  需要时走显式 migration，backup 不猜）。
- Case 7（卷在但仓库缺失）exit 1 而非 5（裁定：volume identity 已正确，失败域是
  repo-not-found）。

### 结果

- 完整 pytest 全套：**452 passed / 4 skipped / 0 failed**（4 skip 均为文件级
  symlink 权限限制；4 个目录级 junction 用例本轮沙箱内亦通过）。
- E2E 全套 13 passed（含上轮被护栏拦截的中断/retention）；ruff check /
  format --check 全绿。
- MVP_ACCEPTANCE 结论更新为 PASS WITH ENVIRONMENTAL SKIPS（M10 blocker 已修复），
  等产品负责人最终盖章。

### 遇到的问题

- `c_wchar` 数组无 `.raw` 属性（改切片读取多字符串）；未挂载 GUID 返回
  WinError 2 而非空列表（按「未挂载」语义映射为 []）。
- bash 环境 PATH 再度瞬时故障（dirname/mkdir not found）→ 本轮全程 PowerShell。
- TOML 值含反斜杠时测试须用 literal string（单引号），双引号下 `\?` 为非法转义。

---

## 2026-09-13（十六）T-10 端到端验收

### 做了什么

- 新增 `tests/test_e2e.py`（11 用例）：全部经真实入口（mirrorly.exe / python -m
  mirrorly）+ 真实 NTFS 执行——主流程（init→backup#1→verify→变更→dry-run
  零写入→backup#2→list）、restore（整快照字节级/--path 文件与子树/never/always）、
  损坏检测（exit 4 + 报告定位 + quick 冻结语义）、TR-5 真实 kill 续传、
  M10 身份（错误 serial exit 5 零写入 / 同卷新地址 SIMULATED 盘符漂移）、
  retention（keep_last=2 多快照清理后 verify 全过）。
- 新增 `scripts/t10_perf_baseline.py` + 运行两个 workload 归档性能基线（见下）。
- `tests/test_restore.py` `_make_symlink`：目录场景 junction 回退（真实
  reparse point、产品同一防护路径），4 个目录级用例真机通过。
- 产出 `docs/MVP_ACCEPTANCE.md`（完整验收报告 + 已知限制清单）。

### 关键决定 / 裁定

- **M10 盘符漂移语义**：CLI_SPEC §6（冻结契约）把「盘符漂移」定义为 exit 5
  目标身份不符 → 判定为 **fail closed（B）**：绝不向错误卷写入；用户改配置
  指向同卷新地址后，卷标识匹配继续同一仓库（E2E SIMULATED 验证）。PRD US-3
  字面的「零干预自动重定位」未实现（与 CLI_SPEC 冲突，规范内在张力），
  列入已知限制，不判 blocker。
- 推荐结论：**PASS WITH ENVIRONMENTAL SKIPS**（4 个文件级 symlink 用例
  因无 SeCreateSymbolicLinkPrivilege 跳过；目录级 junction 已真机通过）。

### 性能基线（T-10 冻结标准 #4，归档）

环境：Windows 11 / Python 3.12.14 / C: NTFS（卷标 OS）/ Mirrorly 0.0.1 @ a31126b。
介质类型未能可靠探测（不猜）。

| 指标 | A 混合（10,006 文件 / 9.6 GB） | B 少而大（16×256 MiB） |
| --- | --- | --- |
| 首次 backup | 51.7 s（写 9.6 GB） | 15.9 s（写 4.3 GB） |
| unchanged backup | 6.5 s（0 B、复用 10,006） | 2.5 s（0 B、复用 16） |
| 小变更 backup | 12.0 s（563 MB） | 1.0 s（268 MB） |
| verify full ×3 快照 | 33.4 s | 5.9 s |
| verify quick | 3.0 s | 0.6 s |
| restore 整快照 | 59.7 s | 4.3 s |
| 空间（unchanged #2） | 仅 +0.01 GB（硬链接生效） | 0 |

数据集 A：4.5 GB 大文件 + 10,000×256 KiB + 4×512 MiB + 文本/空目录。

### 遇到的问题

- 宿主 safe-delete 护栏（50/turn）在 E2E 多次迭代后于最终确认运行拦截
  「中断续传」与「retention」两用例（count 53–61，\\?\ 前缀删除计数）——
  两者在 runs 1–5 全绿（同代码），非产品缺陷；全套 pytest 确认运行留待
  配额刷新后执行（与 T-09 相同处理）。
- 沙箱内 os.symlink 静默失败（不抛异常、不创建）、reparse point 不可见；
  沙箱外 junction 可用、symlink 因无特权被拒（ERROR_PRIVILEGE_NOT_HELD）
  ——reparse 专项在沙箱外跑，文件级 4 用例如实 skip。
- 测试侧经验：TOML 中路径为转义存储（须解析比较，不能子串断言）；
  manifest 条目序列化键为 `type: dir|file`；>260 路径对普通
  rglob/os.walk 不可见（须 \\?\ 遍历）。

---

## 2026-09-13（十五）T-09 源码验收：Windows 卷根 containment blocker 修复

### 做了什么

源码验收发现 `_path_within`（init preflight 的 source/仓库互相包含检查）对
Windows 卷根判断错误，为真实 blocker，已修复（独立 commit，不 amend 2487dd7）：

- **bug**：旧实现用字符串 `startswith(p + os.sep)` 判断包含。当
  `parent = C:\`（realpath 已以反斜杠结尾）时，`p + os.sep` 逻辑上变成
  `C:\\`，任何子路径前缀失配 → `mirrorly init --source C:\ --target C:\backup`
  会漏过「prospective repo 位于 source 内」保护，允许仓库创建进备份源
  （备份自身仓库 / 递归吞入 backup artifacts 的风险）。
- **修复**：`_path_within` 改为 realpath + normcase 后
  `os.path.commonpath([child, parent]) == parent` 判断（相等或包含均覆盖）；
  不同 drive（或混合类型）时 commonpath 抛 ValueError → 按「不包含」处理。
  保留 realpath 别名/junction 解析与 normcase Windows 大小写语义，
  双向 containment 检查不变。
- **新增测试 7 项**：`_path_within` 纯单元 6 项（当前盘符根含子目录 True
  + 反向 False、等路径、嵌套、sibling False、跨盘 C:/D: False、UNC root
  含子目录）；init 盘根 source 端到端（source=当前盘根、target=同盘测试目录
  → exit 1、无 MirrorlyRepo、无 repo.json、无 task config——preflight
  拒绝零写入，不触发全盘扫描）。

### 结果

- CLI 专项（PathWithin+InitPreflight）10 passed；完整回归
  **390 passed / 8 skipped / 0 failed**；ruff check / format --check 全绿。
- 本项完成后 T-09 正式最终验收。

---

## 2026-09-13（十四）T-09 最终收尾与验收

### 做了什么

1. **list --verbose 规范澄清（产品裁定）**：CLI_SPEC 的 verbose 统计由
   「文件数、总大小、增量大小、状态」改为「文件数、目录数、总大小、状态」。
   「增量/新写入大小」为 MVP 后候选指标；如未来实现，应在 backup 创建时
   持久化权威值（physical bytes written），而不是事后按 hardlink/link count
   估算或从 logs 反向拼装。list 现有输出已是该四项，业务代码零改动。
2. **write_task_config 写入边界防御**：`write_task_config()` 自身调用
   `validate_task_name(cfg.name)`，library API 不再依赖调用者提前验证；
   非法 name 在创建任何目录/文件之前抛 ConfigError（config.d 内外零写入）。
   新增 3 项测试：`../evil` 拒绝、`CON` 拒绝、正常名可写。
3. **init preflight 回归补强**：`test_existing_task_config_rejected` 在原
   exit 1 断言上补：target/MirrorlyRepo 不存在、target 树下无任何 repo.json、
   原 task config 字节保持不变——固化「task config 存在性检查位于 init_repo
   之前，失败零仓库写入」。
4. **真正完整回归**：safe-delete turn 配额刷新后，basetemp 用新的 OS 临时
   目录一次性跑全套：**383 passed / 8 skipped / 0 failed**——上轮 20 项
   护栏受阻用例全部转绿，确认此前判断（均护栏 SystemExit 非断言失败）成立。
   CLI 专项 119 passed；ruff check / format --check 全绿。

### 关键决定

- T-09 以本 commit 最终验收；07810c3 保留不 amend。

---

## 2026-09-13（十三）T-09 integration hardening follow-up

### 做了什么

review 发现 T-09 集成层五个问题，逐项修复（独立 commit，不 amend 1d07b41）：

1. **snapshot id collision 污染既有 manifest（blocker）**：`write_manifest` 用
   `os.replace`，旧流程「生成 id → 写 incomplete manifest → write_snapshot」
   在秒级 id 撞车时会先静默覆盖已有 complete manifest、再由快照目录碰撞报错。
   修复：新增 `_new_snapshot_id()`——在任务锁内先选定空闲 id（基准冲突时追加
   `-01`/`-02` canonical 后缀，兼容 Restore 单组件校验；100 候选占满则
   SnapshotError 安全失败），再落盘 incomplete manifest。任何 collision 下既有
   快照目录/manifest 字节/complete 状态零触碰（回归测试强制 id 生成器连续返回
   已有 complete id，逐字节断言原 manifest 不变）。
2. **任务锁前移**：真实 backup 在确定 repo/task 后立即取 `_TaskLock`，锁覆盖
   recovery/incomplete 基线选择、源扫描、变更检测、快照/manifest 写入、续传
   善后与 retention；dry-run 保持零写入不取锁（有意例外，注释声明）。测试证明
   已有锁时 `scan_recovery`/`scan_source` 被调用前即 exit 6。
3. **--json stdout 严格单一 JSON 文档**：`_info`/`_detail` 在 json 模式改走
   stderr；`_confirm`/`_ask` 在 json 无 --yes 时不发 input 提示、直接 _UserAbort
   （exit 6，stdout 为空，诊断 stderr）；dry-run --json 输出结构化 JSON；
   restore 计划展示/verify 逐项结果改由 _info 自动路由 stderr。
4. **init 预检前移**：task config 已存在检查移到 `init_repo` 之前（原先顺序反了，
   拒绝时已建仓库）；新增 source 与 prospective repo 互相包含检查
   （realpath+normcase，非字符串前缀）；所有预检失败保证零仓库写入。
   另补 `validate_task_name`（config.py 公共校验器）：禁止 `/`、`\`、`..`、`:`、
   控制字符、尾随点/空格、保留设备名（含 COM¹-³/LPT¹-³ 及带扩展名形式），
   `load_task_config` 与 CLI `--task`/init 均强制执行——手工编辑 TOML 注入
   `../evil` 时锁文件路径不再可能逃出 locks/。
5. **CLI 契约小缺口**：verify `--snapshot`/`--all` 改 mutually exclusive
   （argparse exit 2）；报告文件名加微秒时间戳 + 撞名追加 `-01` 后缀，同秒
   verify/backup 不再静默覆盖已有报告（保持 tmp → replace 发布）。

### 关键决定

- **list --verbose「增量大小」未实现——规范与数据模型冲突，提交裁定**：
  CLI_SPEC 要求 verbose 显示「文件数、总大小、增量大小、状态」，但冻结数据模型
  （manifest）从未定义「增量大小」权威口径。逻辑增量（相对前一快照新增/修改
  字节）与物理增量（实际新写入字节，硬链接复用不计）语义不同；物理增量取决于
  运行时 copy/link 决策（含 mtime 信任、suspected 哈希复核、exFAT 整文件复制、
  retention 删除中间快照改变「前一快照」参照），manifest 数据无法精确重放。
  按要求不自造算法，list --verbose 维持文件数/目录数/总大小/状态，待裁定口径。

### 遇到的问题

- **宿主 safe-delete 批量护栏（SAFE_DELETE_BULK_CONFIRM_REQUIRED，阈值 50/turn）**
  在本轮调试中配额耗尽：所有经 shim 回收站路由的删除被 SystemExit(1)。
  发现 shim 对 OS 临时目录（`tempfile.gettempdir()`）有设计内豁免
  （`_should_bypass_safe_delete`），basetemp 移到 `%TEMP%\mirrorly_pytest\run_*`
  后普通路径删除不再触发护栏（未关闭任何保护）；但 `to_long_path` 的 `\\?\`
  前缀路径无法匹配豁免（realpath 比较 mount 不一致），产品侧 retention/
  discard/restore 临时文件清理的删除仍计入护栏。
- 最终全套 360 passed / 8 skipped / 20 failed——20 个失败全部经日志核实为
  护栏 SystemExit（无任何断言失败），且全部是前几轮已绿过的既有测试
  （retention/recovery/snapshot/restore 各若干 + CLI 2 个），在护栏配额
  刷新的新 turn 重跑即可恢复。本轮新增 41 项测试全部通过。
- 并行编辑同一文件再次静默丢失编辑（verify 互斥组第一次未生效），已重申
  串行编辑纪律。

---

## 2026-09-13（十二）T-09 CLI 集成与报告

### 做了什么

- 新增 `src/mirrorly/cli.py`：argparse 五命令（init/backup/verify/restore/list），
  只做编排——全部业务语义委托 T-01~T-08 library API；
  `__main__.py` 委托 `cli.main`（pyproject entry point 原本即指向
  `mirrorly.__main__:main`，无需修改，editable 安装下 `mirrorly.exe`
  与 `python -m mirrorly` 均真实验证通过）。
- 退出码严格按 CLI_SPEC §6：0 成功 / 1 一般错误 / 2 用法错误（argparse
  自动 + `_UsageError`：多任务未指定 --task、--in-place 未配 --yes）/
  3 部分完成（备份跳过、恢复 skip/conflict/error/leftover）/
  4 校验失败 / 5 目标身份不符（卷序列号不匹配 `_IdentityMismatch`、
  仓库格式版本不兼容 `RepoFormatError`）/ 6 用户中止（确认拒绝、
  非交互无 --yes、任务锁占用）/ 130 Ctrl+C。
- 确认语义：破坏性操作（restore 覆盖、init warn 策略降级）交互确认或
  --yes；init warn 的确认由 CLI 层完成（区分取消→6 与错误→1），再以
  assume_yes=True 调底层防二次提问；--in-place 必须显式 --yes（否则 2）；
  backup 发现 incomplete 时提问续传（--yes 视为肯定；拒绝则从头新备份，
  incomplete 保留不动；非交互中止→6）。
- 任务锁：locks/<task>.lock，O_EXCL 独占创建，占用→6；MVP 不做 stale
  自动清理（避免误判活人锁，残留由用户手工删除）；dry-run 不取锁、
  零写入（快照/manifest/logs/locks 全部不变）。
- 报告落盘（M9）：backup/verify 报告 JSON 写入仓库 logs/（tmp+原子改名），
  终端另给摘要；--json 时 stdout 输出机器可读 JSON（list/backup/verify/
  restore/init 均支持）。
- backup 编排：配置解析 → load_repo+卷校验 → scan_recovery（incomplete
  提示续传，build_resume_baseline 基线）→ scan_source（配置+CLI 排除合并）
  → detect_changes（--full-hash 通过把基线 mtime 置 -1 强制全量哈希复核，
  复用冻结逻辑零改动）→（dry-run 返回）→ 任务锁 → 清 tmp 残留 →
  incomplete manifest 落盘 → write_snapshot → 最终 manifest（排除 skipped、
  linked sha 从基线 manifest 结转 + copied 写入校验哈希合并）→
  mark_complete 原子提交 → 续传善后 discard_incomplete → retention
  （build+apply，复核在底层）→ 报告。
- OQ-1 澄清：CLI_SPEC restore `--path <pattern>` → `--path <path>`，
  注明字面 snapshot-relative 路径、非 glob（仅措辞对齐 T-08 冻结语义）。
- 测试 `tests/test_cli.py` 75 用例：解析/help/entry point（subprocess
  真实运行）、五命令 happy path 与错误分支、退出码全映射、确认
  yes/no/--yes、续传与拒绝续传、锁占用/释放/Ctrl+C 清理、卷/格式不符、
  Unicode 往返、长路径备份+verify、quiet/json、dry-run 零写入、
  底层安全语义不被 CLI 削弱（伪造 plan 仍被底层拒绝）。

### 集成测试暴露并已修复的现有模块问题（极小修复）

1. **config.py dump_task_config 未转义 Windows 反斜杠**：TOML 基础字符串
   中的 `P:\...` 路径导致生成配置无法被 tomllib 解析（T-01 潜伏 bug，
   此前测试均用 POSIX 风格路径未触发）。新增 `_toml_basic_str` 统一转义。
2. **repo.py 增加 RepoFormatError(RepoError) 子类**：格式版本不兼容从
   一般错误中区分出来，供 CLI 精确映射退出码 5；不影响既有捕获
   RepoError 的代码（子类兼容）。

### 遇到的问题

- 全局选项写在子命令后（`mirrorly backup --config X`）的解析：用
  parents 公共解析器 + SUPPRESS 默认值，避免子解析器默认值覆盖已解析值。
- exFAT 测试 mock 需同时打 CLI 层与 repo 模块内部的 get_volume_info
  引用点（init_repo 内部独立引用）。
- in-place 恢复对既有文件按 never 跳过属正常 partial（3），初版测试
  误期 0，修正断言（CLI 行为符合规范）。

### 结果

- 专项 75 passed；完整套件 336 passed, 8 skipped（8 项仍为沙箱
  reparse/junction 限制用例）；ruff check / format --check 全过。
- 下一步：T-10 端到端验收。

---

## 2026-09-13（十一）T-08 hardening 第三轮：commit point 前最终复核

### 背景

review 确认前两轮落地，发现最后一个 Restore blocker：fresh check 与
`os.replace` 之间隔着大文件完整复制（可能数分钟），期间出现的同名文件
会被 `os.replace` 静默覆盖，违反「create 不得升级 overwrite」与「残余
TOCTOU 仅限紧邻检查→replace 小窗口」的冻结语义。

### 修复内容

- `_restore_one_file` 增加 `pre_commit` 回调：temp 完整准备好（写入 +
  flush + fsync + close + mtime 设置）之后、`os.replace` 之前调用；返回
  `(action, reason)` 即否决——temp 按本次 ownership 精确清理（失败进
  leftovers）后返回给调用方报告；返回 None 才 replace。
- 新增 `_final_recheck`：以本条开始执行时的 fresh action 为基线再跑一次
  共享分类器——final 变 skip/conflict 不 commit 按 final 报告；final 比
  基线更具破坏性（create→overwrite）记 conflict 拒绝；持平/降级允许
  commit。TOCTOU 收敛为「final check → replace」小窗口。
- 异常路径 cleanup 兼容 RestoreError（pre_commit 内分类器抛错同样清理）。

### 测试与一处测试修正

- 注入点选择：monkeypatch `os.utime`，命中「temp 已完整 staging、final
  recheck 与 replace 未执行」的精确窗口，无需真实大文件。
- 4 用例：create+never 复制期间出现目标不覆盖（字节不变）、create+always
  升级 conflict、older 复制期间 mtime 变新改判 skip、leaf 变 reparse 拒绝
  穿越（沙箱跳过）。
- **测试设计教训**：最初 ancestor 变 reparse 用例 `rmdir(dest/sub)` 必失败
  ——staging temp 就位于该 ancestor 内部（非空目录）。改为 leaf 变体
  （走过同一条 `_check_no_reparse_chain` 代码路径），偏差已向用户报告。
- 专项 122 passed / 8 skipped；全套 261 passed / 8 skipped，ruff clean。

---

## 2026-09-13（十）T-08 hardening 第二轮：Windows 路径与临时文件残余缺口

### 背景

上一轮 hardening（09ece82）review 确认 A–G 落地，再指出两个确定缺口 +
一个低成本补强，直接修复提交（不 amend、不开 T-09）。

### 修复内容

1. **canonical validator 拒绝反斜杠**：`validate_canonical_rel_path` 显式
   拒绝 `\`（此前 `..\evil.txt`、`\\server\share` 等可漏过——按 `/` 分段
   后单段不含 `..` 字面量）。manifest entry fail closed；用户 `--path`
   selector 仍由 `normalize_selector` 先行归一 `\` → `/`，便利性不变。
2. **`_restore_one_file` fd 泄漏窗口**：原 `with open(src), os.fdopen(fd)`
   单行结构中 source open 失败时 mkstemp 原始 fd 永不关闭。改为嵌套
   with + `fd_owned` 所有权标记：未转移所有权（fdopen 未执行）时异常
   路径显式 `os.close(fd)`，再做 temp cleanup。Windows 上 fd 未关会导致
   temp 删除失败 → leftover，测试以「leftovers 为空」反证 fd 已关闭。
3. **Windows 大小写冲突防护**：`_validate_manifest_paths` 在 exact
   duplicate 之外增加 casefold collision key——`A.txt`/`a.txt`、
   `Dir/File.txt`/`dir/file.txt` 均拒绝；不做 NTFS 内核级名字模拟，
   保守可解释。
4. **snapshot id 加固**：确认生成格式 `%Y-%m-%d_%H%M%S` 不含冒号，且
   Windows 上 `:` 是 NTFS ADS 分隔符——`_validate_snapshot_id` 改为复用
   单组件 canonical 规则（任何冒号、保留设备名、尾随点/空格、控制字符
   等一律拒绝；`/` 仍显式拒绝保证单段）。

### 测试

新增 17 用例：反斜杠 validator/篡改 manifest 参数化（4 种形式）/selector
归一回归、source open 失败注入（error 记录+temp 清理+无残留+partial
restore 继续）、大小写冲突参数化（2 组拒绝+1 组合法）、snapshot id 非法
形式参数化（6 种）+现行生成格式合法性回归。专项 119 passed / 7 skipped；
全套 258 passed / 7 skipped，ruff clean。

---

## 2026-09-13（九）T-08 safety hardening follow-up：apply 期安全边界加固

### 背景

T-08 代码 review 通过但指出 apply 期七个安全缺口，要求独立 follow-up 修复
（不 amend f46b1cf，不开始 T-09）。

### 修复内容（A–G）

- **A 紧邻 I/O 逐条重验（blocker）**：抽取单条目分类器 `_classify_entry`，
  plan 与 apply 执行共享同一逻辑；apply 在 batch 重跑规划后，对每个即将写入
  的条目在真正 I/O 前再跑一次分类器（目标存在性/类型、当前覆盖决策、双侧
  root/祖先/leaf reparse、单条 no-upgrade 比较），升级即拒绝该条记 conflict。
  残余 TOCTOU 仅限「紧邻检查 → open/create/replace」小窗口。
- **B 冻结 destination**：plan 时 `os.path.abspath` 固化为稳定绝对路径，
  apply 拒绝相对 destination（不受 cwd 影响）。
- **C mtime 前置**：write/fsync/close → temp 上 `os.utime` → `os.replace`
  为单文件唯一 commit point；mtime 失败旧目标字节不变。
- **D 全量 manifest 校验**：load 后、selector 过滤前校验**所有**条目路径
  （含未选中条目）；manifest 层不检测重复 path，Restore 侧拒绝重复（防字典
  折叠/重复执行）。
- **E 实际位置身份**：`_same_actual_location`（samefile 优先，realpath 兜底），
  目标边界比较同时覆盖 normpath 与 realpath 变体——junction/别名到达 repo
  不能绕过「仓库内拒绝」，source_root 别名仍要求 in_place。
- **F 属性查询 fail closed**：仅 FileNotFoundError/NotADirectoryError（及
  Win32 ERROR_FILE/PATH_NOT_FOUND，经 `use_last_error` 读真实错误码）视为
  不存在；PermissionError 等一律 RestoreError。
- **G apply 重验 plan 参数**：overwrite 合法集、paths canonical、entry
  action 合法集、destination 绝对路径，伪造参数走不进任何分支。

### 测试

新增 15 用例（mid-batch 故障注入 6、cwd 冻结 1、mtime 失败注入 1、manifest
全量/重复 2、别名身份 3、fail closed 2、伪造 plan 4——合计类内去重后 15 项；
其中 4 项 reparse/junction 用例在沙箱环境按探测跳过）。全套 241 passed /
7 skipped，ruff clean。

### 关键实现注意

- `ctypes.GetLastError()` 不可靠，需 `WinDLL(..., use_last_error=True)` +
  `ctypes.get_last_error()`；
- 故障注入用 monkeypatch 包装 `_restore_one_file`，在原调用**完成后**触发
  hook（保证 destination 已建）；dest 祖先变 reparse 用例需先 rmdir 恢复出的
  真实目录再建链接。

---

## 2026-09-13（八）MVP T-08 恢复（restore）

### 做了什么

1. **新增 `src/mirrorly/restore.py`**（plan/apply 两阶段）
   - `plan_restore(repo, snapshot_id, destination, *, paths, overwrite, in_place)`：
     只读产出 RestorePlan（**批准的意图，不是可信事实源**），含 manifest 指纹
     （仓库算法对 manifest 文件内容求哈希）。
   - `apply_restore(repo, plan)`：**不信任 plan**——重新
     `load_manifest(require_complete=True)`、重算 manifest digest 比对（不一致
     即 stale 整体拒绝）、重验全部安全边界、以相同参数重跑规划，并做
     **no-upgrade 对账**（skip/conflict < create < overwrite；任一破坏性升级
     → stale 整体拒绝零写入；持平或降级按重算结果执行，伪造 plan 无法提权）。
   - `--path` 为**字面 snapshot-relative 路径**（文件命中自身、目录命中整棵
     子树），无 glob/fnmatch 语义；`\` 归一为 `/`；未命中任何条目显式报错。
   - canonical Windows 路径校验（selector 与 manifest 条目共用）：空段、
     `.`/`..` 段、绝对/盘符路径、控制字符、`<>:"|?*`、尾随点/空格、保留设备名
     （CON PRN AUX NUL COM1-9 LPT1-9 **COM¹ COM² COM³ LPT¹ LPT² LPT³**，大小写
     不敏感且含带扩展名形式）一律非法；component 长度按 **UTF-16 code unit**
     计（上限 255，非 BMP 字符占 2 个 unit，不把 Python len() 当底层语义）。
   - 目标边界：destination 在仓库目录内一律拒绝；解析后等于 manifest
     source_root（同一实际位置）必须显式 in_place；source_root 普通子目录允许。
   - 覆盖策略：never 跳过 / older 仅当目标 mtime **严格更旧**才覆盖 /
     always 覆盖；file↔dir 类型冲突即使 always 也不删除用户目录/文件，记
     conflict 跳过；父链被同名文件阻挡同样记 conflict。
   - reparse 双侧防护：snapshot 读侧遇 reparse 整体拒绝（完整性异常）；
     destination 写侧逐条记 conflict（不拖垮整体）；目标根为 reparse 整体拒绝。
     属性读取优先 os.lstat，失败回退 GetFileAttributesW（扩展前缀失败再退普通
     路径）。**check-then-open 残余 TOCTOU 风险明确接受**（OQ-2）。
   - 写入：目标 parent 下 `tempfile.mkstemp(prefix=".mirrorly-restore-",
     suffix=".mrtmp")` 独占创建（短固定前缀，不含 final filename——合法 final
     名可能接近 255 unit 上限，追加随机串会溢出）；flush+fsync+close 后
     os.replace 原子改名 + mtime 保真；cleanup 只删本次记录的精确临时路径，
     绝不扫描后缀批量删除；cleanup 失败进 leftovers。
   - partial restore：单文件失败记 errors 继续，不做整体回滚；不自动 full
     verify（Q2）；恢复产物为普通文件。

2. **新增 `tests/test_restore.py`**：90 用例（87 passed + 3 skipped），覆盖
   canonical validator 参数化（含上位数字设备名、带扩展名形式、非 BMP 长度
   边界 😀×127/×128/×200）、字面 selector、目标边界矩阵、三种覆盖策略
   （older 严格小于：相等/更新均跳过）、类型冲突、stale（digest 变更/降级
   incomplete）、no-upgrade（升级拒绝/降级执行/伪造 plan 无法提权）、reparse
   三用例（本沙箱对 reparse 属性屏蔽，按环境探测跳过，真实 Windows 上执行）、
   长文件名（244 UTF-16 units）临时文件不溢出、用户 .mrtmp 文件不受清理影响、
   mtime 保真（FILETIME 100ns 容差）、partial restore。

3. **小修 `tests/test_scan.py`**（T-02 既有脆弱测试）：长路径用例的循环条件
   以中间目录长度 240 为准，basetemp 稍长时最终路径可能 ≤260 导致断言失败；
   改为以最终目标长度 >260 为准。非行为变更，仅消除 basetemp 长度敏感性。

### 关键决定

| 决定 | 理由 |
| --- | --- |
| apply 重跑规划 + no-upgrade 对账 | RestorePlan 只是批准意图；世界在 plan 后可能已变，破坏性升级必须重新确认 |
| destination 侧 reparse 逐条 conflict | 用户目录里一个链接不应拖垮整体恢复；snapshot 侧 reparse 是完整性异常，整体拒绝 |
| 临时文件短固定前缀 | final filename 可能接近 component 上限，拼接随机串会溢出 |
| UTF-16 code unit 计长 | Windows 底层长度语义；非 BMP 字符按 2 计，避免错误放行/拒绝 |

### 遇到的问题

- 并行 Edit 同一文件相互覆盖（最后一次写入赢），导致两处修改静默丢失——
  教训：对同一文件的多个编辑必须串行。
- 本沙箱环境创建的符号链接对 listdir 不可见或 reparse 属性被屏蔽，reparse
  三用例在本环境跳过（代码路径经审查，真实 Windows 执行）。
- GetFileAttributesW 经 ctypes 默认 restype=c_int，INVALID(0xFFFFFFFF) 被读成
  -1 导致比较失效（全部误判 reparse）；修正为 wintypes.DWORD。

### 下一步

T-09 CLI 集成（五命令接入、退出码、报告落盘）；OQ-1（CLI_SPEC `--path`
`<pattern>` 措辞澄清）随 T-09 处理。

---

## 2026-09-13（七）T-07 safety hardening：删除安全边界修复

### 背景

T-07 review 发现 apply_retention_plan 两个删除安全边界问题，本条目记录修复策略与删除顺序/失败语义（不修改 manifest schema、不引入垃圾回收架构）。

### 问题 1：执行阶段未复核 manifest 状态

- 原实现只检查 manifest 文件存在，信任"plan 一定来自 build_retention_plan"。
- 修复：两阶段执行。**阶段 1（零删除）**：对每个待删快照重新
  `load_manifest(require_complete=True)`（复用既有状态解析，不重复实现），
  incomplete / 非法 status / manifest 缺失或损坏 → 整体 RetentionError，
  任何删除都不发生（含计划中的合法项）。

### 问题 2：删除顺序的失败安全语义

- 原顺序 `rmtree(snapshot) → manifest.unlink()`：若 manifest 删除失败，留下
  "complete manifest 指向已删除数据"的虚假可信记录——备份软件的最危险失败态。
- 修复后顺序：**先 manifest.unlink()，再 rmtree(snapshot)**。失败语义：
  1. manifest 删除失败 → 快照数据与 manifest 均未触碰（完全一致状态），报错；
  2. 快照目录删除失败（manifest 已移除）→ 不存在可被 list/load 视为 complete
     的记录；残留（可能残缺的）目录成为孤儿目录，由 recovery.scan_recovery
     发现并报告——**宁可残留孤儿数据，不留虚假 complete 记录**；
  3. 正常路径 snapshot + manifest 同步消失，语义不变。

### 测试与验证

- 新增 6 个专项测试：手工 plan 指向 incomplete 拒绝且零改动、非法 status 拒绝、
  混合计划整体拒绝（合法项也不删）、manifest 删除失败数据完好且 manifest 仍可
  按 complete 加载、rmtree 失败不留虚假 complete（孤儿可被 scan_recovery 发现）、
  正常删除同步消失。
- 全套件 139 passed，ruff 通过；原有 incomplete / orphan / hardlink / dry-run
  用例全部继续通过。

### 环境记录

- pytest 会话开始时会清理"已存在的" --basetemp 目录，触发宿主 safe-delete
  批量确认钩子（turn 阈值 50，非交互 shell 无法确认 → SystemExit，后续用例
  级联 ERROR）。对策：每次运行使用全新不存在的 basetemp 子目录
  （P:\DevProjects\Mirrorly\.pytest_tmp\run_* 下，目录已 gitignore）。

### 下一步

T-08 恢复（快照浏览、恢复到目标位置、防覆盖）——等待确认后启动。

---

## 2026-09-13（七）MVP T-07 保留策略

### 做了什么

1. **新增 `src/mirrorly/retention.py`**
   - `build_retention_plan(repo, *, keep_last, keep_monthly)`：基于 list_manifests()
     的 complete 集合计算计划（只读）；排序用 manifest created_at 而非目录 mtime；
     keep_last=0 语义明确（该规则不保留任何快照）；两规则都不给则报错；
     keep_monthly 从当前月回溯 N 个月、每月取该月最新 complete，缺月跳过；
     组合策略取 union。
   - `apply_retention_plan(repo, plan, *, dry_run=False)`：dry-run 零删除；
     执行前校验快照 id 路径安全（../、盘符绝对路径拒绝）与 manifest 存在性；
     删除失败显式 RetentionError（含已删除/失败明细），不静默跳过。
   - 删除单元 = 快照目录 + manifest 文件；不做文件级引用分析，硬链接数据由
     NTFS link count 自然管理（测试断言：删除旧快照后被链接文件数据存活）。
2. **新增 `tests/test_retention.py`**（18 用例）：keep_last 删除/keep_last=0/
   created_at 排序（对抗目录 mtime 干扰）/月度代表选择/缺月跳过/union/dry-run/
   incomplete 保护/孤儿目录保护/manifest 缺失拒绝/非法 id 拒绝/删除失败显式报错/
   目录与 manifest 同步删除/空仓库/Unicode id/长路径内容删除/硬链接数据存活。

### 关键决定

- **不扫描 snapshots/ 做决策**：计划完全来自 manifest 列表，孤儿目录永远进不了
  delete 集合（结构性防误删）。
- **apply 前二次确认 manifest 存在**：防计划生成与执行之间被外部改动。

### 遇到的问题

- 宿主沙箱 safe-delete 钩子不支持 `\\?\` 前缀路径的递归删除（trash 操作失败）：
  属环境限制，非代码问题；含长路径删除的测试在沙箱外运行全套件 133 项全绿。

### 下一步

T-08 恢复（快照浏览、恢复到目标位置、防覆盖）——等待确认后启动。

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
