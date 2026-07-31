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
# Логика получения статуса релиза (для callback и Mini App)
# =============================
async def get_jira_release_status(
    JIRA_EMAIL: str,
    JIRA_API_TOKEN: str,
    JIRA_PROJECT_KEY: str,
    JIRA_URL: str
) -> str:
    """
    Возвращает отформатированный HTML-текст со статусом следующего релиза.
    """
    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)

    # Получаем версии проекта
    versions_url = f"{JIRA_URL}/rest/api/3/project/{JIRA_PROJECT_KEY}/versions"
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(versions_url, ssl=SSL_CONTEXT) as resp:
            if resp.status != 200:
                return f"❌ Не удалось получить версии проекта (статус {resp.status})"
            versions = await resp.json()

    # Фильтруем только невыпущенные версии
    unreleased = [v for v in versions if not v.get("released", False)]
    if not unreleased:
        return "✅ Все запланированные релизы уже выпущены!"

    # Находим версию с наименьшим номером (следующую в очереди)
    def parse_version(v_name):
        return [int(s) for s in re.findall(r'\d+', v_name)]

    target_release = min(unreleased, key=lambda v: parse_version(v["name"]) or [0])
    release_name = target_release["name"]
    version_id = target_release.get("id")

    jql = f'project="{JIRA_PROJECT_KEY}" AND fixVersion={version_id} ORDER BY priority DESC'
    search_url = f"{JIRA_URL}/rest/api/3/search/jql?jql={quote(jql)}&fields=key,summary,status,subtasks&maxResults=200"

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(search_url, ssl=SSL_CONTEXT) as resp:
            if resp.status != 200:
                return f"❌ Не удалось получить задачи релиза (статус {resp.status})"
            data = await resp.json()
            issues = data.get("issues", [])
            
            total_subtasks = sum(len(issue["fields"].get("subtasks", [])) for issue in issues)

            if not issues:
                return f"✅ Задачи для релиза <b>{release_name}</b> не найдены."
            
            lines = [f"📊 <b>Статус задач будущего релиза {release_name}:</b>\n",
                     f"🐞 Найдено багов: <b>{total_subtasks}</b>\n"]
            for issue in issues:
                lines.append(f"🔹 <a href='{JIRA_URL}/browse/{issue['key']}'>{issue['key']} — {issue['fields']['summary']}</a> — <b>{issue['fields']['status']['name']}</b>")
            return "\n".join(lines)


async def get_release_status_data(
    JIRA_EMAIL: str,
    JIRA_API_TOKEN: str,
    JIRA_PROJECT_KEY: str,
    JIRA_URL: str
) -> dict | None:
    """Те же данные, что и get_jira_release_status(), но структурированные —
    для Mini App (web/miniapp_api.py::get_next_release_status), где раньше
    их пытались вытащить регулярками из готового текста для Telegram и
    промахивались мимо реального формата (там нет строк "Готово: N" и т.п.,
    только список задач с инлайн-статусом), из-за чего всегда получалось
    "Релиз N/A" и "0 из 0".
    Возвращает None, если версии/задачи получить не удалось."""
    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)

    versions_url = f"{JIRA_URL}/rest/api/3/project/{JIRA_PROJECT_KEY}/versions"
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(versions_url, ssl=SSL_CONTEXT) as resp:
            if resp.status != 200:
                return None
            versions = await resp.json()

    unreleased = [v for v in versions if not v.get("released", False)]
    if not unreleased:
        return {"version": None, "total": 0, "done": 0, "in_progress": 0, "pending": 0}

    def parse_version(v_name):
        return [int(s) for s in re.findall(r'\d+', v_name)]

    target_release = min(unreleased, key=lambda v: parse_version(v["name"]) or [0])
    release_name = target_release["name"]
    version_id = target_release.get("id")

    jql = f'project="{JIRA_PROJECT_KEY}" AND fixVersion={version_id} ORDER BY priority DESC'
    search_url = f"{JIRA_URL}/rest/api/3/search/jql?jql={quote(jql)}&fields=key,summary,status,subtasks&maxResults=200"

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(search_url, ssl=SSL_CONTEXT) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            issues = data.get("issues", [])

    done = in_progress = pending = 0
    for issue in issues:
        category_key = issue["fields"]["status"].get("statusCategory", {}).get("key", "")
        if category_key == "done":
            done += 1
        elif category_key == "indeterminate":
            in_progress += 1
        else:
            pending += 1

    return {
        "version": release_name,
        "total": len(issues),
        "done": done,
        "in_progress": in_progress,
        "pending": pending,
    }

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
