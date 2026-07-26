from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.context import verified_identity
from app.api.rate_limit import enforce_authenticated_rate_limit
from app.core.auth import VerifiedIdentity
from app.services.export_service import (
    ExportDeliveryResponse,
    ExportDownloadResponse,
    ExportExpiredError,
    ExportStateConflictError,
    ExportStorageUnavailableError,
    ExportSubjectNotFoundError,
    ExportSubjectRequest,
    OffboardingArchiveResponse,
    OffboardingRequest,
    OffboardingResponse,
    OffboardingStateConflictError,
    TenantExportResponse,
    archive_offboarding,
    begin_offboarding,
    confirm_export_delivery,
    create_export_download,
    request_tenant_export,
)
from app.services.export_storage import ExportStorage, create_export_storage
from app.services.platform_operations import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    PlatformAdminRequiredError,
)

router = APIRouter(prefix="/api/v1/platform", tags=["platform-exports"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


def _raise_service_error(exc: Exception) -> NoReturn:
    if isinstance(exc, PlatformAdminRequiredError):
        raise HTTPException(status_code=403, detail="platform_admin_required") from exc
    if isinstance(exc, ExportSubjectNotFoundError):
        raise HTTPException(status_code=404, detail="export_subject_not_found") from exc
    if isinstance(exc, ExportExpiredError):
        raise HTTPException(status_code=410, detail="export_expired") from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(status_code=409, detail="idempotency_key_reused") from exc
    if isinstance(exc, IdempotencyInProgressError):
        raise HTTPException(status_code=409, detail="idempotency_request_in_progress") from exc
    if isinstance(exc, (ExportStateConflictError, OffboardingStateConflictError)):
        raise HTTPException(status_code=409, detail="export_lifecycle_conflict") from exc
    if isinstance(exc, ExportStorageUnavailableError):
        raise HTTPException(status_code=503, detail="export_storage_unavailable") from exc
    raise HTTPException(status_code=503, detail="export_service_unavailable") from exc


def _storage(request: Request) -> ExportStorage:
    current = getattr(request.app.state, "export_storage", None)
    if current is None:
        current = create_export_storage(request.app.state.settings)
        request.app.state.export_storage = current
    return current


@router.post(
    "/exports",
    response_model=TenantExportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_export(
    request: Request,
    payload: ExportSubjectRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> TenantExportResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await request_tenant_export(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.get(
    "/exports/{export_id}/download",
    response_model=ExportDownloadResponse,
)
async def download_export(
    request: Request,
    export_id: UUID,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> ExportDownloadResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await create_export_download(
            request.app.state.database_pool,
            _storage(request),
            request.app.state.settings,
            actor_id=identity.auth_user_id,
            export_id=export_id,
            request_id=request.state.request_id,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/exports/{export_id}/confirm-delivery")
async def confirm_delivery(
    request: Request,
    export_id: UUID,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> ExportDeliveryResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await confirm_export_delivery(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            export_id=export_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/offboarding",
    response_model=OffboardingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_offboarding(
    request: Request,
    payload: OffboardingRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> OffboardingResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await begin_offboarding(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except Exception as exc:
        _raise_service_error(exc)


@router.post(
    "/offboarding/{case_id}/archive",
    response_model=OffboardingArchiveResponse,
)
async def archive_tenant(
    request: Request,
    case_id: UUID,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> OffboardingArchiveResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await archive_offboarding(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            case_id=case_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )
    except Exception as exc:
        _raise_service_error(exc)
