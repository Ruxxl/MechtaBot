# Refactored main.py
# Полная интеграция FSM Jira и остального функционала

import asyncio
import aiohttp
import ssl
import os
import re
import logging
from dotenv import load_dotenv
from typing import List, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from hr_topics import HR_TOPICS
from photo_handler import handle_photo_message
from text_handler import process_text_message
from calendar_service import check_calendar_events
from daily_reminder import handle_jira_release_status, start_reminders
from release_notifier import jira_release_check

# =======================
# Настройка окружения
# =======================
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

# =======================
# Логирование
# =======================
def setup_logger():
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    return logging.getLogger("bot")

logger = setup_logger()

# =======================
# Инициализация бота
# =======================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# =======================
# FSM для Jira
# =======================
class JiraFSM(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_priority = State()
    waiting_links = State()
    waiting_links_input = State()
    waiting_screenshots = State()

# =======================
# Утилиты
# =======================
def clean_summary(text: str, tags: List[str]) -> str:
    for tag in tags:
        text = re.sub(re.escape(tag), '', text, flags=re.IGNORECASE)
    return ' '.join(text.split()).strip()

def get_thread_prefix(message: Message) -> str:
    return THREAD_PREFIXES.get(getattr(message, 'message_thread_id', None), '')

async def send_safe(chat_id: int, text: str):
    try:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("Не удалось отправить сообщение %s: %s", chat_id, e)

# =======================
# Создание задачи Jira (для FSM)
# =======================
async def create_jira_ticket_fsm(data: dict, author: str) -> Optional[str]:
    title = data.get("title", "Без заголовка")
    description = data.get("description", "")
    priority = data.get("priority", "Medium")
    links = data.get("links", [])
    files = data.get("files", [])

    full_text = description
    if links:
        full_text += "\n\n🔗 Ссылки:\n" + "\n".join(links)

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "parent": {"key": JIRA_PARENT_KEY},
            "summary": f"[Telegram] {title}"[:255],
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"[Telegram] Автор: {author}\n{full_text}"}]}]
            },
            "issuetype": {"name": "Подзадача"},
            "priority": {"name": priority}
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
                    logger.error("Ошибка создания подзадачи: %s — %s", resp.status, error)
                    return None
                result = await resp.json()
                issue_key = result.get("key")
                logger.info("Подзадача %s создана", issue_key)
        except Exception as e:
            logger.exception("Ошибка запроса к Jira: %s", e)
            return None

        # Прикрепление файлов
        if files:
            for i, file_id in enumerate(files):
                file = await bot.get_file(file_id)
                file_bytes = await bot.download_file(file.file_path)
                attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
                data_attach = aiohttp.FormData()
                data_attach.add_field('file', file_bytes.read(), filename=f"screenshot_{i+1}.jpg", content_type='image/jpeg')
                headers = {"X-Atlassian-Token": "no-check"}
                try:
                    async with session.post(attach_url, data=data_attach, headers=headers, ssl=ssl_context) as attach_resp:
                        if attach_resp.status in (200, 201):
                            logger.info("Скриншот %s прикреплён к подзадаче %s", i+1, issue_key)
                except Exception as e:
                    logger.exception("Ошибка прикрепления скриншота: %s", e)
    return issue_key

# =======================
# FSM Handlers
# =======================
@dp.message(F.text == "/jira")
async def start_jira_fsm(message: Message, state: FSMContext):
    await state.clear()
    await state.update_data(files=[])
    await message.answer("🚀 <b>Регистрация дефекта</b>\n\n📌 <b>Шаг 1:</b> Введите заголовок дефекта (коротко и ясно):")
    await state.set_state(JiraFSM.waiting_title)

@dp.message(JiraFSM.waiting_title)
async def jira_title_handler(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title:
        await message.answer("⚠️ Заголовок не может быть пустым. Попробуйте ещё раз:")
        return
    await state.update_data(title=title)
    await message.answer("📝 <b>Шаг 2:</b> Введите описание дефекта.\nОпишите суть, что нужно сделать и любые детали.")
    await state.set_state(JiraFSM.waiting_description)

@dp.message(JiraFSM.waiting_description)
async def jira_description_handler(message: Message, state: FSMContext):
    description = message.text.strip()
    await state.update_data(description=description)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Низкий", callback_data="priority_low"),
         InlineKeyboardButton(text="🟡 Средний", callback_data="priority_medium"),
         InlineKeyboardButton(text="🔴 Высокий", callback_data="priority_high")]
    ])
    await message.answer("⚡ <b>Шаг 3:</b> Выберите приоритет задачи:", reply_markup=kb)
    await state.set_state(JiraFSM.waiting_priority)

# =======================
# Приоритет + переход к ссылкам
# =======================
@dp.callback_query(JiraFSM.waiting_priority)
async def jira_priority_handler(callback: CallbackQuery, state: FSMContext):
    mapping = {"priority_low": "Low", "priority_medium": "Medium", "priority_high": "High"}
    await state.update_data(priority=mapping.get(callback.data, "Medium"))

    # Шаг 4: ссылки с кнопкой "Пропустить"
    kb_links = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_links")]
    ])
    await callback.message.answer("🔗 <b>Шаг 4:</b> Введите ссылки через пробел или нажмите 'Пропустить'", reply_markup=kb_links)
    await state.set_state(JiraFSM.waiting_links_input)
    await callback.answer()

@dp.message(JiraFSM.waiting_links_input)
async def jira_links_input_handler(message: Message, state: FSMContext):
    links_text = message.text.strip()
    links = [] if links_text.lower() in ("пропустить", "skip") else links_text.split()
    await state.update_data(links=links)

    # Шаг 5: скриншоты с кнопкой "Пропустить"
    kb_screenshots = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_screenshots")]
    ])
    await message.answer("📸 <b>Шаг 5:</b> Прикрепите скриншоты или нажмите 'Пропустить'", reply_markup=kb_screenshots)
    await state.set_state(JiraFSM.waiting_screenshots)

# =======================
# Пропустить ссылки
# =======================
@dp.callback_query(F.data == "skip_links")
async def skip_links(callback: CallbackQuery, state: FSMContext):
    await state.update_data(links=[])
    kb_screenshots = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_screenshots")]
    ])
    await callback.message.answer("📸 <b>Шаг 5:</b> Прикрепите скриншоты или нажмите 'Пропустить'", reply_markup=kb_screenshots)
    await state.set_state(JiraFSM.waiting_screenshots)
    await callback.answer()

# =======================
# Пропустить скриншоты
# =======================
@dp.callback_query(F.data == "skip_screenshots")
async def skip_screenshots(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    issue_key = await create_jira_ticket_fsm(await state.get_data(), author=callback.from_user.full_name)
    await state.clear()
    if issue_key:
        text_notify = f"✅ <b>Подзадача создана!</b>\n🔑 <b>{issue_key}</b>\n👤 Автор: <b>{callback.from_user.full_name}</b>\n"
        if data.get("links"):
            text_notify += "🔗 Ссылки:\n" + "\n".join(data["links"]) + "\n"
        files = data.get("files", [])
        if files:
            text_notify += f"📎 Прикреплено файлов: {len(files)}\n"
        text_notify += f"\n<a href=\"{JIRA_URL}/browse/{issue_key}\">Открыть задачу в Jira</a>"
        await callback.message.answer(text_notify, reply_markup=ReplyKeyboardRemove())
    else:
        await callback.message.answer("❌ Ошибка при создании подзадачи.", reply_markup=ReplyKeyboardRemove())
    await callback.answer()

# =======================
# Обработка скриншотов при обычной отправке фото
# =======================
@dp.message(JiraFSM.waiting_screenshots)
async def jira_screenshots_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    files = data.get("files", [])

    kb_skip = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="skip_screenshots")]
    ])

    if message.photo:
        for photo in message.photo[-1:]:
            if photo.file_id not in files:
                files.append(photo.file_id)
        await state.update_data(files=files)
        await message.answer(
            f"✅ Скриншот добавлен. Всего файлов: {len(files)}\nПрикрепите ещё или нажмите 'Пропустить'.",
            reply_markup=kb_skip
        )
        return
    else:
        await message.answer(
            "⚠️ Пожалуйста, отправьте фото или нажмите 'Пропустить'.",
            reply_markup=kb_skip
        )
        return


# =======================
# Остальной функционал (HR, фото, текст, фоновые таски)
# =======================
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
        create_jira_ticket=create_jira_ticket_fsm
    )

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    await process_text_message(
        message=message,
        TRIGGER_TAGS=TRIGGER_TAGS,
        CHECK_TAG=CHECK_TAG,
        THREAD_PREFIXES=THREAD_PREFIXES,
        create_jira_ticket=create_jira_ticket_fsm,
        bot=bot,
        JIRA_URL=JIRA_URL
    )

async def run_background_task(coro_func, *args, interval: int = 60, **kwargs):
    while True:
        try:
            await coro_func(*args, **kwargs)
        except asyncio.CancelledError:
            logger.info("Фоновая задача отменена")
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

# =======================
# Запуск бота
# =======================
async def main():
    logger.info("🚀 Бот стартует")
    try:
        asyncio.create_task(check_calendar_events(bot, TESTERS_CHANNEL_ID))
        asyncio.create_task(start_reminders(bot, TESTERS_CHANNEL_ID))
        asyncio.create_task(run_background_task(jira_release_check, bot, TESTERS_CHANNEL_ID,
                                                JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY,
                                                JIRA_URL, logger, interval=500))
    except Exception as e:
        logger.exception("Ошибка запуска фоновых тасков: %s", e)

    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except Exception:
        logger.exception("Критическая ошибка при запуске")
