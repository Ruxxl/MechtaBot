import asyncio
import random
import logging
import aiohttp
from aiogram import Bot
from web.admin_handler import monitor

logger = logging.getLogger("bot.code_review")

USER_MAP = {
    "ruslan.nadyrov@ddream.kz": "@peaceffuul",
    "kurmangali.kussainov@ddream.kz": "@Kurmangali_kusainoff",
    "vladislav.folker@ddream.kz": "@john_folker",
    "nurgissa.ussen@ddream.kz": "@nurgi17"
}

REVIEWERS = ["@Kurmangali_kusainoff", "@peaceffuul", "@john_folker", "@nurgi17"]
processed_issues = set()

async def check_code_review_tasks(bot: Bot, channel_id: int, thread_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str):
    base_url = jira_url.rstrip('/')
    monitor.update_status("Code Review Monitor", "OK")
    
    api_url = f"{base_url}/rest/api/3/search/jql"
    
    jql = f'project = "{project_key}" AND status = "Код ревью" ORDER BY updated DESC'
    auth = aiohttp.BasicAuth(jira_email, jira_token)
    
    payload = {
        "jql": jql,
        "maxResults": 20,
        "fields": ["summary", "status", "assignee"],
        "expand": "changelog" 
    }
    
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            async with session.post(api_url, json=payload) as response:
                if response.status != 200:
                    return

                data = await response.json()
                issues = data.get("issues", [])

                for issue in issues:
                    issue_key = issue["key"]
                    if issue_key in processed_issues:
                        continue
                    
                    fields = issue.get("fields", {})
                    summary = fields.get("summary", "")
                    
                    changelog = issue.get("changelog", {})
                    histories = changelog.get("histories", [])
                    
                    status_changer_email = None
                    for history in reversed(histories):
                        items = history.get("items", [])
                        if any(i.get("field") == "status" and i.get("toString") == "Код ревью" for i in items):
                            status_changer_email = history.get("author", {}).get("emailAddress")
                            break
                    
                    author_tg = USER_MAP.get(status_changer_email)

                    if "[back]" in summary.lower():
                        reviewer = "@DamirShaniyazov"
                        task_type = "[BACK]"
                    else:
                        available_reviewers = [r for r in REVIEWERS if r != author_tg]
                        reviewer = random.choice(available_reviewers if available_reviewers else REVIEWERS)
                        task_type = "[FRONT]"
        
                    message_text = (
                        f"🔍 <b>Задача на код ревью</b> ({task_type})\n\n"
                        f"📌 <a href='{base_url}/browse/{issue_key}'>{issue_key}</a>: {summary}\n"
                        f"🎯 Назначаю: {reviewer}\n"
                        f"👤 Отправил: {author_tg or 'Не определен'}"
                    )
                    
                    logger.info(f"⚠️ Уведомление Code Review пропущено: {issue_key}")
                    processed_issues.add(issue_key)

                current_keys = {i["key"] for i in issues}
                processed_issues.intersection_update(current_keys)

    except Exception as e:
        logger.exception(f"Ошибка в Code Review мониторе: {e}")

async def run_code_review_monitor(bot: Bot, channel_id: int, thread_id: int, jira_email: str, jira_token: str, jira_url: str, project_key: str, interval: int = 100):
    """Цикл запуска проверки"""
    logger.info(f"Монитор Code Review запущен для группы {channel_id}, топик: {thread_id}")
    while True:
        # Передаем все аргументы по порядку
        await check_code_review_tasks(bot, channel_id, thread_id, jira_email, jira_token, jira_url, project_key)
        await asyncio.sleep(interval)
