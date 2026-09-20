"""The checkout guard must reject a different source tree, even with the same version."""

import pytest

from checkout_qualification import MODULES, ROOT, require_checkout


@pytest.mark.parametrize("wrong_module", MODULES)
def test_wrong_checkout_is_rejected(tmp_path, wrong_module):
    modules = {
        name: str(
            ROOT
            / "src"
            / "mirrorly"
            / ("__init__.py" if name == "mirrorly" else name.rsplit(".", 1)[1] + ".py")
        )
        for name in MODULES
    }
    modules[wrong_module] = str(tmp_path / "another-checkout" / "src" / "mirrorly" / "cli.py")
    with pytest.raises(RuntimeError, match="Wrong Mirrorly checkout"):
        require_checkout(modules)
