# Refactored main.py
# Улучшенная структура, безопасные фильтры, обработка фоновой задачи и логирование.

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

from hr_topics import HR_TOPICS
from photo_handler import handle_photo_message
from text_handler import process_text_message
from calendar_service import check_calendar_events
from daily_reminder import handle_jira_release_status, start_reminders
from release_notifier import jira_release_check
from site_checker import site_checker

# =======================
# Настройка окружения
# =======================
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
JIRA_EMAIL = os.getenv('JIRA_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY', 'AS')
JIRA_PARENT_KEY = os.getenv('JIRA_PARENT_KEY', 'AS-3150')
JIRA_URL = os.getenv('JIRA_URL', 'https://mechtamarket.atlassian.net')
ADMIN_ID = int(os.getenv('ADMIN_ID', '998292747'))
TESTERS_CHANNEL_ID = int(os.getenv('TESTERS_CHANNEL_ID', '-1002196628724'))

TRIGGER_TAGS = ['#bug', '#jira']
CHECK_TAG = '#check'
THREAD_PREFIXES = {1701: '[Back]', 1703: '[Front]'}


# =======================
# Логирование (встроенное)
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
# Dispatcher без параметров — современный стиль
dp = Dispatcher()


# =======================
# Утилиты
# =======================

def clean_summary(text: str, tags: List[str]) -> str:
    """Удаляет заданные теги из текста"""
    for tag in tags:
        text = re.sub(re.escape(tag), '', text, flags=re.IGNORECASE)
    return ' '.join(text.split()).strip()


def get_thread_prefix(message: Message) -> str:
    """Возвращает префикс подзадачи по thread_id"""
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
    # Отвечаем в том же чате
    await callback.message.answer(text)
    await callback.answer()


# =======================
# Обработка фото
# =======================
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    # передаём create_jira_ticket как callback
    await handle_photo_message(
        bot=bot,
        message=message,
        trigger_tags=TRIGGER_TAGS,
        create_jira_ticket=create_jira_ticket
    )


# =======================
# Обработка текста
# =======================
# Исключаем команды (начинающиеся с '/') чтобы не мешать стандартным командам
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    await process_text_message(
        message=message,
        TRIGGER_TAGS=TRIGGER_TAGS,
        CHECK_TAG=CHECK_TAG,
        THREAD_PREFIXES=THREAD_PREFIXES,
        create_jira_ticket=create_jira_ticket,
        bot=bot,
        JIRA_URL=JIRA_URL
    )


# =======================
# Создание задачи Jira
# =======================
async def create_jira_ticket(
        text: str,
        author: str,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        thread_prefix: str = ""
) -> Tuple[bool, Optional[str]]:

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    cleaned_text = clean_summary(text, TRIGGER_TAGS)
    # Включаем префикс, если он передан
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

    # Если нужно отключить верификацию (в dev/railway), делаем контекст
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession(auth=auth) as session:
        # Создание задачи
        try:
            async with session.post(f"{JIRA_URL}/rest/api/3/issue", json=payload, ssl=ssl_context) as resp:
                if resp.status != 201:
                    error = await resp.text()
                    logger.error("❌ Ошибка при создании задачи: %s — %s", resp.status, error)
                    return False, None

                result = await resp.json()
                issue_key = result.get("key")
                logger.info("✅ Задача %s создана", issue_key)
        except Exception as e:
            logger.exception("Ошибка при запросе к Jira: %s", e)
            return False, None

        # Формирование уведомления
        notify_text = (
            f"📨 Создан новый баг!\n"
            f"🔑 <b>{issue_key}</b>\n"
            f"👤 Автор: <b>{author}</b>\n\n"
            f"🔗 <a href=\"{JIRA_URL}/browse/{issue_key}\">Открыть задачу</a>\n\n"
            f"📝 <b>Описание:</b>\n{text}"
        )

        # Отправка уведомлений безопасно
        await send_safe(ADMIN_ID, notify_text)
        await send_safe(TESTERS_CHANNEL_ID, notify_text)

        # Прикрепление файла (если есть)
        if file_bytes and filename:
            attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
            attach_headers = {"X-Atlassian-Token": "no-check"}
            data = aiohttp.FormData()
            data.add_field('file', file_bytes, filename=filename, content_type='image/jpeg')

            try:
                async with session.post(attach_url, data=data, headers=attach_headers, ssl=ssl_context) as attach_resp:
                    if attach_resp.status in (200, 201):
                        logger.info("📎 Фото прикреплено к задаче %s", issue_key)
                    else:
                        error = await attach_resp.text()
                        logger.error("❌ Ошибка при вложении: %s — %s", attach_resp.status, error)
                        return False, None
            except Exception as e:
                logger.exception("Ошибка при прикреплении файла: %s", e)
                return False, None

    return True, issue_key


# =======================
# Фоновая задача — биндер
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

# callback для кнопки Jira Release
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
    asyncio.create_task(run_background_task(jira_release_check, bot, TESTERS_CHANNEL_ID, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_URL, logger, interval=1800))

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
