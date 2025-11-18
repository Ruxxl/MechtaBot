import asyncio
import aiohttp
import ssl
import re
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from icalendar import Calendar
import datetime
from aiogram.types import InputFile
from aiogram.types import FSInputFile


load_dotenv()

# --- Переменные окружения ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.environ.get('JIRA_PROJECT_KEY', 'AS')
JIRA_PARENT_KEY = os.environ.get('JIRA_PARENT_KEY', 'AS-1679')
JIRA_URL = os.environ.get('JIRA_URL', 'https://mechtamarket.atlassian.net')

ADMIN_ID = int(os.environ.get('ADMIN_ID', '998292747'))
TESTERS_CHANNEL_ID = int(os.environ.get('TESTERS_CHANNEL_ID', '-1002196628724'))

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()  # Aiogram v3+

# --- Настройки календаря ---
ICS_URL = "https://calendar.yandex.ru/export/ics.xml?private_token=dba95cc621742f7b9ba141889e288d2e0987fae3&tz_id=Asia/Almaty"
CHECK_INTERVAL = 60  # проверка календаря каждые 60 секунд
NOTIFY_MINUTES = 5   # уведомление за 60 минут до события

# Подписанные чаты на уведомления
# Подписка на чат "Тестировщики" сразу
subscribed_chats = {TESTERS_CHANNEL_ID}

# --- HR темы ---
HR_TOPICS = {
    "attendance": {
        "title": "Отметки приход/уход",
        "text": "Все запросы об отметках (приход/уход) необходимо направлять на <b>Адильжана</b>."
    },
    "bs_order": {
        "title": "Порядок согласования БС",
        "text": "Обновлён порядок согласования заявок в Битриксе..."
    },
    "business_trip": {
        "title": "Командировка ✈️",
        "text": "✈️ Инструкция по командировке..."
    }
}

# --- Telegram команды ---
@dp.message(F.text == "/getid")
async def get_chat_id(message: Message):
    await message.reply(f"Chat ID: <code>{message.chat.id}</code>")

@dp.message(F.text == "/start")
async def start(message: Message):
    subscribed_chats.add(message.chat.id)
    await message.reply("✅ Этот чат подписан на уведомления о событиях из календаря!")

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

# --- Jira обработка ---
TRIGGER_TAGS = ['#bug', '#jira']
CHECK_TAG = '#check'
THREAD_PREFIXES = {1701: '[Back]', 1703: '[Front]'}

def clean_summary(text: str, tags: list[str]) -> str:
    for tag in tags:
        text = re.sub(re.escape(tag), '', text, flags=re.IGNORECASE)
    return ' '.join(text.split()).strip()

def get_thread_prefix(message: Message) -> str:
    return THREAD_PREFIXES.get(message.message_thread_id, '')

# --- Обработка фото ---
@dp.message(F.photo)
async def handle_photo(message: Message):
    caption = message.caption or ""
    caption_lower = caption.lower()
    print(f"📸 Получено фото: {caption}")

    if any(tag in caption_lower for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, ssl=ssl_context) as photo_response:
                if photo_response.status == 200:
                    photo_bytes = await photo_response.read()
                    success, issue_key = await create_jira_ticket(
                        caption,
                        message.from_user.full_name,
                        file_bytes=photo_bytes,
                        filename="telegram_photo.jpg",
                        thread_prefix=get_thread_prefix(message)
                    )
                    if success:
                        await message.reply(
                            f"✅ Задача <b>{issue_key}</b> создана!\n"
                            f"🔗 <a href='{JIRA_URL}/browse/{issue_key}'>{JIRA_URL}/browse/{issue_key}</a>"
                        )
                    else:
                        await message.reply("❌ Ошибка при создании задачи в Jira.")
                else:
                    await message.reply("❌ Не удалось скачать фото с Telegram.")

@dp.message(F.text)
async def handle_text(message: Message):
    text = message.text or ""
    text_lower = text.lower()
    print(f"✉️ Получено сообщение: {text}")

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

# --- Jira функция ---
async def create_jira_ticket(text: str, author: str, file_bytes: bytes = None, filename: str = None, thread_prefix: str = "") -> tuple[bool, str | None]:
    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    cleaned_text = clean_summary(text, TRIGGER_TAGS)

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    create_url = f"{JIRA_URL}/rest/api/3/issue"
    headers = {"Content-Type": "application/json"}
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

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(create_url, json=payload, headers=headers, ssl=ssl_context) as response:
            if response.status != 201:
                error = await response.text()
                print(f"❌ Ошибка при создании задачи: {response.status} — {error}")
                return False, None

            result = await response.json()
            issue_key = result["key"]
            notify_text = (
                f"📨 Создан новый баг!\n"
                f"🔑 <b>{issue_key}</b>\n"
                f"👤 Автор: <b>{author}</b>\n\n"
                f"🔗 <a href=\"{JIRA_URL}/browse/{issue_key}\">Открыть задачу</a>\n\n"
                f"📝 <b>Описание:</b>\n{text}"
            )
            try:
                await bot.send_message(ADMIN_ID, notify_text)
                await bot.send_message(TESTERS_CHANNEL_ID, notify_text, parse_mode="HTML")
            except Exception as e:
                print(f"Не удалось отправить уведомления: {e}")

            if file_bytes and filename:
                attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
                attach_headers = {"X-Atlassian-Token": "no-check"}
                data = aiohttp.FormData()
                data.add_field('file', file_bytes, filename=filename, content_type='image/jpeg')
                async with session.post(attach_url, data=data, headers=attach_headers, ssl=ssl_context) as attach_response:
                    if attach_response.status in (200, 201):
                        print(f"📎 Фото прикреплено к задаче {issue_key}")
                    else:
                        error = await attach_response.text()
                        print(f"❌ Ошибка при вложении: {attach_response.status} — {error}")
                        return False, None

            return True, issue_key

# --- Функции уведомлений из календаря ---
async def fetch_ics():
    async with aiohttp.ClientSession() as session:
        async with session.get(ICS_URL) as resp:
            return await resp.text()

async def parse_events():
    data = await fetch_ics()
    cal = Calendar.from_ical(data)
    events = []
    for component in cal.walk():
        if component.name == "VEVENT":
            start = component.get('dtstart').dt
            if isinstance(start, datetime.date) and not isinstance(start, datetime.datetime):
                start = datetime.datetime.combine(start, datetime.time.min)
            if start.tzinfo is None:
                start = start.replace(tzinfo=datetime.timezone.utc)

            attendees = component.get('attendee')
            if attendees:
                if not isinstance(attendees, list):
                    attendees = [attendees]
                attendees_list = [a.params.get('CN', str(a)) for a in attendees]
            else:
                attendees_list = []

            events.append({
                "summary": str(component.get('summary')),
                "start": start,
                "attendees": attendees_list
            })
    return events

async def notify_events():
    sent = set()
    photo_path = "event.jpg"  # заранее подготовленная фотография

    while True:
        if not subscribed_chats:
            await asyncio.sleep(CHECK_INTERVAL)
            continue

        events = await parse_events()
        now = datetime.datetime.now(datetime.timezone.utc)

        for event in events:
            diff = (event["start"] - now).total_seconds()
            if 0 < diff <= NOTIFY_MINUTES * 60:
                key = (event.get("summary", ""), event.get("start"))
                if key not in sent:
                    attendees_list = event.get("attendees")
                    participants = ", ".join(attendees_list) if attendees_list else "нет участников"

                    text = (
                        f"⏰ Событие через {NOTIFY_MINUTES} минут: {event.get('summary', '')}\n"
                        f"👥 Участники: {participants}"
                    )

                    photo = FSInputFile(photo_path)  # <-- используем FSInputFile для локального файла

                    for chat_id in subscribed_chats:
                        try:
                            await bot.send_photo(
                                chat_id,
                                photo=photo,
                                caption=text,
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            print(f"Ошибка при отправке фото: {e}")
                            await bot.send_message(chat_id, text)

                    sent.add(key)

        await asyncio.sleep(CHECK_INTERVAL)

# --- Запуск бота ---
async def main():
    asyncio.create_task(notify_events())
    print("🚀 Бот запущен и ждет сообщений")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
