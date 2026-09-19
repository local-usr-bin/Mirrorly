# Mirrorly GUI 设计方向入口

> 2026-09-19 · GUI Phase 0。当前长期文档见 [docs/gui](gui/README.md)。GUI 尚未实现。

已批准的方向是 **Modern Windows + Fresh Spring Garden**：WinUI 3 的 Windows/Fluent 骨架、清新浅草绿、白/极淡绿白、少量独立粉花/嫩叶装饰。品牌色与 success/warning/error 语义分离，状态始终有 icon + text。

一级导航为 Home / Backups / Restore / Activity / Settings；Snapshots 属于具体 Backup。Home 优先状态、当前备份摘要与 Back up now，随后少量活动，不做统计 dashboard。普通用户的任务、下一步、安全解释优先于技术信息。

GUI 使用独立 Python worker 与版本化本地 IPC，未来与 CLI 共用 application service；不以 CLI 私有编排/人类输出作为长期 GUI API。本文旧版“CLI 可视化外壳、以 --json 为 GUI 最终接口”的设想由 [GUI-ADR-001](gui/ARCHITECTURE.md) 取代；CLI 参数/退出码/JSON 兼容性仍必须保持。

资源结构、Visual Baseline v0、DPI、键盘、screen reader、High Contrast、tray/notification 约束见 [DESIGN_RESOURCES](gui/DESIGN_RESOURCES.md)；运行结果/队列/取消边界见 [OPERATIONS](gui/OPERATIONS.md)。旧设计与 release 历史保留在 Git 中，本次不改冻结 tag 或历史验收。
