# Refactored main.py
# Рабочий вариант с исправленным Jira FSM

import asyncio
import aiohttp
import ssl
import os
import re
import logging
from functools import partial
from dotenv import load_dotenv
from typing import List, Tuple, Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from hr_topics import HR_TOPICS
from photo_handler import handle_photo_message
from text_handler import process_text_message
from calendar_service import check_calendar_events
from daily_reminder import handle_jira_release_status, start_reminders
from release_notifier import jira_release_check
from jira_fsm import JiraFSM, start_jira_fsm, jira_title_step, jira_description_step, \
                     jira_priority_step, jira_links_step, jira_screenshots_step, create_jira_ticket_extended

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
        await bot.send_message(chat_id, text)
    except Exception as e:
        logger.error("Не удалось отправить сообщение %s: %s", chat_id, e)

# =======================
# Команды
# =======================
@dp.message(F.text == "/getid")
async def get_chat_id(message: Message):
    await message.reply(f"Chat ID: <code>{message.chat.id}</code>")

# =======================
# HR Меню
# =======================
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

# =======================
# Обработка фото
# =======================
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    await handle_photo_message(
        bot=bot,
        message=message,
        trigger_tags=TRIGGER_TAGS,
        create_jira_ticket=create_jira_ticket_extended
    )

# =======================
# Обработка текста вне FSM
# =======================
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, state: FSMContext):
    if await state.get_state():
        return  # пропускаем, если пользователь в FSM
    await process_text_message(
        message=message,
        TRIGGER_TAGS=TRIGGER_TAGS,
        CHECK_TAG=CHECK_TAG,
        THREAD_PREFIXES=THREAD_PREFIXES,
        create_jira_ticket=create_jira_ticket_extended,
        bot=bot,
        JIRA_URL=JIRA_URL
    )

# =======================
# Callback Jira Release
# =======================
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
# Jira FSM регистрация
# =======================
dp.message.register(start_jira_fsm, Command(commands=["jira"]))

dp.message.register(jira_title_step, JiraFSM.waiting_title)
dp.message.register(jira_description_step, JiraFSM.waiting_description)
dp.message.register(jira_priority_step, JiraFSM.waiting_priority)
dp.message.register(jira_links_step, JiraFSM.waiting_links)

dp.message.register(
    partial(
        jira_screenshots_step,
        JIRA_EMAIL=JIRA_EMAIL,
        JIRA_API_TOKEN=JIRA_API_TOKEN,
        JIRA_PROJECT_KEY=JIRA_PROJECT_KEY,
        JIRA_PARENT_KEY=JIRA_PARENT_KEY,
        JIRA_URL=JIRA_URL
    ),
    JiraFSM.waiting_screenshots,
    F.text | F.photo
)

# =======================
# Фоновая задача
# =======================
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

# =======================
# Запуск бота
# =======================
async def main():
    logger.info("🚀 Бот стартует")

    # 1) Запускаем календарный сервис как таск (если check_calendar_events содержит свой loop)
    try:
        asyncio.create_task(check_calendar_events(bot, TESTERS_CHANNEL_ID))
        logger.info("Запущен check_calendar_events в фоне")
    except Exception as e:
        logger.exception("Не удалось запустить check_calendar_events: %s", e)

    # 2) Запускаем ежедневные напоминания тоже в фоне (не await!)
    try:
        asyncio.create_task(start_reminders(bot, TESTERS_CHANNEL_ID))
        logger.info("Запущен start_reminders в фоне")
    except Exception as e:
        logger.exception("Не удалось запустить start_reminders: %s", e)

    # 3) Запуск мониторинга релизов Jira (каждые 30 мин)
    asyncio.create_task(run_background_task(jira_release_check, bot, TESTERS_CHANNEL_ID, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_URL, logger, interval=500))

    # 5) Теперь запускаем polling — он держит главный цикл
    logger.info("Запуск polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except Exception:
        logger.exception("Критическая ошибка при запуске")
