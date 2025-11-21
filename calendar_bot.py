import asyncio
import aiohttp
import datetime
from icalendar import Calendar
from zoneinfo import ZoneInfo
import os
from logger_config import setup_logger

logger = setup_logger(__name__)

async def fetch_ics(ICS_URL):
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession() as session:
        async with session.get(ICS_URL, ssl=ssl_context) as resp:
            if resp.status != 200:
                logger.error("Не удалось скачать .ics: %s", resp.status)
                return None
            return await resp.text()

async def parse_events(ICS_URL):
    data = await fetch_ics(ICS_URL)
    if not data:
        return []
    cal = Calendar.from_ical(data)
    events = []
    try:
        local_tz = ZoneInfo("Asia/Almaty")
    except Exception:
        local_tz = datetime.timezone.utc
    for component in cal.walk():
        if component.name == "VEVENT":
            start = component.get('dtstart').dt
            if isinstance(start, datetime.date) and not isinstance(start, datetime.datetime):
                start = datetime.datetime.combine(start, datetime.time.min)
            if start.tzinfo is None:
                start = start.replace(tzinfo=local_tz)
            start_utc = start.astimezone(datetime.timezone.utc)
            attendees = component.get('attendee')
            attendees_list = []
            if attendees:
                if not isinstance(attendees, list):
                    attendees = [attendees]
                for a in attendees:
                    try:
                        cn = a.params.get('CN')
                        attendees_list.append(cn if cn else str(a))
                    except Exception:
                        attendees_list.append(str(a))
            events.append({
                "summary": str(component.get('summary')),
                "start": start_utc,
                "attendees": attendees_list
            })
    return events

async def notify_events(bot, subscribed_chats, ICS_URL, NOTIFY_MINUTES, CHECK_INTERVAL, EVENT_PHOTO_PATH):
    sent = set()
    while True:
        if not subscribed_chats:
            await asyncio.sleep(CHECK_INTERVAL)
            continue
        events = await parse_events(ICS_URL)
        now = datetime.datetime.now(datetime.timezone.utc)
        for event in events:
            diff = (event["start"] - now).total_seconds()
            if 0 < diff <= NOTIFY_MINUTES * 60:
                key = (event.get("summary", ""), event.get("start"))
                if key in sent:
                    continue
                participants = ", ".join(event.get("attendees", [])) or "нет участников"
                text = f"⏰ Встреча через {NOTIFY_MINUTES} минут: {event.get('summary', '')}\n👥 Участники: {participants}"
                for chat_id in list(subscribed_chats):
                    try:
                        if os.path.isfile(EVENT_PHOTO_PATH):
                            from aiogram.types import FSInputFile
                            await bot.send_photo(chat_id, photo=FSInputFile(EVENT_PHOTO_PATH), caption=text, parse_mode="HTML")
                        else:
                            await bot.send_message(chat_id, text, parse_mode="HTML")
                    except Exception as e:
                        logger.exception("Ошибка при отправке уведомления в %s: %s", chat_id, e)
                        try:
                            await bot.send_message(chat_id, text, parse_mode="HTML")
                        except Exception:
                            logger.exception("Повторная отправка текстом не удалась для %s", chat_id)
                sent.add(key)
        await asyncio.sleep(CHECK_INTERVAL)
