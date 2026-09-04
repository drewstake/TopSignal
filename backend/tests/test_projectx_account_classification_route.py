import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import app.main as main_module


def test_classification_refresh_route_returns_only_fresh_hub_observation(monkeypatch):
    observed_at = datetime(2026, 9, 4, 2, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")
    monkeypatch.setattr(
        main_module,
        "refresh_projectx_account_classification_once",
        lambda **kwargs: (
            SimpleNamespace(
                account_id=kwargs["account_id"],
                provider_simulated=True,
                provider_classification_observed_at=observed_at,
                source="projectx_user_hub",
            )
        ),
    )

    result = main_module.refresh_projectx_account_automation_classification(101)

    assert result.account_id == 101
    assert result.provider_simulated is True
    assert result.provider_classification_observed_at == observed_at
    assert result.source == "projectx_user_hub"


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (
            main_module.ProjectXAccountClassificationProbeTimeout(
                "projectx_account_classification_timeout"
            ),
            504,
            "projectx_account_classification_timeout",
        ),
        (
            main_module.ProjectXAccountClassificationProbeUnavailable(
                "projectx_account_classification_unavailable"
            ),
            503,
            "projectx_account_classification_unavailable",
        ),
        (LookupError("account_not_found"), 404, "account_not_found"),
        (
            ValueError("csv_import_accounts_cannot_refresh_automation_classification"),
            400,
            "csv_import_accounts_cannot_refresh_automation_classification",
        ),
    ],
)
def test_classification_refresh_route_fails_closed(monkeypatch, error, expected_status, expected_detail):
    monkeypatch.setattr(main_module, "get_authenticated_user_id", lambda: "user-a")

    def fail(**_kwargs):
        raise error

    monkeypatch.setattr(
        main_module,
        "refresh_projectx_account_classification_once",
        fail,
    )

    with pytest.raises(HTTPException) as raised:
        main_module.refresh_projectx_account_automation_classification(101)

    assert raised.value.status_code == expected_status
    assert raised.value.detail == expected_detail
