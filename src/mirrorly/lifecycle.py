"""Repository-global durable physical-attempt sequencing.

Every fresh or resumed snapshot attempt reserves a new uint64 sequence while
the repository writer lock is held.  Retention and incomplete cleanup never
modify this high-water state, so deleted attempts cannot make an old sequence
available again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import RFC_4122, UUID

from .durable import write_json_durable
from .repo import RepoFormatError, RepoInfo

LIFECYCLE_STATE_FILE = "lifecycle.json"
LIFECYCLE_STATE_FORMAT_VERSION = 1
MAX_LIFECYCLE_SEQUENCE = 2**64 - 1
EXHAUSTED_NEXT_SEQUENCE = 2**64
LIFECYCLE_SEQUENCE_WIDTH = 20

_SEQUENCED_SNAPSHOT_ID_RE = re.compile(
    rf"\d{{4}}-\d{{2}}-\d{{2}}_\d{{6}}-s(?P<sequence>\d{{{LIFECYCLE_SEQUENCE_WIDTH}}})-"
    r"(?P<uuid>[0-9a-f]{32})"
)


class LifecycleStateError(RepoFormatError):
    """Lifecycle state is missing, malformed, or contradicts repository artifacts."""


@dataclass(frozen=True)
class LifecycleState:
    repo_id: str
    next_sequence: int
    format_version: int = LIFECYCLE_STATE_FORMAT_VERSION


def lifecycle_state_path(repo: RepoInfo) -> Path:
    return repo.path / LIFECYCLE_STATE_FILE


def parse_snapshot_sequence(snapshot_id: str) -> int | None:
    match = _SEQUENCED_SNAPSHOT_ID_RE.fullmatch(snapshot_id)
    if match is None:
        return None
    value = int(match.group("sequence"))
    if value > MAX_LIFECYCLE_SEQUENCE:
        raise LifecycleStateError(f"快照 id 中 lifecycle sequence 超出 uint64: {snapshot_id!r}")
    identity = UUID(hex=match.group("uuid"))
    if identity.version != 4 or identity.variant != RFC_4122:
        raise LifecycleStateError(f"快照 id 未携带完整 UUIDv4 identity: {snapshot_id!r}")
    return value


def _validate_uint64_next(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LifecycleStateError("lifecycle state next_sequence 必须是整数")
    if not 0 <= value <= EXHAUSTED_NEXT_SEQUENCE:
        raise LifecycleStateError(f"lifecycle state next_sequence 超出范围: {value!r}")
    return value


def _state_to_dict(state: LifecycleState) -> dict:
    return {
        "format_version": state.format_version,
        "repo_id": state.repo_id,
        "next_sequence": state.next_sequence,
    }


def initialize_lifecycle_state(repo: RepoInfo) -> LifecycleState:
    """Create the initial state for a new repo or a v1 migration."""

    path = lifecycle_state_path(repo)
    if path.exists():
        raise LifecycleStateError(f"lifecycle state 已存在，拒绝覆盖: {path}")
    state = LifecycleState(repo_id=repo.repo_id, next_sequence=0)
    try:
        write_json_durable(path, _state_to_dict(state))
    except OSError as exc:
        raise LifecycleStateError(f"lifecycle state 初始化失败: {path}（{exc}）") from exc
    return state


def _load_state_file(repo: RepoInfo) -> LifecycleState:
    path = lifecycle_state_path(repo)
    if not path.is_file():
        raise LifecycleStateError(f"repo format v2 缺少 mandatory lifecycle state: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleStateError(f"lifecycle state 无法读取或已损坏: {path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise LifecycleStateError("lifecycle state 顶层必须是 JSON object")
    if data.get("format_version") != LIFECYCLE_STATE_FORMAT_VERSION:
        raise LifecycleStateError(
            "lifecycle state 格式版本不兼容: "
            f"{data.get('format_version')!r}（支持 {LIFECYCLE_STATE_FORMAT_VERSION}）"
        )
    if data.get("repo_id") != repo.repo_id:
        raise LifecycleStateError(
            f"lifecycle state repo_id 不匹配: {data.get('repo_id')!r} != {repo.repo_id!r}"
        )
    return LifecycleState(
        repo_id=repo.repo_id,
        next_sequence=_validate_uint64_next(data.get("next_sequence")),
    )


def _observed_v2_sequences(repo: RepoInfo) -> list[int]:
    """Read sequence evidence from manifests and sequence-bearing artifact names."""

    observed: list[int] = []
    manifests_dir = repo.path / "manifests"
    for path in manifests_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LifecycleStateError(
                f"无法验证 manifest lifecycle sequence: {path}（{exc}）"
            ) from exc
        if data.get("format_version") == 2:
            value = data.get("lifecycle_seq")
            if isinstance(value, bool) or not isinstance(value, int):
                raise LifecycleStateError(f"manifest lifecycle_seq 非法: {path}")
            if not 0 <= value <= MAX_LIFECYCLE_SEQUENCE:
                raise LifecycleStateError(f"manifest lifecycle_seq 超出 uint64: {path}")
            encoded = parse_snapshot_sequence(data.get("snapshot_id", ""))
            if encoded is None:
                raise LifecycleStateError(f"manifest v2 snapshot id 缺少 sequence: {path}")
            if encoded != value:
                raise LifecycleStateError(f"manifest lifecycle_seq 与 snapshot id 不一致: {path}")
            observed.append(value)

    for path in (repo.path / "snapshots").iterdir():
        encoded = parse_snapshot_sequence(path.name)
        if encoded is not None:
            observed.append(encoded)
    suffix = ".json.tmp"
    for path in (repo.path / "manifests.tmp").glob(f"*{suffix}"):
        encoded = parse_snapshot_sequence(path.name[: -len(suffix)])
        if encoded is not None:
            observed.append(encoded)
    return observed


def load_lifecycle_state(
    repo: RepoInfo,
    *,
    validate_artifacts: bool = True,
) -> LifecycleState:
    state = _load_state_file(repo)
    if validate_artifacts:
        observed = _observed_v2_sequences(repo)
        if observed and state.next_sequence <= max(observed):
            raise LifecycleStateError(
                "lifecycle state rollback/corruption: "
                f"next_sequence={state.next_sequence}, observed_max={max(observed)}"
            )
    return state


def reserve_lifecycle_sequence(repo: RepoInfo) -> int:
    """Durably consume and return one never-before-issued physical-attempt sequence."""

    if repo.format_version != 2:
        raise LifecycleStateError(
            "必须先将 repository migration 到 format v2 才能 reserve sequence"
        )
    state = load_lifecycle_state(repo)
    if state.next_sequence == EXHAUSTED_NEXT_SEQUENCE:
        raise LifecycleStateError("lifecycle sequence 已耗尽（uint64），拒绝 wrap/reuse")
    reserved = state.next_sequence
    updated = LifecycleState(repo_id=repo.repo_id, next_sequence=reserved + 1)
    path = lifecycle_state_path(repo)
    try:
        write_json_durable(path, _state_to_dict(updated))
    except OSError as exc:
        raise LifecycleStateError(f"lifecycle sequence durable reservation 失败: {exc}") from exc
    # Verify the authoritative bytes before exposing the reservation to callers.
    published = load_lifecycle_state(repo)
    if published.next_sequence != reserved + 1:
        raise LifecycleStateError("lifecycle sequence reservation publication verification failed")
    return reserved
