from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.api.v1.imports import router as imports_router
from app.api.v1.leads import router as leads_router
from app.api.v1.me import router as me_router
from app.api.v1.suppressions import router as suppressions_router
from app.api.v1.templates import router as templates_router
from app.api.v1.workspaces import router as workspaces_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(me_router)
api_router.include_router(workspaces_router, prefix="/workspaces", tags=["workspaces"])
api_router.include_router(
    leads_router,
    prefix="/workspaces/{workspace_id}",
    tags=["leads"],
)
api_router.include_router(
    templates_router,
    prefix="/workspaces/{workspace_id}",
    tags=["templates"],
)
api_router.include_router(
    imports_router,
    prefix="/workspaces/{workspace_id}",
    tags=["imports"],
)
api_router.include_router(
    suppressions_router,
    prefix="/workspaces/{workspace_id}",
    tags=["suppressions"],
)
