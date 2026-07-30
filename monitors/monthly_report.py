import calendar
import logging
import os
from datetime import datetime, timedelta

import aiohttp
from aiogram import Bot, F, types
from aiogram.types import Message
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

# Типы задач, которые считаем в строке "Задачи в спринтах месяца" (см. типы
# проекта в Jira). "Эпик" и "Подзадача" сознательно не учитываются: эпик —
# контейнер для группы задач, подзадачи уже считаются отдельно (поле "bugs").
SPRINT_TASK_TYPES = ["Task DEV", "Баг", "Task BA", "Улучшение", "Задание"]

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
# Спринты и задачи внутри них (Jira Agile API)
# =======================
async def _fetch_project_board_ids(session: aiohttp.ClientSession, base_url: str, jira_project_key: str) -> list:
    """Возвращает ID всех досок (scrum/kanban), привязанных к проекту."""
    boards_url = f"{base_url}/rest/agile/1.0/board"
    board_ids = []
    start_at = 0

    while True:
        params = {"projectKeyOrId": jira_project_key, "startAt": start_at, "maxResults": 50}
        try:
            async with session.get(boards_url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"Ошибка получения досок проекта: {resp.status}")
                    break
                data = await resp.json()
        except Exception as e:
            logger.error(f"Ошибка запроса досок проекта: {e}")
            break

        board_ids.extend(b["id"] for b in data.get("values", []) if "id" in b)

        if data.get("isLast", True):
            break
        start_at += 50

    return board_ids


async def _fetch_sprint_ids_in_period(session: aiohttp.ClientSession, base_url: str, jira_project_key: str, start: datetime, end: datetime) -> list:
    """Возвращает ID всех спринтов проекта (по всем его доскам), которые
    ПЕРЕСЕКАЮТСЯ с периодом [start, end] — то есть хотя бы часть дат спринта
    (от startDate до endDate) попадает в отчетный месяц. Это включает и
    спринты, начавшиеся в прошлом месяце, но завершившиеся в этом (например
    спринт с 15 июня по 2 июля учитывается в отчете за июль). Спринт может
    быть в состоянии active/closed/future — берем все."""
    board_ids = await _fetch_project_board_ids(session, base_url, jira_project_key)
    if not board_ids:
        logger.warning(f"Не найдено ни одной доски для проекта {jira_project_key} — спринты не будут учтены")
        return []

    sprint_ids = set()

    for board_id in board_ids:
        sprints_url = f"{base_url}/rest/agile/1.0/board/{board_id}/sprint"
        start_at = 0

        while True:
            params = {"startAt": start_at, "maxResults": 50, "state": "active,closed,future"}
            try:
                async with session.get(sprints_url, params=params) as resp:
                    if resp.status != 200:
                        # Не у каждой доски (например kanban без спринтов) есть этот эндпоинт — пропускаем молча
                        break
                    data = await resp.json()
            except Exception as e:
                logger.error(f"Ошибка запроса спринтов доски {board_id}: {e}")
                break

            for sprint in data.get("values", []):
                sprint_start_str = sprint.get("startDate")
                sprint_end_str = sprint.get("endDate")
                if not sprint_start_str:
                    continue
                try:
                    sprint_start = datetime.fromisoformat(sprint_start_str.replace("Z", "+00:00")).astimezone(TZ)
                except ValueError:
                    continue

                # endDate может отсутствовать у активных/future спринтов без
                # заданной даты завершения — в этом случае считаем, что спринт
                # длится "по настоящее время" (не исключаем по верхней границе).
                if sprint_end_str:
                    try:
                        sprint_end = datetime.fromisoformat(sprint_end_str.replace("Z", "+00:00")).astimezone(TZ)
                    except ValueError:
                        sprint_end = sprint_start
                else:
                    sprint_end = sprint_start

                # Пересечение диапазонов: спринт учитывается, если его период
                # [sprint_start, sprint_end] пересекается с отчетным периодом
                # [start, end] — то есть спринт "затрагивает" этот месяц, даже
                # если стартовал раньше или закончится позже.
                if sprint_start <= end and sprint_end >= start:
                    sprint_ids.add(sprint["id"])

            if data.get("isLast", True):
                break
            start_at += 50

    return list(sprint_ids)


async def _fetch_sprint_tasks_count(session: aiohttp.ClientSession, base_url: str, jira_project_key: str, sprint_ids: list) -> int:
    """Считает количество задач типов SPRINT_TASK_TYPES, которые лежат в
    указанных спринтах (JQL 'sprint in (...)' — задача считается один раз,
    даже если попадала в несколько спринтов из списка). Учитывает пагинацию
    через nextPageToken — без нее результат может тихо обрезаться."""
    if not sprint_ids:
        return 0

    types_jql = ", ".join(f'"{t}"' for t in SPRINT_TASK_TYPES)
    sprints_jql = ", ".join(str(s) for s in sprint_ids)
    jql = f'project="{jira_project_key}" AND issuetype in ({types_jql}) AND sprint in ({sprints_jql})'

    search_url = f"{base_url}/rest/api/3/search/jql"
    total = 0
    next_page_token = None

    try:
        while True:
            payload = {
                "jql": jql,
                "fields": ["key"],
                "maxResults": 100,
            }
            if next_page_token:
                payload["nextPageToken"] = next_page_token

            async with session.post(search_url, json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"Ошибка поиска задач по спринтам: {resp.status}")
                    return total
                data = await resp.json()

            total += len(data.get("issues", []))
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        return total
    except Exception as e:
        logger.error(f"Ошибка запроса задач по спринтам: {e}")
        return total


async def _fetch_tasks_for_month_report(jira_email, jira_token, jira_project_key, jira_url, start: datetime, end: datetime):
    """
    Fetches a list of tasks (key, summary, status) for the given period.
    """
    base_url = jira_url.rstrip("/")
    auth = aiohttp.BasicAuth(jira_email, jira_token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    # JQL для получения задач, которые были обновлены в течение месяца.
    # Можно изменить на 'created >= ...' если нужны задачи, созданные в этот период.
    jql = (
        f'project="{jira_project_key}" AND updated >= "{start.strftime("%Y-%m-%d %H:%M")}" '
        f'AND updated <= "{end.strftime("%Y-%m-%d %H:%M")}" ORDER BY updated DESC'
    )

    payload = {
        "jql": jql,
        "fields": ["key", "summary", "status"],
        "maxResults": 50, # Ограничиваем количество задач для Mini App
    }

    task_list = []
    try:
        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            async with session.post(f"{base_url}/rest/api/3/search/jql", json=payload) as resp:
                if resp.status != 200:
                    logger.error(f"Error fetching tasks for month report: {resp.status}")
                    return []
                data = await resp.json()
                for issue in data.get("issues", []):
                    task_list.append({"key": issue["key"], "summary": issue["fields"]["summary"], "status": issue["fields"]["status"]["name"]})
    except Exception as e:
        logger.error(f"Exception fetching tasks for month report: {e}")
        return []
    return task_list


# =======================
# Запрос данных в Jira
# =======================
async def _fetch_period_report(jira_email, jira_token, jira_project_key, jira_url, start: datetime, end: datetime):
    """
    Возвращает {"releases": N, "tasks": N, "bugs": N, "sprint_tasks": N} по релизам,
    вышедшим в [start, end], где "bugs" — суммарное количество подзадач во всех
    задачах этих релизов (та же логика, что уже используется в release_notifier.py),
    а "sprint_tasks" — количество задач типов SPRINT_TASK_TYPES в спринтах,
    ПЕРЕСЕКАЮЩИХСЯ с этим периодом (независимо от релизов и статуса задачи).
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

            # 4. Задачи в спринтах, пересекающихся с периодом (см. SPRINT_TASK_TYPES)
            sprint_ids = await _fetch_sprint_ids_in_period(session, base_url, jira_project_key, start, end)
            sprint_tasks = await _fetch_sprint_tasks_count(session, base_url, jira_project_key, sprint_ids)

    except Exception as e:
        logger.exception(f"Ошибка при формировании месячного отчета: {e}")
        return None

    return {
        "releases": releases_count,
        "tasks": total_tasks,
        "bugs": total_bugs,
        "sprint_tasks": sprint_tasks,
    }


def _format_diff_line(current_value: int, prev_value: int, label: str = "прошлым месяцем") -> str:
    diff = current_value - prev_value
    if diff > 0:
        return f"📈 На {diff} больше, чем {label} ({prev_value})"
    elif diff < 0:
        return f"📉 На {abs(diff)} меньше, чем {label} ({prev_value})"
    return f"➖ Столько же, сколько и {label} ({prev_value})"


# =======================
# Точка входа для фоновой задачи (30-е число, весь месяц целиком)
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
    те же самые релизы/задачи/баги/задачи спринтов пересчитываются за период
    предыдущего месяца прямо из Jira, и просто сравниваются с текущим месяцем.
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

    bugs_diff_line = _format_diff_line(current["bugs"], previous["bugs"])
    sprint_diff_line = _format_diff_line(current["sprint_tasks"], previous["sprint_tasks"])

    month_name = MONTHS_RU.get(now.month, now.strftime("%B"))
    text = (
        f"📊 <b>Итоги месяца — {month_name} {now.year}</b>\n\n"
        f"🚀 Релизов: <b>{current['releases']}</b>\n"
        f"📝 Задач: <b>{current['tasks']}</b>\n"
        f"🐞 Багов (подзадач в релизах): <b>{current['bugs']}</b>\n"
        f"{bugs_diff_line}\n\n"
        f"🆕 Задачи в спринтах месяца: <b>{current['sprint_tasks']}</b>\n"
        f"<i>(Task DEV, Баг, Task BA, Улучшение, Задание)</i>\n"
        f"{sprint_diff_line}"
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


# =======================
# Отчет "по запросу" — с 1-го числа текущего месяца до момента запуска команды
# =======================
async def build_on_demand_report(
    jira_email: str,
    jira_token: str,
    jira_project_key: str,
    jira_url: str,
):
    """
    В отличие от check_monthly_report (который ждет 30-е число и берет период
    целиком 1-30), эта функция всегда считает период с 1-го числа текущего
    месяца ПО ТЕКУЩИЙ МОМЕНТ — то есть "живой" срез на момент вызова команды.

    Для сравнения берется тот же по длине отрезок прошлого месяца (1-е число —
    тот же день/время), а не месяц целиком — иначе сравнение, например, 15 дней
    текущего месяца с 30 днями прошлого было бы некорректным.

    Возвращает готовый HTML-текст сообщения, либо None при ошибке запроса к Jira.
    """
    now = datetime.now(TZ)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = now

    current = await _fetch_period_report(jira_email, jira_token, jira_project_key, jira_url, start, end)
    if current is None:
        return None
    
    # Тот же по длине отрезок предыдущего месяца: с 1-го числа до того же
    # дня/времени, но не дальше последнего дня прошлого месяца.
    prev_ref = _prev_month_reference(now)
    prev_start = prev_ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_day_prev = calendar.monthrange(prev_ref.year, prev_ref.month)[1]
    end_day = min(now.day, last_day_prev)
    prev_end = prev_ref.replace(day=end_day, hour=now.hour, minute=now.minute, second=now.second, microsecond=0)

    previous = await _fetch_period_report(jira_email, jira_token, jira_project_key, jira_url, prev_start, prev_end)
    if previous is None:
        return None
    
    task_list = await _fetch_tasks_for_month_report(jira_email, jira_token, jira_project_key, jira_url, start, end)

    return {
        "current": current,
        "previous": previous,
        "task_list": task_list,
        "now": now
    }


# =======================
# Команда /monthreport — отчет по запросу в любой момент
# =======================
def register_monthly_report_handlers(dp, bot: Bot, jira_config: dict):

    @dp.message(F.text.startswith("/monthreport"))
    async def monthreport_command(message: Message):
        monitor.update_status("Monthly Report", "OK")
        loading = await message.reply("⏳ Формирую отчет с начала месяца по текущий момент...")

        report_data = await build_on_demand_report(
            jira_config['email'],
            jira_config['token'],
            jira_config['project'],
            jira_config['url'],
        )
        if report_data is None:
            monitor.update_status("Monthly Report", "ERROR")
            await loading.edit_text("❌ Не удалось получить данные из Jira для отчета. Попробуй позже.")
            return

        current = report_data["current"]
        previous = report_data["previous"]
        now = report_data["now"]
        
        # Calculate end_day for the previous month's comparison text
        prev_ref = _prev_month_reference(now)
        end_day = min(now.day, calendar.monthrange(prev_ref.year, prev_ref.month)[1])

        bugs_diff_line = _format_diff_line(current["bugs"], previous["bugs"])
        sprint_diff_line = _format_diff_line(current["sprint_tasks"], previous["sprint_tasks"])
        month_name = MONTHS_RU.get(now.month, now.strftime("%B"))

        text = (
            f"📊 <b>Отчет с начала месяца — {month_name} {now.year}</b>\n"
            f"🗓 Период: 01.{now.month:02d}.{now.year} — {now.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"🚀 Релизов: <b>{current['releases']}</b>\n"
            f"📝 Задач: <b>{current['tasks']}</b>\n"
            f"🐞 Багов (подзадач в релизах): <b>{current['bugs']}</b>\n"
            f"{bugs_diff_line}\n\n"
            f"🆕 Задачи в спринтах месяца: <b>{current['sprint_tasks']}</b>\n"
            f"<i>(Task DEV, Баг, Task BA, Улучшение, Задание)</i>\n"
            f"{sprint_diff_line}\n\n"
            f"<i>Сравнение — с тем же отрезком прошлого месяца (01–{end_day:02d} число)</i>"
        )
        await loading.edit_text(text)