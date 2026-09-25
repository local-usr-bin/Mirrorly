# GUI 资源、交互骨架与可访问性

> 2026-09-19 · 产品/资源原则 APPROVED；资源命名、组件拆分为 PROPOSED DESIGN。
> Phase 0 当时没有附图。用户随后提供 v0，再提供 **Visual Baseline v1**；v1 取代 v0，现为最高优先级视觉参考。不是可直接复用的页面资产，也不要求像素复刻。
> 2026-09-20 实施状态：[PHASE1B](PHASE1B.md)。下文仍是长期产品原则，不能把未来能力视为本阶段已实现。

## 信息与操作

一级导航：**Home / Backups / Restore / Activity / Settings**。Snapshots 属于 `Backups → 某 Backup → Snapshots`，不做一级入口；Logs 只通过 View technical details 等次级入口。普通用户按“看 Documents 以前的版本”完成任务。

Home 顺序：当前状态 → 当前备份摘要/Back up now → 少量最近活动。摘要显示 source 与真实备份路径、上次结果时间、Open in File Explorer；不用复杂 dashboard 和统计卡片墙。一个 Backup 可用完整摘要卡；2–3 个用紧凑列表；很多时优先 needs attention、最近运行的少量任务及 View all backups。阈值/数量集中配置，ViewModel 投影决定集合，View 负责响应式呈现。

Back up now 明显且由 queue coordinator 处理；已有其他 Backup Running 时可 enqueue，当前 Backup Running/Queued 则定位现有项。Waiting 有明确文本与 Remove from queue。进度区域只显示真实阶段/可用计数，无可靠 denominator 时 indeterminate；Cancel 不可用时不能出现一个会强杀进程的按钮。

创建 Backup 的双栏 Source/Backup location 提供 Back、Up、breadcrumb/path 输入、folder list、inline 验证。尽量复用 Windows 选择习惯，不重新实现完整 Explorer。单 source/单 repo；窗口变窄时改上下布局。浏览 IO 异步，失联盘/权限错误不冻结窗口；最终路径和 identity 交给 Python 验证。

成功使用克制的 ✓ + 文字/时间，提供 Open backup / View details，不弹必须 OK 的庆功框。失败说明事实、已知原因、解决方向和可执行下一步；技术代码/trace 放二级。未知原因明说未知，不能给每种失败同一“重新插盘”诊断。危险确认说明具体后果，不能只有 Are you sure。

## Visual Baseline v1 与资源结构

**Modern Windows + Fresh Spring Garden**：Windows 11/Fluent 骨架，明亮清新的浅草绿 navigation，白/极淡绿白内容，清楚的稍深草绿主按钮，少量粉色小花与嫩绿枝叶。避免偏黄/奶油/橄榄/复古绿、蒲公英、大片花田及白花主调；保持桌面应用感，不做 enterprise dashboard、杀毒软件式巨型绿勾或恐吓红字。

当前 Light 主题为贴近已批准的 Visual Baseline v1 渲染，navigation surface（含 Compact/Expanded pane）使用 `#EDF7EC`，主 AccentButton 的静止背景使用 `#429D48`。这两项是有意选择的视觉值；不改变 High Contrast 系统色、按钮白色前景或独立的成功/警告/错误语义色。

| 集中资源组 | 建议 semantic keys / 规则 |
| --- | --- |
| 品牌与 surfaces | `MirrorlyAccentBrush`、`MirrorlyAccentHoverBrush`、`MirrorlySurfaceBrush`、`MirrorlySurfaceElevatedBrush`、`MirrorlyNavigationSurfaceBrush` |
| 状态 | `MirrorlyStatusSuccessBrush`、`MirrorlyStatusWarningBrush`、`MirrorlyStatusErrorBrush`；foreground/background 成对验证对比 |
| 文字 | `MirrorlyTextPrimaryBrush`、`MirrorlyTextSecondaryBrush`；system font 与系统文字样式为起点；标题/正文/辅助文本集中定义 |
| 空间与圆角 | `PageMargin`、`ContentSpacing`、`CardSpacing`、`CornerRadiusSmall/Medium/Large`；布局用 auto/star/重排，不散布固定页宽/高 |
| 装饰 | `MirrorlyFlowerPinkBrush`、`MirrorlyLeafGreenBrush`；独立 assets 和可见性策略 |

以 WinUI ResourceDictionary / ThemeDictionaries 和标准控件资源组织，在使用层引用语义 key。不要给每个 page 复制相同 hex/spacing，也不强求用一个 token 表达含义不同的尺寸。特殊图标尺寸/布局 breakpoint 有合理理由可固定，但应集中、有可验证的适用范围。本轮不冻结具体 hex/字体大小/圆角像素值。

建议复用：BackupSummary、OperationStatus、QueueItem、IssueSummary、PathDisplay、EmptyState、TechnicalDetails、DecorationPresenter。只有出现真实重复才抽组件；页面顺序、Home 密度、按钮位置的调整应局限于资源/组件/View/ViewModel。安全编排、IPC DTO 不引用 visual tokens。

品牌草绿不等于成功语义。成功绿色 + ✓ + 明确文字；Warning 琥珀 + warning icon + 文字；Failure 红色 + error icon + 文字。粉色仅品牌装饰，不承担状态。状态不只靠颜色，不能通过更换花朵表达成功/失败。

## 独立装饰与主题

花草是独立视觉层，可独立换图、移动、缩放和隐藏；不是把背景/卡片/内容烙成整页图片。Phase 1B fidelity pass 使用 `Assets/Decorations/spring-sprig-sidebar.svg` 与 `spring-sprig-header.svg` 两个原创静态矢量资产，由 `Components/SpringSprig` / 标准 `SvgImageSource` 呈现。资源定义占位尺寸及 render transform，资产本身不能撑开页面。不占点击区、不遮挡文本/焦点，以 Raw accessibility view 排除出常规 Control/Content 导航；不用承载功能信息。Working/Failure 时隐藏，High Contrast 直接隐藏。完整 screen reader 行为仍须实测。

当前 Home 下方主植物在 Expanded 导航中沿用 pane footer；Compact 导航、关闭的 pane 且窗口宽度至少 960 epx、高于 600 epx 时，复用同一 SVG 的较小左侧独立宿主。低于此宽度或高度时隐藏，以免挤占必要内容。两宿主互斥，High Contrast 通过主题资源隐藏装饰；导航的 640/1280 epx 阈值不因植物而改变。
当前开发机 175% DPI 的 package-context Debug 实测：默认约 1120×840 epx 的 Home 显示左下 Compact 主植物；最大化约 1463×843 epx 显示原 Expanded 侧栏主植物；560×480 epx 窄窗口隐藏主植物且 Home 操作可达；Backups 页面不保留装饰空列。100%/125% DPI 和真实 High Contrast 切换仍留待最终发行视觉验收；本次 High Contrast 仅验证了资源/宿主绑定及确定性测试。

v1 植物方向：轻植物学插画、细弯枝、多种嫩绿、不同大小/角度/弯曲的叶片、轻叶脉和粉色花瓣层次。左下两朵花必须分处主枝两侧：靠枝梢的一朵在上侧向外舒展，另一朵在相对下侧；不做两朵都垂在枝条下方的构图。右上更小、更轻，不进入状态卡。

**静态页面禁止游离/飘落花瓣**。未来仅真实任务开始时允许短暂一次性 flourish，不是进度，不循环；详见 [MOTION](MOTION.md)。本阶段只记录规范，不实现动画或假队列。

长期 Theme 为 System / Light / Dark。Light 是首个完整招牌视觉；Dark 以后单独设计，不能反色或变黑绿荧光粉。资源架构现在支持 ThemeDictionaries，但未实现/验证 Dark 前不能把一个空的 Dark 选项宣称为完整支持。High Contrast 使用系统功能色/状态文字与边界，不用品牌颜色强盖。主题与 live system theme 变化需测试；不引入 skin marketplace、任意用户主题或主题插件。

## DPI / responsive / accessibility：实施门槛

WinUI DPI scaling 不保证布局正确。核心动作和必要状态优先，空间不足先重排/折叠辅助信息、隐藏高级信息；主内容有滚动兜底。按钮可换行/移至可达操作区，不被下方装饰/大路径挤出屏幕；不靠缩字体解决。

| 测试维度 | 必须满足 |
| --- | --- |
| DPI 100 / 125 / 150 / 175 / 200% | 操作不裁切/覆盖，图标文字清晰，路径可展开/复制 |
| Windows Text Size（默认与显著增大） | 长标题/错误/按钮文字重排，独立于显示缩放测试 |
| Small / large / maximized | 主要内容与操作可滚动访问；双栏可改纵向；多 Backup 不成卡墙 |
| Mixed DPI monitors | 窗口跨屏、关闭后恢复位置仍在可见工作区；缩放/尺寸变化不丢焦点 |
| Keyboard | Tab/Shift+Tab、Enter/Space、列表方向键；适用选择支持 Ctrl/Shift；焦点顺序随布局合理变化 |
| Focus | 始终可见，无焦点陷阱；关闭 dialog/详情回到触发点，队列自动变化不抢焦点 |
| Screen reader / UI Automation | 为操作、路径、状态、进度、列表选择提供名称/角色/值；状态变化适度通告，heartbeat 不逐次朗读 |
| High Contrast | 系统色可读、焦点可见、状态有 icon/text；花草隐藏 |
| Light / future Dark | 每种已支持主题覆盖错误、禁用、选中、焦点、进度；未实现主题不能记 PASS |

优先标准 WinUI controls，定制组件补 AutomationProperties/必要 automation peer；不用只有 hover 才出现的关键动作。参考官方 [accessibility overview](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessibility-overview)、[text requirements](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/accessible-text-requirements)、[High Contrast](https://learn.microsoft.com/en-us/windows/apps/design/accessibility/high-contrast-themes)。

## Tray 与 notification

Minimize → taskbar；Close × → 隐藏到 notification area，desktop runtime/queue 继续。首次 Close 轻量提示：

> Mirrorly is still running. The window has been closed, but your backup will continue in the notification area. You can exit Mirrorly from the tray icon.

提供 Don't show this again，偏好存在 GUI Settings。Tray：Open Mirrorly / Back up now / 分隔线 / Exit Mirrorly。一个 Backup 可直接 enqueue；多个打开明确选择，不隐式 Back up all。Running-on-Exit 依 [OPERATIONS](OPERATIONS.md) 的 O-01，不由 tray handler 自作主张。

Windows notifications 展示 Backup complete / Backup needs attention / Backup failed；隐藏状态下完成需要通知支持。toast 只是辅助：关闭通知/系统未展示/用户没看见都不影响 Home 持久 issue。通知点击以 operation/backup identity 激活对应页面，不从 notification 参数执行任意命令/路径；过期 snapshot 显示不可用，不自动触发 retry。通知设置与可见窗口内的克制反馈协同，避免同一事件反复弹出。
