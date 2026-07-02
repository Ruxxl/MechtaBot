import html
import logging
import ssl
from typing import List, Optional

import aiohttp
from aiogram import Bot, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from web.admin_handler import monitor

logger = logging.getLogger("bot.team_tasks")

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# Статусы, которые не считаем "рабочими" и не показываем в списке задач/подзадач.
# Сравнение регистронезависимое (см. _is_excluded).
EXCLUDED_STATUSES = {"ожидание релиза", "готово"}

CALLBACK_PREFIX = "team_user:"


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _is_excluded(status_name: str) -> bool:
    return (status_name or "").strip().lower() in EXCLUDED_STATUSES


# =======================
# Jira API
# =======================
async def fetch_assignable_users(jira_config: dict) -> List[dict]:
    """Возвращает пользователей, которым можно назначать задачи в проекте
    (GET /rest/api/3/user/assignable/search), с постраничной подгрузкой."""
    base_url = jira_config["url"].rstrip("/")
    project = jira_config["project"]
    auth = aiohttp.BasicAuth(jira_config["email"], jira_config["token"])
    headers = {"Accept": "application/json"}

    users: List[dict] = []
    start_at = 0
    max_results = 50
    url = f"{base_url}/rest/api/3/user/assignable/search"

    async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
        while True:
            params = {"project": project, "startAt": start_at, "maxResults": max_results}
            async with session.get(url, params=params, ssl=SSL_CONTEXT) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Ошибка получения пользователей проекта: {resp.status} — {body[:300]}")
                    return users
                page = await resp.json()

            if not page:
                break

            for u in page:
                account_id = u.get("accountId")
                if not account_id:
                    continue
                # Пропускаем сервисные/бот-аккаунты Atlassian
                if u.get("accountType") == "app":
                    continue
                users.append({
                    "accountId": account_id,
                    "displayName": u.get("displayName", "Без имени"),
                })

            if len(page) < max_results:
                break
            start_at += max_results

    users.sort(key=lambda u: u["displayName"].lower())
    return users


async def fetch_user_sprint_tasks(jira_config: dict, account_id: str) -> List[dict]:
    """Задачи (без подзадач верхнего уровня), назначенные на пользователя,
    которые находятся в активном спринте и не в исключенных статусах.
    Подзадачи каждой задачи подтягиваются полем 'subtasks' в том же запросе."""
    base_url = jira_config["url"].rstrip("/")
    project = jira_config["project"]
    auth = aiohttp.BasicAuth(jira_config["email"], jira_config["token"])
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    excluded_jql = ", ".join(f'"{s}"' for s in ("Ожидание релиза", "Готово"))
    jql = (
        f'project="{project}" AND assignee="{account_id}" '
        f'AND sprint in openSprints() AND issuetype != Подзадача '
        f'AND status not in ({excluded_jql}) '
        f'ORDER BY updated DESC'
    )

    payload = {
        "jql": jql,
        "fields": ["key", "summary", "status", "subtasks"],
        "maxResults": 100,
    }

    search_url = f"{base_url}/rest/api/3/search/jql"
    async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
        async with session.post(search_url, json=payload, ssl=SSL_CONTEXT) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(f"Ошибка поиска задач пользователя: {resp.status} — {body[:300]}")
                return []
            data = await resp.json()

    return data.get("issues", [])


# =======================
# Форматирование
# =======================
def _users_keyboard(users: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for u in users:
        row.append(
            InlineKeyboardButton(
                text=u["displayName"],
                callback_data=f"{CALLBACK_PREFIX}{u['accountId']}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_user_tasks(jira_url: str, display_name: str, issues: List[dict]) -> str:
    jira_url = jira_url.rstrip("/")

    if not issues:
        return (
            f"📭 У пользователя <b>{_esc(display_name)}</b> нет задач в текущем "
            f"спринте (в рабочих статусах)."
        )

    lines = [f"📋 <b>Задачи {_esc(display_name)} в текущем спринте:</b>\n"]

    for issue in issues:
        key = issue.get("key")
        fields = issue.get("fields", {})
        summary = fields.get("summary", "Без названия")
        status = fields.get("status", {}).get("name", "?")
        url = f"{jira_url}/browse/{key}"

        lines.append(f"🔹 <a href='{url}'>{key} — {_esc(summary)}</a> — <b>{_esc(status)}</b>")

        for sub in fields.get("subtasks", []):
            sub_key = sub.get("key")
            sub_fields = sub.get("fields", {})
            sub_summary = sub_fields.get("summary", "Без названия")
            sub_status = sub_fields.get("status", {}).get("name", "?")

            if _is_excluded(sub_status):
                continue

            sub_url = f"{jira_url}/browse/{sub_key}"
            lines.append(
                f"    └ <a href='{sub_url}'>{sub_key} — {_esc(sub_summary)}</a> — <i>{_esc(sub_status)}</i>"
            )

        lines.append("")

    return "\n".join(lines).strip()


# =======================
# Регистрация хендлеров
# =======================
def register_team_tasks_handlers(dp, bot: Bot, jira_config: dict):

    @dp.message(F.text == "/team")
    async def show_team(message: Message):
        monitor.update_status("Team Tasks", "OK")
        loading = await message.reply("⏳ Загружаю список пользователей Jira...")
        try:
            users = await fetch_assignable_users(jira_config)
        except Exception as e:
            logger.exception(f"Ошибка получения пользователей: {e}")
            monitor.update_status("Team Tasks", f"ERROR: {e}")
            await loading.edit_text("❌ Не удалось получить список пользователей Jira.")
            return

        if not users:
            await loading.edit_text("🤷 Не нашел пользователей, доступных для назначения в проекте.")
            return

        await loading.edit_text(
            "👥 Выберите пользователя, чтобы посмотреть его задачи в спринте:",
            reply_markup=_users_keyboard(users),
        )

    @dp.callback_query(F.data.startswith(CALLBACK_PREFIX))
    async def show_user_tasks(callback: CallbackQuery):
        await callback.answer("🔍 Загружаю задачи...")
        account_id = callback.data[len(CALLBACK_PREFIX):]

        # Достаем отображаемое имя из текста нажатой кнопки (чтобы не делать лишний запрос к Jira)
        display_name: Optional[str] = None
        if callback.message and callback.message.reply_markup:
            for row in callback.message.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.callback_data == callback.data:
                        display_name = btn.text
        display_name = display_name or "пользователя"

        try:
            issues = await fetch_user_sprint_tasks(jira_config, account_id)
        except Exception as e:
            logger.exception(f"Ошибка получения задач пользователя: {e}")
            monitor.update_status("Team Tasks", f"ERROR: {e}")
            await callback.message.answer("❌ Не удалось получить задачи пользователя.")
            return

        text = _format_user_tasks(jira_config["url"], display_name, issues)
        await callback.message.answer(text, disable_web_page_preview=True)