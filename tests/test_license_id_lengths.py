from __future__ import annotations

import pytest

from pipe1_license_server.admin import _new_id
from pipe1_license_server.app import _id


@pytest.mark.parametrize(
    "prefix",
    [
        "org",
        "lic",
        "key",
        "act",
        "feature",
        "quota",
        "usage",
        "ent",
        "audit",
        "trs",
        "trc",
        "sample",
    ],
)
def test_admin_generated_ids_fit_postgres_varchar_36(prefix: str) -> None:
    assert len(_new_id(prefix)) <= 36


@pytest.mark.parametrize(
    "prefix",
    [
        "act",
        "ent",
        "trs",
        "trc",
        "sample",
    ],
)
def test_api_generated_ids_fit_postgres_varchar_36(prefix: str) -> None:
    assert len(_id(prefix)) <= 36
