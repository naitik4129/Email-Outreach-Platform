from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.api.v1.me import router as me_router
from app.api.v1.workspaces import router as workspaces_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(me_router)
api_router.include_router(workspaces_router, prefix="/workspaces", tags=["workspaces"])

