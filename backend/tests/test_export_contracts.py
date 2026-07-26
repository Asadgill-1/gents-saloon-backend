import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.export_service import (
    EXPORT_SCHEMA_VERSION,
    ExportDataset,
    ExportSubjectRequest,
    build_export_archive,
)


def test_export_scope_requires_the_matching_shop_shape() -> None:
    with pytest.raises(ValidationError):
        ExportSubjectRequest(
            business_id=uuid4(),
            shop_id=uuid4(),
            scope="business",
        )
    with pytest.raises(ValidationError):
        ExportSubjectRequest(
            business_id=uuid4(),
            scope="shop",
        )


def test_archive_is_versioned_complete_and_redacts_credential_like_audit_keys() -> None:
    business_id = uuid4()
    archive_bytes = build_export_archive(
        subject=ExportSubjectRequest(
            business_id=business_id,
            scope="business",
        ),
        datasets=[
            ExportDataset(
                name="bots",
                columns=("id", "bot_username"),
                rows=({"id": str(uuid4()), "bot_username": "safe_bot"},),
            ),
            ExportDataset(
                name="audit_log",
                columns=("id", "after"),
                rows=(
                    {
                        "id": str(uuid4()),
                        "after": {
                            "safe": "kept",
                            "token_ciphertext": "must-not-survive",
                            "nested": {"webhook_secret": "must-not-survive"},
                        },
                    },
                ),
            ),
        ],
        generated_at=datetime(2026, 7, 26, 6, 0, tzinfo=UTC),
    )

    assert hashlib.sha256(archive_bytes).hexdigest()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert set(archive.namelist()) == {
            "manifest.json",
            "bots.json",
            "bots.csv",
            "audit_log.json",
            "audit_log.csv",
        }
        manifest = json.loads(archive.read("manifest.json"))
        audit = json.loads(archive.read("audit_log.json"))

    assert manifest["schema_version"] == EXPORT_SCHEMA_VERSION
    assert manifest["subject"]["business_id"] == str(business_id)
    assert manifest["datasets"][0]["row_count"] == 1
    exported_after = audit["rows"][0]["after"]
    assert exported_after["safe"] == "kept"
    assert exported_after["token_ciphertext"] == "[REDACTED]"
    assert exported_after["nested"]["webhook_secret"] == "[REDACTED]"
    assert "must-not-survive" not in archive_bytes.decode(errors="ignore")


def test_export_and_offboarding_routes_are_registered() -> None:
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]

    assert "/api/v1/platform/exports" in paths
    assert "/api/v1/platform/exports/{export_id}/download" in paths
    assert "/api/v1/platform/exports/{export_id}/confirm-delivery" in paths
    assert "/api/v1/platform/offboarding" in paths
    assert "/api/v1/platform/offboarding/{case_id}/archive" in paths
