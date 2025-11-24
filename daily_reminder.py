import asyncio
import logging
from datetime import datetime, timedelta
from dateutil import tz
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

async def daily_reminder(bot, TESTERS_CHANNEL_ID):
    """Ежедневное уведомление в 08:00 по Астане."""
    timezone = tz.gettz("Asia/Almaty")

    while True:
        now = datetime.now(timezone)
        target_time = now.replace(hour=12, minute=47, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        text = (
            "☀️ Доброе утро, коллеги!\n\n"
            "Не забудьте отметиться в программе <b>Clockster</b>.\n"
            "Желаем классного дня и продуктивной работы! 💪"
        )

        try:
            await bot.send_message(TESTERS_CHANNEL_ID, text, parse_mode=ParseMode.HTML)
            logger.info("✅ Отправлено ежедневное уведомление")
        except Exception as e:
            logger.error(f"Ошибка отправки ежедневного уведомления: {e}")

        # Ждем минуту, чтобы случайно не повторилось
        await asyncio.sleep(60)

# ----------------------------
# Новая функция для вечернего уведомления
# ----------------------------
async def evening_reminder(bot, TESTERS_CHANNEL_ID):
    """Ежедневное вечернее уведомление в 17:01 по Алматы."""
    timezone = tz.gettz("Asia/Almaty")

    while True:
        now = datetime.now(timezone)
        target_time = now.replace(hour=12, minute=48, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        text = (
            "🌇 Добрый вечер, коллеги!\n\n"
            "Не забудьте отметиться в программе <b>Clockster</b>.\n"
            "Хорошо отдохните после работы и наберитесь сил! 😎"
        )

        try:
            await bot.send_message(TESTERS_CHANNEL_ID, text, parse_mode=ParseMode.HTML)
            logger.info("✅ Отправлено вечернее уведомление")
        except Exception as e:
            logger.error(f"Ошибка отправки вечернего уведомления: {e}")

        await asyncio.sleep(60)  # Чтобы случайно не повторилось

async def start_reminders(bot, TESTERS_CHANNEL_ID):
    asyncio.create_task(daily_reminder(bot, TESTERS_CHANNEL_ID))
    asyncio.create_task(evening_reminder(bot, TESTERS_CHANNEL_ID))