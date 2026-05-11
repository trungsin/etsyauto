"""APScheduler BackgroundScheduler — manages periodic background jobs."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

# Module-level singleton so main.py can start/stop it in lifespan
scheduler = BackgroundScheduler(timezone="UTC")


def _register_jobs() -> None:
    """Register all recurring jobs. Called once during startup."""
    from app.config import settings as _settings  # local import; singleton
    from app.workers.title_optimizer import run_title_optimizer_job  # local import avoids circular deps
    from app.workers.mockup_pipeline import run_mockup_pipeline_job  # local import avoids circular deps

    scheduler.add_job(
        run_title_optimizer_job,
        trigger="interval",
        seconds=30,
        id="title_optimizer",
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    logger.info("Registered job: title_optimizer (interval=30s)")

    scheduler.add_job(
        run_mockup_pipeline_job,
        trigger="interval",
        seconds=60,
        id="mockup_pipeline",
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    logger.info("Registered job: mockup_pipeline (interval=60s)")

    # v0.8.1 hotfix — review-sync jobs gated behind NOTION_SYNC_ENABLED.
    # Notion deprecated by default in v0.8; flip flag true to reactivate.
    if _settings.notion_sync_enabled:
        from app.workers.notion_sync import sync_to_notion, pull_approvals  # local import avoids circular deps

        scheduler.add_job(
            sync_to_notion,
            trigger="interval",
            seconds=30,
            id="sync_to_notion",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        logger.info("Registered job: sync_to_notion (interval=30s)")

        scheduler.add_job(
            pull_approvals,
            trigger="interval",
            seconds=60,
            id="pull_approvals",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        logger.info("Registered job: pull_approvals (interval=60s)")
    else:
        logger.info(
            "Notion sync jobs SKIPPED (NOTION_SYNC_ENABLED=false, v0.8 default). "
            "Set NOTION_SYNC_ENABLED=true in .env to re-enable."
        )

    from app.workers.etsy_uploader import run_etsy_uploader_job  # local import avoids circular deps

    scheduler.add_job(
        run_etsy_uploader_job,
        trigger="interval",
        seconds=600,
        id="etsy_uploader",
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    logger.info("Registered job: etsy_uploader (interval=600s)")

    # v0.8.0 — idea mining job (guarded by feature flag)
    if _settings.idea_mining_enabled:
        from app.services.idea_miner_service import run_all as run_idea_mining  # local import avoids circular deps

        scheduler.add_job(
            run_idea_mining,
            trigger="interval",
            seconds=_settings.etsy_miner_interval_sec,
            id="idea_mining_job",
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        logger.info(
            "Registered job: idea_mining_job (interval=%ds)", _settings.etsy_miner_interval_sec
        )
    else:
        logger.info("idea_mining_job skipped — idea_mining_enabled=False")


def start() -> None:
    """Start the scheduler; called from FastAPI lifespan startup."""
    if not scheduler.running:
        _register_jobs()
        scheduler.start()
        logger.info("Scheduler started")


def shutdown() -> None:
    """Gracefully shut down the scheduler; called from FastAPI lifespan shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def is_running() -> bool:
    """Return True if the scheduler is currently running."""
    return scheduler.running
