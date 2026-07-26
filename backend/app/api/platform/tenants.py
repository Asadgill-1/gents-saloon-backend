from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.context import verified_identity
from app.api.rate_limit import enforce_authenticated_rate_limit
from app.core.auth import VerifiedIdentity
from app.services.tenant_service import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    OwnerIdentityInactiveError,
    OwnerIdentityNotFoundError,
    PlatformAdminRequiredError,
    TenantOnboardingConflictError,
    TenantOnboardingRequest,
    TenantOnboardingResponse,
    onboard_tenant,
)

router = APIRouter(prefix="/api/v1/platform/tenants", tags=["platform-tenants"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


@router.post("", response_model=TenantOnboardingResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    request: Request,
    payload: TenantOnboardingRequest,
    idempotency_key: IdempotencyKey,
    identity: Annotated[VerifiedIdentity, Depends(verified_identity)],
) -> TenantOnboardingResponse:
    await enforce_authenticated_rate_limit(request, identity.auth_user_id)
    try:
        return await onboard_tenant(
            request.app.state.database_pool,
            actor_id=identity.auth_user_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
            payload=payload,
        )
    except PlatformAdminRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform_admin_required",
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency_key_reused",
        ) from exc
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency_request_in_progress",
        ) from exc
    except OwnerIdentityNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="owner_identity_not_found",
        ) from exc
    except OwnerIdentityInactiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="owner_identity_inactive",
        ) from exc
    except TenantOnboardingConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="tenant_onboarding_conflict",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tenant_onboarding_unavailable",
        ) from exc
