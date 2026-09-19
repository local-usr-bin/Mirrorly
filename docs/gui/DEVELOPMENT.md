# GUI 开发环境、实施阶段与验证

> 2026-09-19 · PROPOSED DESIGN / 安装计划；本轮不安装、下载 SDK/package、不创建工程。
> 稳定渠道版本是本日官方文档快照，实施前应确认 servicing 状态并记录实际使用版本，不自动追 Preview/Experimental。

**2026-09-20 更新**：下方工具安装计划及“当前环境”段落保留 Phase 0 当日检查
快照，不再表示这台机器缺少 SDK。已安装工具与实际技术原型验证见
[PHASE1A](PHASE1A.md)。开发包验证与最终分发必须分开验收，见 O-09。

## 当前环境与 Phase 1 前置工具

**CURRENT FACT**：`dotnet` 可见于 `C:\Program Files\dotnet\dotnet.exe`，`dotnet --list-sdks` 无输出；host/runtime 存在不表示具备 SDK。当前 Python project 环境为 Python 3.12；本轮不改变 Conda、PATH、Developer Mode 或 Windows 安装组件。

推荐先以 Windows 11 x64 开发/验证，正式最低系统和发布架构仍是 O-05。以下组合适用于已批准的 WinUI/C# 路线，是 Phase 1 工具计划，不是已完成 build 的宣称。

| 项目 | 计划版本/组件 | 用途与来源 |
| --- | --- | --- |
| .NET SDK | **10.0.401，.NET 10 LTS，x64** | 含 C# 14 / .NET Desktop Runtime 10.0.12；[官方下载页](https://dotnet.microsoft.com/en-us/download/dotnet/10.0) |
| Visual Studio | **Visual Studio 2026 Stable 18.10.1**，可用 Community | C#/XAML editor、debugger、MSBuild；[Stable release history](https://learn.microsoft.com/en-us/visualstudio/releases/2026/release-history) |
| Workload | **WinUI application development**，ID `Microsoft.VisualStudio.Workload.Universal` | 含 managed WinUI 开发基础；选择 C# 工具，不默认加 C++ WinUI 工具；[官方 quickstart](https://learn.microsoft.com/en-us/windows/apps/get-started/start-here) / [workload IDs](https://learn.microsoft.com/en-us/visualstudio/install/workload-component-id-vs-professional?view=visualstudio) |
| Windows SDK | **10.0.26100.9169**（26100 稳定系列 servicing） | Windows headers/libs、打包/签名工具；[Windows SDK downloads](https://learn.microsoft.com/en-us/windows/apps/windows-sdk/downloads) |
| Windows App SDK | **Stable 2.5.1**，`Microsoft.WindowsAppSDK` NuGet | WinUI 3 仍叫 WinUI 3，不能把 App SDK 2.x 当 UI 主版本；[release channels](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/release-channels) / [downloads](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/downloads) |
| C#/XAML 工具 | 上述 workload 的 C# WinUI templates、XAML compiler/tooling、MSBuild、NuGet support | 不要求额外 Python GUI toolkit；.NET desktop 所需 managed components 随 workload 核对，不强制另装无关 workload |
| Packaged 开发选项 | **MSIX Packaging Tools**（`Microsoft.VisualStudio.ComponentGroup.MSIX.Packaging`）、SDK 的 MakeAppx/SignTool | 若接受 packaged 开发模板则需要；Developer Mode 和本地调试签名/证书信任的系统改动另行授权 |
| Build Tools（可选） | **Build Tools for Visual Studio 2026 Stable 18.10.1** + 对应 MSBuild/WinUI/XAML/Windows SDK/MSIX components | 后续 headless CI 的替代构建环境；开发机有完整 VS 时不重复安装；不能只凭 dotnet host 声称具备全部打包能力 |
| Python | 现有 **3.12** 环境（本机 3.12.14） | 后续 shared service/worker 验证复用；生产 Python 分发另审，不要求最终用户安装 Conda |

.NET 10 是 LTS，官方支持到 2028-11-14；不为长周期 GUI 新项目选择即将结束支持的旧 .NET 作为默认。Windows App SDK 独立 servicing，不能继承 .NET 的 LTS 期限，需随 Stable 安全修订维护。[.NET 支持政策](https://dotnet.microsoft.com/en-us/platform/support/policy/dotnet-core)、[App SDK 支持政策](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/release-channels)。

建议 target API surface 为 `net10.0-windows10.0.26100.0`；**26100.9169 是安装包 servicing 版本，26100.0 是目标 API 标识，均不自动等于最低运行 OS**。`TargetPlatformMinVersion` 要综合 WinUI、.NET、Python 与 Windows 支持范围单独决定。旧 CLI 的 Windows 10/11 声明不能直接变成新 GUI 的全部 build/edition 支持承诺。

推荐 Phase 1 用 C# WinUI Blank App (Packaged) 做开发验证，正式发行 packaged/unpackaged 仍 O-06。若选择 CLI 模板路线，官方模板包为 `Microsoft.WindowsAppSDK.WinUI.CSharp.Templates`，不是已有 SDK；VS 路线无需为凑齐工具再单独安装它。不要执行可顺带启用系统设置的一键安装脚本作为本轮工作。

Phase 1 获准后应记录实际 SDK/MSBuild/package 版本，在合适的工程配置中固定版本，并验证 template defaults；模板成功不能证明 Python child 启动、tray、通知、最小系统或安装包都兼容。生产分发还需决定 .NET/App SDK 的 framework-dependent/self-contained 方式、Python runtime/bundle、签名、卸载/升级保留本地数据策略。Phase 1 不需要购置正式签名证书或发布安装包。

## Python worker 分发的独立验收门槛

**CURRENT FACT / OPEN DECISION O-09**：官方 [single-project MSIX 限制](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/single-project-msix#limitations)
为一个包只支持一个 executable。Phase 1A 调用开发机包外 Python 与 worktree
脚本，故 packaged output PASS 仅证明这条开发技术链，不证明最终分发已解决。

正式分发前需在 [ARCHITECTURE](ARCHITECTURE.md#python-worker-分发与打包open-decision-o-09)
两候选间另行决定：A. MSIX/package identity + Windows Application Packaging
Project 或其他官方支持方案；B. unpackaged/self-contained WinUI + 传统 installer。
本轮不迁移项目、不创建 packaging project、不新增 installer 工具。后续必须验证
无开发 SDK/Conda/worktree 的机器、Python runtime/依赖及脚本落点、子进程启动、
通知身份、签名、安装升级与卸载。保持 Python core 不变。

## 建议 Phase 1 范围

**Shell / presentation foundation，使用 fake application client**：

1. 获批工具环境与开发形态；建立最小 WinUI shell、5 项导航、集中 theme/layout/typography resources。
2. 做 Home/Backup 摘要、状态/问题、QueueItem 等最小组件；fixture 驱动 0/1/3/很多 Backup 的成功、issues、post-commit failure、offline、queued/working 状态。
3. ViewModel 与 queue coordinator 的内存模型测试；模拟 FIFO、去重、Remove/dispatch 竞争、Close 保持队列。fixture 明确为演示，不与真实 Backup 按钮混用。
4. 验证键盘、screen reader、High Contrast、小窗口、Text Size、DPI 的布局基础；花草可暂用独立占位资产，不先做完整美术稿。
5. 验证 tray/window lifecycle、notification activation 的平台可行性；只针对 fake operation。尚未批准的 Running-on-Exit 不作为已完成产品流程，评审两种候选。

验收点：shell 可构建运行、页面不直连 IO/core、状态案例可复现、核心动作可达、资源可集中调整。**不提取 cli.py、不接真实 backup/restore、不实施 cooperative cancel、不写生产 worker**。这样 UI 基础不以安全关键重构为隐含前提；后者需下一独立任务批准。

## 后续阶段（PROPOSED DESIGN）

| 阶段 | 范围与退出条件 |
| --- | --- |
| Phase 0（本轮） | 文档、契约草案、OPEN DECISION 登记；等待 review |
| Phase 1 | 上述 shell/fake state/resources/窗口平台验证；工具安装独立授权 |
| Phase 2 | 单独提取 shared application service；CLI 等价回归；冻结结构化结果与确认边界；不顺带改 core semantics |
| Phase 3 | 最小 worker/stdio handshake、inspect/list/只读浏览、结果/故障 contract；解决 O-03、配置归属与身份校验 |
| Phase 4 | 创建单 Backup、真实 backup FIFO、可信粗粒度阶段、Activity/attention；O-01 明确后才接真实 Exit 流程 |
| Phase 5 | Restore plan/confirm/apply、Skip/Replace、verify coverage 表达；先解决 O-04/O-08 |
| 独立安全任务 | 评审 O-02 后才加 observer/checkpoints/cancel；不以 Phase 1 按钮需求强迫 core 承诺不存在的数据 |
| Phase 6 | settings/activity 持久化收口、实际 tray/notification 故障恢复、分发/升级验证、主题与完整 accessibility 矩阵 |

Phase 4 的粗阶段只由 application 实际边界发出；core 内的 current-file/bytes 必须等待独立扩展任务。支持真实 Running Cancel 是用户体验目标，若 GUI 发布时仍缺失必须显式批准范围调整，不能把 gap 静默遗留为“已经完成首版”。

## Testing strategy

| 层/风险 | 验证要求 |
| --- | --- |
| ViewModel / state projection | 用 typed fixtures 验证 complete+skips、post-commit failure、unknown、offline stale、成功但旧 issue 未消除；无 WinUI 窗口即可验证逻辑 |
| Queue | FIFO/去重/原子 Remove-vs-dispatch、配置变更、失败后下一项、unknown 禁止 dispatch、Close/Minimize、Exit 清 pending；无 sleep 依赖，用受控 fake executor/clock |
| IPC | 拆/合帧、Unicode/长路径、超限/坏 JSON、版本不匹配、能力 false、重复 ID、慢消费者、大 stderr、最终结果丢失、worker EOF；不自动重发写请求 |
| Shared service / CLI compatibility | 参数、exit、JSON、确认、task→repo lock、publish/report/retention/recovery 的原测试保持；新 DTO 不改变 CLI 输出；精确覆盖 pre/post-commit 故障 |
| Real Windows integration | 临时独立 NTFS repo，身份/盘符变化、锁占用、断盘/权限、hardlink、restore 额外文件保留/冲突、计划过期、partial effects；不触碰用户备份 |
| Cancellation（实现后） | 每个批准 checkpoint 的前/后、publish 竞争、finalizing 太迟、重复请求、restore 已写入保留、cleanup/report 失败；安全边界不能只用 mock 验证 |
| UI Automation / manual | create→queue→history→restore 的键盘路径，UI responsive、大文件无假进展、error actions、tray 重开、通知激活、focus 与 Narrator；选工具时避免默认添加未维护依赖 |
| DPI / theme | [DESIGN_RESOURCES](DESIGN_RESOURCES.md) 全矩阵，保存关键状态截图/环境参数；HC/文字放大与实际操作验证，不能只检查截图像素 |
| Packaging | clean Windows VM 无 dev SDK/Conda 环境运行；Python child/路径/读写位置、通知/tray、升级/卸载、不同架构/目标 OS；正式签名/发布另批准 |

现在是文档变更，只检查链接、JSON 示例、diff 和 scope，不重跑 CLI audit，也不把既有 570 passed / 4 environmental skips、Windows E2E 13 passed 写成 GUI 验收。以后涉及 Python 变更按 AGENTS 用 `python -m pytest`、`ruff check .`、`ruff format --check .`，报告每个环境 skip，不能算 PASS。

## 开工门槛

Phase 1 前确认 O-05 开发目标、O-06 开发打包形态及工具安装许可；详细协议、取消、运行退出、真实配置和并发决策可按阶段解决。没有必要为了画 shell 先改 core，也不能以 shell 获准推导生产 worker/安全重构获准。OPEN DECISION 的唯一登记入口为 [README](README.md)。
