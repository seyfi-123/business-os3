from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api import auth, tenants, intelligence, ai
from app.api import sales, inventory, products, crm, suppliers, finance, audit

app = FastAPI(title=settings.APP_NAME, description=settings.APP_TAGLINE_TJ)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def root():
    return {"app": settings.APP_NAME,
            "tagline_tj": settings.APP_TAGLINE_TJ, "status": "ok"}


for r in (auth, tenants, products, sales, inventory, crm, suppliers,
          finance, intelligence, ai, audit):
    app.include_router(r.router)
