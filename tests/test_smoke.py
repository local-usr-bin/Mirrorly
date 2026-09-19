"""冒烟测试：验证包可正常导入、环境可用。"""

import tomllib
from pathlib import Path

import mirrorly


def test_package_importable() -> None:
    """镜像包应可导入并暴露版本号。"""
    assert mirrorly.__version__ == "0.1.0"


def test_source_versions_match() -> None:
    """项目 metadata 与运行时版本保持一致，不依赖已安装 distribution metadata。"""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as stream:
        project = tomllib.load(stream)["project"]
    assert project["version"] == mirrorly.__version__ == "0.1.0"
