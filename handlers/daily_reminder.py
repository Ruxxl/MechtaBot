import asyncio
import random
import logging
import re
from datetime import datetime, timedelta
from dateutil import tz
from urllib.parse import quote
import aiohttp
import ssl
from services.ai_service import ai_service
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from web.admin_handler import monitor
from aiogram.enums import ParseMode 

logger = logging.getLogger(__name__)

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

MORNING_WISHES = [
    "Пусть сегодня код пишется сам, а баги боятся одного вашего взгляда! ✨",
    "Заряжаем ваши мониторы на успех и отсутствие конфликтов при мердже! 🚀",
    "Желаем, чтобы сегодня всё работало с первого раза, а алгоритмы были изящными! 💎",
    "Пусть каждое ваше решение сегодня будет элегантным, а кофе — по-настоящему вдохновляющим! ☕️",
    "Желаем продуктивности уровня 'Zero Bugs' и настроения 'Production Ready'! ⚡️"
]

EVENING_WISHES = [
    "Время очистить кэш рабочих мыслей и загрузить режим полного релакса! 🔋",
    "Пусть вечер пройдет без алертов и в максимально ламповой атмосфере! 🌙",
    "Вы сегодня отлично потрудились, пора устроить себе заслуженный 'Shutdown' от задач! 🥂",
    "Желаем уютного вечера: пусть ваш внутренний аккумулятор зарядится до 100%! 🧘‍♂️",
    "Пусть ваш личный 'Uptime' вечером будет направлен только на радость и отдых! 🍦"
]

async def generate_ai_wish(wish_type: str) -> str:
    """Генерирует оригинальное пожелание с помощью Groq AI"""
    try:
        prompts = {
            "morning": "Напиши короткое (1-2 предложения) оригинальное и веселое пожелание доброго утра для IT-команды. Используй айтишный сленг (баги, коммиты, кофе, прод). Только текст пожелания, без кавычек.",
            "evening": "Напиши короткое (1-2 предложения) оригинальное пожелание хорошего вечера для программистов. Используй метафоры (очистка кэша, shutdown, релакс). Только текст пожелания, без кавычек."
        }
        
        wish = await ai_service.generate_groq(
            prompt=prompts[wish_type],
            max_tokens=150,
            temperature=0.8
        )
        return wish if wish else random.choice(MORNING_WISHES if wish_type == "morning" else EVENING_WISHES)
    except Exception as e:
        logger.error(f"Ошибка генерации пожелания: {e}")
        return random.choice(MORNING_WISHES if wish_type == "morning" else EVENING_WISHES)


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
async def handle_jira_release_status(callback: CallbackQuery,
                                     JIRA_EMAIL,
                                     JIRA_API_TOKEN,
                                     JIRA_PROJECT_KEY,
                                     JIRA_URL):
    await callback.answer()

    text = await get_jira_release_status(
        JIRA_EMAIL,
        JIRA_API_TOKEN,
        JIRA_PROJECT_KEY,
        JIRA_URL
    )

    # Ответ на callback всегда идет в тот же чат/поток, где была кнопка
    await callback.message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


# =============================
# Утреннее уведомление
# =============================
async def daily_reminder(bot, chat_id, thread_id=None):
    timezone = tz.gettz("Asia/Almaty")

    while True:
        monitor.update_status("Daily Reminders", "OK")
        now = datetime.now(timezone)
        target_time = now.replace(hour=8, minute=5, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        await asyncio.sleep((target_time - now).total_seconds())

        now = datetime.now(timezone)
        if now.weekday() >= 5:
            logger.info("⏭ Утреннее уведомление пропущено (выходной)")
            continue

        ai_wish = await generate_ai_wish("morning")

        text = (
            "☀️ Доброе утро, коллеги!\n\n"
            "Не забудьте отметиться в <b>Clockster</b>.\n"
            f"{ai_wish}"
        )

        try:
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_clockster_keyboard()
            )
            logger.info("✅ Отправлено утреннее уведомление в группу")
        except Exception as e:
            logger.error(f"Ошибка отправки утреннего уведомления: {e}")

        await asyncio.sleep(60)


# =============================
# Вечернее уведомление
# =============================
async def evening_reminder(bot, chat_id, thread_id=None):
    timezone = tz.gettz("Asia/Almaty")

    while True:
        now = datetime.now(timezone)
        target_time = now.replace(hour=17, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        await asyncio.sleep((target_time - now).total_seconds())

        now = datetime.now(timezone)
        if now.weekday() >= 5:
            logger.info("⏭ Вечернее уведомление пропущено (выходной)")
            continue

        ai_wish = await generate_ai_wish("evening")

        text = (
            "🌇 Добрый вечер, коллеги!\n\n"
            "Не забудьте отметиться в <b>Clockster</b>.\n"
            f"{ai_wish}"
        )

        try:
            await bot.send_message(
                chat_id=chat_id, 
                message_thread_id=thread_id,
                text=text, 
                parse_mode=ParseMode.HTML, 
                reply_markup=get_clockster_keyboard()
            )
            logger.info("✅ Отправлено вечернее уведомление в группу")
        except Exception as e:
            logger.error(f"Ошибка отправки вечернего уведомления: {e}")

        await asyncio.sleep(60)


# =============================
# Запуск двух напоминаний
# =============================
async def start_reminders(bot, chat_id, thread_id=None):
    asyncio.create_task(daily_reminder(bot, chat_id, thread_id))
    asyncio.create_task(evening_reminder(bot, chat_id, thread_id))
