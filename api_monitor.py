import asyncio
import aiohttp
import logging
from datetime import datetime

# Настройки
CHECK_INTERVAL = 120  # 10 минут
TARGET_CHAT_ID = 998292747  # Твой ID для уведомлений

# Список API для мониторинга (добавь свои ссылки сюда)
API_ENDPOINTS = [
    "https://www.mechta.kz/api/v2/header/info",
    "https://www.mechta.kz/api/v2/favorites",
    "https://www.mechta.kz/api/v2/header/cities"
  ]

async def check_api_health(bot, logger):
    async with aiohttp.ClientSession() as session:
        for url in API_ENDPOINTS:
            try:
                # Ставим таймаут, чтобы проверка не зависла
                async with session.get(url, timeout=15) as response:
                    # Проверяем на 500-е и 400-е ошибки
                    if response.status >= 400:
                        msg = (
                            f"‼️ <b>API ERROR</b>\n\n"
                            f"<b>URL:</b> {url}\n"
                            f"<b>Status:</b> <code>{response.status}</code>\n"
                            f"<b>Time:</b> {datetime.now().strftime('%H:%M:%S')}"
                        )
                        await bot.send_message(TARGET_CHAT_ID, msg)
                        logger.warning(f"API Alert: {url} returned {response.status}")
            
            except Exception as e:
                # Ошибки сети, DNS или таймауты
                msg = (
                    f"🚫 <b>API UNREACHABLE</b>\n\n"
                    f"<b>URL:</b> {url}\n"
                    f"<b>Error:</b> <code>{type(e).__name__}</code>\n"
                    f"<b>Details:</b> {str(e)[:100]}"
                )
                await bot.send_message(TARGET_CHAT_ID, msg)
                logger.error(f"API Connection error: {url} -> {e}")

async def run_api_monitor(bot, logger):
    logger.info("Сервис мониторинга API запущен.")
    while True:
        await check_api_health(bot, logger)
        await asyncio.sleep(CHECK_INTERVAL)
