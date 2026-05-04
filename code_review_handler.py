import asyncio
import random
import logging
import aiohttp
from aiogram import Bot

logger = logging.getLogger("bot.code_review")

# Карта соответствия Email (Jira) -> Telegram username
# ВНИМАНИЕ: Почта Руслана обновлена на ruslan.nadyrov@ddream.kz согласно логам консоли
USER_MAP = {
    "ruslan.nadyrov@ddream.kz": "@peaceffuul",
    "kurmangali.kussainov@ddream.kz": "@Kurmangali_kusainoff",
    "Vladislav": "@john_folker",
    "nurgissa.ussen@ddream.kz": "@nurgi17"
}

# Список потенциальных ревьюеров
REVIEWERS = [
    "@Kurmangali_kusainoff",
    "@peaceffuul",
    "@john_folker",
    "@nurgi17"
]

# Память бота для предотвращения дублей
processed_issues = set()

async def check_code_review_tasks(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str):
    base_url = str(jira_url).rstrip('/')
    # Эндпоинт для поиска JQL в Jira v3
    api_url = f"{base_url}/rest/api/3/search/jql"
    
    jql = (
        f'project = "{project_key}" '
        f'AND status = "Код ревью" '
        f'ORDER BY updated DESC'
    )
    
    auth = aiohttp.BasicAuth(jira_email, jira_token)
    
    # ИСПРАВЛЕНИЕ: expand теперь передается строкой "changelog"
    payload = {
        "jql": jql,
        "maxResults": 20,
        "fields": ["summary", "status", "assignee"],
        "expand": "changelog" 
    }
    
    # Явно указываем заголовки для Jira Cloud
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            async with session.post(api_url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Ошибка Jira API ({response.status}): {error_text}")
                    return

                data = await response.json()
                issues = data.get("issues", [])

                for issue in issues:
                    issue_key = issue["key"]
                    if issue_key in processed_issues:
                        continue
                    
                    fields = issue.get("fields", {})
                    summary = fields.get("summary", "")
                    
                    # Извлекаем историю для поиска автора перехода
                    changelog = issue.get("changelog", {})
                    histories = changelog.get("histories", [])
                    
                    status_changer_email = None
                    
                    # Итерируем историю с конца (самые свежие изменения)
                    for history in reversed(histories):
                        items = history.get("items", [])
                        # Проверяем, был ли в этом событии переход в "Код ревью"
                        if any(i.get("field") == "status" and i.get("toString") == "Код ревью" for i in items):
                            status_changer_email = history.get("author", {}).get("emailAddress")
                            break
                    
                    # Сопоставляем email с Telegram (Руслан: ruslan.nadyrov@ddream.kz)
                    author_tg = USER_MAP.get(status_changer_email)

                    if "[back]" in summary.lower():
                        reviewer = "@DamirShaniyazov"
                        task_type = "🛠 Backend"
                    else:
                        # ИСКЛЮЧЕНИЕ: Убираем автора из списка доступных ревьюеров
                        available_reviewers = [r for r in REVIEWERS if r != author_tg]
                        
                        # Если автор — единственный в списке, выбираем из всех (fallback)
                        reviewer = random.choice(available_reviewers if available_reviewers else REVIEWERS)
                        task_type = "🎨 Frontend/Common"
        
                    message_text = (
                        f"🔍 <b>Задача на код ревью</b> ({task_type})\n\n"
                        f"📌 <a href='{base_url}/browse/{issue_key}'>{issue_key}</a>: {summary}\n"
                        f"🎯 Назначаю: {reviewer}\n"
                        f"👤 Отправил: {author_tg or 'Не определен'}"
                    )
                    
                    await bot.send_message(
                        chat_id=-1002196628724, 
                        message_thread_id=channel_id,
                        text=message_text,
                        disable_web_page_preview=True,
                        parse_mode="HTML"
                    )
                    processed_issues.add(issue_key)

                # Чистим кэш
                current_keys = {i["key"] for i in issues}
                processed_issues.intersection_update(current_keys)

    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
async def run_code_review_monitor(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str, interval: int = 100):
    """Цикл запуска проверки раз в 5 минут"""
    while True:
        await check_code_review_tasks(bot, channel_id, jira_email, jira_token, jira_url, project_key)
        await asyncio.sleep(interval)
