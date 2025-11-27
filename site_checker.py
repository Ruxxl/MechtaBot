import asyncio
import aiohttp
from datetime import datetime
from aiogram import Bot
from aiogram.enums import ParseMode
import logging

logger = logging.getLogger(__name__)

# Список сайтов для проверки
SITES_TO_CHECK = [
    "https://www.mechta.kz"
]


# Интервал проверок в секундах
CHECK_INTERVAL = 10  # 5 минут

async def check_site(bot: Bot, url: str, chat_id: int):
    headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"}
    
    """Проверка конкретного сайта и уведомление при ошибке"""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    text = (
                        f"⚠️ Сайт недоступен!\n"
                        f"🌐 URL: {url}\n"
                        f"⏱ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Код ответа: {resp.status}"
                    )
                    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                    logger.warning(f"Сайт {url} недоступен: {resp.status}")
                else:
                    logger.info(f"Сайт {url} работает корректно")
    except Exception as e:
        text = (
            f"⚠️ Ошибка при проверке сайта!\n"
            f"🌐 URL: {url}\n"
            f"⏱ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Ошибка: {str(e)}"
        )
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        logger.exception(f"Ошибка при проверке сайта {url}: {e}")

async def site_checker(bot: Bot, chat_id: int, interval: int = CHECK_INTERVAL):
    """Фоновая задача для проверки всех сайтов каждые N секунд"""
    while True:
        for url in SITES_TO_CHECK:
            await check_site(bot, url, chat_id)
        await asyncio.sleep(interval)
