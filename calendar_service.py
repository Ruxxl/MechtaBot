import asyncio
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import aiohttp
from icalendar import Calendar
from dateutil import tz
from dateutil.rrule import rrulestr

from aiogram.types import FSInputFile
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

# =======================
# КОНФИГ
# =======================
ICS_URL = os.getenv(
    "ICS_URL",
    "https://calendar.yandex.ru/export/ics.xml?private_token=XXX&tz_id=Asia/Almaty"
)

CHECK_INTERVAL = int(os.getenv("CALENDAR_CHECK_INTERVAL", 30))
ALERT_BEFORE = timedelta(minutes=5)

EVENT_PHOTO_PATH = "event.jpg"
TZ = tz.gettz("Asia/Almaty")

# email → telegram mention
MENTION_MAP = {
    "ruslan.issin@mechta.kz": " @ISNVO ",
    "yernazar.kadyrbekov@mechta.kz": " @yernazarr ",
    "madina.imasheva@mechta.kz": "@Kurokitamoko ",
    "nargiza.marassulova@mechta.kz": " @m_nargi ",
    "kurmangali.kussainov@mechta.kz": " @Kurmangali_kusainoff ",
    "damir.shaniiazov@mechta.kz": " @DamirShaniyazov ",
    "gulnur.yermagambetova@mechta.kz": " @gunya_tt ",
    "karlygash.tashmukhambetova@mechta.kz": " @karlybirdkarly ",
    "sultan.nadirbek@mechta.kz": " @av3nt4d0r ",
    "yerlan.nurakhmetov@mechta.kz": " @coolywooly ",
    "nurgissa.ussen@mechta.kz": " @nurgi17 ",
    "azamat.zhumabekov@mechta.kz": " @azamat_zhumabek ",
    "damir.kuanysh@mechta.kz": " @KuanyshovD ",
    "abzal.zholkenov@mechta.kz": " @zholkenov ",
    "amirbek.ashirbek@mechta.kz": " @amir_ashir ",
    "ruslan.nadyrov@mechta.kz": " @nopeacefulll ",
    "kamilla.aisakhunova@mechta.kz": " @aisakhunovak ",
    "vladislav.borovkov@mechta.kz": " @john_folker "
}

# чтобы не слать дубли
calendar_sent_notifications = set()


# =======================
# UTILS
# =======================
def normalize_dt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


async def fetch_calendar() -> Optional[Calendar]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(ICS_URL) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return Calendar.from_ical(text)
                logger.error(f"ICS load error: {resp.status}")
    except Exception as e:
        logger.error(f"fetch_calendar error: {e}")
    return None


def get_event_occurrences(component, window_start, window_end) -> List[datetime]:
    """Разворачивает RRULE только в нужном временном окне"""
    dtstart = normalize_dt(component.get("dtstart").dt)
    rrule = component.get("rrule")

    if not rrule:
        return [dtstart]

    rule = rrulestr(
        rrule.to_ical().decode(),
        dtstart=dtstart
    )

    return list(rule.between(window_start, window_end, inc=True))


def parse_attendees(component) -> str:
    attendees = component.get("attendee")
    if not attendees:
        return "не указаны"

    if not isinstance(attendees, list):
        attendees = [attendees]

    result = []
    for a in attendees:
        email = str(a).replace("mailto:", "").strip()
        result.append(MENTION_MAP.get(email, email))

    return ", ".join(result)


# =======================
# MAIN LOOP
# =======================
async def check_calendar_events(bot, chat_id):
    logger.info("📅 Calendar watcher started")

    while True:
        cal = await fetch_calendar()
        now = datetime.now(TZ)

        if not cal:
            await asyncio.sleep(CHECK_INTERVAL)
            continue

        window_start = now - timedelta(minutes=10)
        window_end = now + timedelta(minutes=10)

        for component in cal.walk("VEVENT"):
            summary = component.get("summary", "Без названия")
            attendees_text = parse_attendees(component)

            occurrences = get_event_occurrences(
                component,
                window_start,
                window_end
            )

            for start in occurrences:
                alert_time = start - ALERT_BEFORE
                event_key = (summary, start)

                if alert_time <= now < start and event_key not in calendar_sent_notifications:
                    text = (
                        f"📅 <b>Встреча скоро начнётся</b>\n\n"
                        f"📝 <b>{summary}</b>\n"
                        f"⏰ Начало: <b>{start.strftime('%H:%M')}</b>\n"
                        f"👥 Участники: {attendees_text}"
                    )

                    try:
                        if os.path.exists(EVENT_PHOTO_PATH):
                            photo = FSInputFile(EVENT_PHOTO_PATH)
                            await bot.send_photo(
                                chat_id=chat_id,
                                photo=photo,
                                caption=text,
                                parse_mode=ParseMode.HTML
                            )
                        else:
                            await bot.send_message(
                                chat_id=chat_id,
                                text=text,
                                parse_mode=ParseMode.HTML
                            )

                        calendar_sent_notifications.add(event_key)
                        logger.info(f"Sent calendar alert: {event_key}")

                    except Exception as e:
                        logger.error(f"Send error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)
