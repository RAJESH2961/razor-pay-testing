"""Compare recent unfinished payments with Razorpay. Schedule every 15 minutes."""
from loguru import logger

from app.core.database import SessionLocal
from app.modules.payments import service
from app.modules.payments.gateway import get_gateway


def main() -> None:
    with SessionLocal() as db:
        logger.info("Reconcile finished: {}", service.reconcile(db, get_gateway()))


if __name__ == "__main__":
    main()
