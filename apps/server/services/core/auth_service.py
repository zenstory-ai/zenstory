import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import cast
from uuid import uuid4

from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import RefreshTokenRecord, User
from utils.logger import get_logger

logger = get_logger(__name__)

# JWT 配置 - 从环境变量读取，开发模式自动生成
_jwt_secret_env = os.getenv("JWT_SECRET_KEY")
if _jwt_secret_env:
    SECRET_KEY = _jwt_secret_env
else:
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning(
        "JWT_SECRET_KEY not set — auto-generated for development. "
        "Set a fixed value for production."
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
ALLOW_LEGACY_UNTYPED_TOKENS = os.getenv("ALLOW_LEGACY_UNTYPED_TOKENS", "false").lower() == "true"
ALLOW_LEGACY_REFRESH_WITHOUT_JTI = (
    os.getenv("ALLOW_LEGACY_REFRESH_WITHOUT_JTI", "false").lower() == "true"
)
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
MIN_JWT_SECRET_LENGTH = 32

# 密码加密
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 密码流
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


def _normalize_environment_name(raw: str | None) -> str:
    normalized = (raw or "").strip().lower()
    if normalized in {"prod"}:
        return "production"
    if normalized in {"stage"}:
        return "staging"
    if normalized in {"dev"}:
        return "development"
    if normalized in {"test"}:
        return "testing"
    return normalized


def _resolve_runtime_environment() -> str:
    """
    Resolve runtime environment from multiple common variable names.

    Priority:
    1) ENVIRONMENT
    2) APP_ENV
    3) RAILWAY_ENVIRONMENT
    4) FASTAPI_ENV
    """
    for var_name in ("ENVIRONMENT", "APP_ENV", "RAILWAY_ENVIRONMENT", "FASTAPI_ENV"):
        candidate = os.getenv(var_name)
        if candidate and candidate.strip():
            return _normalize_environment_name(candidate)
    return "development"


# 密码哈希
def hash_password(password: str) -> str:
    # bcrypt has a 72 byte limit, truncate if necessary
    if len(password.encode('utf-8')) > 72:
        password = password[:72]
    return cast(str, pwd_context.hash(password))


# 验证密码
def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False

    try:
        return cast(bool, pwd_context.verify(plain_password, hashed_password))
    except UnknownHashError:
        # For malformed non-empty hashes, keep legacy behavior for tests/callers.
        raise
    except Exception:
        # Covers unexpected verification errors; treat as non-match.
        return False


def password_fingerprint(user: User) -> str:
    """Short digest of the stored password hash, embedded in access tokens as ``pwf``.

    Any password change (self-service reset or change-password) changes the
    bcrypt hash and therefore this value, which invalidates every access token
    issued before the change. OAuth-only accounts hash the empty string.
    """
    return hashlib.sha256((user.hashed_password or "").encode("utf-8")).hexdigest()[:16]


def access_token_claims(user: User) -> dict:
    """Claims for a user's access token: subject plus password fingerprint."""
    return {"sub": user.id, "pwf": password_fingerprint(user)}


def access_token_matches_password(payload: dict, user: User) -> bool:
    """False when the token carries a ``pwf`` from before the latest password change.

    Tokens issued before ``pwf`` existed carry no claim and stay valid until
    they expire (ACCESS_TOKEN_EXPIRE_MINUTES).
    """
    token_fingerprint = payload.get("pwf")
    if token_fingerprint is None:
        return True
    return hmac.compare_digest(str(token_fingerprint), password_fingerprint(user))


def revoke_active_refresh_tokens_for_user(
    session: Session,
    *,
    user_id: str,
    reason: str,
) -> int:
    """Revoke all active refresh tokens for a user (caller commits)."""
    # Share refresh's User -> token lock order so rotation cannot insert a
    # descendant after our active-token snapshot. Do not reload pending changes.
    session.exec(select(User.id).where(User.id == user_id).with_for_update()).first()
    now = utcnow()
    active_records = session.exec(
        select(RefreshTokenRecord).where(
            RefreshTokenRecord.user_id == user_id,
            RefreshTokenRecord.revoked_at.is_(None),
        )
    ).all()

    for record in active_records:
        record.revoked_at = now
        record.revoke_reason = reason
        record.updated_at = now
        session.add(record)

    return len(active_records)


# 创建访问令牌
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    # Ensure 'sub' is a string (JWT requirement)
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "typ": TOKEN_TYPE_ACCESS})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return cast(str, encoded_jwt)


# 创建刷新令牌
def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    # Ensure 'sub' is a string (JWT requirement)
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "typ": TOKEN_TYPE_REFRESH})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return cast(str, encoded_jwt)


def generate_token_jti() -> str:
    """Generate a unique token ID (jti)."""
    return uuid4().hex


def get_refresh_token_expires_at() -> datetime:
    """Return refresh token expiration datetime (UTC aware)."""
    return utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)


def validate_auth_runtime_configuration() -> None:
    """
    Validate critical auth configuration.

    In production-like environments, fail fast on weak JWT settings.
    """
    environment = _resolve_runtime_environment()
    strict_mode = environment in {"production", "staging"}
    jwt_secret_env = os.getenv("JWT_SECRET_KEY")
    allow_legacy_untyped_tokens = (
        os.getenv("ALLOW_LEGACY_UNTYPED_TOKENS", "false").strip().lower() == "true"
    )

    if strict_mode:
        if not jwt_secret_env or len(jwt_secret_env) < MIN_JWT_SECRET_LENGTH:
            raise RuntimeError(
                "JWT_SECRET_KEY must be explicitly set (at least 32 characters) in production/staging."
            )

        if allow_legacy_untyped_tokens:
            raise RuntimeError(
                "ALLOW_LEGACY_UNTYPED_TOKENS must be false in production/staging."
            )


def _enforce_auth_runtime_configuration_on_import() -> None:
    """Fail fast in strict environments when auth configuration is unsafe."""
    environment = _resolve_runtime_environment()
    if environment in {"production", "staging"}:
        validate_auth_runtime_configuration()


_enforce_auth_runtime_configuration_on_import()


# 验证令牌
def verify_token(token: str, expected_type: str | None = None) -> dict | None:
    try:
        payload = cast(dict, jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]))
        if expected_type is not None:
            token_type = payload.get("typ")
            if token_type is None:
                if not ALLOW_LEGACY_UNTYPED_TOKENS:
                    return None
            elif token_type != expected_type:
                return None
        return payload
    except JWTError:
        return None


# 获取当前用户
def get_current_user(
    token: str = Depends(oauth2_scheme), session: Session = Depends(get_session)
) -> User:
    payload = verify_token(token, expected_type=TOKEN_TYPE_ACCESS)
    if payload is None:
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = session.get(User, user_id)
    if user is None or not access_token_matches_password(payload, user):
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


# 获取当前活跃用户
async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise APIException(
            error_code=ErrorCode.AUTH_INACTIVE_USER,
            status_code=400
        )
    return current_user


# 可选获取当前用户（不强制认证）
async def get_optional_current_user(
    token: str | None = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User | None:
    if token is None:
        return None

    payload = verify_token(token, expected_type=TOKEN_TYPE_ACCESS)
    if payload is None:
        return None

    user_id = cast(str | None, payload.get("sub"))
    if user_id is None:
        return None

    user = session.get(User, user_id)
    if user is None or not access_token_matches_password(payload, user):
        return None
    return user


# 获取当前超级用户
async def get_current_superuser(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """验证当前用户是否为超级用户,如果不是则抛出 403 异常"""
    if not current_user.is_superuser:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return current_user
