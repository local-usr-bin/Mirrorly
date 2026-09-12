"""冒烟测试：验证包可正常导入、环境可用。"""

import mirrorly


def test_package_importable() -> None:
    """镜像包应可导入并暴露版本号。"""
    assert mirrorly.__version__ == "0.0.1"
