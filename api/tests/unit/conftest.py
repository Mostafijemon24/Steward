"""Unit tests that do not need PostgreSQL. Overrides DB-backed fixtures from tests/conftest.py."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    yield


@pytest.fixture(scope="session")
def owner_engine(_database: None) -> Iterator[Any]:
    yield None


@pytest.fixture(autouse=True)
def _reset_rate_limits(owner_engine: Any) -> None:
    return
