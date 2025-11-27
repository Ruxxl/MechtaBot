# site_checker.py
import asyncio
import logging
from datetime import datetime
from aiogram import Bot
from aiogram.enums import ParseMode
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# Список сайтов для проверки
SITES_TO_CHECK = [
    "https://www.mechta.kz"
]

# Интервал проверок в секундах
CHECK_INTERVAL = 10  # 5 минут

async def check_site(bot: Bot, url: str, chat_id: int):
    """Проверка конкретного сайта через Playwright"""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = await browser.new_page()
            try:
                response = await page.goto(url, timeout=15000)
                status = response.status if response else None

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
                    logger.info(f"Сайт {url} работает корректно")
            except PlaywrightTimeoutError:
                text = (
                    f"⚠️ Сайт не отвечает (таймаут)!\n"
                    f"🌐 URL: {url}\n"
                    f"⏱ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                logger.warning(f"Сайт {url} не отвечает (timeout)")
            finally:
                await browser.close()
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
