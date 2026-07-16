"""Versioned API router."""

from fastapi import APIRouter

from app.api import auth, github_webhooks, health, installations

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(installations.router, prefix="/installations", tags=["installations"])
api_router.include_router(github_webhooks.router, prefix="/webhooks", tags=["webhooks"])
