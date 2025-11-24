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
        target_time = now.replace(hour=12, minute=35, second=0, microsecond=0)
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
