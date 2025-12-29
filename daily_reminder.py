# daily_reminders.py
import asyncio
import logging
from datetime import datetime, timedelta
from dateutil import tz
from urllib.parse import quote
import aiohttp
import ssl
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

# =============================
# Название конкретного релиза
# =============================
RELEASE_NAME = "[WEB] Релиз 3.9"

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
    await callback.answer()  # закрываем “часики”

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    # Получаем версии проекта
    versions_url = f"{JIRA_URL}/rest/api/3/project/{JIRA_PROJECT_KEY}/versions"
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(versions_url, ssl=ssl_context) as resp:
            if resp.status != 200:
                await callback.message.answer(f"❌ Не удалось получить версии проекта (статус {resp.status})")
                return
            versions = await resp.json()

    release = next((v for v in versions if v["name"] == RELEASE_NAME), None)
    if not release:
        await callback.message.answer(f"❌ Релиз '{RELEASE_NAME}' не найден")
        return

    version_id = release.get("id")
    jql = f'project="{JIRA_PROJECT_KEY}" AND fixVersion={version_id} ORDER BY priority DESC'
    search_url = f"{JIRA_URL}/rest/api/3/search/jql?jql={quote(jql)}&fields=key,summary,status&maxResults=200"

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(search_url, ssl=ssl_context) as resp:
            if resp.status != 200:
                await callback.message.answer(f"❌ Не удалось получить задачи релиза (статус {resp.status})")
                return
            data = await resp.json()
            issues = data.get("issues", [])

            if not issues:
                text = f"✅ Задачи для релиза <b>{RELEASE_NAME}</b> не найдены."
            else:
                lines = [f"📊 <b>Статус задач будущего релиза {RELEASE_NAME}:</b>\n"]
                for issue in issues:
                    key = issue.get("key")
                    summary = issue["fields"].get("summary", "Без названия")
                    status = issue["fields"]["status"]["name"]
                    # Формируем ссылку на Jira
                    url = f"{JIRA_URL}/browse/{key}"
                    lines.append(f"🔹 <a href='{url}'>{key} — {summary}</a> — <b>{status}</b>")
                text = "\n".join(lines)

    await callback.message.answer(text, parse_mode=ParseMode.HTML)


# =============================
# Утреннее уведомление
# =============================
async def daily_reminder(bot, TESTERS_CHANNEL_ID):
    timezone = tz.gettz("Asia/Almaty")

    while True:
        now = datetime.now(timezone)
        target_time = now.replace(hour=8, minute=5, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        await asyncio.sleep((target_time - now).total_seconds())

        text = (
            "☀️ Доброе утро, коллеги!\n\n"
            "Не забудьте отметиться в <b>Clockster</b>.\n"
            "Желаем классного дня и продуктивной работы! 💪"
        )

        try:
            await bot.send_message(TESTERS_CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=get_clockster_keyboard())
            logger.info("✅ Отправлено утреннее уведомление")
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
        target_time = now.replace(hour=17, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)

        await asyncio.sleep((target_time - now).total_seconds())

        text = (
            "🌇 Добрый вечер, коллеги!\n\n"
            "Не забудьте отметиться в <b>Clockster</b>.\n"
            "Хорошего вечера и приятного отдыха! 😎"
        )

        try:
            await bot.send_message(TESTERS_CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=get_clockster_keyboard())
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
