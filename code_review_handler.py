import asyncio
import random
import logging
import aiohttp
from aiogram import Bot

logger = logging.getLogger("bot.code_review")

# Список ревьюеров (username в Telegram)
REVIEWERS = [
    "@Kurmangali_kusainoff",
    "@Yernazar_Kadyrbekov",
    "@Madina_Imasheva",
    "@Nargiza_Marassulova"
]

# Множество для хранения ключей задач, которые уже обработаны, 
# чтобы не тегать людей повторно каждые 5 минут
processed_issues = set()

async def check_code_review_tasks(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str):
    """
    Проверяет задачи в статусе 'Код ревью' и назначает случайного ревьюера.
    """
    jql = f'project = "{project_key}" AND status = "Код ревью" ORDER BY created DESC'
    api_url = f"{jira_url}/rest/api/3/search"
    
    auth = aiohttp.BasicAuth(jira_email, jira_token)
    
    payload = {
        "jql": jql,
        "fields": ["summary", "status"],
        "maxResults": 50
    }

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.post(api_url, json=payload) as response:
                if response.status != 200:
                    logger.error(f"Ошибка Jira API: {response.status}")
                    return

                data = await response.json()
                issues = data.get("issues", [])

                for issue in issues:
                    issue_key = issue["key"]
                    
                    # Если задача новая для нас
                    if issue_key not in processed_issues:
                        summary = issue["fields"]["summary"]
                        reviewer = random.choice(REVIEWERS)
                        
                        message_text = (
                            f"🔍 <b>Задача на код ревью</b>\n\n"
                            f"📌 <a href='{jira_url}/browse/{issue_key}'>{issue_key}</a>: {summary}\n"
                            f"🎯 Назначаю: {reviewer}"
                        )
                        
                        await bot.send_message(channel_id, message_text, disable_web_page_preview=True)
                        processed_issues.add(issue_key)
                        logger.info(f"Назначен ревьюер для {issue_key}")

                # Очистка старых ключей (опционально), чтобы память не росла бесконечно
                # Если задач в списке нет, а в processed_issues они есть — значит они вышли из ревью
                current_keys = {i["key"] for i in issues}
                keys_to_remove = processed_issues - current_keys
                for k in keys_to_remove:
                    processed_issues.remove(k)

    except Exception as e:
        logger.exception(f"Ошибка в check_code_review_tasks: {e}")

async def run_code_review_monitor(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str, interval: int = 300):
    """Цикл для запуска каждые 5 минут (300 секунд)"""
    while True:
        await check_code_review_tasks(bot, channel_id, jira_email, jira_token, jira_url, project_key)
        await asyncio.sleep(interval)
