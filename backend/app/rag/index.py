"""Index PayFlow data into Qdrant.

python -m app.rag.index            # one pass
python -m app.rag.index --loop     # every RAG_INDEX_INTERVAL_SECONDS (docker compose service)
"""

import argparse
import logging
import time
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.errors import RagUnavailable
from app.core.logging import configure_logging
from app.db import models  # noqa: F401
from app.rag.dependencies import build_components, build_indexer

logger = logging.getLogger("app.rag.index")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.rag.index")
    parser.add_argument("--loop", action="store_true", help="keep running")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    indexer = build_indexer(build_components(settings), settings)

    while True:
        try:
            report = indexer.run()
            logger.info("rag_index_pass %s", report.summary())
            failed = False
        except RagUnavailable as exc:
            logger.warning("rag_index_unavailable error=%s", exc.message)
            failed = True
        if not args.loop:
            return 1 if failed else 0
        time.sleep(settings.rag_index_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
