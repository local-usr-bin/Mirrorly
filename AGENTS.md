# Mirrorly Agent Instructions

These instructions apply to the entire repository.

## Project

Mirrorly is a Windows-first personal versioned backup tool.

Keep its core direction stable:

- One-way backup, not bidirectional sync.
- Whole-file snapshots with NTFS hardlink reuse for unchanged files.
- Backup output remains ordinary, browsable files.
- Safety and explainability take priority over feature accumulation.

## Sources of Truth

When information conflicts, use this order:

1. Current repository source.
2. Committed tests.
3. Committed design and project documentation.
4. The current task prompt, then historical summaries.

Start with the relevant committed documents instead of duplicating them here:

- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/DESIGN_DECISIONS.md`
- `docs/CLI_SPEC.md`
- `docs/TECH_RISKS.md`

## Safety Invariants

- Ordinary contents of a complete snapshot are immutable.
- Destructive behavior defaults to fail closed.
- Do not guess through ambiguous metadata or repository identity.
- A filename suffix alone never proves that a file is Mirrorly-owned.
- Full verify success must have an explicit, explainable integrity meaning.
- Do not silently reduce content or hash verification coverage.
- Keep the roles of source data, snapshot data, and manifests distinct and explainable.

## Development Workflow

- At the start of every task, inspect `git rev-parse HEAD` and `git status --short`.
- Do not start unrelated work with a dirty working tree.
- Handle one finding or one scoped task at a time.
- Do not fix, refactor, or clean up unrelated issues opportunistically.
- For high-risk patches: modify and test, report for review, and commit only after approval.
- Passing tests does not imply architecture review approval.
- Do not push unless explicitly requested.
- Do not move, delete, or rewrite historical tags.

## Validation

- Use Python 3.12 or newer from the current project environment.
- Run tests with `python -m pytest`.
- Run lint checks with `ruff check .`.
- Run formatting checks with `ruff format --check .`.
- Do not install new validation dependencies unless the task explicitly requires them.

## Platform

- The primary supported and validation platform is Windows 10/11.
- NTFS-specific behavior, especially hardlink semantics, is part of correctness.
- Reparse points, Volume GUID handling, and long paths may require real Windows testing.
- Explain every environmental skip; a skip is not a substitute for a regression pass.
