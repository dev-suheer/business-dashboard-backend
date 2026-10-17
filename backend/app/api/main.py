from fastapi import APIRouter

from app.api.routes import food, login, reconcile, travel, utils

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(utils.router)
api_router.include_router(reconcile.router)
api_router.include_router(travel.router)
api_router.include_router(food.router)
