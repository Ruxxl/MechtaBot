import asyncio
import aiohttp
import ssl
import re
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

load_dotenv()

BOT_TOKEN = os.environ.get('BOT_TOKEN')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN')
JIRA_PROJECT_KEY = os.environ.get('JIRA_PROJECT_KEY', 'AS')
JIRA_PARENT_KEY = os.environ.get('JIRA_PARENT_KEY', 'AS-1679')
JIRA_URL = os.environ.get('JIRA_URL', 'https://mechtamarket.atlassian.net')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
ADMIN_ID = int(os.environ.get('ADMIN_ID', '998292747'))
dp = Dispatcher()

HR_TOPICS = {
    "attendance": {
        "title": "Отметки приход/уход",
        "text": (
            "Все запросы об отметках (приход/уход) необходимо направлять на <b>Адильжана</b>."
        )
    },
    "bs_order": {
        "title": "Порядок согласования БС",
        "text": (
            "Обновлён порядок согласования заявок в Битриксе\n\n"
"В рамках оптимизации внутренних процессов обновлён порядок согласования заявок в Битрикс24 (БС — полдня, БС — целый день, отпуск).\n"
"Просьба ознакомиться с новой последовательностью:\n\n"
"🔄 Новый порядок согласования:\n\n"
"1️⃣ Сотрудник\n"
"→ подаёт заявку на HR-менеджера — Сеитову Лауру.\n\n"
"2️⃣ Система (Битрикс)\n"
"→ автоматически перенаправляет заявку вашему продакт-менеджеру по оргструктуре.\n\n"
"3️⃣ Продакт-менеджер\n"
"→ рассматривает и утверждает заявку.\n\n"
"4️⃣ HR-менеджер (Лаура)\n"
"→ получает заявку обратно и указывает утверждающего — Директора по информационно-техническому обеспечению — Әділжана Әлкенова.\n\n"
"5️⃣ Әділжан Әлкенов\n"
"→ подтверждает заявку.\n"
"После этого бизнес-процесс автоматически направляется HR головного офиса на финальное утверждение.\n\n"
"🖊 Подписание документов\n\n"
"После согласования заявку подписывают:\n"
"✅ сотрудник, подавший заявку;\n"
"✅ HR-менеджер;\n"
"✅ Әділжан Әлкенов (директор).\n\n"
"📌 Что изменилось:\n\n"
"• Заявка теперь подаётся сразу на HR-менеджера.\n"
"• Продакт-менеджер подключается автоматически вторым шагом."
        )
    },
    "business_trip": {
        "title": "Командировка ✈️",
        "text": (
            "✈️ <b>Инструкция по командировке</b>\n\n"
            "1️⃣ <b>Перед поездкой:</b>\n"
            "Запустите процесс в Битрикс24:\n\n"
            "• Укажите дату выезда и возвращения.\n"
            "• Введите ФИО, направление и цель поездки.\n\n"
            "После согласования:\n"
            "• Вам будут отправлены билеты и командировочное удостоверение (УДО) и скан приказа.\n"
            "• Подпишите скан приказа и отправьте его на почту <b>viktoriya.gussyatnikova@mechta.kz</b>.\n\n"
            "Проверьте внимательно:\n"
            "• ФИО.\n• Номер командировочного удостоверения.\n• Даты вылета и прилета.\n\n"
            "🧾 <b>Регистрация на рейс:</b>\n"
            "Пройдите онлайн на сайте авиакомпании.\n"
            "Сохраните посадочный талон:\n• Либо скачайте на почту.\n• Либо получите на стойке регистрации.\n\n"
            "⚠️ Обязательно:\n"
            "Поставьте печать на посадочном талоне в аэропорту.\n"
            "Скриншоты с телефона не принимаются, сохраните оригинал!\n"
            "При утере посадочного талона расходы по билету оплачиваются самостоятельно.\n\n"
            "📘 В командировочном удостоверении:\n"
            "Поставьте печати на конференции.\n"
            "*(Образец заполнения и мест для печатей прилагается.)*\n\n"
            "2️⃣ <b>После возвращения:</b>\n"
            "В течение 3 рабочих дней сдайте HR (<b>Виктория Гусятникова</b>):\n"
            "• Отчёт о командировке (шаблон во вложении).\n"
            "• Два посадочных талона с печатями.\n"
            "• Командировочное удостоверение (с печатями).\n\n"
            "3️⃣ <b>Ознакомление с документами:</b>\n"
            "После отправки билетов обязательно:\n"
            "• Прочитайте <b>Положение о командировании</b>.\n\n"
            "😊 Удачной командировки!"
        )
    }
}

@dp.message(F.text.lower().contains("#hr"))
async def hr_menu(message: Message):
    """Реагирует на тег #hr и показывает меню выбора темы"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=HR_TOPICS["attendance"]["title"], callback_data="hr_attendance")],
        [InlineKeyboardButton(text=HR_TOPICS["bs_order"]["title"], callback_data="hr_bs_order")],
        [InlineKeyboardButton(text=HR_TOPICS["business_trip"]["title"], callback_data="hr_business_trip")]

    ])

    await message.reply("📋 Выберите интересующую тему:", reply_markup=kb)


@dp.callback_query(F.data.startswith("hr_"))
async def hr_topic_detail(callback: CallbackQuery):
    """Выводит подробности выбранной темы HR"""
    topic_key = callback.data.split("_", 1)[1]

    if topic_key in HR_TOPICS:
        text = HR_TOPICS[topic_key]["text"]
    else:
        text = "❌ Неизвестная тема."

    await callback.message.answer(text)
    await callback.answer()

TRIGGER_TAGS = ['#bug', '#jira']
CHECK_TAG = '#check'
THREAD_PREFIXES = {1701: '[Back]', 1703: '[Front]'}

def clean_summary(text: str, tags: list[str]) -> str:
    for tag in tags:
        text = re.sub(re.escape(tag), '', text, flags=re.IGNORECASE)
    return ' '.join(text.split()).strip()

def get_thread_prefix(message: Message) -> str:
    return THREAD_PREFIXES.get(message.message_thread_id, '')

@dp.message(F.photo)
async def handle_photo(message: Message):
    caption = message.caption or ""
    caption_lower = caption.lower()
    print(f"📸 Получено фото: {caption}")

    if any(tag in caption_lower for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        file_path = file.file_path
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, ssl=ssl_context) as photo_response:
                if photo_response.status == 200:
                    photo_bytes = await photo_response.read()
                    success, issue_key = await create_jira_ticket(
                        caption,
                        message.from_user.full_name,
                        file_bytes=photo_bytes,
                        filename="telegram_photo.jpg",
                        thread_prefix=get_thread_prefix(message)
                    )
                    if success:
                        await message.reply(
    f"✅ Задача <b>{issue_key}</b> создана!\n"
    f"🔗 <a href='{JIRA_URL}/browse/{issue_key}'>{JIRA_URL}/browse/{issue_key}</a>"
)
                    else:
                        await message.reply("❌ Ошибка при создании задачи в Jira.")
                else:
                    await message.reply("❌ Не удалось скачать фото с Telegram.")

@dp.message(F.text)
async def handle_text(message: Message):
    text = message.text or ""
    text_lower = text.lower()
    print(f"✉️ Получено сообщение: {text}")

    if CHECK_TAG in text_lower:
        await message.reply("✅ Бот работает и готов принимать задачи.")
        return

    if any(tag in text_lower for tag in TRIGGER_TAGS):
        await message.reply("🔄 Обнаружен тег, создаю задачу в Jira...")
        success, issue_key = await create_jira_ticket(
            text,
            message.from_user.full_name,
            file_bytes=None,
            filename=None,
            thread_prefix=get_thread_prefix(message)
        )
        if success:
            await message.reply(
    f"✅ Задача <b>{issue_key}</b> создана!\n"
    f"🔗 <a href='{JIRA_URL}/browse/{issue_key}'>{JIRA_URL}/browse/{issue_key}</a>"
)
        else:
            await message.reply("❌ Ошибка при создании задачи в Jira.")

async def create_jira_ticket(text: str, author: str, file_bytes: bytes = None, filename: str = None, thread_prefix: str = "") -> tuple[bool, str | None]:
    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    cleaned_text = clean_summary(text, TRIGGER_TAGS)

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    create_url = f"{JIRA_URL}/rest/api/3/issue"
    headers = {"Content-Type": "application/json"}
    summary = f"[Telegram] {cleaned_text}".strip()[:255]
    payload = {
    "fields": {
        "project": {"key": JIRA_PROJECT_KEY},
        "parent": {"key": "AS-3047"},   # <-- вот это главное
        "summary": summary,
        "description": {
            "type": "doc",
            "version": 1,
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": f"[Telegram] Автор: {author}\n{text}"}]
            }]
        },
        "issuetype": {"name": "Подзадача"}   # название как в твоей Jira (обычно "Sub-task" или "Подзадача")
    }
}

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(create_url, json=payload, headers=headers, ssl=ssl_context) as response:
            if response.status != 201:
                error = await response.text()
                print(f"❌ Ошибка при создании задачи: {response.status} — {error}")
                return False, None

            result = await response.json()
            issue_key = result["key"]
            issue_url = f"{JIRA_URL}/browse/{issue_key}"
            print(f"✅ Задача {issue_key} создана")
            notify_text = (
                f"📨 Создан новый баг!\n"
                f"🔑 <b>{issue_key}</b>\n"
                f"👤 Автор: <b>{author}</b>\n\n"
                f"🔗 <a href=\"{issue_url}\">Открыть задачу</a>\n\n"
                f"📝 <b>Описание:</b>\n"
                f"{text}"
            )

        try:
            await bot.send_message(ADMIN_ID, notify_text)
        except Exception as e:
            print(f"Не удалось отправить уведомление админу: {e}")
            
        if file_bytes and filename:
            attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
            attach_headers = {"X-Atlassian-Token": "no-check"}
            data = aiohttp.FormData()
            data.add_field('file', file_bytes, filename=filename, content_type='image/jpeg')

            async with session.post(attach_url, data=data, headers=attach_headers, ssl=ssl_context) as attach_response:
                if attach_response.status in (200, 201):
                    print(f"📎 Фото прикреплено к задаче {issue_key}")
                else:
                    error = await attach_response.text()
                    print(f"❌ Ошибка при вложении: {attach_response.status} — {error}")
                    return False, None

        return True, issue_key

async def main():
    print("🚀 Бот запущен и ждет сообщений")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
