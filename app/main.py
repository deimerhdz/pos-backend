
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.redis import token_blocklist
from app.core.db import initialize_database
from app.api.v1.admin.router import router as admin_router
from app.api.v1.auth.routes import auth_router
from app.api.v1.categories.router import router as categories_router
from app.api.v1.unit_measures.router import router as unit_measures_router
from app.api.v1.products.router import router as products_router
from app.api.v1.users.router import router as users_router
from app.api.v1.super_admin.router import router as super_admin_router
from app.api.v1.menu.router import router as menu_router
from app.api.v1.orders.router import router as orders_router
from app.api.v1.catalog.router import router as catalog_router
from app.api.v1.inventory.router import router as inventory_router
from app.api.v1.cash.router import router as cash_router
from app.api.v1.sales.router import router as sales_router
from app.api.v1.uploads.router import router as uploads_router
from app.api.v1.cart.router import router as cart_router
from app.api.v1.invoices.router import router as invoices_router
from app.api.v1.health.router import router as health_router
from app.api.v1.tenant.router import router as tenant_router
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    force=True 
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 🚀 STARTUP
    try:
        initialize_database()  # mejor aquí
        await token_blocklist.ping()
        print("✅ Redis conectado correctamente")
    except Exception as e:
        print("❌ Error en startup:", e)
        raise e  # opcional: detiene la app si falla Redis

    yield  # 👉 la app corre aquí

    # 🛑 SHUTDOWN
    try:
        await token_blocklist.close()
        print("🔌 Redis cerrado")
    except Exception as e:
        print("❌ Error cerrando Redis:", e)

def create_app()->FastAPI:
    app = FastAPI(title="Pos", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://([a-z0-9-]+\.)?localhost:4200$|^https://([a-z0-9-]+\.)?skeilopos\.com$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    initialize_database()

    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(categories_router, prefix="/api/v1")
    app.include_router(unit_measures_router, prefix="/api/v1")
    app.include_router(products_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(super_admin_router, prefix="/api/v1")
    app.include_router(menu_router, prefix="/api/v1")
    app.include_router(orders_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(inventory_router, prefix="/api/v1")
    app.include_router(cash_router, prefix="/api/v1")
    app.include_router(sales_router, prefix="/api/v1")
    app.include_router(uploads_router, prefix="/api/v1")
    app.include_router(cart_router, prefix="/api/v1")
    app.include_router(invoices_router, prefix="/api/v1")
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(tenant_router, prefix="/api/v1")
    return app

app = create_app()
