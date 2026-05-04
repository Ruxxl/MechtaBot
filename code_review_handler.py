import asyncio
import random
import logging
import aiohttp
from aiogram import Bot

logger = logging.getLogger("bot.code_review")

# Список ревьюеров
REVIEWERS = [
    "@Kurmangali_kusainoff",
    "@peaceffuul",
    "@john_folker",
    "@nurgi17"
]

# Память бота, чтобы не спамить об одной и той же задаче
processed_issues = set()

async def check_code_review_tasks(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str):
    """
    Проверяет задачи в статусе 'Код ревью', исключает [back] и назначает ревьюера.
    """
    base_url = str(jira_url).rstrip('/')
    api_url = f"{base_url}/rest/api/3/search/jql"
    
    # НЮАНС 1: JQL фильтрация. Оператор !~ "[back]" пытается исключить бэкенд на уровне БД.
    jql = (
        f'project = "{project_key}" '
        f'AND status = "Код ревью" '
        f'AND summary !~ "[back]" '
        f'ORDER BY created DESC'
    )
    
    auth = aiohttp.BasicAuth(jira_email, jira_token)
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
                issues = data.get("results") or data.get("issues") or []

                if not issues:
                    return

                for issue in issues:
                    issue_key = issue["key"]
                    summary = issue["fields"].get("summary", "")

                    # НЮАНС 2: Дополнительная проверка в коде.
                    # Jira иногда игнорирует спецсимволы вроде [] в JQL.
                    # Эта проверка гарантирует, что [back] точно не пройдет.
                    if "[back]" in summary.lower():
                        continue

                    if issue_key not in processed_issues:
                        reviewer = random.choice(REVIEWERS)
                        
                        message_text = (
                            f"🔍 <b>Задача на код ревью</b>\n\n"
                            f"📌 <a href='{base_url}/browse/{issue_key}'>{issue_key}</a>: {summary}\n"
                            f"🎯 Назначаю: {reviewer}"
                        )
                        
                        await bot.send_message(chat_id=channel_id, text=message_text, disable_web_page_preview=True)
                        processed_issues.add(issue_key)
                        logger.info(f"Назначен ревьюер {reviewer} для {issue_key}")

                # Очистка памяти: оставляем только те задачи, которые всё еще в статусе ревью
                current_keys = {i["key"] for i in issues}
                processed_issues.intersection_update(current_keys)

    except Exception as e:
        logger.exception(f"Критическая ошибка в check_code_review_tasks: {e}")

async def run_code_review_monitor(bot: Bot, channel_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str, interval: int = 300):
    """Бесконечный цикл мониторинга"""
    while True:
        await check_code_review_tasks(bot, channel_id, jira_email, jira_token, jira_url, project_key)
        await asyncio.sleep(interval)
