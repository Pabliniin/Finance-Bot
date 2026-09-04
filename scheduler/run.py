"""Entry point del contenedor `scheduler`: proceso persistente con APScheduler
que dispara daily_job e intraday_job segun config.yaml. Es el comando de
docker-compose.yml para el servicio `scheduler`.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.discord_log_handler import install as install_discord_alert_handler
from config.loader import load_config
from scheduler import daily_job, intraday_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("scheduler.log", encoding="utf-8")],
)
logger = logging.getLogger("scheduler.run")


def _daily_trigger(hhmm: str) -> CronTrigger:
    hour, minute = (int(p) for p in hhmm.split(":"))
    return CronTrigger(hour=hour, minute=minute, timezone="UTC")


def _run_daily_job() -> None:
    try:
        result = daily_job.run()
        logger.info("daily_job ok: %s", result)
    except Exception:
        logger.exception("daily_job fallo")


def _run_intraday_job() -> None:
    try:
        result = intraday_job.run()
        logger.info("intraday_job ok: %s", result)
    except Exception:
        logger.exception("intraday_job fallo")


def main() -> None:
    install_discord_alert_handler()  # se resuelve aqui, no al importar: ERROR/CRITICAL -> canal de alertas
    cfg = load_config()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(_run_daily_job, _daily_trigger(cfg.schedule.daily_job_utc), id="daily_job")
    scheduler.add_job(
        _run_intraday_job,
        CronTrigger.from_crontab(cfg.schedule.intraday_job_cron, timezone="UTC"),
        id="intraday_job",
    )
    logger.info(
        "scheduler arrancado: daily_job a las %s UTC, intraday_job con cron '%s'",
        cfg.schedule.daily_job_utc, cfg.schedule.intraday_job_cron,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
