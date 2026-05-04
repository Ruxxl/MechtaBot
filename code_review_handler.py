import asyncio
import random
import logging
import aiohttp
from aiogram import Bot

logger = logging.getLogger("bot.code_review")

# Список ревьюеров
REVIEWERS = [
    "@Kurmangali_kusainoff"
]

processed_issues = set()

async def check_code_review_tasks(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str):
    """
    Проверяет задачи в статусе 'Код ревью' через НОВЫЙ эндпоинт /search/jql.
    """
    # Гарантируем корректный URL
    base_url = str(jira_url).rstrip('/')
    api_url = f"{base_url}/rest/api/3/search/jql" # Новый эндпоинт, который требует Jira
    
    jql = f'project = "{project_key}" AND status = "Код ревью" ORDER BY created DESC'
    
    auth = aiohttp.BasicAuth(jira_email, jira_token)
    
    # Тело запроса в точности как в рабочем JS
    payload = {
        "jql": jql,
        "maxResults": 50,
        "fields": ["summary", "status", "assignee"]
    }

    try:
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.post(api_url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Ошибка Jira API: {response.status} - {error_text}")
                    return

                data = await response.json()
                
                # В новом эндпоинте результаты лежат в ключе 'results'
                issues = data.get("results") or data.get("issues") or []

                if not issues:
                    # Если задач нет, просто выходим (тихий режим)
                    return

                for issue in issues:
                    issue_key = issue["key"]
                    
                    if issue_key not in processed_issues:
                        summary = issue["fields"]["summary"]
                        reviewer = random.choice(REVIEWERS)
                        
                        message_text = (
                            f"🔍 <b>Задача на код ревью</b>\n\n"
                            f"📌 <a href='{base_url}/browse/{issue_key}'>{issue_key}</a>: {summary}\n"
                            f"🎯 Назначаю: {reviewer}"
                        )
                        
                        await bot.send_message(chat_id=channel_id, text=message_text, disable_web_page_preview=True)
                        processed_issues.add(issue_key)
                        logger.info(f"Назначен ревьюер {reviewer} для {issue_key}")

                # Очистка: если задачи больше нет в списке 'Код ревью', убираем её из памяти
                current_keys = {i["key"] for i in issues}
                keys_to_forget = [k for k in processed_issues if k not in current_keys]
                for k in keys_to_forget:
                    processed_issues.remove(k)

    except Exception as e:
        logger.exception(f"Критическая ошибка в check_code_review_tasks: {e}")

async def run_code_review_monitor(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str, interval: int = 300):
    """Цикл мониторинга"""
    while True:
        await check_code_review_tasks(bot, channel_id, jira_email, jira_token, jira_url, project_key)
        await asyncio.sleep(interval)
