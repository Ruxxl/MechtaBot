import aiohttp
import ssl
import logging
from typing import List, Optional, Tuple

from aiogram import Bot, types
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger("bot")

# =======================
# FSM состояния Jira
# =======================
class JiraFSM(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_priority = State()
    waiting_links = State()
    waiting_screenshots = State()

# =======================
# Создание Jira задачи
# =======================
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
                    {"type": "paragraph", "content": [{"type": "text", "text": desc_text}]}
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

        # Прикрепляем скриншоты
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
# FSM обработчики
# =======================
async def start_jira_fsm(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(JiraFSM.waiting_title)
    await message.answer("📝 Введите заголовок задачи:")

async def jira_title_step(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(JiraFSM.waiting_description)
    await message.answer("✏️ Введите описание задачи:")

async def jira_description_step(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Высокий", callback_data="prio_high")],
        [InlineKeyboardButton(text="🟡 Средний", callback_data="prio_medium")],
        [InlineKeyboardButton(text="🟢 Низкий", callback_data="prio_low")]
    ])
    await state.set_state(JiraFSM.waiting_priority)
    await message.answer("⚡ Выберите приоритет:", reply_markup=kb)

async def jira_priority_step(callback: CallbackQuery, state: FSMContext):
    mapping = {"prio_high": "High", "prio_medium": "Medium", "prio_low": "Low"}
    priority = mapping.get(callback.data, "Medium")
    await state.update_data(priority=priority)
    await state.set_state(JiraFSM.waiting_links)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="skip_links")]])
    await callback.message.answer("🔗 Укажите ссылки (или пропустите)", reply_markup=kb)
    await callback.answer()

async def jira_links_step(message: Message, state: FSMContext):
    links = None if message.text.strip() in ["-", "—"] else message.text.strip()
    await state.update_data(links=links)
    await state.set_state(JiraFSM.waiting_screenshots)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Готово", callback_data="screens_done")]])
    await message.answer("📸 Прикрепите скриншоты или нажмите 'Готово'", reply_markup=kb)

async def jira_screenshots_step(message: Message, state: FSMContext, bot: Bot, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_PARENT_KEY, JIRA_URL):
    data = await state.get_data()
    screenshots = data.get("screenshots", [])

    if message.photo:
        screenshots.append(message.photo[-1].file_id)
    await state.update_data(screenshots=screenshots)
    await message.answer("📎 Скриншот добавлен. Можно добавить ещё или нажать 'Готово'.")

async def jira_finish(callback: CallbackQuery, state: FSMContext, bot: Bot, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_PARENT_KEY, JIRA_URL):
    data = await state.get_data()
    ok, issue_key = await create_jira_ticket_extended(
        title=data["title"],
        description=data["description"],
        priority=data.get("priority", "Medium"),
        links=data.get("links"),
        screenshots=data.get("screenshots", []),
        bot=bot,
        JIRA_EMAIL=JIRA_EMAIL,
        JIRA_API_TOKEN=JIRA_API_TOKEN,
        JIRA_PROJECT_KEY=JIRA_PROJECT_KEY,
        JIRA_PARENT_KEY=JIRA_PARENT_KEY,
        JIRA_URL=JIRA_URL
    )
    if ok:
        await callback.message.answer(f"✅ Jira задача создана: <b>{issue_key}</b>\n🔗 {JIRA_URL}/browse/{issue_key}")
    else:
        await callback.message.answer("❌ Ошибка при создании Jira задачи")
    await state.clear()
    await callback.answer()
