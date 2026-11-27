from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.cookies import clear_refresh_cookie, set_refresh_cookie
from app.dependencies import AuthContext, get_current_auth, raise_auth_error
from app.db.session import get_db
from app.schemas.auth import (
    AcceptInviteRequest,
    ChangeEmailRequest,
    ChangePasswordRequest,
    DepartmentOut,
    LoginRequest,
    LogoutAllResponse,
    MembershipOption,
    MfaCodeRequest,
    MfaDisableRequest,
    MfaSetupResponse,
    OkResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PlanOut,
    RefreshRequest,
    SendInviteSmsRequest,
    SendOtpRequest,
    SendOtpResponse,
    SessionUser,
    SignupOut,
    SignupPaymentConfirmRequest,
    SignupRegisterRequest,
    SignupRegisterResponse,
    SwitchMembershipRequest,
    TokenPair,
    VerifyOtpRequest,
    WorkplaceOut,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _ip(request: Request, x_forwarded_for: str | None) -> str | None:
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _refresh_from_request(request: Request, body: RefreshRequest | None) -> str | None:
    if body and body.refresh_token:
        return body.refresh_token
    return request.cookies.get(settings.refresh_cookie_name)


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    x_forwarded_for: str | None = Header(default=None),
) -> TokenPair:
    try:
        tokens = await auth_service.login(
            db,
            email=body.email,
            password=body.password,
            membership_id=body.membership_id,
            mfa_code=body.mfa_code,
            ip_address=_ip(request, x_forwarded_for),
            user_agent=request.headers.get("user-agent"),
        )
        set_refresh_cookie(response, tokens.refresh_token)
        return tokens
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/logout", response_model=OkResponse)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
    body: RefreshRequest | None = None,
) -> OkResponse:
    try:
        await auth_service.logout(
            db,
            user_id=auth.user_id,
            refresh_token=_refresh_from_request(request, body),
        )
        clear_refresh_cookie(response)
        return OkResponse()
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/logout-all", response_model=LogoutAllResponse)
async def logout_all(
    response: Response,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> LogoutAllResponse:
    try:
        result = await auth_service.logout_all(db, user_id=auth.user_id)
        clear_refresh_cookie(response)
        return LogoutAllResponse(revoked=result["revoked"])
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    body: RefreshRequest | None = None,
    x_forwarded_for: str | None = Header(default=None),
) -> TokenPair:
    token = _refresh_from_request(request, body)
    if not token:
        raise_auth_error(auth_service.AuthError("Refresh token required.", status_code=401))
        raise
    try:
        tokens = await auth_service.refresh_session(
            db,
            refresh_token=token,
            ip_address=_ip(request, x_forwarded_for),
            user_agent=request.headers.get("user-agent"),
        )
        set_refresh_cookie(response, tokens.refresh_token)
        return tokens
    except auth_service.AuthError as exc:
        clear_refresh_cookie(response)
        raise_auth_error(exc)
        raise


@router.get("/me", response_model=SessionUser)
async def me(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> SessionUser:
    try:
        return await auth_service.current_session(
            db, user_id=auth.user_id, membership_id=auth.membership_id
        )
    except auth_service.AuthError as exc:
        raise_auth_error(exc)


@router.post("/password/forgot", response_model=SendOtpResponse)
async def forgot_password(
    body: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_forwarded_for: str | None = Header(default=None),
) -> SendOtpResponse:
    try:
        result = await auth_service.request_password_reset(
            db,
            phone=body.phone,
            ip_address=_ip(request, x_forwarded_for),
        )
        return SendOtpResponse(
            expires_in_sec=result.get("expires_in_sec", 600),
            demo_code=result.get("demo_code"),
            sms_sent=bool(result.get("sms_sent")),
        )
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/password/reset", response_model=OkResponse)
async def reset_password(
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    try:
        await auth_service.reset_password(
            db,
            phone=body.phone,
            code=body.code,
            password=body.password,
        )
        return OkResponse()
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/invite/accept", response_model=TokenPair)
async def accept_invite(
    body: AcceptInviteRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    x_forwarded_for: str | None = Header(default=None),
) -> TokenPair:
    try:
        tokens = await auth_service.accept_invite(
            db,
            token=body.token,
            password=body.password,
            ip_address=_ip(request, x_forwarded_for),
            user_agent=request.headers.get("user-agent"),
        )
        set_refresh_cookie(response, tokens.refresh_token)
        return tokens
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/membership/switch", response_model=TokenPair)
async def switch_membership(
    body: SwitchMembershipRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
    x_forwarded_for: str | None = Header(default=None),
) -> TokenPair:
    try:
        tokens = await auth_service.switch_membership(
            db,
            user_id=auth.user_id,
            membership_id=body.membership_id,
            ip_address=_ip(request, x_forwarded_for),
            user_agent=request.headers.get("user-agent"),
        )
        set_refresh_cookie(response, tokens.refresh_token)
        return tokens
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/email/change", response_model=SessionUser)
async def change_email(
    body: ChangeEmailRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> SessionUser:
    try:
        return await auth_service.change_email(
            db,
            user_id=auth.user_id,
            membership_id=auth.membership_id,
            current_password=body.current_password,
            new_email=str(body.new_email),
        )
    except auth_service.AuthError as exc:
        raise_auth_error(exc)


@router.post("/password/change", response_model=OkResponse)
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> OkResponse:
    try:
        await auth_service.change_password(
            db,
            user_id=auth.user_id,
            current_password=body.current_password,
            new_password=body.new_password,
        )
        return OkResponse()
    except auth_service.AuthError as exc:
        raise_auth_error(exc)


@router.get("/memberships", response_model=list[MembershipOption])
async def memberships(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> list[MembershipOption]:
    return await auth_service.list_membership_options(db, user_id=auth.user_id)


@router.get("/workplaces", response_model=list[WorkplaceOut])
async def workplaces(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> list[WorkplaceOut]:
    return await auth_service.list_my_workplaces(db, user_id=auth.user_id)


@router.get("/departments", response_model=list[DepartmentOut])
async def my_departments(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> list[DepartmentOut]:
    try:
        session = await auth_service.current_session(
            db, user_id=auth.user_id, membership_id=auth.membership_id
        )
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise
    return await auth_service.list_my_departments(db, workplace_id=session.workplace_id)


@router.post("/otp/send", response_model=SendOtpResponse)
async def send_otp(
    body: SendOtpRequest,
    request: Request,
    x_forwarded_for: str | None = Header(default=None),
) -> SendOtpResponse:
    try:
        result = await auth_service.send_otp(
            phone=body.phone, ip_address=_ip(request, x_forwarded_for)
        )
        return SendOtpResponse(
            expires_in_sec=result["expires_in_sec"],
            demo_code=result.get("demo_code"),
            sms_sent=bool(result.get("sms_sent")),
        )
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/otp/verify", response_model=OkResponse)
async def verify_otp(body: VerifyOtpRequest) -> OkResponse:
    try:
        await auth_service.verify_otp(phone=body.phone, code=body.code)
        return OkResponse()
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/invite/sms", response_model=OkResponse)
async def send_invite_sms(
    body: SendInviteSmsRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> OkResponse:
    """Send invite link/token by SMS via Kavenegar (admin/owner)."""
    _ = auth
    try:
        result = await auth_service.send_invite_sms(
            phone=body.phone,
            invite_token=body.invite_token,
            invite_url=body.invite_url,
        )
        return OkResponse(sms_sent=result.get("sms_sent"))
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/mfa/setup", response_model=MfaSetupResponse)
async def mfa_setup(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> MfaSetupResponse:
    try:
        result = await auth_service.mfa_setup_start(db, user_id=auth.user_id)
        return MfaSetupResponse(secret=result["secret"], otpauth_url=result["otpauth_url"])
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/mfa/confirm", response_model=OkResponse)
async def mfa_confirm(
    body: MfaCodeRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> OkResponse:
    try:
        await auth_service.mfa_setup_confirm(db, user_id=auth.user_id, code=body.code)
        return OkResponse()
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/mfa/disable", response_model=OkResponse)
async def mfa_disable(
    body: MfaDisableRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> OkResponse:
    try:
        await auth_service.mfa_disable(
            db,
            user_id=auth.user_id,
            current_password=body.current_password,
            code=body.code,
        )
        return OkResponse()
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.get("/signup/plans", response_model=list[PlanOut])
async def signup_plans() -> list[PlanOut]:
    return auth_service.list_plans()


@router.post("/signup/register", response_model=SignupRegisterResponse)
async def signup_register(
    body: SignupRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_forwarded_for: str | None = Header(default=None),
) -> SignupRegisterResponse:
    """
    Full self-serve signup after SMS OTP verification.
    Creates admin user + workplace (inactive until payment).
    """
    try:
        return await auth_service.signup_register(
            db,
            full_name=body.full_name,
            email=str(body.email),
            phone=body.phone,
            password=body.password,
            organization=body.organization,
            org_type=body.org_type,
            plan_id=body.plan_id,
            city=body.city,
            country=body.country,
            preferred_language=body.preferred_language,
            ip_address=_ip(request, x_forwarded_for),
            user_agent=request.headers.get("user-agent"),
        )
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.post("/signup/payment/confirm", response_model=TokenPair)
async def signup_payment_confirm(
    body: SignupPaymentConfirmRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    x_forwarded_for: str | None = Header(default=None),
) -> TokenPair:
    """Payment stub confirm — activates workplace and returns session tokens."""
    try:
        tokens = await auth_service.signup_confirm_payment(
            db,
            signup_id=body.signup_id,
            checkout_id=body.checkout_id,
            ip_address=_ip(request, x_forwarded_for),
            user_agent=request.headers.get("user-agent"),
        )
        set_refresh_cookie(response, tokens.refresh_token)
        return tokens
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise


@router.get("/owner/signups", response_model=list[SignupOut])
async def owner_list_signups(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
) -> list[SignupOut]:
    try:
        return await auth_service.list_signups_for_owner(db, user_id=auth.user_id)
    except auth_service.AuthError as exc:
        raise_auth_error(exc)
        raise
