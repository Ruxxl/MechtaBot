# site_checker.py
import asyncio
from datetime import datetime
import logging
from aiogram import Bot
from aiogram.enums import ParseMode
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# Список сайтов для проверки
SITES_TO_CHECK = [
    "https://www.mechta.kz"
]

# Интервал проверок в секундах
CHECK_INTERVAL = 10  # каждые 5 минут


async def check_site(bot: Bot, chat_id: int, url: str):
    """Проверка конкретного сайта через Playwright"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            response = await page.goto(url, timeout=15000)
            status = response.status if response else "no response"

            if status != 200:
                text = (
                    f"⚠️ Сайт недоступен!\n"
                    f"🌐 URL: {url}\n"
                    f"⏱ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Код ответа: {status}"
                )
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                logger.warning(f"Сайт {url} недоступен: {status}")
            else:
                logger.info(f"Сайт {url} работает корректно ({status})")

        except Exception as e:
            text = (
                f"⚠️ Ошибка при проверке сайта!\n"
                f"🌐 URL: {url}\n"
                f"⏱ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Ошибка: {str(e)}"
            )
            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            logger.exception(f"Ошибка при проверке сайта {url}: {e}")

        finally:
            await browser.close()


async def site_checker(bot: Bot, chat_id: int, interval: int = CHECK_INTERVAL):
    """Фоновая задача для проверки всех сайтов каждые interval секунд"""
    while True:
        for url in SITES_TO_CHECK:
            await check_site(bot, chat_id, url)
        await asyncio.sleep(interval)
