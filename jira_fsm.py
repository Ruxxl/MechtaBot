import aiohttp
import ssl
import logging
from typing import List, Optional, Tuple

from aiogram import Bot, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from main import JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_PARENT_KEY, JIRA_URL

logger = logging.getLogger("bot")

# =======================
# FSM для команды /jira
# =======================
class JiraFSM(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_priority = State()
    waiting_links = State()
    waiting_screenshots = State()

# =======================
# Вспомогательные функции
# =======================
async def create_jira_ticket_extended(
        title: str,
        description: str,
        priority: str,
        links: Optional[str],
        screenshots: List[str],
        bot: Bot
) -> Tuple[bool, Optional[str]]:

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    desc_text = f"{description}\n\nДоп. информация:\n{links or '—'}"
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "parent": {"key": JIRA_PARENT_KEY},
            "summary": title[:255],
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": desc_text}]
                    }
                ]
            },
            "priority": {"name": priority},
            "issuetype": {"name": "Подзадача"}
        }
    }

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession(auth=auth) as session:
        try:
            async with session.post(f"{JIRA_URL}/rest/api/3/issue", json=payload, ssl=ssl_ctx) as resp:
                if resp.status != 201:
                    logger.error(await resp.text())
                    return False, None
                data = await resp.json()
                issue_key = data["key"]
        except Exception as e:
            logger.exception(e)
            return False, None

        for file_id in screenshots:
            try:
                file = await bot.get_file(file_id)
                file_bytes = await bot.download_file(file.file_path)
                form = aiohttp.FormData()
                form.add_field("file", file_bytes, filename="screenshot.jpg", content_type="image/jpeg")
                async with session.post(
                    f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments",
                    data=form,
                    headers={"X-Atlassian-Token": "no-check"},
                    ssl=ssl_ctx
                ) as resp_attach:
                    if resp_attach.status not in (200, 201):
                        logger.error("Ошибка прикрепления скрина: %s %s", resp_attach.status, await resp_attach.text())
            except Exception as e:
                logger.exception(e)

    return True, issue_key
