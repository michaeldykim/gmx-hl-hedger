# src/app/main.py
from __future__ import annotations

import logging
from apscheduler.schedulers.background import BackgroundScheduler

from .config import SETTINGS
from .clients.gmx import GMXClient
from .clients.hyperliquid import HLClient
from .strategy.hedger import Hedger


# Logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
summary = logging.getLogger("summary")


def main() -> None:
    # Instantiate clients
    gmx = GMXClient()
    hl = HLClient()
    hedger = Hedger(gmx_client=gmx, hl_client=hl)

    poll = SETTINGS.poll_interval_sec
    logger.info(
        "Starting GMX v2 + Hyperliquid hedger | DRY_RUN=%s | poll=%ss",
        SETTINGS.dry_run,
        poll,
    )

    # Optional: one-time health check at boot
    try:
        hc = hl.health_check()
        summary.info(
            "HL health ok | wallet=%s | ETH idx=%s | sz_dec=%s | mid=%s",
            hc.get("wallet"),
            hc.get("eth_idx"),
            hc.get("eth_sz_dec"),
            hc.get("mid_price"),
        )
    except Exception as e:
        logger.warning("HL health check failed at startup: %s", e)

    def task():
        try:
            rep = hedger.reconcile(dry_run=SETTINGS.dry_run)

            # Prefer adjusted pool long ETH if present, else raw, else 0.0
            pool_long_display = getattr(
                rep,
                "pool_long_eth_adj",
                getattr(rep, "pool_long_eth_raw", 0.0),
            )

            # Concise summary line
            summary.info(
                "target_short=%+.4f, current=%+.4f, trade=%+.4f, pool_long_eth=%.4f, share=%.6f",
                rep.target_eth_short,
                rep.current_eth_pos,
                rep.delta_to_trade,
                pool_long_display,
                rep.gm_share,
            )
        except Exception as e:
            logger.error("Rebalance failed: %s", e, exc_info=True)

    # Schedule
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(task, trigger="interval", seconds=poll, id="task")
    sched.start()

    # Run once immediately
    task()

    # Keep process alive
    try:
        import time
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()


if __name__ == "__main__":
    main()
