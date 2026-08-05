import logging

from services.autotest_service import AutotestRunsService
from web.admin_handler import monitor

logger = logging.getLogger("bot.autotest_runs")


async def check_autotest_runs(service: AutotestRunsService):
    """Опрашивает GitHub Actions по прогонам автотестов MechtaATest (Cypress)
    и обновляет кэш service — тот же объект передан в Mini App API как
    services["autotests"] (см. main.py, web/miniapp_api.py).

    Вызывается по таймеру через run_background_task (main.py), как и остальные
    фоновые мониторы бота."""
    try:
        await service.refresh()
        monitor.update_status("Autotest Runs Monitor", "OK")
    except Exception as e:
        logger.exception(f"Ошибка опроса прогонов автотестов: {e}")
        monitor.update_status("Autotest Runs Monitor", "ERROR")
