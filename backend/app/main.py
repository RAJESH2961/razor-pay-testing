from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.payments.router import router as payments_router
from app.modules.payments.service import PaymentError
from app.modules.users.router import router as users_router

app = FastAPI(
    title="EdTech Payments",
    docs_url=None if settings.APP_ENV == "production" else "/docs",
    redoc_url=None,
    openapi_url=None if settings.APP_ENV == "production" else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(PaymentError)
def handle_payment_error(_: Request, exc: PaymentError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


api = APIRouter(prefix="/api/v1")
api.include_router(users_router)
api.include_router(payments_router)
app.include_router(api)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
