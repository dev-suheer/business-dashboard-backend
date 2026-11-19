from fastapi import APIRouter

from app.api.routes import categories, login, runs, utils

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(utils.router)
api_router.include_router(categories.router)
api_router.include_router(categories.functions_router)
api_router.include_router(categories.standard_router)
api_router.include_router(runs.router)
