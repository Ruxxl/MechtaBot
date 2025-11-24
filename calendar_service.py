import asyncio
import os
import logging
from datetime import datetime, timedelta
from dateutil import tz
from typing import Optional
import aiohttp
from icalendar import Calendar
from aiogram.types import FSInputFile
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

# =======================
# КОНФИГ КАЛЕНДАРЯ
# =======================
ICS_URL = os.getenv(
    "ICS_URL",
    "https://calendar.yandex.ru/export/ics.xml?private_token=dba95cc621742f7b9ba141889e288d2e0987fae3&tz_id=Asia/Almaty"
)
CHECK_INTERVAL = int(os.getenv("CALENDAR_CHECK_INTERVAL", 30))
ALERT_BEFORE = timedelta(minutes=40)

EVENT_PHOTO_PATH = "event.jpg"

# Словарь замен email → @mention
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

# уже отправленные уведомления
calendar_sent_notifications = set()


# =======================
# Функции календаря
# =======================
async def fetch_calendar() -> Optional[Calendar]:
    """Скачивает и парсит ICS календарь."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(ICS_URL) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return Calendar.from_ical(text)
                else:
                    logger.error(f"Ошибка загрузки ICS: {resp.status}")
    except Exception as e:
        logger.error(f"Ошибка fetch_calendar: {e}")

    return None


def normalize_dt(dt):
    """Делает datetime timezone-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz.gettz("Asia/Almaty"))
    return dt


async def check_calendar_events(bot, chat_id):
    """Главный цикл уведомлений по календарю."""
    logger.info("📅 Календарный модуль запущен")

    while True:
        cal = await fetch_calendar()
        if cal:
            now = datetime.now(tz=tz.gettz("Asia/Almaty"))

            for component in cal.walk():
                if component.name != "VEVENT":
                    continue

                start = normalize_dt(component.get('dtstart').dt)
                summary = component.get('summary')
                attendees = component.get('attendee')

                # Формирование списка участников
                if attendees:
                    if isinstance(attendees, list):
                        attendees_raw = [str(a) for a in attendees]
                    else:
                        attendees_raw = [str(attendees)]

                    attendees_list = []
                    for a in attendees_raw:
                        email = a.replace("mailto:", "").strip()
                        attendees_list.append(MENTION_MAP.get(email, email))

                    attendees_text = ", ".join(attendees_list)
                else:
                    attendees_text = "не указаны"

                alert_time = start - ALERT_BEFORE
                event_key = (summary, start.date())

                if alert_time <= now < start and event_key not in calendar_sent_notifications:
                    text = (
                        f"📅 Встреча скоро начнется!\n"
                        f"📝 Название: <b>{summary}</b>\n"
                        f"⏰ Начало: {start.strftime('%H:%M')}\n"
                        f"👥 Участники: {attendees_text}"
                    )

                    try:
                        if os.path.exists(EVENT_PHOTO_PATH):
                            file = FSInputFile(EVENT_PHOTO_PATH)
                            await bot.send_photo(chat_id=chat_id, photo=file, caption=text)
                        else:
                            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)

                        calendar_sent_notifications.add(event_key)
                        logger.info(f"Отправлено уведомление по календарю: {event_key}")

                    except Exception as e:
                        logger.error(f"Ошибка отправки сообщения: {e}")

        await asyncio.sleep(CHECK_INTERVAL)