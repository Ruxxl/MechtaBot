import asyncio
import os
import logging
from dotenv import load_dotenv
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Твои импорты
from hr_topics import HR_TOPICS
from photo_handler import handle_photo_message
from text_handler import process_text_message, THREAD_PREFIXES
from calendar_service import check_calendar_events
from daily_reminder import handle_jira_release_status, start_reminders
from release_notifier import jira_release_check
from jira_fsm import register_jira_handlers, create_jira_issue
from webhook_handler import WebhookHandler

# =======================
# Настройка окружения
# =======================
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
# Твои конкретные ID
TARGET_GROUP_ID = -1002196628724
TARGET_THREAD_ID = 42896

# Jira Config
JIRA_CONFIG = {
    'email': os.getenv('JIRA_EMAIL'),
    'token': os.getenv('JIRA_API_TOKEN'),
    'project': os.getenv('JIRA_PROJECT_KEY', 'AS'),
    'parent': os.getenv('JIRA_PARENT_KEY', 'AS-3312'),
    'url': os.getenv('JIRA_URL', 'https://mechtamarket.atlassian.net').rstrip('/')
}

JIRA_URL = JIRA_CONFIG['url'] # Для совместимости с text_handler

TRIGGER_TAGS = ['#bug', '#jira']
CHECK_TAG = '#check'

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

# Инициализируем обработчик вебхуков (теперь он доступен глобально для команд)
webhook_handler = WebhookHandler(bot=bot, target_group_id=TARGET_GROUP_ID, target_thread_id=TARGET_THREAD_ID)

# =======================
# Веб-сервер для Render
# =======================
async def handle_web_root(request):
    return web.Response(text="Bot is alive!")

async def start_web_server(handler: WebhookHandler):
    app = web.Application()
    
    app.router.add_get('/', handle_web_root)
    app.router.add_post('/webhook/notify', handler.handle_notification)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    logger.info(f"🌐 Веб-сервер запущен на порту {port}")
    await site.start()

# Регистрация хендлеров для работы с Jira через FSM
logger.info("📝 Регистрация хендлеров Jira FSM...")
register_jira_handlers(
    dp=dp, 
    bot=bot, 
    jira_config=JIRA_CONFIG, 
    target_group_id=TARGET_GROUP_ID, 
    target_thread_id=TARGET_THREAD_ID
)

# =======================
# Обработчики (Handlers)
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

@dp.message(F.text == "/stands")
async def show_stands_status(message: Message):
    # 1. Формируем клавиатуру с кнопками-ссылками
    buttons = []
    for name, url in webhook_handler.stand_urls.items():
        # Делаем название кнопки короче и красивее
        btn_label = name.replace("deploy ", "").upper()
        if "EXTERNAL" in btn_label: btn_label = "INTEGRATIONS"
        if "SSR PROD" in btn_label: btn_label = "PRODUCTION"
        
        buttons.append(InlineKeyboardButton(text=f"🖥 {btn_label}", url=url))
    
    # Группируем кнопки по 2 в ряд
    kb_rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    if not webhook_handler.latest_builds:
        await message.answer(
            "📭 Данных о последних сборках пока нет, но вы можете перейти на стенды по кнопкам ниже:",
            reply_markup=kb
        )
        return

    report = "🖥️ <b>Последние сборки на стендах:</b>\n\n"
    for stand, info in webhook_handler.latest_builds.items():
        report += (
            f"📍 <b>Стенд:</b> {stand}\n"
            f"📝 <b>Коммит:</b> <i>{info['commit']}</i>\n"
            f"👤 <b>Инициатор:</b> @{info['actor']}\n"
            f"📅 <b>Дата:</b> {info['date']}\n"
            f"───────────────────\n"
        )
    await message.answer(report, reply_markup=kb, disable_web_page_preview=True)

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    # Обертка для соответствия интерфейсу
    async def jira_wrapper(text, author, file_bytes, filename, thread_prefix):
        key = await create_jira_issue(
            bot=bot, jira_config=JIRA_CONFIG, 
            title=text[:50], description=text, author=author,
            files=[message.photo[-1].file_id], thread_prefix=thread_prefix
        )
        return bool(key), key

    await handle_photo_message(
        bot=bot, message=message, trigger_tags=TRIGGER_TAGS, 
        create_jira_ticket=jira_wrapper, jira_url=JIRA_URL
    )

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    async def jira_wrapper(text, author, file_bytes, filename, thread_prefix):
        key = await create_jira_issue(
            bot=bot, jira_config=JIRA_CONFIG, 
            title=text[:50], description=text, author=author,
            thread_prefix=thread_prefix
        )
    await process_text_message(
        message=message, TRIGGER_TAGS=TRIGGER_TAGS, CHECK_TAG=CHECK_TAG, 
        THREAD_PREFIXES=THREAD_PREFIXES, create_jira_ticket=jira_wrapper, bot=bot, JIRA_URL=JIRA_URL
    )

@dp.callback_query(F.data == "jira_release_status")
async def callback_jira_release_status(callback: CallbackQuery):
    await handle_jira_release_status(
        callback, 
        JIRA_CONFIG['email'], 
        JIRA_CONFIG['token'], 
        JIRA_CONFIG['project'], 
        JIRA_CONFIG['url']
    )

# =======================
# Фоновая обертка
# =======================
async def run_background_task(coro_func, *args, interval: int = 60, **kwargs):
    while True:
        try:
            await coro_func(*args, **kwargs)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Ошибка в задаче {coro_func.__name__}: {e}")
        await asyncio.sleep(interval)

# =======================
# Главная функция
# =======================
async def main():
    logger.info("🚀 Бот стартует")

    # Запуск Health Check сервера
    logger.info("🌐 Запуск веб-сервера (Health Check & Webhooks)...")
    asyncio.create_task(start_web_server(webhook_handler))

    # 1. Сервисы календаря и напоминаний
    logger.info("📅 Запуск мониторинга календаря...")
    asyncio.create_task(check_calendar_events(bot, TARGET_GROUP_ID))
    logger.info("⏰ Запуск ежедневных напоминаний...")
    asyncio.create_task(start_reminders(bot, TARGET_GROUP_ID, TARGET_THREAD_ID))

    # 2. Мониторинг релизов Jira
    logger.info("📦 Запуск фонового мониторинга релизов Jira...")
    asyncio.create_task(run_background_task(
        jira_release_check, 
        bot, 
        TARGET_GROUP_ID, 
        JIRA_CONFIG['email'], 
        JIRA_CONFIG['token'], 
        JIRA_CONFIG['project'], 
        JIRA_CONFIG['url'], 
        logger, 
        100, 
        thread_id=TARGET_THREAD_ID
    ))

    # 4. Очистка вебхуков
    logger.info("🧹 Очистка старых вебхуков...")
    await bot.delete_webhook(drop_pending_updates=True)

    # Запуск Polling
    logger.info(f"Запуск polling для группы {TARGET_GROUP_ID}, топик {TARGET_THREAD_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")