import asyncio
import aiohttp
from datetime import datetime
from aiogram import Bot
from aiogram.enums import ParseMode
import logging

logger = logging.getLogger(__name__)

# ===============================
# Настройки
# ===============================
SITES_TO_CHECK = [
    "https://www.mechta.kz"
]

CHECK_INTERVAL = 10  # Проверка каждые 5 минут

# Заголовки как у браузера (для обхода 403)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ===============================
# Проверка одного сайта
# ===============================
async def check_site(session: aiohttp.ClientSession, bot: Bot, url: str, chat_id: int):
    try:
        async with session.get(url) as resp:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if resp.status != 200:
                text = (
                    f"⚠️ Сайт недоступен!\n"
                    f"🌐 URL: {url}\n"
                    f"⏱ Время: {now_str}\n"
                    f"Код ответа: {resp.status}"
                )
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                logger.warning(f"{now_str} | Сайт {url} недоступен: {resp.status}")
            else:
                logger.info(f"{now_str} | Сайт {url} работает корректно (HTTP {resp.status})")
    except Exception as e:
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        text = (
            f"⚠️ Ошибка при проверке сайта!\n"
            f"🌐 URL: {url}\n"
            f"⏱ Время: {now_str}\n"
            f"Ошибка: {str(e)}"
        )
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        logger.exception(f"{now_str} | Ошибка при проверке сайта {url}: {e}")


# ===============================
# Фоновая проверка сайтов
# ===============================
async def site_checker(bot: Bot, chat_id: int, interval: int = CHECK_INTERVAL):
    timeout = aiohttp.ClientTimeout(total=10)
    ssl_context = aiohttp.Fingerprint(None)  # Используем стандартный SSL
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
        while True:
            for url in SITES_TO_CHECK:
                await check_site(session, bot, url, chat_id)
            await asyncio.sleep(interval)
