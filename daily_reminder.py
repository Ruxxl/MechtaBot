import asyncio
import logging
from datetime import datetime, timedelta
from dateutil import tz
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

# =============================
# Кнопки Clockster + Jira
# =============================
def get_clockster_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Отметиться в Clockster", url="https://ruxxl.github.io/clockster-launch/")],
            [InlineKeyboardButton(text="📊 Посмотреть статус будущего релиза", callback_data="jira_release_status")]
        ]
    )

# =============================
# Callback кнопки "Посмотреть статус релиза"
# =============================
async def handle_jira_release_status(callback: CallbackQuery, bot):
    await callback.answer()  # закрываем “часики”
    from release_notifier import get_jira_release_status_text
    text = await get_jira_release_status_text()
    await callback.message.answer(text, parse_mode=ParseMode.HTML)


# =============================
# Утреннее уведомление
# =============================
async def daily_reminder(bot, TESTERS_CHANNEL_ID):
    timezone = tz.gettz("Asia/Almaty")

    while True:
        now = datetime.now(timezone)
        target_time = now.replace(hour=9, minute=8, second=0, microsecond=0)
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
                reply_markup=get_clockster_keyboard()
            )
            logger.info("✅ Отправлено ежедневное утреннее уведомление")
        except Exception as e:
            logger.error(f"Ошибка отправки утреннего уведомления: {e}")

        await asyncio.sleep(60)


# =============================
# Вечернее уведомление
# =============================
async def evening_reminder(bot, TESTERS_CHANNEL_ID):
    timezone = tz.gettz("Asia/Almaty")

    while True:
        now = datetime.now(timezone)
        target_time = now.replace(hour=17, minute=5, second=0, microsecond=0)
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
                reply_markup=get_clockster_keyboard()
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
