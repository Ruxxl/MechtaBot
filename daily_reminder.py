import asyncio
import logging
from datetime import datetime, timedelta
from dateutil import tz

from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

# =============================
# Кнопка Clockster
# =============================
def get_clockster_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Отметиться в Clockster", url="https://ruxxl.github.io/clockster-launch/")]
        ]
    )

# =============================
# Утреннее уведомление
# =============================
async def daily_reminder(bot, TESTERS_CHANNEL_ID):
    """Ежедневное уведомление в 08:05 по Астане."""
    timezone = tz.gettz("Asia/Almaty")

    while True:
        now = datetime.now(timezone)
        target_time = now.replace(hour=8, minute=5, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        text = (
            "☀️ Доброе утро, коллеги!\n\n"
            "Не забудьте отметиться в <b>Clockster</b>.\n"
            "Желаем классного дня и продуктивной работы! 💪"
        )

        try:
            await bot.send_message(
                TESTERS_CHANNEL_ID,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_clockster_keyboard()  # ⬅️ Вот кнопка
            )
            logger.info("✅ Отправлено ежедневное утреннее уведомление")
        except Exception as e:
            logger.error(f"Ошибка отправки ежедневного уведомления: {e}")

        await asyncio.sleep(60)

# =============================
# Вечернее уведомление
# =============================
async def evening_reminder(bot, TESTERS_CHANNEL_ID):
    """Ежедневное вечернее уведомление в 17:01 по Астане."""
    timezone = tz.gettz("Asia/Almaty")

    while True:
        now = datetime.now(timezone)
        target_time = now.replace(hour=17, minute=1, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        text = (
            "🌇 Добрый вечер, коллеги!\n\n"
            "Не забудьте отметиться в <b>Clockster</b>.\n"
            "Хорошего вечера и приятного отдыха! 😎"
        )

        try:
            await bot.send_message(
                TESTERS_CHANNEL_ID,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_clockster_keyboard()  # ⬅️ Кнопка и тут
            )
            logger.info("✅ Отправлено вечернее уведомление")
        except Exception as e:
            logger.error(f"Ошибка отправки вечернего уведомления: {e}")

        await asyncio.sleep(60)

# =============================
# Запуск двух напоминаний
# =============================
async def start_reminders(bot, TESTERS_CHANNEL_ID):
    asyncio.create_task(daily_reminder(bot, TESTERS_CHANNEL_ID))
    asyncio.create_task(evening_reminder(bot, TESTERS_CHANNEL_ID))
