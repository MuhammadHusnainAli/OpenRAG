"""OAuth via the official provider libraries (no wrapper client).

  * Google     authorization-code flow; ID token verified with ``google-auth``.
  * Microsoft ``msal`` ConfidentialClientApplication (official MSAL).
  * GitHub     direct authorization-code flow (no official SDK) over httpx.

CSRF is enforced with a signed, timestamped state (``itsdangerous``) carried in a
short-lived cookie and echoed in the ``state`` query param. Account linking on a
matching email is only allowed when the provider verified that email.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.disposable_email import normalize_email
from app.data.models import AuthProvider, User
from app.data.repositories import oauth as oauth_repo
from app.data.repositories import users as user_repo
from app.services.errors import ServiceError

_settings = get_settings()
_state_serializer = URLSafeTimedSerializer(_settings.secret_key, salt="oauth-state")
STATE_MAX_AGE = 600  # seconds

GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"


@dataclass
class Identity:
    provider_account_id: str
    email: str | None
    email_verified: bool
    display_name: str | None
    avatar_url: str | None


#  state helpers 
def make_state(provider: str) -> str:
    return _state_serializer.dumps({"p": provider})


def verify_state(provider: str, cookie_state: str | None, query_state: str | None) -> None:
    if not cookie_state or not query_state or cookie_state != query_state:
        raise ServiceError("OAuth state mismatch.", status_code=400, code="oauth_state")
    try:
        data = _state_serializer.loads(cookie_state, max_age=STATE_MAX_AGE)
    except SignatureExpired as exc:
        raise ServiceError("OAuth state expired.", status_code=400, code="oauth_state") from exc
    except BadSignature as exc:
        raise ServiceError("Invalid OAuth state.", status_code=400, code="oauth_state") from exc
    if data.get("p") != provider:
        raise ServiceError("OAuth provider mismatch.", status_code=400, code="oauth_state")


def is_enabled(provider: str) -> bool:
    if provider == "google":
        return bool(_settings.google_client_id and _settings.google_client_secret)
    if provider == "microsoft":
        return bool(_settings.microsoft_client_id and _settings.microsoft_client_secret)
    if provider == "github":
        return bool(_settings.github_client_id and _settings.github_client_secret)
    return False


def redirect_uri(provider: str) -> str:
    return f"{_settings.oauth_redirect_base}/{provider}/callback"


#  Microsoft (MSAL) 
def _msal_app():
    import msal

    return msal.ConfidentialClientApplication(
        _settings.microsoft_client_id,
        authority=f"https://login.microsoftonline.com/{_settings.microsoft_tenant or 'common'}",
        client_credential=_settings.microsoft_client_secret,
    )


#  authorization URL 
def authorization_url(provider: str, state: str) -> str:
    uri = redirect_uri(provider)
    if provider == "google":
        params = {
            "client_id": _settings.google_client_id,
            "redirect_uri": uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTHORIZE}?{urlencode(params)}"
    if provider == "github":
        params = {
            "client_id": _settings.github_client_id,
            "redirect_uri": uri,
            "scope": "read:user user:email",
            "state": state,
            "allow_signup": "true",
        }
        return f"{GITHUB_AUTHORIZE}?{urlencode(params)}"
    if provider == "microsoft":
        return _msal_app().get_authorization_request_url(
            scopes=["User.Read"], state=state, redirect_uri=uri
        )
    raise ServiceError("Unknown provider.", status_code=404)


#  code exchange -> Identity 
async def exchange(provider: str, code: str) -> Identity:
    if provider == "google":
        return await _exchange_google(code)
    if provider == "github":
        return await _exchange_github(code)
    if provider == "microsoft":
        return await _exchange_microsoft(code)
    raise ServiceError("Unknown provider.", status_code=404)


async def _exchange_google(code: str) -> Identity:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            GOOGLE_TOKEN,
            data={
                "code": code,
                "client_id": _settings.google_client_id,
                "client_secret": _settings.google_client_secret,
                "redirect_uri": redirect_uri("google"),
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise ServiceError("Google token exchange failed.", status_code=400, code="oauth_exchange")
    id_token = resp.json().get("id_token")
    if not id_token:
        raise ServiceError("Google did not return an ID token.", status_code=400)

    # verify with the official google-auth library (signature + aud + exp)
    def _verify():
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token as google_id_token

        return google_id_token.verify_oauth2_token(
            id_token, ga_requests.Request(), _settings.google_client_id
        )

    claims = await asyncio.to_thread(_verify)
    return Identity(
        provider_account_id=claims["sub"],
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified", False)),
        display_name=claims.get("name"),
        avatar_url=claims.get("picture"),
    )


async def _exchange_microsoft(code: str) -> Identity:
    def _acquire():
        return _msal_app().acquire_token_by_authorization_code(
            code, scopes=["User.Read"], redirect_uri=redirect_uri("microsoft")
        )

    result = await asyncio.to_thread(_acquire)
    if "id_token_claims" not in result:
        raise ServiceError(
            f"Microsoft sign-in failed: {result.get('error_description', 'unknown error')}",
            status_code=400,
            code="oauth_exchange",
        )
    claims = result["id_token_claims"]
    email = claims.get("email") or claims.get("preferred_username")
    return Identity(
        provider_account_id=claims.get("oid") or claims["sub"],
        email=email,
        email_verified=True,  # Microsoft Entra verifies tenant accounts
        display_name=claims.get("name"),
        avatar_url=None,
    )


async def _exchange_github(code: str) -> Identity:
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            GITHUB_TOKEN,
            headers={"Accept": "application/json"},
            data={
                "client_id": _settings.github_client_id,
                "client_secret": _settings.github_client_secret,
                "code": code,
                "redirect_uri": redirect_uri("github"),
            },
        )
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise ServiceError("GitHub token exchange failed.", status_code=400, code="oauth_exchange")

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}
        profile = (await client.get(f"{GITHUB_API}/user", headers=headers)).json()
        emails = (await client.get(f"{GITHUB_API}/user/emails", headers=headers)).json()

    primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
    email = primary["email"] if primary else profile.get("email")
    return Identity(
        provider_account_id=str(profile["id"]),
        email=email,
        email_verified=primary is not None,
        display_name=profile.get("name") or profile.get("login"),
        avatar_url=profile.get("avatar_url"),
    )


#  upsert 
async def login_or_create(db: AsyncSession, provider: AuthProvider, identity: Identity) -> User:
    account = await oauth_repo.get_by_provider(db, provider, identity.provider_account_id)
    if account is not None:
        user = await user_repo.get_by_id(db, account.user_id)
        if user is None:
            raise ServiceError("Linked account is missing its user.", status_code=500)
        return user

    if not identity.email:
        raise ServiceError("Provider did not return an email address.", code="oauth_no_email")
    normalized = normalize_email(identity.email)

    existing = await user_repo.get_by_email(db, normalized)
    if existing is not None:
        if not identity.email_verified:
            raise ServiceError(
                "This email already has an account. Sign in with your password.",
                status_code=409,
                code="link_requires_verified_email",
            )
        await oauth_repo.link(
            db,
            user_id=existing.id,
            provider=provider,
            provider_account_id=identity.provider_account_id,
            email=normalized,
        )
        return existing

    user = await user_repo.create(
        db,
        email=normalized,
        password_hash=None,
        display_name=identity.display_name,
        email_verified=identity.email_verified,
        avatar_url=identity.avatar_url,
    )
    await oauth_repo.link(
        db,
        user_id=user.id,
        provider=provider,
        provider_account_id=identity.provider_account_id,
        email=normalized,
    )
    return user
