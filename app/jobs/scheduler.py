from apscheduler.schedulers.background import BackgroundScheduler

from app.jobs import tasks

scheduler = BackgroundScheduler(timezone="UTC")


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(tasks.cashback_job, "interval", hours=1, id="cashback", replace_existing=True)
    scheduler.add_job(tasks.withdrawal_job, "interval", minutes=5, id="withdrawals", replace_existing=True)
    scheduler.add_job(tasks.request_expiry_job, "interval", hours=1, id="request_expiry", replace_existing=True)
    scheduler.add_job(tasks.cleanup_job, "cron", hour=3, minute=0, id="cleanup", replace_existing=True)
    scheduler.add_job(tasks.reconciliation_job, "cron", hour=2, minute=0, id="reconciliation", replace_existing=True)
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
