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
    # Защита от кривых URL
    if not str(jira_url).startswith('http'):
        logger.error(f"❌ Критической адрес Jira: '{jira_url}'. Проверьте порядок аргументов в main.py!")
        return

    base_url = jira_url.rstrip('/')
    api_url = f"{base_url}/rest/api/3/search"
    
    # ... остальной код
    
    # 2. Формируем JQL (убедись, что project_key передается верно)
    jql = f'project = "{project_key}" AND status = "Код ревью" ORDER BY created DESC'
    
    auth = aiohttp.BasicAuth(jira_email, jira_token)
    
    payload = {
        "jql": jql,
        "fields": ["summary", "status"],
        "maxResults": 50
    }

    try:
        # Рекомендуется использовать один сеанс, но для простоты оставим внутри (или вынеси в main)
        async with aiohttp.ClientSession(auth=auth) as session:
            # logger.info(f"Запрос к Jira: {api_url}") # Раскомментируй для отладки, если снова упадет
            async with session.post(api_url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Ошибка Jira API: {response.status} - {error_text}")
                    return

                data = await response.json()
                # Jira Cloud в методе POST обычно возвращает 'issues'
                issues = data.get("issues", [])

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
