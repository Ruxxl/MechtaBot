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

# Статусы "Готово" и "Ожидает релиза" (в любых вариациях формулировки) не
# считаем "рабочими" — см. _is_excluded ниже, там гибкое сравнение.
CALLBACK_PREFIX = "team_user:"


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _is_excluded(status_name: str) -> bool:
    """Проверяет, нужно ли скрыть задачу/подзадачу с данным статусом.
    Сравнение по вхождению подстроки, а не точное совпадение — потому что
    в Jira статус может называться "Ожидает релиза", "Ожидание релиза" и т.п.,
    и точное сравнение легко "промахивается" мимо реальной формулировки."""
    name = (status_name or "").strip().lower()
    if not name:
        return False
    if "готов" in name or name in {"done", "closed", "закрыто"}:
        return True
    if "ожида" in name and "релиз" in name:
        return True
    return False


# Максимальная длина названия задачи/подзадачи в выводе. Длинные названия
# обрезаются по границе слова — иначе перенос строки в Telegram "съезжает"
# и портит дерево (├─/└─) и иконки статусов.
TASK_SUMMARY_MAX = 70
SUBTASK_SUMMARY_MAX = 60


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return f"{cut}…"


# Иконки по статусу — сопоставление по вхождению подстроки (регистронезависимо),
# т.к. в разных проектах названия статусов могут немного отличаться.
# Порядок важен: проверяются сверху вниз, первое совпадение побеждает.
STATUS_ICON_RULES = [
    ("код ревью", "🔍"),
    ("ревью", "🔍"),
    ("тестир", "🧪"),
    ("qa", "🧪"),
    ("проверк", "👀"),
    ("в работе", "🔧"),
    ("progress", "🔧"),
    ("блок", "⛔"),
    ("block", "⛔"),
    ("открыт", "🆕"),
    ("to do", "🆕"),
    ("todo", "🆕"),
    ("backlog", "🗂"),
]
DEFAULT_STATUS_ICON = "⚪"


def _status_icon(status_name: str) -> str:
    name = (status_name or "").strip().lower()
    for needle, icon in STATUS_ICON_RULES:
        if needle in name:
            return icon
    return DEFAULT_STATUS_ICON


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
    которые находятся в активном спринте. Фильтрация по статусу ("Готово",
    "Ожидает релиза") делается на стороне Python через _is_excluded —
    не через JQL, т.к. точная формулировка статуса в разных проектах может
    отличаться."""
    base_url = jira_config["url"].rstrip("/")
    project = jira_config["project"]
    auth = aiohttp.BasicAuth(jira_config["email"], jira_config["token"])
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    jql = (
        f'project="{project}" AND assignee="{account_id}" '
        f'AND sprint in openSprints() AND issuetype != Подзадача '
        f'ORDER BY updated DESC'
    )

    payload = {
        "jql": jql,
        "fields": ["key", "summary", "status"],
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

    issues = data.get("issues", [])
    return [i for i in issues if not _is_excluded(i.get("fields", {}).get("status", {}).get("name", ""))]


async def fetch_subtasks_by_parents(jira_config: dict, parent_keys: List[str]) -> dict:
    """Запрашивает ВСЕ подзадачи для списка родительских задач одним JQL-запросом
    (parent in (...)) — независимо от того, на кого они назначены. Это надежнее,
    чем полагаться на поле 'subtasks' в ответе поиска задач: оно не всегда
    возвращается эндпоинтом /search/jql так, как для классического /search.

    Возвращает {parent_key: [issue, ...]}."""
    if not parent_keys:
        return {}

    base_url = jira_config["url"].rstrip("/")
    auth = aiohttp.BasicAuth(jira_config["email"], jira_config["token"])
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    keys_joined = ", ".join(parent_keys)
    jql = f'parent in ({keys_joined}) ORDER BY parent ASC'

    payload = {
        "jql": jql,
        "fields": ["key", "summary", "status", "parent"],
        "maxResults": 200,
    }

    search_url = f"{base_url}/rest/api/3/search/jql"
    async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
        async with session.post(search_url, json=payload, ssl=SSL_CONTEXT) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(f"Ошибка поиска подзадач: {resp.status} — {body[:300]}")
                return {}
            data = await resp.json()

    grouped: dict = {}
    for issue in data.get("issues", []):
        parent_key = issue.get("fields", {}).get("parent", {}).get("key")
        if not parent_key:
            continue
        grouped.setdefault(parent_key, []).append(issue)

    return grouped


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


def _format_user_tasks(jira_url: str, display_name: str, issues: List[dict], subtasks_by_parent: dict) -> str:
    jira_url = jira_url.rstrip("/")

    if not issues:
        return (
            f"📭 У пользователя <b>{_esc(display_name)}</b> нет задач в текущем "
            f"спринте (в рабочих статусах)."
        )

    # Считаем общее число подзадач (после фильтрации исключенных статусов),
    # чтобы показать сводку в шапке сообщения.
    visible_subtasks_total = 0
    for issue in issues:
        for sub in subtasks_by_parent.get(issue.get("key"), []):
            sub_status = sub.get("fields", {}).get("status", {}).get("name", "")
            if not _is_excluded(sub_status):
                visible_subtasks_total += 1

    header = (
        f"📋 <b>Задачи {_esc(display_name)} — текущий спринт</b>\n"
        f"🧩 Задач: <b>{len(issues)}</b>   🔸 Подзадач: <b>{visible_subtasks_total}</b>\n"
        f"{'─' * 28}"
    )
    lines = [header, ""]

    for idx, issue in enumerate(issues, start=1):
        key = issue.get("key")
        fields = issue.get("fields", {})
        summary = _truncate(fields.get("summary", "Без названия"), TASK_SUMMARY_MAX)
        status = fields.get("status", {}).get("name", "?")
        url = f"{jira_url}/browse/{key}"

        lines.append(
            f"{_status_icon(status)} <b>{idx}.</b> 📌 <a href='{url}'>{key}</a> — <b>{_esc(status)}</b>\n"
            f"    {_esc(summary)}"
        )

        visible_subtasks = [
            sub for sub in subtasks_by_parent.get(key, [])
            if not _is_excluded(sub.get("fields", {}).get("status", {}).get("name", ""))
        ]

        for j, sub in enumerate(visible_subtasks):
            sub_key = sub.get("key")
            sub_fields = sub.get("fields", {})
            sub_summary = _truncate(sub_fields.get("summary", "Без названия"), SUBTASK_SUMMARY_MAX)
            sub_status = sub_fields.get("status", {}).get("name", "?")
            sub_url = f"{jira_url}/browse/{sub_key}"

            branch = "└" if j == len(visible_subtasks) - 1 else "├"
            pad = " " if j == len(visible_subtasks) - 1 else "│"
            lines.append(
                f"   {branch}─ {_status_icon(sub_status)} 🔹 <a href='{sub_url}'>{sub_key}</a> "
                f"<i>({_esc(sub_status)})</i>\n"
                f"   {pad}    {_esc(sub_summary)}"
            )

        lines.append("")  # разделитель между задачами

    lines.append(f"{'─' * 28}\nℹ️ Скрыты статусы: «Готово», «Ожидает релиза»")

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
            parent_keys = [i["key"] for i in issues if i.get("key")]
            subtasks_by_parent = await fetch_subtasks_by_parents(jira_config, parent_keys)
        except Exception as e:
            logger.exception(f"Ошибка получения задач пользователя: {e}")
            monitor.update_status("Team Tasks", f"ERROR: {e}")
            await callback.message.answer("❌ Не удалось получить задачи пользователя.")
            return

        text = _format_user_tasks(jira_config["url"], display_name, issues, subtasks_by_parent)
        await callback.message.answer(text, disable_web_page_preview=True)