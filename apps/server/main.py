# ruff: noqa: E402, I001
"""
zenstory API - Main application entry point

对话式 AI 辅助写小说工作台
"""

# Load environment variables from .env file FIRST
import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

from scripts.check_python_runtime import check_pyexpat

load_dotenv()
check_pyexpat()

# Import routers and other modules
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.admin import router as admin_router
from api.agent import router as agent_router
from api.agent_api import router as agent_api_router
from api.agent_api_keys import router as agent_api_keys_router
from api.auth import router as auth_router
from api.chat import router as chat_router
from api.editor import router as editor_router
from api.export import router as export_router
from api.feedback import router as feedback_router
from api.files import router as files_router
from api.inspirations import router as inspirations_router
from api.materials import router as materials_router
from api.oauth import router as oauth_router
from api.payments import router as payments_router
from api.points import router as points_router
from api.persona import router as persona_router
from api.projects import router as projects_router
from api.public_skills import router as public_skills_router
from api.referral import router as referral_router
from api.skills import router as skills_router
from api.snapshots import router as snapshots_router
from api.stats import router as stats_router
from api.subscription import router as subscription_router
from api.vector_search import router as vector_search_router
from api.verification import router as verification_router
from api.versions import router as versions_router
from api.voice import router as voice_router
from sqlalchemy.exc import IntegrityError

from config.logger_config import configure_logging
from core.error_handler import (
    APIException,
    api_exception_handler,
    general_exception_handler,
    http_exception_handler,
    integrity_error_handler,
    validation_exception_handler,
)
from config.payment_settings import get_payment_settings
from database import init_db
from middleware.body_size_limit import BodySizeLimitMiddleware
from middleware.logging_middleware import LoggingMiddleware
from services.infra.readiness_service import check_readiness
from services.skill_md_service import skill_md_service
from services.subscription.zpay_service import zpay_service
from utils.logger import get_logger, log_with_context

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan handler."""
    await init_db()
    zpay_service.log_configuration_status(get_payment_settings())
    log_with_context(
        logger,
        logging.INFO,
        f"{os.getenv('APP_NAME', 'zenstory API')} started successfully",
        version=os.getenv("APP_VERSION", "1.0.0"),
        docs_url="/docs",
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
    yield
    # Usage-ledger rows are written by background tasks; let in-flight ones land.
    from services.usage.llm_usage_service import drain_pending_usage_records

    try:
        await asyncio.wait_for(drain_pending_usage_records(), timeout=5)
    except asyncio.TimeoutError:
        logger.warning("Timed out after 5s draining pending usage records on shutdown")


# Create FastAPI app
app = FastAPI(
    title=os.getenv("APP_NAME", "zenstory API"),
    description="对话式 AI 辅助写小说工作台",
    version=os.getenv("APP_VERSION", "1.0.0"),
    lifespan=lifespan,
)

# Configure logging (must be before middleware)
configure_logging()
logger = get_logger(__name__)

# Register global exception handlers
app.add_exception_handler(APIException, api_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(IntegrityError, integrity_error_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Middleware order: the last one added is the outermost, so requests flow
# CORS -> Logging -> BodySizeLimit -> app. Logging sits inside CORS so even
# 500s it produces carry CORS headers, and outside the body limit so 413s are
# logged with a request id.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(LoggingMiddleware)

# Configure CORS
environment = os.getenv("ENVIRONMENT", "development").strip().lower()
is_production_like = environment in {"production", "staging"}
cors_origins_env = os.getenv("CORS_ORIGINS", "")
configured_cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

default_dev_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

if is_production_like:
    all_origins = sorted(set(configured_cors_origins))
else:
    all_origins = sorted(set(configured_cors_origins + default_dev_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=all_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Let the browser read correlation ids so feedback reports can be matched
    # to server logs.
    expose_headers=["X-Request-ID", "X-Trace-ID", "X-Agent-Run-ID"],
)

if is_production_like and not all_origins:
    log_with_context(
        logger,
        logging.WARNING,
        "CORS_ORIGINS is empty in production-like environment; browsers will reject cross-origin credentials requests",
        environment=environment,
    )

# Register routers
app.include_router(auth_router)
app.include_router(verification_router)
app.include_router(oauth_router)
app.include_router(projects_router)
app.include_router(snapshots_router)
app.include_router(stats_router)
app.include_router(files_router)
app.include_router(versions_router)
app.include_router(export_router)
app.include_router(agent_router)
app.include_router(editor_router)
app.include_router(chat_router)
app.include_router(voice_router)
app.include_router(skills_router)
app.include_router(public_skills_router)
app.include_router(inspirations_router)
app.include_router(materials_router)
app.include_router(admin_router)
app.include_router(referral_router)
app.include_router(persona_router)
app.include_router(subscription_router)
app.include_router(payments_router)
app.include_router(points_router)
app.include_router(agent_api_keys_router)
app.include_router(agent_api_router)
app.include_router(vector_search_router)
app.include_router(feedback_router)


# Root endpoint
@app.get("/")
def read_root():
    """Health check endpoint."""
    return {
        "message": f"Welcome to {os.getenv('APP_NAME', 'zenstory API')} - ZenStory写作助手",
        "version": os.getenv("APP_VERSION", "1.0.0"),
        "docs": "/docs",
    }


# Health check endpoint
@app.get("/health")
def health_check():
    """Liveness check for the Railway deploy healthcheck; touches no dependency."""
    return {"status": "healthy"}


@app.get("/health/ready")
async def readiness_check():
    """Readiness check: database and (when configured) Redis, with short timeouts."""
    ready, payload = await check_readiness()
    return JSONResponse(status_code=200 if ready else 503, content=payload)


# SKILL.md endpoint (public, no authentication required)
@app.get("/skill.md", response_class=PlainTextResponse)
async def get_skill_md(request: Request, lang: str = "zh"):
    """Return SKILL.md documentation for AI agents.

    Args:
        lang: Language for documentation ("zh" for Chinese, "en" for English)

    Returns:
        SKILL.md content as plain text with cache headers
    """
    content = skill_md_service.generate_skill_md(lang)
    etag = '"' + hashlib.md5(content.encode()).hexdigest()[:16] + '"'

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match == etag:
        from starlette.responses import Response
        return Response(status_code=304, headers={"ETag": etag})

    return PlainTextResponse(
        content=content,
        headers={
            "Cache-Control": "public, max-age=300",
            "ETag": etag,
        },
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
