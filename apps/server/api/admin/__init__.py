"""
Admin API package.

This package contains modularized admin API endpoints organized by domain.
All endpoints are exposed under the /api/admin prefix.
"""
from fastapi import APIRouter

from .audit import router as audit_router
from .checkin import router as checkin_router
from .codes import router as codes_router
from .dashboard import router as dashboard_router
from .feedback import router as feedback_router
from .inspirations import router as inspirations_router
from .payment_orders import router as payment_orders_router
from .plans import router as plans_router
from .points import router as points_router
from .prompts import router as prompts_router
from .quotas import router as quotas_router
from .referrals import router as referrals_router
from .skills import router as skills_router
from .subscriptions import router as subscriptions_router
from .usage import router as usage_router
from .users import router as users_router

# Create main admin router with the /api/admin prefix
router = APIRouter(prefix="/api/admin", tags=["admin"])

# Include all sub-module routers (they inherit the prefix from parent)
router.include_router(users_router)
router.include_router(prompts_router)
router.include_router(skills_router)
router.include_router(inspirations_router)
router.include_router(feedback_router)
router.include_router(plans_router)
router.include_router(payment_orders_router)
router.include_router(codes_router)
router.include_router(subscriptions_router)
router.include_router(dashboard_router)
router.include_router(audit_router)
router.include_router(points_router)
router.include_router(checkin_router)
router.include_router(referrals_router)
router.include_router(quotas_router)
router.include_router(usage_router)

__all__ = ["router"]
