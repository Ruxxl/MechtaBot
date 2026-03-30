import asyncio
import os
import logging
from dotenv import load_dotenv

# Добавляем aiohttp для веб-сервера
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Твои импорты
from hr_topics import HR_TOPICS
from photo_handler import handle_photo_message
from text_handler import process_text_message
from calendar_service import check_calendar_events
from daily_reminder import handle_jira_release_status, start_reminders
from release_notifier import jira_release_check
from jira_fsm import register_jira_handlers

# =======================
# Настройка окружения
# =======================
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
JIRA_EMAIL = os.getenv('JIRA_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY', 'AS')
JIRA_PARENT_KEY = os.getenv('JIRA_PARENT_KEY', 'AS-3312')
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
# Веб-сервер для Render (Health Check)
# =======================
async def handle_web_root(request):
    return web.Response(text="Bot is alive and kicking!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_web_root)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render передает порт в переменную окружения PORT
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    logger.info(f"🌐 Веб-сервер запущен на порту {port}")
    await site.start()

# =======================
# Инициализация бота
# =======================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

register_jira_handlers(dp, bot, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_PARENT_KEY, JIRA_URL)

# --- Обработчики (Твои без изменений) ---

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
    await handle_photo_message(bot=bot, message=message, trigger_tags=TRIGGER_TAGS, create_jira_ticket=None)

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    await process_text_message(message=message, TRIGGER_TAGS=TRIGGER_TAGS, CHECK_TAG=CHECK_TAG, 
                               THREAD_PREFIXES=THREAD_PREFIXES, create_jira_ticket=None, bot=bot, JIRA_URL=JIRA_URL)

async def run_background_task(coro_func, *args, interval: int = 60, **kwargs):
    while True:
        try:
            await coro_func(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Ошибка в фоновой задаче {coro_func.__name__}: {e}")
        await asyncio.sleep(interval)

@dp.callback_query(F.data == "jira_release_status")
async def callback_jira_release_status(callback: CallbackQuery):
    await handle_jira_release_status(callback, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_URL)

# =======================
# Запуск бота
# =======================
async def main():
    logger.info("🚀 Бот стартует")

    # 1) Запускаем Веб-сервер для Render (чтобы не было ошибки портов)
    asyncio.create_task(start_web_server())

    # 2) Фоновые задачи
    try:
        asyncio.create_task(check_calendar_events(bot, TESTERS_CHANNEL_ID))
        asyncio.create_task(start_reminders(bot, TESTERS_CHANNEL_ID))
    except Exception as e:
        logger.exception(f"Ошибка запуска сервисов: {e}")

    # 3) Запуск мониторинга релизов Jira
    asyncio.create_task(run_background_task(jira_release_check, bot, TESTERS_CHANNEL_ID, 
                                           JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_URL, logger, interval=100))

    # 4) Удаляем вебхуки перед поллингом (на всякий случай)
    await bot.delete_webhook(drop_pending_updates=True)

    # 5) Запуск polling
    logger.info("Запуск polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка бота")
    except Exception:
        logger.exception("Критическая ошибка при запуске")
