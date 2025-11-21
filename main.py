import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from logger_config import setup_logger
from config import *
from hr import get_hr_keyboard, HR_TOPICS
from jira import create_jira_ticket
from calendar_bot import notify_events

logger = setup_logger(__name__)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

subscribed_chats = {TESTERS_CHANNEL_ID}

# --- Здесь добавляешь свои обработчики сообщений, фото, HR меню и callback ---

async def main():
    asyncio.create_task(notify_events(bot, subscribed_chats, ICS_URL, NOTIFY_MINUTES, CHECK_INTERVAL, EVENT_PHOTO_PATH))
    logger.info("🚀 Бот запущен и ждет сообщений")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
