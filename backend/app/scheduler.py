"""APScheduler BackgroundScheduler — manages periodic background jobs."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

# Module-level singleton so main.py can start/stop it in lifespan
scheduler = BackgroundScheduler(timezone="UTC")


def _register_jobs() -> None:
    """Register all recurring jobs. Called once during startup."""
    from app.workers.title_optimizer import run_title_optimizer_job  # local import avoids circular deps
    from app.workers.mockup_pipeline import run_mockup_pipeline_job  # local import avoids circular deps
    from app.workers.notion_sync import sync_to_notion, pull_approvals  # local import avoids circular deps

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
