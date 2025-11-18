import asyncio
import aiohttp
import ssl
import re
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from icalendar import Calendar
import datetime

load_dotenv()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '998292747'))
TESTERS_CHANNEL_ID = int(os.environ.get('TESTERS_CHANNEL_ID', '-1002196628724'))

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- Словарь участников ICS → Telegram ник ---
PARTICIPANTS_MAP = {
    "nurgissa.ussen": "@nurgi17",
    "kurmangali.kussainov": "@Kurmangali_kusainoff",
    "madina.imasheva": "@Kurokitamoko",
    "ruslan.issin": "@ISNRUS",
    "yernazar.kadyrbekov": "@yernazarr"
}

# --- Подписанные чаты (Testers) ---
subscribed_chats = {TESTERS_CHANNEL_ID}

# --- Настройки календаря ---
ICS_URL = "https://calendar.yandex.ru/export/ics.xml?private_token=dba95cc621742f7b9ba141889e288d2e0987fae3&tz_id=Asia/Almaty"
CHECK_INTERVAL = 60  # проверка каждые 60 секунд
NOTIFY_MINUTES = 0  # уведомление за 60 минут
photo_path = "event.jpg"  # локальная фотография для уведомлений

# --- Пример HR-меню (оставляем твои данные) ---
HR_TOPICS = {
    "attendance": {"title": "Отметки приход/уход", "text": "Все запросы об отметках (приход/уход) направлять на Адильжана."},
    "bs_order": {"title": "Порядок согласования БС", "text": "Инструкция по БС..."},
    "business_trip": {"title": "Командировка ✈️", "text": "Инструкция по командировке..."}
}

@dp.message(F.text == "/getid")
async def get_chat_id(message: Message):
    await message.reply(f"Chat ID: <code>{message.chat.id}</code>")

@dp.message(F.text.lower().contains("#hr"))
async def hr_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=HR_TOPICS["attendance"]["title"], callback_data="hr_attendance")],
        [InlineKeyboardButton(text=HR_TOPICS["bs_order"]["title"], callback_data="hr_bs_order")],
        [InlineKeyboardButton(text=HR_TOPICS["business_trip"]["title"], callback_data="hr_business_trip")]
    ])
    await message.reply("📋 Выберите тему:", reply_markup=kb)

@dp.callback_query(F.data.startswith("hr_"))
async def hr_topic_detail(callback: CallbackQuery):
    topic_key = callback.data.split("_", 1)[1]
    text = HR_TOPICS.get(topic_key, {}).get("text", "❌ Неизвестная тема.")
    await callback.message.answer(text)
    await callback.answer()

# --- Функции для работы с ICS ---
async def parse_events():
    """Скачивает ICS и возвращает список событий с start, summary и attendees"""
    events = []
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession() as session:
        async with session.get(ICS_URL, ssl=ssl_context) as resp:
            if resp.status != 200:
                print(f"Ошибка при скачивании ICS: {resp.status}")
                return events
            data = await resp.text()
            cal = Calendar.from_ical(data)
            for component in cal.walk():
                if component.name == "VEVENT":
                    start = component.get('dtstart').dt
                    if isinstance(start, datetime.date) and not isinstance(start, datetime.datetime):
                        start = datetime.datetime.combine(start, datetime.time(0, 0))
                    start = start.replace(tzinfo=datetime.timezone.utc)
                    summary = str(component.get('summary', ''))
                    attendees = []
                    if component.get('attendee'):
                        raw_attendees = component.get('attendee')
                        if not isinstance(raw_attendees, list):
                            raw_attendees = [raw_attendees]
                        for a in raw_attendees:
                            email = str(a).split(":")[-1]
                            attendees.append(email)
                    events.append({"start": start, "summary": summary, "attendees": attendees})
    return events

# --- Функция уведомлений ---
async def notify_events():
    sent = set()
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
                    attendees_list = event.get("attendees", [])
                    participants = [PARTICIPANTS_MAP.get(a, a) for a in attendees_list]
                    participants_text = ", ".join(participants) if participants else "нет участников"

                    text = f"⏰ Событие через {NOTIFY_MINUTES} минут: {event.get('summary','')}\n" \
                           f"👥 Участники: {participants_text}"

                    photo = FSInputFile(photo_path)
                    for chat_id in subscribed_chats:
                        try:
                            await bot.send_photo(chat_id, photo=photo, caption=text, parse_mode="HTML")
                        except Exception as e:
                            print(f"Ошибка при отправке фото: {e}")
                            await bot.send_message(chat_id, text)
                    sent.add(key)
        await asyncio.sleep(CHECK_INTERVAL)

# --- Основной запуск ---
async def main():
    print("🚀 Бот запущен и ожидает события")
    asyncio.create_task(notify_events())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
