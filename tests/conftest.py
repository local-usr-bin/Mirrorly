"""Qualify the installed checkout before collecting any core regression tests."""

import pytest

from checkout_qualification import qualify_checkout


def pytest_sessionstart(session):
    try:
        qualify_checkout()
    except (RuntimeError, ImportError, OSError, ValueError) as error:
        raise pytest.UsageError(f"Mirrorly checkout qualification failed: {error}") from error
