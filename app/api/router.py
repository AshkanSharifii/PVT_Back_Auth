from fastapi import APIRouter

from app.api.routes import admin, auth, health, owner, subscriptions

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(owner.router)
api_router.include_router(subscriptions.router)
