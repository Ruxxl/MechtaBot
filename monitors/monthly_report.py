import calendar
import logging
import os
from datetime import datetime, timedelta

import aiohttp
from aiogram import types
from dateutil import tz

from web.admin_handler import monitor

logger = logging.getLogger("bot.monthly_report")

TZ = tz.gettz("Asia/Almaty")

REPORT_PHOTO_PATH = "assets/monthryreport.jpg"

MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}

# Месяц, за который отчет уже отправлен (в формате "YYYY-MM").
# Хранится в памяти процесса — так же, как и другие "защелки" в проекте
# (processed_issues в code_review_handler.py, notified_versions в release_notifier.py).
# Если воркер перезапустится в тот же отчетный день после отправки — отчет
# теоретически может отправиться повторно, но это тот же риск, что и у
# остальных подобных мониторов в проекте.
_last_sent_month = None


# =======================
# Вспомогательные функции по датам
# =======================
def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _report_day(dt: datetime) -> int:
    """День, когда нужно отправлять отчет — 30-е число, либо последний день месяца, если он короче."""
    last_day_of_month = calendar.monthrange(dt.year, dt.month)[1]
    return min(30, last_day_of_month)


def _period_bounds(dt: datetime):
    """Период отчета для месяца, в котором лежит dt: с 1-го числа по 30-е число.
    Если в месяце меньше 30 дней (например, февраль) — до последнего дня месяца."""
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day_of_month = calendar.monthrange(dt.year, dt.month)[1]
    end_day = min(30, last_day_of_month)
    end = dt.replace(day=end_day, hour=23, minute=59, second=59, microsecond=0)
    return start, end


def _prev_month_reference(dt: datetime) -> datetime:
    """Возвращает дату внутри предыдущего месяца (последний день того месяца),
    чтобы передать её в _period_bounds() и получить границы предыдущего периода."""
    first_of_month = dt.replace(day=1)
    return first_of_month - timedelta(days=1)


# =======================
# Запрос данных в Jira
# =======================
async def _fetch_period_report(jira_email, jira_token, jira_project_key, jira_url, start: datetime, end: datetime):
    """
    Возвращает {"releases": N, "tasks": N, "bugs": N} по релизам, вышедшим в [start, end],
    где "bugs" — суммарное количество подзадач во всех задачах этих релизов
    (та же логика, что уже используется в release_notifier.py).
    Возвращает None при ошибке запроса к Jira.
    """
    base_url = jira_url.rstrip("/")
    auth = aiohttp.BasicAuth(jira_email, jira_token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    releases_count = 0
    total_tasks = 0
    total_bugs = 0

    try:
        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            # 1. Получаем все версии (релизы) проекта
            versions_url = f"{base_url}/rest/api/3/project/{jira_project_key}/versions"
            async with session.get(versions_url) as resp:
                if resp.status != 200:
                    logger.error(f"Не удалось получить версии проекта: {resp.status}")
                    return None
                versions = await resp.json()

            # 2. Оставляем только релизы, выпущенные в нашем периоде
            period_versions = []
            for v in versions:
                if not v.get("released"):
                    continue
                release_date_str = v.get("releaseDate")
                if not release_date_str:
                    continue
                try:
                    release_date = datetime.strptime(release_date_str, "%Y-%m-%d").replace(tzinfo=TZ)
                except ValueError:
                    continue
                if start <= release_date <= end:
                    period_versions.append(v)

            releases_count = len(period_versions)

            # 3. Для каждого релиза считаем задачи и подзадачи (баги)
            search_url = f"{base_url}/rest/api/3/search/jql"
            for v in period_versions:
                version_id = v.get("id")
                jql = f'project="{jira_project_key}" AND fixVersion={version_id}'
                payload = {
                    "jql": jql,
                    "fields": ["key", "subtasks"],
                    "maxResults": 200,
                }
                async with session.post(search_url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error(f"Ошибка поиска задач релиза {version_id}: {resp.status}")
                        continue
                    data = await resp.json()
                    issues = data.get("issues", [])
                    total_tasks += len(issues)
                    total_bugs += sum(len(i["fields"].get("subtasks", [])) for i in issues)

    except Exception as e:
        logger.exception(f"Ошибка при формировании месячного отчета: {e}")
        return None

    return {"releases": releases_count, "tasks": total_tasks, "bugs": total_bugs}


def _format_diff_line(current_bugs: int, prev_bugs: int) -> str:
    diff = current_bugs - prev_bugs
    if diff > 0:
        return f"📈 На {diff} больше, чем в прошлом месяце ({prev_bugs})"
    elif diff < 0:
        return f"📉 На {abs(diff)} меньше, чем в прошлом месяце ({prev_bugs})"
    return f"➖ Столько же, сколько и в прошлом месяце ({prev_bugs})"


# =======================
# Точка входа для фоновой задачи
# =======================
async def check_monthly_report(
    bot,
    target_group_id: int,
    target_thread_id: int,
    jira_email: str,
    jira_token: str,
    jira_project_key: str,
    jira_url: str,
):
    """
    Вызывается периодически (см. main.py через run_background_task).
    Срабатывает только в отчетный день месяца (30-е число, либо последний день
    месяца, если он короче 30 дней) и только один раз за месяц.

    Сравнение с предыдущим месяцем считается не из сохраненной истории, а заново —
    те же самые релизы/задачи/баги пересчитываются за период предыдущего месяца
    прямо из Jira, и просто сравниваются с текущим месяцем.
    """
    global _last_sent_month

    monitor.update_status("Monthly Report", "OK")
    now = datetime.now(TZ)
    report_day = _report_day(now)

    # Отправляем отчет в отчетный день, и (на случай сбоя Jira API) продолжаем
    # пытаться в последующие дни этого же месяца, пока не получится.
    if now.day < report_day:
        return

    month_key = _month_key(now)
    if _last_sent_month == month_key:
        return  # отчет за этот месяц уже отправлен

    # Текущий месяц
    start, end = _period_bounds(now)
    current = await _fetch_period_report(jira_email, jira_token, jira_project_key, jira_url, start, end)
    if current is None:
        logger.warning("Не удалось получить данные за текущий месяц — попробуем при следующей проверке")
        return

    # Предыдущий месяц — считаем заново, а не берем из истории
    prev_ref = _prev_month_reference(now)
    prev_start, prev_end = _period_bounds(prev_ref)
    previous = await _fetch_period_report(jira_email, jira_token, jira_project_key, jira_url, prev_start, prev_end)
    if previous is None:
        logger.warning("Не удалось получить данные за предыдущий месяц — попробуем при следующей проверке")
        return

    diff_line = _format_diff_line(current["bugs"], previous["bugs"])

    month_name = MONTHS_RU.get(now.month, now.strftime("%B"))
    text = (
        f"📊 <b>Итоги месяца — {month_name} {now.year}</b>\n\n"
        f"🚀 Релизов: <b>{current['releases']}</b>\n"
        f"📝 Задач: <b>{current['tasks']}</b>\n"
        f"🐞 Багов (подзадач в релизах): <b>{current['bugs']}</b>\n"
        f"{diff_line}"
    )

    try:
        if os.path.exists(REPORT_PHOTO_PATH):
            photo = types.FSInputFile(REPORT_PHOTO_PATH)
            await bot.send_photo(
                chat_id=target_group_id,
                message_thread_id=target_thread_id,
                photo=photo,
                caption=text,
                parse_mode="HTML",
            )
        else:
            logger.warning(f"Файл {REPORT_PHOTO_PATH} не найден — отправляю отчет без картинки")
            await bot.send_message(
                chat_id=target_group_id,
                message_thread_id=target_thread_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        logger.info(f"✅ Ежемесячный отчет за {month_key} отправлен")
    except Exception as e:
        logger.error(f"Ошибка отправки ежемесячного отчета: {e}")
        return  # не помечаем месяц как отправленный, если сообщение не дошло

    _last_sent_month = month_key
