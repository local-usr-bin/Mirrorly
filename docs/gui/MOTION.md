# Mirrorly motion — backup-start flourish

> 2026-09-20 · APPROVED product specification; one-shot production integration implemented after Phase 4C.
> Visual Baseline v1 supersedes v0.

## Static presentation

Normal stationary Home contains only branches, leaves and flowers attached to
branches. **No floating, falling or scattered standalone petals.** Decorative
assets must not imply an operation is happening.

## Trigger and meaning

A short, one-shot petal flourish means only: **A backup task has just started.**
Trigger when the application layer confirms a real task changes Queued → Running,
or a non-queued task actually starts execution. Clicking Back up now, enqueueing,
preparing a request, selecting a fixture, opening Home or reconnecting the UI is
not this trigger. Do not infer execution from a process existing or heartbeat.

For Documents → Photos → Projects in a serial queue:

1. Documents actually starts: one flourish.
2. Photos and Projects are waiting: no animation.
3. Photos actually starts: one flourish.
4. Projects actually starts: one flourish.

## Delivery safeguards

The implementation deduplicates the start transition by operation identity
within the current session; repeated delivery/rebinding must not replay it. Do not
persist or invent an operation identity merely to implement this visual effect.
If a real transition arrives while the window is hidden, omit the flourish rather
than replaying stale motion when the user reopens it. This is presentation-only;
it must not gate, delay or alter execution.

`ProductionWorkerClient` exposes the existing `backup.run` **accepted** response to
`DesktopSession`; `BackupExecutionCoordinator.BackupAdmitted` forwards that
confirmed admission to `MainWindow`. The earlier local `Running` presentation is
not a motion trigger. A rejected/unadmitted request has no operation ID and cannot
trigger the flourish. The app-scoped `PetalMotionPolicy` consumes each operation ID
once, even when Home is hidden, the plant is not visible, animations are disabled,
or High Contrast is active. No motion is saved for later navigation/tray reopening.
The stationary Running layout still hides the plant. At confirmed admission the
current Compact or Expanded plant host appears for the one short flourish, then
returns to the established Running visibility. Crossing a responsive breakpoint
does not create another trigger.

## Timing and accessibility

- Approximately **250–500 ms** for the principal motion; a short fade may finish
  slightly later. Small, light and unobtrusive, with no layout movement.
- One shot only. No `Forever`, repeat loop, timer-driven petal emitter or spawning
  based on file count/bytes/progress. No ongoing petals while backup runs.
- Motion is never evidence that file IO is advancing and carries no ETA. Current
  Backup UI has no phase, item, byte or percentage progress capability.
- When Windows animations are disabled, a reduced-motion preference applies, or
  High Contrast is active, omit it completely. Text/status must remain equivalent.
- Motion stays in a separate non-hit-testable, non-focusable decorative layer,
  outside accessibility Control/Content navigation and away from controls/text.

## Acceptance checks

Verify one start, waiting-only, three sequential tasks, duplicate start delivery,
view re-entry, hidden window, animations disabled, reduced motion and High Contrast.
Disabling motion must leave status/queue/operation behavior identical. A failure or
cancel event must never start or prolong the flourish. The flourish does not
resolve cancellation or progress contracts.

**Historical Phase 1B boundary:** that phase documented behavior only. The current
integration uses real production admission and leaves business work unchanged.
