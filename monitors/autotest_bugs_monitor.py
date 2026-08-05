import logging

import aiohttp
from aiogram import Bot
from aiogram.enums import ParseMode

from services.db_service import get_known_autotest_subtask_keys, save_autotest_subtask_keys
from web.admin_handler import monitor

logger = logging.getLogger("bot.autotest_bugs")


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
    new_keys = [key for key in current_by_key if key not in known_keys]

    if not new_keys:
        return

    logger.info(f"🐞 Найдено новых багов автотестов: {len(new_keys)}")

    lines = []
    for key in new_keys:
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
            f"✅ Уведомление о {len(new_keys)} новых багах отправлено в группу {target_group_id} (топик {thread_id})"
        )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления о багах автотестов в TG: {e}")
        monitor.update_status("Autotest Bugs Monitor", "ERROR")
        return

    await save_autotest_subtask_keys(parent_key, new_keys)
