import os
import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta
from icalendar import Calendar
from dateutil import tz

from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from main import bot, TESTERS_CHANNEL_ID   # ← важный импорт

logger = logging.getLogger(__name__)

ICS_URL = "https://calendar.yandex.ru/export/ics.xml?private_token=dba95cc621742f7b9ba141889e288d2e0987fae3&tz_id=Asia/Almaty"
CHECK_INTERVAL = 60
ALERT_BEFORE = timedelta(minutes=1)
EVENT_PHOTO_PATH = "event.jpg"

calendar_sent_notifications = set()

EMAIL_TO_USERNAME = {
    "ruslan.issin@mechta.kz": "@ISNVO",
    "yernazar.kadyrbekov@mechta.kz": "@Yernazar",
}


def format_attendees(attendees):
    if not attendees:
        return "не указаны"

    if not isinstance(attendees, list):
        attendees = [attendees]

    result = []
    for a in attendees:
        a = str(a)
        if "mailto:" in a:
            email = a.replace("mailto:", "").strip()
            if email in EMAIL_TO_USERNAME:
                result.append(EMAIL_TO_USERNAME[email])
            else:
                result.append(email)
        else:
            result.append(a)

    return ", ".join(result)


async def fetch_calendar():
    async with aiohttp.ClientSession() as session:
        async with session.get(ICS_URL) as resp:
            if resp.status == 200:
                data = await resp.text()
                return Calendar.from_ical(data)
            else:
                logger.error(f"Ошибка при получении ICS: {resp.status}")
                return None


async def check_calendar_events():
    while True:
        cal = await fetch_calendar()
        if not cal:
            await asyncio.sleep(CHECK_INTERVAL)
            continue

        now = datetime.now(tz=tz.gettz("Asia/Almaty"))

        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            start = component.get('dtstart').dt
            summary = component.get('summary')
            attendees = component.get('attendee')

            attendees_text = format_attendees(attendees)
            alert_time = start - ALERT_BEFORE

            if alert_time <= now < start and summary not in calendar_sent_notifications:
                text = (
                    f"📅 Встреча скоро начнется!\n"
                    f"📝 Название: <b>{summary}</b>\n"
                    f"⏰ Начало: {start.strftime('%H:%M')}\n"
                    f"👥 Участники: {attendees_text}"
                )

                try:
                    if os.path.exists(EVENT_PHOTO_PATH):
                        file = FSInputFile(EVENT_PHOTO_PATH)
                        await bot.send_photo(
                            chat_id=TESTERS_CHANNEL_ID,
                            photo=file,
                            caption=text,
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await bot.send_message(
                            chat_id=TESTERS_CHANNEL_ID,
                            text=text,
                            parse_mode=ParseMode.HTML
                        )

                    calendar_sent_notifications.add(summary)
                    logger.info(f"Отправлено уведомление по календарю: {summary}")

                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}")

        await asyncio.sleep(CHECK_INTERVAL)
