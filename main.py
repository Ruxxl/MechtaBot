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
from data.hr_topics import HR_TOPICS
from handlers.photo_handler import handle_photo_message
from handlers.personal_photo_handler import handle_personal_photo, handle_personal_text
from handlers.text_handler import process_text_message, THREAD_PREFIXES
from services.calendar_service import check_calendar_events, ICS_URL
from handlers.daily_reminder import handle_jira_release_status, start_reminders
from monitors.release_notifier import jira_release_check
from handlers.jira_fsm import register_jira_handlers, create_jira_issue
from web.webhook_handler import WebhookHandler
from services.translator_service import register_translator_handlers
from web.admin_handler import AdminHandler, monitor
from services.ai_service import ai_service
from monitors.code_review_handler import run_code_review_monitor
from handlers.vision_handler import handle_vision_message
from monitors.monthly_report import check_monthly_report, register_monthly_report_handlers
from handlers.stress_handler import register_stress_handlers, trigger_smoke_test
from handlers.faq_handler import register_faq_handlers
from handlers.team_tasks_handler import register_team_tasks_handlers
from handlers.help_handler import register_help_handlers
from handlers.bugreport_handler import register_bugreport_handlers
from services.db_service import init_db, close_db, get_latest_builds as get_stand_builds_from_db
from web.miniapp_api import setup_miniapp_routes




# =======================
# Настройка окружения
# =======================
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
# Твои конкретные ID
TARGET_GROUP_ID = -1002196628724
TARGET_THREAD_ID = 42896
VISION_THREAD_ID = 1886
TRANSLATION_THREAD_ID = 12741  # Укажи здесь ID темы для перевода
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
PERSONAL_CALENDAR_URL = "https://calendar.yandex.kz/export/ics.xml?private_token=11da362d0fa9b6f7260c4d97a3113fbba258a129&tz_id=Asia/Tashkent"
PERSONAL_CHAT_ID = 998292747

# Jira Config
JIRA_CONFIG = {
    'email': os.getenv('JIRA_EMAIL'),
    'token': os.getenv('JIRA_API_TOKEN'),
    'project': os.getenv('JIRA_PROJECT_KEY', 'AS'),
    'parent': os.getenv('JIRA_PARENT_KEY', 'AS-3312'),
    'url': os.getenv('JIRA_URL', 'https://mechtamarket.atlassian.net').rstrip('/')
}

JIRA_URL = JIRA_CONFIG['url'] # Для совместимости с text_handler

# Confluence Config — по умолчанию используем тот же email/token, что и в Jira
# (на Atlassian Cloud это обычно один и тот же аккаунт), но их можно переопределить
# отдельными переменными окружения.
CONFLUENCE_CONFIG = {
    'url': os.getenv('CONFLUENCE_URL') or (JIRA_CONFIG['url'] + '/wiki'),
    'email': os.getenv('CONFLUENCE_EMAIL') or JIRA_CONFIG['email'],
    'token': os.getenv('CONFLUENCE_API_TOKEN') or JIRA_CONFIG['token'],
    'space': os.getenv('CONFLUENCE_SPACE_KEY') or None,
    # Опционально: ограничить поиск конкретной страницей (используется только
    # если parent_page_id ниже не задан).
    'page_id': os.getenv('CONFLUENCE_PAGE_ID') or None,
    # Родительская страница "Чек листы" — /ask будет искать по ВСЕМ её
    # дочерним страницам (включая новые, которые добавят позже), а не по
    # одной закрепленной странице. Имеет приоритет над page_id.
    'parent_page_id': os.getenv('CONFLUENCE_PARENT_PAGE_ID', '806060055'),
}

TRIGGER_TAGS = ['#bug', '#jira']
CHECK_TAG = '#check'

# Список воркфлоу (имена как в GitHub Actions .yml, регистр не важен), после
# успешного деплоя которых автоматически запускается smoke-тест на стенд.
# По умолчанию — только preprod, чтобы не грузить прод/остальные стенды без ведома.
SMOKE_TEST_WORKFLOWS = {
    w.strip().lower()
    for w in os.getenv("SMOKE_TEST_WORKFLOWS", "deploy preprod").split(",")
    if w.strip()
}

# =======================
# Логирование
# =======================
class ConflictFilter(logging.Filter):
    """Фильтр для исключения ошибок конфликта сессий из логов."""
    def filter(self, record):
        return "Conflict: terminated by other getUpdates request" not in record.getMessage()

def setup_logger():
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logger = logging.getLogger("bot")
    logger.setLevel(logging.INFO)
    
    # Добавляем фильтр на корневой логгер, чтобы он применялся ко всем обработчикам
    logging.getLogger().addFilter(ConflictFilter())
    
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[logging.StreamHandler(), monitor])
    return logger

logger = setup_logger()

# =======================
# Инициализация бота
# =======================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Initialize aiohttp application globally
app = web.Application()
dp = Dispatcher()

# Коллбэк для WebhookHandler: запускает smoke-тест после успешного деплоя
# нужного стенда (см. SMOKE_TEST_WORKFLOWS выше и handlers/stress_handler.py)
async def _smoke_test_callback(host: str, env_label: str):
    # ВРЕМЕННО ОТКЛЮЧЕНО: нагрузочное тестирование
    # await trigger_smoke_test(bot, TARGET_GROUP_ID, TARGET_THREAD_ID, host, env_label)
    logger.info(f"⏸ Smoke-тест для '{env_label}' пропущен — функция временно отключена.")
    

# Инициализируем обработчик вебхуков (теперь он доступен глобально для команд)
webhook_handler = WebhookHandler(
    bot=bot, 
    target_group_id=TARGET_GROUP_ID, 
    target_thread_id=TARGET_THREAD_ID,
    github_token=os.getenv('GITHUB_TOKEN'),
    repo_full_name=os.getenv('GITHUB_REPO'), # например "mechta-kz/my-repo"
    smoke_test_callback=_smoke_test_callback,
    smoke_test_workflows=SMOKE_TEST_WORKFLOWS,
)


setup_miniapp_routes(app, services={ # 'app' is now defined
    "jira": jira_client,           # твой существующий Jira-клиент
    "github_events": github_store, # хранилище последних webhook-событий
    "stands": stands_config,       # то, что уже отдаёт /stands
    "ask": ask_engine,             # confluence-поиск из /ask
    "help": None,                  # пока не структурировано — оставь None
    "releases": None,
})

# =======================
# Веб-сервер для Render
# =======================
async def handle_web_root(request):
    return web.Response(text="Bot is alive!")

async def start_web_server(app: web.Application, webhook_h: WebhookHandler, bot_info: types.User): # Pass app as argument

    admin_h = AdminHandler(bot_username=bot_info.username)

    app.router.add_get('/', handle_web_root)
    app.router.add_post('/webhook/notify', webhook_h.handle_notification)
    app.router.add_get('/admin', admin_h.handle_dashboard)
    
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

# Регистрация хендлеров нагрузочного тестирования (/stress)
logger.info("🚦 Регистрация хендлеров нагрузочного тестирования...")
# ВРЕМЕННО ОТКЛЮЧЕНО: нагрузочное тестирование
# register_stress_handlers(dp=dp, bot=bot)

# Регистрация команды /team — список пользователей Jira и их задачи в спринте
logger.info("👥 Регистрация хендлеров команды /team...")
register_team_tasks_handlers(dp=dp, bot=bot, jira_config=JIRA_CONFIG)

# Регистрация команды /monthreport — отчет с 1-го числа по текущий момент
logger.info("📊 Регистрация команды /monthreport...")
register_monthly_report_handlers(dp=dp, bot=bot, jira_config=JIRA_CONFIG)

# рядом с register_team_tasks_handlers(...)
logger.info("🐛 Регистрация хендлера /bugreport...")
register_bugreport_handlers(
    dp=dp,
    bot=bot,
    jira_config=JIRA_CONFIG,
    create_jira_issue_func=create_jira_issue,
)

# Регистрация /help и /start — интерактивное меню возможностей бота
logger.info("ℹ️ Регистрация хендлеров /help...")
register_help_handlers(dp=dp, bot=bot)

# Регистрация FAQ-бота (/ask, #faq) — поиск по Confluence + ответ через Groq.
# Регистрируется до общих текстовых хендлеров ниже по файлу, чтобы тег #faq
# перехватывался раньше общей Jira-логики (см. handle_text ниже).
logger.info("📚 Регистрация FAQ-бота (Confluence)...")
register_faq_handlers(dp=dp, bot=bot, confluence_config=CONFLUENCE_CONFIG)

# Инициализация AI сервисов
ai_service.init_groq(GROQ_API_KEY)


# Регистрация переводчика для конкретной темы
if TRANSLATION_THREAD_ID:
    logger.info(f"🌐 Регистрация переводчика для темы {TRANSLATION_THREAD_ID}")
    register_translator_handlers(dp, TRANSLATION_THREAD_ID)

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

def get_stands_keyboard():
    """Формирует клавиатуру со списком всех стендов"""
    buttons = []
    for name in webhook_handler.stand_urls.keys():
        btn_label = name.replace("deploy ", "").upper()
        if "EXTERNAL" in btn_label: btn_label = "INTEGRATIONS"
        if "SSR PROD" in btn_label: btn_label = "PRODUCTION"
        
        buttons.append(InlineKeyboardButton(text=f"🖥 {btn_label}", callback_data=f"stand_info:{name}"))
    
    kb_rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)

@dp.message(F.text == "/stands")
async def show_stands_status(message: Message):
    await message.answer(
        "Выберите стенд, чтобы узнать детали последней сборки:",
        reply_markup=get_stands_keyboard()
    )

@dp.callback_query(F.data == "back_to_stands")
async def handle_back_to_stands(callback: CallbackQuery):
    """Возврат к основному списку стендов"""
    await callback.message.edit_text(
        "Выберите стенд, чтобы узнать детали последней сборки:",
        reply_markup=get_stands_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("stand_info:"))
async def handle_stand_info_callback(callback: CallbackQuery):
    stand_key = callback.data.split(":", 1)[1]
    url = webhook_handler.stand_urls.get(stand_key)
    # 1. Пробуем получить свежие данные напрямую из API (возвращает список)
    builds = await webhook_handler.fetch_latest_build_from_api(stand_key)

    # 2. Если API недоступно, берем из локального кэша (от вебхуков)
    if not builds:
        builds = webhook_handler.latest_builds.get(stand_key.upper())

    # 3. Если и кэш пуст (например, бот только что перезапустился) — БД
    if not builds:
        builds = await get_stand_builds_from_db(stand_key)
    
    # Формируем красивое имя для заголовка
    stand_name_display = stand_key.replace("deploy ", "").upper()
    if "EXTERNAL" in stand_name_display: stand_name_display = "INTEGRATIONS"
    if "SSR PROD" in stand_name_display: stand_name_display = "PRODUCTION"
    
    # 1. Пробуем получить свежие данные напрямую из API (возвращает список)
    builds = await webhook_handler.fetch_latest_build_from_api(stand_key)
    
    # 2. Если API недоступно, берем из локального кэша (от вебхуков)
    if not builds:
        builds = webhook_handler.latest_builds.get(stand_key.upper())
    
    if not builds:
        text = f"📍 <b>Стенд:</b> {stand_name_display}\n\n📭 Данных о последних сборках пока нет."
        latest = None
    else:
        latest = builds[0]
        lines = [f"📍 <b>Стенд:</b> {stand_name_display}\n"]
        for i, build in enumerate(builds):
            prefix = "🟢 <b>Последний</b>" if i == 0 else f"🔹 #{i + 1}"
            lines.append(
                f"{prefix}\n"
                f"   📝 <i>{build['commit']}</i>\n"
                f"   👤 @{build['actor']}   📅 {build['date']}"
            )
        text = "\n\n".join(lines)
    
    # Формируем кнопки
    kb_list = [[InlineKeyboardButton(text="🌐 Открыть стенд", url=url)]] if url else []
    if latest and latest.get("url"):
        kb_list.append([InlineKeyboardButton(text="🛠 Последний билд в GitHub", url=latest["url"])])
    kb_list.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_stands")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
    
    await callback.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    await callback.answer()

# Фото, присланное боту в ЛИЧНЫЕ сообщения -> пересылаем в основной чат.
# Зарегистрирован ВЫШЕ общего @dp.message(F.photo), чтобы перехватывать
# личные фото раньше Jira-логики (которая рассчитана на группу).
@dp.message(F.photo, F.chat.type == "private")
async def handle_private_photo(message: types.Message):
    await handle_personal_photo(
        bot=bot,
        message=message,
        target_group_id=TARGET_GROUP_ID,
        target_thread_id=TARGET_THREAD_ID,
    )

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    caption = message.caption or ""
    caption_lower = caption.lower()
 
    # Есть Jira-тег → создаём задачу (старое поведение, работает во всех топиках)
    if any(tag in caption_lower for tag in TRIGGER_TAGS):
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
        return
 
    # Нет тега → анализ скриншота ТОЛЬКО в топике VISION_THREAD_ID (1886)
    #if message.message_thread_id == VISION_THREAD_ID:
     #   await handle_vision_message(bot=bot, message=message)

@dp.message(F.text & ~F.text.startswith("/"), F.chat.type == "private")
async def handle_private_text(message: Message):
    await handle_personal_text(
        bot=bot,
        message=message,
        target_group_id=TARGET_GROUP_ID,
        target_thread_id=TARGET_THREAD_ID,
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

    bot_user = await bot.get_me()

    # Инициализация AI сервисов (Groq / Gemini)
    # Инициализация AI сервиса (Groq)
    # Инициализация AI сервиса
    logger.info("⚙️ Инициализация Groq AI сервиса...")
    ai_service.init_groq(GROQ_API_KEY)

    logger.info("🗄 Подключение к базе данных...")
    await init_db()

    # Запуск Health Check сервера
    logger.info("🌐 Запуск веб-сервера (Health Check & Webhooks)...") # Pass the globally defined 'app'
    asyncio.create_task(start_web_server(app, webhook_handler, bot_user))

    # Обновляем начальные статусы в админке
    monitor.update_status("Core", "OK")
    monitor.update_status("GitHub Webhooks", "OK")
    monitor.update_status("Calendar Service", "OK")
    monitor.update_status("Daily Reminders", "OK")
    monitor.update_status("Jira Release Monitor", "OK")
    monitor.update_status("Translator Service", "OK")
    monitor.update_status("Jira FSM", "OK")
    monitor.update_status("Monthly Report", "OK")
    monitor.update_status("Stress Test", "OK")
    monitor.update_status("FAQ Bot", "OK")

    # 1. Сервисы календаря и напоминаний
    logger.info("📅 Запуск мониторинга календаря...")
    # Мониторинг общего календаря группы
    asyncio.create_task(check_calendar_events(bot, TARGET_GROUP_ID, ICS_URL))
    # Мониторинг твоего личного календаря
    asyncio.create_task(check_calendar_events(bot, PERSONAL_CHAT_ID, PERSONAL_CALENDAR_URL))

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

    # 3. Мониторинг Code Review
    logger.info("🔍 Запуск мониторинга Code Review...")
    asyncio.create_task(run_code_review_monitor(
        bot, 
        TARGET_GROUP_ID, 
        TARGET_THREAD_ID, 
        JIRA_CONFIG['email'], 
        JIRA_CONFIG['token'], 
        JIRA_CONFIG['url'], 
        JIRA_CONFIG['project']
    ))

    # 3.1 Ежемесячный отчет по релизам/задачам/багам (1-30 число, отправка 30-го)
    logger.info("📊 Запуск мониторинга ежемесячного отчета...")
    asyncio.create_task(run_background_task(
        check_monthly_report,
        bot,
        TARGET_GROUP_ID,
        TARGET_THREAD_ID,
        JIRA_CONFIG['email'],
        JIRA_CONFIG['token'],
        JIRA_CONFIG['project'],
        JIRA_CONFIG['url'],
        interval=3600  # проверяем раз в час, отправит отчет только в отчетный день
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
