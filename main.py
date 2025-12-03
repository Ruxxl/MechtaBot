import asyncio
import aiohttp
import ssl
import os
import re
import logging
from dotenv import load_dotenv
from typing import List, Tuple, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from hr_topics import HR_TOPICS
from photo_handler import handle_photo_message
from text_handler import process_text_message
from calendar_service import check_calendar_events
from daily_reminder import handle_jira_release_status, start_reminders
from release_notifier import jira_release_check

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
JIRA_EMAIL = os.getenv('JIRA_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY', 'AS')
JIRA_PARENT_KEY = os.getenv('JIRA_PARENT_KEY', 'AS-3231')
JIRA_URL = os.getenv('JIRA_URL', 'https://mechtamarket.atlassian.net')
ADMIN_ID = int(os.getenv('ADMIN_ID', '998292747'))
TESTERS_CHANNEL_ID = int(os.getenv('TESTERS_CHANNEL_ID', '-1002196628724'))

TRIGGER_TAGS = ['#bug', '#jira']
CHECK_TAG = '#check'
THREAD_PREFIXES = {1701: '[Back]', 1703: '[Front]'}

def setup_logger():
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    return logging.getLogger("bot")

logger = setup_logger()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

def clean_summary(text: str, tags: List[str]) -> str:
    for tag in tags:
        text = re.sub(re.escape(tag), '', text, flags=re.IGNORECASE)
    return ' '.join(text.split()).strip()

def get_thread_prefix(message: Message) -> str:
    return THREAD_PREFIXES.get(getattr(message, 'message_thread_id', None), '')

async def send_safe(chat_id: int, text: str):
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        logger.error("Не удалось отправить сообщение %s: %s", chat_id, e)

@dp.message(F.text == "/getid")
async def get_chat_id(message: Message):
    await message.reply(f"Chat ID: <code>{message.chat.id}</code>")

@dp.message(F.text.func(lambda t: bool(t) and "#hr" in t.lower()))
async def hr_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=HR_TOPICS["attendance"]["title"], callback_data="hr_attendance")],
        [InlineKeyboardButton(text=HR_TOPICS["bs_order"]["title"], callback_data="hr_bs_order")],
        [InlineKeyboardButton(text=HR_TOPICS["business_trip"]["title"], callback_data="hr_business_trip")],
        [InlineKeyboardButton(text=HR_TOPICS["uvolnenie"]["title"], callback_data="hr_uvolnenie")]
    ])
    await message.reply("📋 Выберите интересующую тему:", reply_markup=kb)

@dp.callback_query(F.data.startswith("hr_"))
async def hr_topic_detail(callback: CallbackQuery):
    topic_key = callback.data.split("_", 1)[1]
    text = HR_TOPICS.get(topic_key, {}).get("text", "❌ Неизвестная тема.")
    await callback.message.answer(text)
    await callback.answer()

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await handle_photo_message(
        bot=bot,
        message=message,
        trigger_tags=TRIGGER_TAGS,
        create_jira_ticket=create_jira_ticket
    )

async def create_jira_ticket(
        text: str,
        author: str,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        thread_prefix: str = ""
) -> Tuple[bool, Optional[str]]:

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    cleaned_text = clean_summary(text, TRIGGER_TAGS)
    summary = f"{thread_prefix} [Telegram] {cleaned_text}".strip()[:255]

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "parent": {"key": JIRA_PARENT_KEY},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": f"[Telegram] Автор: {author}\n{text}"}]
                }]
            },
            "issuetype": {"name": "Подзадача"}
        }
    }

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession(auth=auth) as session:
        try:
            async with session.post(f"{JIRA_URL}/rest/api/3/issue", json=payload, ssl=ssl_context) as resp:
                if resp.status != 201:
                    error = await resp.text()
                    logger.error("❌ Ошибка при создании задачи: %s — %s", resp.status, error)
                    return False, None
                result = await resp.json()
                issue_key = result.get("key")
        except Exception as e:
            logger.exception("Ошибка при запросе к Jira: %s", e)
            return False, None

        notify_text = (
            f"📨 Создан новый баг!\n"
            f"🔑 <b>{issue_key}</b>\n"
            f"👤 Автор: <b>{author}</b>\n\n"
            f"🔗 <a href=\"{JIRA_URL}/browse/{issue_key}\">Открыть задачу</a>\n\n"
            f"📝 <b>Описание:</b>\n{text}"
        )

        await send_safe(ADMIN_ID, notify_text)
        await send_safe(TESTERS_CHANNEL_ID, notify_text)

        if file_bytes and filename:
            attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
            attach_headers = {"X-Atlassian-Token": "no-check"}
            data = aiohttp.FormData()
            data.add_field('file', file_bytes, filename=filename, content_type='image/jpeg')
            try:
                async with session.post(attach_url, data=data, headers=attach_headers, ssl=ssl_context) as attach_resp:
                    if attach_resp.status not in (200, 201):
                        error = await attach_resp.text()
                        logger.error("❌ Ошибка при вложении: %s — %s", attach_resp.status, error)
                        return False, None
            except Exception as e:
                logger.exception("Ошибка при прикреплении файла: %s", e)
                return False, None

    return True, issue_key

class JiraFSM(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_priority = State()
    waiting_links = State()
    waiting_screenshots = State()

@dp.message(F.text == "/jira")
async def jira_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(JiraFSM.waiting_title)
    await message.answer("📝 Введите заголовок дефекта")

@dp.message(JiraFSM.waiting_title, F.text)
async def jira_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(JiraFSM.waiting_description)
    await message.answer("📄 Введите описание дефекта")

@dp.message(JiraFSM.waiting_description, F.text)
async def jira_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Высокий", callback_data="prio_high")],
        [InlineKeyboardButton(text="🟡 Средний", callback_data="prio_medium")],
        [InlineKeyboardButton(text="🟢 Низкий", callback_data="prio_low")],
    ])
    await state.set_state(JiraFSM.waiting_priority)
    await message.answer("⚠️ Выберите критичность дефекта", reply_markup=kb)

@dp.callback_query(JiraFSM.waiting_priority)
async def jira_priority(callback: CallbackQuery, state: FSMContext):
    mapping = {"prio_high": "High", "prio_medium": "Medium", "prio_low": "Low"}
    priority = mapping.get(callback.data, "Medium")
    await state.update_data(priority=priority)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="skip_links")]])
    await state.set_state(JiraFSM.waiting_links)
    await callback.message.answer("🔗 Отправьте ссылки или нажмите «Пропустить»", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "skip_links")
async def jira_skip_links(callback: CallbackQuery, state: FSMContext):
    await state.update_data(links=None)
    await state.set_state(JiraFSM.waiting_screenshots)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Готово", callback_data="screens_done")]])
    await callback.message.answer("🖼 Отправьте скриншоты. После загрузки нажмите «Готово».", reply_markup=kb)
    await callback.answer()

@dp.message(JiraFSM.waiting_links, F.text)
async def jira_links(message: Message, state: FSMContext):
    await state.update_data(links=message.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Готово", callback_data="screens_done")]])
    await state.set_state(JiraFSM.waiting_screenshots)
    await message.answer("🖼 Отправьте скриншоты. После загрузки нажмите «Готово».", reply_markup=kb)

@dp.message(JiraFSM.waiting_screenshots, F.photo)
async def jira_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    screenshots = data.get("screens", [])
    screenshots.append(message.photo[-1].file_id)
    await state.update_data(screens=screenshots)
    await message.answer("📎 Скриншот добавлен. Можете отправить ещё или нажмите «Готово».")

@dp.callback_query(F.data == "screens_done")
async def jira_finish(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ok, issue_key = await create_jira_ticket_extended(
        title=data["title"],
        description=data["description"],
        priority=data.get("priority", "Medium"),
        links=data.get("links"),
        screenshots=data.get("screens", []),
        bot=bot
    )
    if ok:
        await callback.message.answer(
            f"✅ Задача создана!\n"
            f"🔑 <b>{issue_key}</b>\n"
            f"🔗 {JIRA_URL}/browse/{issue_key}"
        )
    else:
        await callback.message.answer("❌ Ошибка при создании задачи")
    await state.clear()
    await callback.answer()

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
                ) as resp:
                    if resp.status not in (200, 201):
                        logger.error("Ошибка прикрепления скрина: %s %s", resp.status, await resp.text())
            except Exception as e:
                logger.exception(e)

    return True, issue_key

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        return
    await process_text_message(
        message=message,
        TRIGGER_TAGS=TRIGGER_TAGS,
        CHECK_TAG=CHECK_TAG,
        THREAD_PREFIXES=THREAD_PREFIXES,
        create_jira_ticket=create_jira_ticket,
        bot=bot,
        JIRA_URL=JIRA_URL
    )

async def run_background_task(coro_func, *args, interval: int = 60, **kwargs):
    while True:
        try:
            await coro_func(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Ошибка в фоновой задаче %s: %s", getattr(coro_func, '__name__', str(coro_func)), e)
        await asyncio.sleep(interval)

@dp.callback_query(F.data == "jira_release_status")
async def callback_jira_release_status(callback: CallbackQuery):
    await handle_jira_release_status(
        callback,
        JIRA_EMAIL,
        JIRA_API_TOKEN,
        JIRA_PROJECT_KEY,
        JIRA_URL
    )

async def main():
    asyncio.create_task(check_calendar_events(bot, TESTERS_CHANNEL_ID))
    asyncio.create_task(start_reminders(bot, TESTERS_CHANNEL_ID))
    asyncio.create_task(run_background_task(
        jira_release_check,
        bot,
        TESTERS_CHANNEL_ID,
        JIRA_EMAIL,
        JIRA_API_TOKEN,
        JIRA_PROJECT_KEY,
        JIRA_URL,
        logger,
        interval=500
    ))
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
