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
    """
    Проверяет задачи через новый эндпоинт /search/jql и находит автора перехода.
    """
    base_url = str(jira_url).rstrip('/')
    # ВНИМАНИЕ: Обновленный эндпоинт согласно вашей ошибке
    api_url = f"{base_url}/rest/api/3/search/jql"
    
    jql = (
        f'project = "{project_key}" '
        f'AND status = "Код ревью" '
        f'ORDER BY updated DESC'
    )
    
    auth = aiohttp.BasicAuth(jira_email, jira_token)
    # Структура payload остается похожей, но мы используем POST на новый эндпоинт
    payload = {
        "jql": jql,
        "maxResults": 20,
        "fields": ["summary", "status"],
        "expand": ["changelog"]
    }

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.post(api_url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Ошибка Jira API ({response.status}): {error_text}")
                    return

                data = await response.json()
                # Jira API /search/jql возвращает задачи в ключе 'issues'
                issues = data.get("issues", [])

                for issue in issues:
                    issue_key = issue["key"]
                    if issue_key in processed_issues:
                        continue
                    
                    summary = issue["fields"].get("summary", "")
                    histories = issue.get("changelog", {}).get("histories", [])
                    
                    # Поиск того, кто перевел задачу (например, Руслан Надыров)
                    status_changer_email = None
                    for history in reversed(histories):
                        items = history.get("items", [])
                        is_review_move = any(
                            item.get("field") == "status" and 
                            item.get("toString") == "Код ревью" 
                            for item in items
                        )
                        if is_review_move:
                            status_changer_email = history.get("author", {}).get("emailAddress")
                            break
                    
                    # Сопоставление с Telegram-аккаунтом (учитываем ruslan.nadyrov@ddream.kz)
                    author_tg = USER_MAP.get(status_changer_email)

                    if "[back]" in summary.lower():
                        reviewer = "@DamirShaniyazov"
                        task_type = "🛠 Backend"
                    else:
                        # Исключаем автора из списка
                        available_reviewers = [r for r in REVIEWERS if r != author_tg]
                        
                        if not available_reviewers:
                            available_reviewers = REVIEWERS
                            
                        reviewer = random.choice(available_reviewers)
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

                # Очистка старых задач из памяти
                current_keys = {i["key"] for i in issues}
                processed_issues.intersection_update(current_keys)

    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")

async def run_code_review_monitor(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str, interval: int = 100):
    """Цикл запуска проверки раз в 5 минут"""
    while True:
        await check_code_review_tasks(bot, channel_id, jira_email, jira_token, jira_url, project_key)
        await asyncio.sleep(interval)
