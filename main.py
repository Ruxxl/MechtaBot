import asyncio
import aiohttp
import ssl
import os
import re
import logging
from dotenv import load_dotenv
from typing import List, Tuple, Optional
from icalendar import Calendar
from datetime import datetime, timedelta
from dateutil import tz

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from hr_topics import HR_TOPICS
from photo_handler import handle_photo_message

# =======================
# Настройка окружения
# =======================
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
JIRA_EMAIL = os.getenv('JIRA_EMAIL')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY', 'AS')
JIRA_PARENT_KEY = os.getenv('JIRA_PARENT_KEY', 'AS-1679')
JIRA_URL = os.getenv('JIRA_URL', 'https://mechtamarket.atlassian.net')
ADMIN_ID = int(os.getenv('ADMIN_ID', '998292747'))
TESTERS_CHANNEL_ID = int(os.getenv('TESTERS_CHANNEL_ID', '-1002196628724'))

TRIGGER_TAGS = ['#bug', '#jira']
CHECK_TAG = '#check'
THREAD_PREFIXES = {1701: '[Back]', 1703: '[Front]'}

# =======================
# Логирование
# =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# =======================
# Инициализация бота
# =======================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
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
    return THREAD_PREFIXES.get(message.message_thread_id, '')

# =======================
# Команды
# =======================
@dp.message(F.text == "/getid")
async def get_chat_id(message: Message):
    await message.reply(f"Chat ID: <code>{message.chat.id}</code>")

# =======================
# HR Меню
# =======================
@dp.message(F.text.lower().contains("#hr"))
async def hr_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=HR_TOPICS["attendance"]["title"], callback_data="hr_attendance")],
        [InlineKeyboardButton(text=HR_TOPICS["bs_order"]["title"], callback_data="hr_bs_order")],
        [InlineKeyboardButton(text=HR_TOPICS["business_trip"]["title"], callback_data="hr_business_trip")]
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
        bot,
        message,
        trigger_tags=TRIGGER_TAGS,
        create_jira_ticket=create_jira_ticket
    )

# =======================
# Обработка текста
# =======================
@dp.message(F.text)
async def handle_text(message: Message):
    text = message.text or ""
    text_lower = text.lower()
    logger.info(f"✉️ Получено сообщение: {text}")

    if CHECK_TAG in text_lower:
        await message.reply("✅ Бот работает и готов принимать задачи.")
        return

    if any(tag in text_lower for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
        success, issue_key = await create_jira_ticket(
            text,
            message.from_user.full_name,
            file_bytes=None,
            filename=None,
            thread_prefix=get_thread_prefix(message)
        )
        if success:
            await message.reply(
                f"✅ Задача <b>{issue_key}</b> создана!\n"
                f"🔗 <a href='{JIRA_URL}/browse/{issue_key}'>{JIRA_URL}/browse/{issue_key}</a>"
            )
        else:
            await message.reply("❌ Ошибка при создании задачи в Jira.")

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
    summary = f"[Telegram] {cleaned_text}".strip()[:255]

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
        # Создание задачи
        async with session.post(f"{JIRA_URL}/rest/api/3/issue", json=payload, ssl=ssl_context) as resp:
            if resp.status != 201:
                error = await resp.text()
                logger.error(f"❌ Ошибка при создании задачи: {resp.status} — {error}")
                return False, None

            result = await resp.json()
            issue_key = result["key"]
            logger.info(f"✅ Задача {issue_key} создана")

        # Формирование уведомления
        notify_text = (
            f"📨 Создан новый баг!\n"
            f"🔑 <b>{issue_key}</b>\n"
            f"👤 Автор: <b>{author}</b>\n\n"
            f"🔗 <a href=\"{JIRA_URL}/browse/{issue_key}\">Открыть задачу</a>\n\n"
            f"📝 <b>Описание:</b>\n{text}"
        )

        # Отправка уведомлений
        try:
            await bot.send_message(ADMIN_ID, notify_text)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")

        try:
            await bot.send_message(TESTERS_CHANNEL_ID, notify_text)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление в канал: {e}")

        # Прикрепление файла
        if file_bytes and filename:
            attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
            attach_headers = {"X-Atlassian-Token": "no-check"}
            data = aiohttp.FormData()
            data.add_field('file', file_bytes, filename=filename, content_type='image/jpeg')

            async with session.post(attach_url, data=data, headers=attach_headers, ssl=ssl_context) as attach_resp:
                if attach_resp.status in (200, 201):
                    logger.info(f"📎 Фото прикреплено к задаче {issue_key}")
                else:
                    error = await attach_resp.text()
                    logger.error(f"❌ Ошибка при вложении: {attach_resp.status} — {error}")
                    return False, None

    return True, issue_key

# =======================
# Уведомления по календарю
# =======================
ICS_URL = "https://calendar.yandex.ru/export/ics.xml?private_token=dba95cc621742f7b9ba141889e288d2e0987fae3&tz_id=Asia/Almaty"
CHECK_INTERVAL = 60
ALERT_BEFORE = timedelta(minutes=5)
calendar_sent_notifications = set()

async def fetch_calendar():
    async with aiohttp.ClientSession() as session:
        async with session.get(ICS_URL) as resp:
            if resp.status == 200:
                data = await resp.text()
                cal = Calendar.from_ical(data)
                return cal
            else:
                logger.error(f"Ошибка при получении ICS: {resp.status}")
                return None

async def check_calendar_events():
    while True:
        cal = await fetch_calendar()
        if cal:
            now = datetime.now(tz=tz.gettz("Asia/Almaty"))
            for component in cal.walk():
                if component.name == "VEVENT":
                    start = component.get('dtstart').dt
                    summary = component.get('summary')
                    attendees = component.get('attendee')

                    if attendees:
                        if isinstance(attendees, list):
                            attendees_list = [str(a) for a in attendees]
                        else:
                            attendees_list = [str(attendees)]
                        attendees_text = ", ".join(attendees_list)
                    else:
                        attendees_text = "не указаны"

                    alert_time = start - ALERT_BEFORE
                    if alert_time <= now < start and summary not in calendar_sent_notifications:
                        text = (
                            f"📅 Встреча скоро начнется!\n"
                            f"📝 Название: <b>{summary}</b>\n"
                            f"👥 Участники: {attendees_text}\n"
                            f"⏰ Начало: {start.strftime('%H:%M %d.%m.%Y')}"
                        )
                        try:
                            await bot.send_message(TESTERS_CHANNEL_ID, text)
                            calendar_sent_notifications.add(summary)
                            logger.info(f"Отправлено уведомление по календарю: {summary}")
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

# =======================
# Запуск бота
# =======================
async def main():
    logger.info("🚀 Бот запущен и ждет сообщений")
    asyncio.create_task(check_calendar_events())  # запускаем календарь
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
