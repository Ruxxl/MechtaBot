import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from aiogram import Bot
from aiogram.enums import ParseMode

from services.db_service import get_known_autotest_subtask_keys, save_autotest_subtask_keys
from web.admin_handler import monitor

logger = logging.getLogger("bot.autotest_bugs")

# Подзадача считается новым багом, только если она создана в Jira не раньше
# этого окна. Старые задачи, которые перенесли под parent_key (сменили родителя
# / сконвертировали в подзадачу), тоже впервые появляются в списке подзадач,
# но в чат уходить не должны — их молча помечаем как известные.
FRESH_WINDOW = timedelta(hours=24)


async def check_autotest_bug_subtasks(
    bot: Bot,
    target_group_id: int,
    thread_id: int,
    jira_email: str,
    jira_token: str,
    jira_url: str,
    parent_key: str,
):
    """Мониторит подзадачи (баги, заведенные автотестами) родительской задачи
    parent_key в Jira. При появлении новых подзадач шлет уведомление в топик
    "Тестировщики".

    Уже отправленные подзадачи хранятся в БД (autotest_bug_subtasks), поэтому
    рестарт/редеплой бота не приводит к повторной отправке — уходят только
    по-настоящему новые. На самом первом запуске (пока в БД нет ни одной
    записи по parent_key) в чат уходят все подзадачи, которые уже существуют
    на этот момент, — дальше в БД остаются только их ключи."""
    logger.info(f"🔎 Проверяю подзадачи автотестов {parent_key}...")
    monitor.update_status("Autotest Bugs Monitor", "OK")

    base_url = jira_url.rstrip("/")
    auth = aiohttp.BasicAuth(jira_email, jira_token)

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.get(
                f"{base_url}/rest/api/3/issue/{parent_key}",
                params={"fields": "subtasks"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Ошибка получения задачи {parent_key}: {resp.status} — {body[:300]}")
                    monitor.update_status("Autotest Bugs Monitor", "ERROR")
                    return
                data = await resp.json()
    except Exception as e:
        logger.exception(f"Ошибка запроса подзадач {parent_key} из Jira: {e}")
        monitor.update_status("Autotest Bugs Monitor", "ERROR")
        return

    subtasks = data.get("fields", {}).get("subtasks", []) or []
    current_by_key = {sub["key"]: sub for sub in subtasks if sub.get("key")}

    known_keys = await get_known_autotest_subtask_keys(parent_key)
    if known_keys is None:
        logger.warning(
            f"БД недоступна, пропускаю проверку новых багов автотестов для {parent_key}; "
            "чтобы избежать повторного спама в чат."
        )
        monitor.update_status("Autotest Bugs Monitor", "ERROR")
        return

    is_first_run = len(known_keys) == 0
    new_keys = [key for key in current_by_key if key not in known_keys]

    if not new_keys:
        return

    created_by_key = await _fetch_created(base_url, auth, new_keys)
    if created_by_key is None:
        monitor.update_status("Autotest Bugs Monitor", "ERROR")
        return

    threshold = datetime.now(timezone.utc) - FRESH_WINDOW
    fresh_keys = [key for key in new_keys if created_by_key.get(key) and created_by_key[key] >= threshold]
    stale_count = len(new_keys) - len(fresh_keys)
    if stale_count:
        logger.info(
            f"Подзадач {parent_key}, созданных раньше {FRESH_WINDOW}, помечено как известные "
            f"без уведомления: {stale_count}"
        )
    if not fresh_keys:
        await save_autotest_subtask_keys(parent_key, new_keys)
        return

    logger.info(f"🐞 Найдено новых багов автотестов: {len(fresh_keys)}")

    # На первом запуске мониторинга для parent_key (в БД еще нет записей) все
    # текущие подзадачи считаются "новыми" — их может быть много, и сообщение
    # со всем списком легко упирается в лимит Telegram на длину сообщения.
    # В чат в этом случае уходит только последняя (самая свежая) подзадача,
    # остальные молча помечаются как известные, чтобы не спамить и не терять
    # их из виду при следующих проверках.
    if is_first_run and len(fresh_keys) > 1:
        logger.info(
            f"Первый запуск мониторинга для {parent_key}: из {len(fresh_keys)} "
            "свежих подзадач в чат уйдет только последняя."
        )
        notify_keys = [fresh_keys[-1]]
    else:
        notify_keys = fresh_keys

    lines = []
    for key in notify_keys:
        fields = current_by_key[key].get("fields", {}) or {}
        summary = fields.get("summary", "Без названия")
        status = (fields.get("status") or {}).get("name", "—")
        lines.append(f'• <a href="{base_url}/browse/{key}">{key}</a> — {summary} [{status}]')

    message_text = (
        "🐞 <b>Новые баги от автотестов</b>\n\n"
        + "\n".join(lines)
    )

    try:
        await bot.send_message(
            chat_id=target_group_id,
            message_thread_id=thread_id,
            text=message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info(
            f"✅ Уведомление о {len(notify_keys)} новых багах отправлено в группу {target_group_id} "
            f"(топик {thread_id}); всего новых подзадач помечено как известные: {len(new_keys)}"
        )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления о багах автотестов в TG: {e}")
        monitor.update_status("Autotest Bugs Monitor", "ERROR")
        return

    await save_autotest_subtask_keys(parent_key, new_keys)


async def _fetch_created(base_url: str, auth: aiohttp.BasicAuth, keys: list) -> Optional[dict]:
    """Дата создания задач keys в Jira: {key: datetime}. None — если Jira не
    ответила (тогда проверка повторится на следующем цикле)."""
    params = {"jql": f"key in ({','.join(keys)})", "fields": "created", "maxResults": len(keys)}
    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.get(f"{base_url}/rest/api/3/search/jql", params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Ошибка получения дат создания подзадач: {resp.status} — {body[:300]}")
                    return None
                data = await resp.json()
    except Exception as e:
        logger.exception(f"Ошибка запроса дат создания подзадач из Jira: {e}")
        return None

    result = {}
    for issue in data.get("issues", []):
        created = (issue.get("fields") or {}).get("created")
        if created:
            result[issue["key"]] = datetime.strptime(created, "%Y-%m-%dT%H:%M:%S.%f%z")
    return result
