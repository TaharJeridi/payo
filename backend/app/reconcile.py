"""Run payment reconciliation once, or forever with a fixed interval.

python -m app.reconcile            # one pass, exit code 1 if Stripe errors occurred
python -m app.reconcile --loop     # every RECONCILE_INTERVAL_SECONDS (docker compose service)
"""

import argparse
import logging
import time
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db import models  # noqa: F401
from app.db.session import get_session_factory
from app.integrations.stripe_gateway import StripeGateway
from app.payments.reconciliation import ReconcileOutcome, Reconciler

logger = logging.getLogger("app.reconcile")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.reconcile")
    parser.add_argument("--loop", action="store_true", help="keep running")
    parser.add_argument("--interval-seconds", type=int, default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    reconciler = Reconciler(get_session_factory(), StripeGateway(settings), settings)
    interval = args.interval_seconds or settings.reconcile_interval_seconds

    while True:
        report = reconciler.run_once()
        logger.info("reconciliation_pass %s", report.summary())
        if not args.loop:
            return 1 if report.outcomes.get(ReconcileOutcome.ERROR) else 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
