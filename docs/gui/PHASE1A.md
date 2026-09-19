# Phase 1A — Technical Vertical Slice

> 2026-09-19 实施，2026-09-20 final closure · **PASS — Technical Vertical Slice validated**。
> 基线：`9cc0cbf82838f7a718038a9f75bfb222d7f3ee72`，`codex/mirrorly-gui`。
> 当前技术原型不代表正式 GUI 已交付；生产 worker/application layer 仍未实现。
> 用户已补齐托盘 Open/Close/Exit 与通知可见性的人工验收；O-09 最终分发方案继续 OPEN。

PASS 的范围是 WinUI packaged app（.NET 10 / Windows App SDK Stable 2.5.1）
build/run、独立 Python fake child、versioned stdio IPC、可观察的 crash/disconnect、
Close-to-tray、tray reopen/Exit、可见 Windows app notification、150/175/200% DPI、
High Contrast、keyboard/focus basics 与 packaged output。existing Mirrorly Python
core 未修改。**不表示真实 backup GUI、生产 worker/cancellation、core 集成、最终
分发或生产通知点击激活已经完成。**

## 实际范围

`desktop/` 有一个 C#/XAML WinUI packaged 项目、一个无第三方测试框架的 C#
测试程序、一个仅使用标准库的 Python fake worker。Home 显示真实 worker
状态、协议版本及测试按钮，其余四项导航是明确占位页。

View 只分发意图与切换导航；`HomeViewModel` 表达状态；`FakeWorkerClient`
处理子进程/IPC；`TrayService` 封装少量 Win32 调用；`NotificationService`
使用 Windows App SDK 通知 API。没有 DI/event bus framework。语义颜色、
间距、圆角与可复用样式集中在 `Themes/PrototypeResources.xaml`。

Light 使用浅草绿导航、近白内容与标准 Fluent 控件。High Contrast 资源引用
系统颜色；Dark 仅保留保守的资源 fallback，不是已批准的正式夜间设计。
主内容可滚动，NavigationView 自动收窄；没有固定文本高度。独立的装饰资产
架构留待后续，本轮没有花草绘制。用户提供的 Visual Baseline v0 是气质参考。

未调用/修改现有 `src/mirrorly`、CLI、core、package metadata、版本或 release。
没有真实队列、取消、进度回调、Activity 存储或 repository 操作。

## 工具与目标

| 项目 | 实际使用 |
| --- | --- |
| OS | Windows 11 25H2 x64，26200.9457 |
| Visual Studio | Community 2026 Stable 18.10.1，VS MSBuild 18.10.1.42706 |
| .NET | SDK 10.0.401；app runtime 10.0.12；`desktop/global.json` 禁止 prerelease |
| TFM | `net10.0-windows10.0.19041.0`；没有接受模板 net8.0 |
| TargetPlatformMinVersion | 模板的 `10.0.17763.0`；仅编译/打包声明，未验证该 OS，非正式支持承诺 |
| Windows SDK 安装 | 10.0.26100.8249 servicing，Kits 目录 10.0.26100.0 |
| App SDK NuGet | **Microsoft.WindowsAppSDK 2.5.1 Stable**；显式覆盖模板默认 |
| SDK BuildTools NuGet | 10.0.26100.4654，模板/App SDK Base 依赖；可与已安装 SDK servicing 并存 |
| Python | 指定 Conda interpreter，3.12.14 x64；不改环境/editable install |
| Package | single-project MSIX，x64，prototype identity/version `Mirrorly.TechnicalPrototype` / `0.0.1.0` |

TFM Windows 数字定义编译可用的 API surface；TargetPlatformMinVersion 定义包声明
的最低 OS。二者都不是开发机的 25H2 build，也不在本轮冻结产品最低 OS。
.NET self-contained；Windows App Runtime framework-dependent。NuGet lock file
记录 App SDK 拆分子包的实际版本，子包版本不必与 umbrella 2.5.1 相同。

官方参考：[2.5.1 package](https://www.nuget.org/packages/Microsoft.WindowsAppSDK/2.5.1)、
[Microsoft app notification quickstart](https://learn.microsoft.com/en-us/windows/apps/develop/notifications/app-notifications/app-notifications-quickstart)。

## Prototype IPC v1

父子进程 stdio，UTF-8（无 BOM）NDJSON，一帧一个 JSON object + LF；允许 JSON
尾部 CR whitespace。单帧最多 65,536 bytes（含 LF）。无网络 listener。
stdout 只承载协议，stderr 异步按有界块 drain 到 Debug 输出，不做持久化。

```json
{"protocol_version":1,"message_type":"request","request_id":"unique-id","payload":{"command":"ping"}}
{"protocol_version":1,"message_type":"response","request_id":"unique-id","payload":{"reply":"pong"}}
```

所有消息有四个字段。类型：request/response/event/error。response/event 回显
request_id；坏到无法取得 ID 的请求得到 request_id=null 的 error。payload 是
object。错误包含 code/message；例如 invalid_request、invalid_frame、unknown_command。
这不是生产错误分类契约；原型无 operation/repository/snapshot identity。

| command | 行为 |
| --- | --- |
| ping | pong response |
| version | phase1a-fake 与 Python version |
| test_event | 同 ID 的 event（含中文）→ response |
| shutdown | goodbye response → exit 0 |
| test_crash | stderr 标注受控崩溃 → exit 23，没有伪造成功 response |

普通 malformed request 返回 error 后可继续；过大/截断 frame 返回 invalid_frame
并退出，避免猜测边界。stdin EOF 令 fake worker 正常退出。C# 支持拆帧、合帧、
Unicode、并发请求的 ID correlation、5 秒 response timeout；未匹配 response、
坏协议、stdout EOF 都不能继续显示健康连接。不自动重发或重启。

Starting → 成功 ping 后 Connected。Connected 只说明最近 ping 成功，不是持续
健康/IO 进度保证。IPC 故障为 Disconnected；意外 process exit 为 Crashed，
错误后的旧请求不能再把 Crashed 覆盖为 Connected。

Close 隐藏时不 shutdown；真正 Exit 先请求 fake shutdown，再关闭 stdin 等待
退出。仅此无副作用 fake peer 有超时后 kill 的清理兜底，**不能复用为生产 worker
取消/Running-on-Exit 实现**。Phase 0 的 O-01/O-02/O-03 生产语义仍待决。

## 验证与手工清单

PASS 必须有实际观察，API 返回成功不代替系统 UI 验证。

| 检查 | 最终结果 / 证据来源 |
| --- | --- |
| Restore / Debug build | PASS；VS MSBuild restore 与 build 成功 |
| Packaged launch | PASS；Developer Mode 当前用户注册后，通过 AppsFolder package identity 启动，出现 WinUI Home |
| Python 启动 | PASS；进程命令行确认指定 interpreter + 当前 worktree absolute script，parent 为 desktop PID |
| Ping / test event | PASS；Home 显示 Connected；按钮触发的中文 test event 实际回到 Home |
| Worker state / crash | PASS；UI 实测自动显示 Crashed / exit 23，请求按钮禁用，窗口保持可操作；服务集成测试也通过 |
| Minimize | PASS；点击 `_` 后 desktop/worker PID 保持，恢复窗口后连接仍在 |
| Close × | PASS；此前工具观察到窗口隐藏而原 desktop/worker PID 均存活；最终用户人工确认窗口消失、tray icon 保留，应用不退出 |
| Tray Open Mirrorly | PASS；最终用户人工确认可从 notification area/tray 恢复原窗口。未单独人工核对 Connected 标签，见下方证据边界 |
| Tray Exit Mirrorly | PASS；最终用户人工确认从菜单退出正常、应用退出、tray icon 消失。worker shutdown/lifecycle 的独立技术证据保留；不伪造该次人工点击的 worker 退出码 |
| Notification visible | PASS；此前 Show 返回有效 ID（复验为 2318）、Setting=Enabled；最终用户实际观察到 Mirrorly 测试通知且行为符合预期。未单独区分横幅/通知中心，不补写未经确认的显示位置 |
| Keyboard | PASS（基础）；实测 Tab / Shift+Tab，清晰的焦点边框；完整遍历与 Enter/Space 清单待补 |
| Light / 175% | PASS；初始验证及此前 closure 复验恢复后外观、内容、核心控件正常；结束时原型报告实际 scale=175% |
| 150% | PASS；此前 closure 复验按授权临时切换显示器 2 的系统 scale，原型实际 scale=150%；约 535×375 effective pixels，导航可展开、核心按钮可滚动访问、中文 test event 返回，关键文本无不可恢复裁切 |
| 200% | PASS；系统 scale 与原型实际 scale 均为 200%；约 534×374 effective pixels，导航可展开、核心按钮可达、Ping 可操作、Tab 焦点清楚，关键文本无不可恢复裁切 |
| High Contrast | PASS（基础）；真实 Windows“夜空”contrast theme，175% 下标题、状态、按钮、导航选中项及 Tab 焦点可辨识；原型采用系统对比度颜色，无需代码修复；不代表完整 Narrator/所有 HC 主题认证 |
| Small window / scroll | PASS（局部）；175% 时 535×375 effective pixels，小窗口通过滚动可访问全部四个核心测试按钮 |
| UI Automation / Narrator | 标准 WinUI controls、有文本可访问名称；当前自动化工具未返回此窗口的 accessibility tree；不宣称 Narrator 验收 |
| MSIX output | PASS；生成 unsigned Debug 与 Release x64 MSIX；未测试签名安装/clean-machine deployment |
| C# tests | Previous automated results：8 passed，0 failed；包括真实子进程、correlation、event、crash、shutdown、framing 与 ViewModel；final closure 未重跑 |
| Python tests | Previous automated results：11 passed，0 failed；仅 phase1a_worker；final closure 未重跑 |
| Python lint/format | Previous automated results：`ruff check .` / `ruff format --check .` PASS；final closure 未重跑 |

复验步骤：packaged launch → Connected → Ping → test event → test notification →
Tab/Shift+Tab/Enter/Space → Minimize/taskbar restore → Close × → tray Open Mirrorly
（worker 应仍 Connected）→ crash test（应自动 Crashed）→ tray Exit（进程结束）。
重开一次正常 worker 后 Exit，检查子进程 exit 0。150%/200%、小窗口、High
Contrast 各重做核心按钮可达性；系统设置改动需先获得授权并恢复原值。

## 打包结果与限制

**2026-09-20 closure 纠偏 / CURRENT FACT**：
[Microsoft single-project MSIX 文档](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/single-project-msix#limitations)
规定生成的包只支持一个 executable；多个 executable 合入同一个 MSIX 需采用
Windows Application Packaging Project。不能把此限制误写成 MSIX 本身不支持
多个进程，也不能以当前原型能启动包外 Python 来证明包内 Python 分发已经可行。

本轮实际验证的是 **packaged WinUI desktop app + 开发机外部 Python worker**：

- interpreter：`C:\Users\sakur\anaconda3\envs\mirrorly\python.exe`
- script：`C:\Users\sakur\.codex\worktrees\1047\Mirrorly\desktop\phase1a_worker\worker.py`

两者均不在当前 MSIX 内。**Phase 1A packaged output PASS ≠ final distribution solved**。
最终 worker distribution / packaging strategy 正式登记为 **OPEN DECISION O-09**：
A. 保留 MSIX/package identity，采用 Windows Application Packaging Project 或其他
官方支持的多 executable 方案；B. unpackaged/self-contained WinUI + 传统 installer /
deployment。候选与验收条件见 [ARCHITECTURE](ARCHITECTURE.md#python-worker-分发与打包open-decision-o-09)，
统一登记见 [README](README.md)。本轮不拍板 A/B，不修改 single-project 原型结构。

输出：`desktop/AppPackages/Mirrorly.Desktop_0.0.1.0_x64_Debug_Test/Mirrorly.Desktop_0.0.1.0_x64_Debug.msix`。
Release：`desktop/AppPackages/Mirrorly.Desktop_0.0.1.0_x64_Test/Mirrorly.Desktop_0.0.1.0_x64.msix`。
所有 build/package/certificate 文件不入 Git。没有正式签名、Store 上传或 release。
此次运行来自当前用户 loose package debug registration，不是安装该 unsigned MSIX。

直接运行裸 EXE 曾在 DeploymentManager 初始化失败；注册后的 package activation
成功。README 提供正确入口。App SDK 框架依赖已有 2.5.1 x64 runtime。测试包包含
.NET runtime，但刻意没有捆绑 Python/worker：worker path 编译时指向本 checkout，
因此目前只能作为这台机器的开发验证包。后续分发策略仍开放，unpackaged 路线未删除。

首次打包提示缺少 `mspdbcmf.exe`、未生成 symbols package。原型不需要 C++ symbol
conversion，现已显式禁用 symbols package，但 SDK prerequisite target 仍输出该
warning；它不阻止 MSIX 生成。未为消除此提示安装额外工具。
Windows 通知点击激活/多实例重定向 DEFERRED，避免为可选项扩大竖切。

Home 另有 `Exit prototype` 测试便利按钮，与 tray Exit 共用同一 fake-worker
清理路径；不代表正式产品首页动作，也不能替代托盘菜单的实际验收。
fake worker 正常 shutdown/exit 0 已由 C# 集成测试验证；从真实 tray 菜单退出
应用的交互验证由用户最终人工验收补齐。隐藏窗口后自动化工具报告该窗口不可
操作，因此 tray Open/Exit 的证据来自用户，没有将工具恢复或重新启动冒充 tray 操作。

## 2026-09-20 closure 记录与最终人工验收

此前 closure 复验与本次 final closure 都只改 GUI 文档，未修改 C#/XAML、
Python worker/tests、项目结构或依赖。以下系统设置操作发生于此前 closure 复验，
本次 final closure 未重新切换设置：Settings 首次自动化访问超时，重试后可用；
通过实际 Windows Settings 调整
显示器 2（保持 2560×1600 分辨率及“仅在 2 上显示”不变）的缩放：
175% → 150% → 200% → **175%**。原型的 XamlRoot scale 读数同步变化。
然后临时设置“夜空”contrast theme，验证完成后选回并应用原值 **“无”**，
Settings 与原型 Light 外观恢复。没有使用模拟缩放替代实际 DPI。

为不干扰原隐藏实例，DPI/主题检查单独打开了一个可见测试实例。通过 Home 的
`Exit prototype` 正常结束它，持有 OS process handles 的只读观察获得 desktop
与 fake worker **exit code 均为 0**。未强杀进程。它与 tray Exit 共用退出方法，
这项证据单独不足以证明托盘菜单交互；最终用户人工验收另行补齐。两项验证
都不代表真实 Backup 的退出或取消语义。

**用户 final closure 报告的人工结果**：

1. **Tray Open Mirrorly — PASS**：从 Windows notification area/tray 打开并正常恢复原窗口。
2. **Close behavior — PASS**：点击 × 后窗口消失、tray icon 保留，应用不直接退出。
3. **Tray Exit Mirrorly — PASS**：菜单退出正常，应用退出、tray icon 消失，行为符合预期。
4. **Windows test notification — PASS**：用户实际观察到 Mirrorly 测试通知，行为符合预期。

用户没有单独核对 tray Open 后 `Worker = Connected` 标签；不声称用户亲眼确认
过该标签，也不将它列为 Phase 1A blocker。此前已有 worker 启动、ping/pong、
version、test_event、shutdown、crash → UI 察觉、Close 后 desktop/worker 存活的
独立证据；本次人工结果补齐的是 tray Open/Exit 与 notification visible。
同样不将先前 Exit prototype 的 exit 0 记录移作人工 tray Exit 的退出码。

**Previous automated results**：8 个 C# / 11 个 Python tests 及 lint/format 通过；
此前 restore/build、DPI/主题实际 UI 检查、Exit prototype 进程退出码记录仍有效。
**This final closure verification**：接收并落档人工结果、检查 GUI 文档链接、
`git diff --check`、确认无本轮意外生产代码变更且 existing Python core 未修改。
本轮无代码修复，不重跑上述自动化测试、restore/build 或完整 CLI audit。

**最终状态：PASS — Technical Vertical Slice validated，可冻结/提交。**
O-09 继续 OPEN；packaged MSIX PASS 不等于最终产品分发已解决，不在本轮选择
A/B 或迁移项目。通知点击激活仍 DEFERRED。

## 与 Phase 0 的关系及下一步

沿用 WinUI → presentation → client → stdio Python child 的边界。原型采用本轮
批准的四字段 envelope；它是隔离的 phase1a 协议，不承诺兼容 Phase 0 生产契约
草案，也没有把 fake shutdown 当成 cooperative cancel。

Phase 1A 已完成限定范围验收。建议下一阶段先评审 shell/视觉/状态 fixture 的
具体范围；shared application extraction、生产 worker、真实 backup/restore、
queue/cancel 均须独立批准。此次用户批准以 `Add Mirrorly GUI Phase 1A technical prototype`
提交当前完整 Phase 1A diff 到 `codex/mirrorly-gui`；不 push/tag/release，不修改 main，
不自动开始下一阶段。
