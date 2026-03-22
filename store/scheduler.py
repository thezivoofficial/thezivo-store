"""
APScheduler setup — runs background jobs inside the Django process.
Started once from StoreConfig.ready() in apps.py.
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def send_abandonment_emails_job():
    """Wrapper called by the scheduler every hour."""
    try:
        from django.core.management import call_command
        call_command("send_abandonment_emails")
    except Exception as e:
        logger.error(f"[Scheduler] send_abandonment_emails failed: {e}")


def start():
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

    scheduler.add_job(
        send_abandonment_emails_job,
        trigger=CronTrigger(minute=0),   # top of every hour
        id="send_abandonment_emails",
        max_instances=1,
        replace_existing=True,
    )

    logger.info("[Scheduler] Starting — abandonment emails every hour.")
    scheduler.start()
