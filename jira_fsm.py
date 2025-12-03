import aiohttp
import ssl
import logging
from typing import List, Optional, Tuple

from aiogram import Bot, types
from aiogram.types import Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

logger = logging.getLogger("bot")


class JiraFSM(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_priority = State()
    waiting_links = State()
    waiting_screenshots = State()


async def create_jira_ticket_extended(
        title: str,
        description: str,
        priority: str,
        links: Optional[str],
        screenshots: List[str],
        bot: Bot,
        JIRA_EMAIL: str,
        JIRA_API_TOKEN: str,
        JIRA_PROJECT_KEY: str,
        JIRA_PARENT_KEY: str,
        JIRA_URL: str
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

        # Прикрепление скриншотов
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


# =======================
# FSM шаги
# =======================

async def start_jira_fsm(message: Message, state: FSMContext):
    await state.set_state(JiraFSM.waiting_title)
    await message.reply("📝 Введите заголовок задачи:")


async def jira_title_step(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(JiraFSM.waiting_description)
    await message.reply("✏️ Введите описание задачи:")


async def jira_description_step(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(JiraFSM.waiting_priority)
    await message.reply("⚡ Укажите приоритет (Low, Medium, High, Highest):")


async def jira_priority_step(message: Message, state: FSMContext):
    valid_priorities = ["Low", "Medium", "High", "Highest"]
    if message.text not in valid_priorities:
        await message.reply(f"❌ Неверный приоритет. Выберите: {', '.join(valid_priorities)}")
        return
    await state.update_data(priority=message.text)
    await state.set_state(JiraFSM.waiting_links)
    await message.reply("🔗 Укажите дополнительные ссылки (или '-' если нет):")


async def jira_links_step(message: Message, state: FSMContext):
    links = None if message.text.strip() in ["-", "—"] else message.text.strip()
    await state.update_data(links=links)
    await state.set_state(JiraFSM.waiting_screenshots)
    await message.reply("📸 Прикрепите скриншоты (можно несколько) или отправьте '-' если нет:")


async def jira_screenshots_step(
        message: Message,
        state: FSMContext,
        JIRA_EMAIL: str,
        JIRA_API_TOKEN: str,
        JIRA_PROJECT_KEY: str,
        JIRA_PARENT_KEY: str,
        JIRA_URL: str
):
    data = await state.get_data()
    screenshots = data.get("screenshots", [])

    if hasattr(message, "text") and message.text.strip() in ["-", "—"]:
        screenshots = screenshots  # ничего не добавляем
    elif hasattr(message, "photo") and message.photo:
        screenshots.append(message.photo[-1].file_id)

    await state.update_data(screenshots=screenshots)

    # Вызов финальной функции Jira
    title = data.get("title")
    description = data.get("description")
    priority = data.get("priority")
    links = data.get("links")

    success, issue_key = await create_jira_ticket_extended(
        title=title,
        description=description,
        priority=priority,
        links=links,
        screenshots=screenshots,
        bot=message.bot,
        JIRA_EMAIL=JIRA_EMAIL,
        JIRA_API_TOKEN=JIRA_API_TOKEN,
        JIRA_PROJECT_KEY=JIRA_PROJECT_KEY,
        JIRA_PARENT_KEY=JIRA_PARENT_KEY,
        JIRA_URL=JIRA_URL
    )

    if success:
        await message.reply(f"✅ Jira задача создана: <b>{issue_key}</b>\n🔗 {JIRA_URL}/browse/{issue_key}")
    else:
        await message.reply("❌ Ошибка при создании Jira задачи. Проверьте лог.")

    await state.clear()
