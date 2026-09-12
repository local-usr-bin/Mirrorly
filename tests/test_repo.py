"""T-01 测试：备份仓库初始化与 repo.json 生成（M1 基础、M10 卷标识、ADR-005/010）。"""

import json

import pytest

from mirrorly import repo
from mirrorly.repo import RepoError, VolumeInfo

NTFS_VOLUME = VolumeInfo(label="BackupDisk", serial="A1B2C3D4", filesystem="NTFS")
EXFAT_VOLUME = VolumeInfo(label="UsbStick", serial="E5F60708", filesystem="exFAT")


def _ntfs_provider(path):
    return NTFS_VOLUME


def _exfat_provider(path):
    return EXFAT_VOLUME


class TestInitOnNtfs:
    def test_creates_directory_structure(self, tmp_path) -> None:
        info = repo.init_repo(tmp_path, volume_info_provider=_ntfs_provider)
        repo_dir = tmp_path / repo.REPO_DIR_NAME
        for sub in ("snapshots", "manifests", "manifests.tmp", "locks", "logs"):
            assert (repo_dir / sub).is_dir(), f"缺少目录 {sub}"
        assert info.path == repo_dir

    def test_repo_json_fields(self, tmp_path) -> None:
        repo.init_repo(tmp_path, volume_info_provider=_ntfs_provider)
        data = json.loads((tmp_path / repo.REPO_DIR_NAME / repo.REPO_INFO_FILE).read_text("utf-8"))
        assert data["format_version"] == repo.FORMAT_VERSION
        assert data["repo_id"]
        assert data["created_at"].endswith("+00:00")
        assert data["hash_algorithm"] in ("blake3", "sha256")
        assert data["volume"]["serial"] == "A1B2C3D4"
        assert data["volume"]["filesystem"] == "NTFS"
        assert data["filesystem_policy"] == "strict"
        assert data["hardlinks"] is True

    def test_repo_json_written_atomically_no_tmp_left(self, tmp_path) -> None:
        repo.init_repo(tmp_path, volume_info_provider=_ntfs_provider)
        leftovers = [p for p in (tmp_path / repo.REPO_DIR_NAME).glob("*.tmp") if p.is_file()]
        assert leftovers == []


class TestFilesystemPolicy:
    def test_strict_rejects_exfat(self, tmp_path) -> None:
        with pytest.raises(RepoError, match="NTFS"):
            repo.init_repo(
                tmp_path, filesystem_policy="strict", volume_info_provider=_exfat_provider
            )
        # 拒绝时不应留下仓库目录
        assert not (tmp_path / repo.REPO_DIR_NAME).exists()

    def test_warn_proceeds_with_confirmation(self, tmp_path) -> None:
        info = repo.init_repo(
            tmp_path,
            filesystem_policy="warn",
            assume_yes=True,
            volume_info_provider=_exfat_provider,
        )
        assert info.hardlinks is False
        data = json.loads((info.path / repo.REPO_INFO_FILE).read_text("utf-8"))
        assert data["filesystem_policy"] == "warn"
        assert data["hardlinks"] is False

    def test_warn_aborts_when_user_declines(self, tmp_path) -> None:
        with pytest.raises(RepoError, match="取消"):
            repo.init_repo(
                tmp_path,
                filesystem_policy="warn",
                assume_yes=False,
                confirm=lambda _msg: False,
                volume_info_provider=_exfat_provider,
            )
        assert not (tmp_path / repo.REPO_DIR_NAME).exists()

    def test_invalid_policy_rejected(self, tmp_path) -> None:
        with pytest.raises(RepoError, match="filesystem_policy"):
            repo.init_repo(
                tmp_path, filesystem_policy="ignore", volume_info_provider=_ntfs_provider
            )


class TestExistingRepo:
    def test_reinit_rejected(self, tmp_path) -> None:
        repo.init_repo(tmp_path, volume_info_provider=_ntfs_provider)
        with pytest.raises(RepoError, match="已存在|已初始化"):
            repo.init_repo(tmp_path, volume_info_provider=_ntfs_provider)


class TestLoadRepo:
    def test_roundtrip(self, tmp_path) -> None:
        created = repo.init_repo(tmp_path, volume_info_provider=_ntfs_provider)
        loaded = repo.load_repo(tmp_path)
        assert loaded.repo_id == created.repo_id
        assert loaded.hash_algorithm == created.hash_algorithm
        assert loaded.volume.filesystem == "NTFS"
        assert loaded.hardlinks is True

    def test_missing_repo_errors(self, tmp_path) -> None:
        with pytest.raises(RepoError, match="未找到|不存在"):
            repo.load_repo(tmp_path)

    def test_incompatible_format_version_errors(self, tmp_path) -> None:
        repo.init_repo(tmp_path, volume_info_provider=_ntfs_provider)
        info_file = tmp_path / repo.REPO_DIR_NAME / repo.REPO_INFO_FILE
        data = json.loads(info_file.read_text("utf-8"))
        data["format_version"] = 999
        info_file.write_text(json.dumps(data), "utf-8")
        with pytest.raises(RepoError, match="格式版本"):
            repo.load_repo(tmp_path)
