import aiohttp
import ssl
import logging
import re
from typing import List, Optional

from aiogram import Bot, F, types
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

logger = logging.getLogger("bot_jira")

# =======================
# FSM для Jira
# =======================
class JiraFSM(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_priority = State()
    waiting_links_input = State()
    waiting_screenshots = State()

# =======================
# Создание подзадачи Jira
# =======================
async def create_jira_ticket_fsm(bot: Bot, JIRA_EMAIL: str, JIRA_API_TOKEN: str, JIRA_PROJECT_KEY: str,
                                 JIRA_PARENT_KEY: str, JIRA_URL: str, data: dict, author: str) -> Optional[str]:
    title = data.get("title", "Без заголовка")
    description = data.get("description", "")
    priority = data.get("priority", "Medium")
    links = data.get("links", [])
    files = data.get("files", [])

    full_text = description
    if links:
        full_text += "\n\n🔗 Ссылки:\n" + "\n".join(links)

    auth = aiohttp.BasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "parent": {"key": JIRA_PARENT_KEY},
            "summary": f"[Telegram] {title}"[:255],
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"[Telegram] Автор: {author}\n{full_text}"}]}]
            },
            "issuetype": {"name": "Подзадача"},
            "priority": {"name": priority}
        }
    }

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with aiohttp.ClientSession(auth=auth) as session:
        try:
            async with session.post(f"{JIRA_URL}/rest/api/3/issue", json=payload, ssl=ssl_context) as resp:
                if resp.status != 201:
                    error = await resp.text()
                    logger.error("Ошибка создания подзадачи: %s — %s", resp.status, error)
                    return None
                result = await resp.json()
                issue_key = result.get("key")
                logger.info("Подзадача %s создана", issue_key)
        except Exception as e:
            logger.exception("Ошибка запроса к Jira: %s", e)
            return None

        # Прикрепление файлов
        if files:
            for i, file_id in enumerate(files):
                file = await bot.get_file(file_id)
                file_bytes = await bot.download_file(file.file_path)
                attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
                data_attach = aiohttp.FormData()
                data_attach.add_field('file', file_bytes.read(), filename=f"screenshot_{i+1}.jpg", content_type='image/jpeg')
                headers = {"X-Atlassian-Token": "no-check"}
                try:
                    async with session.post(attach_url, data=data_attach, headers=headers, ssl=ssl_context) as attach_resp:
                        if attach_resp.status in (200, 201):
                            logger.info("Скриншот %s прикреплён к подзадаче %s", i+1, issue_key)
                except Exception as e:
                    logger.exception("Ошибка прикрепления скриншота: %s", e)

    return issue_key

# =======================
# Регистрация FSM хендлеров
# =======================
def register_jira_handlers(dp, bot: Bot, JIRA_EMAIL: str, JIRA_API_TOKEN: str, JIRA_PROJECT_KEY: str,
                           JIRA_PARENT_KEY: str, JIRA_URL: str):
    
    # ======= /jira =======
    @dp.message(F.text == "/jira")
    async def start_jira_fsm(message: Message, state: FSMContext):
        await state.clear()
        await state.update_data(files=[])
        await message.answer("🚀 <b>Регистрация дефекта</b>\n\n📌 <b>Шаг 1:</b> Введите заголовок дефекта (коротко и ясно):")
        await state.set_state(JiraFSM.waiting_title)

    # ======= Заголовок =======
    @dp.message(JiraFSM.waiting_title)
    async def jira_title_handler(message: Message, state: FSMContext):
        title = message.text.strip()
        if not title:
            await message.answer("⚠️ Заголовок не может быть пустым. Попробуйте ещё раз:")
            return
        await state.update_data(title=title)
        await message.answer("📝 <b>Шаг 2:</b> Введите описание дефекта.\nОпишите суть, что нужно сделать и любые детали.")
        await state.set_state(JiraFSM.waiting_description)

    # ======= Описание =======
    @dp.message(JiraFSM.waiting_description)
    async def jira_description_handler(message: Message, state: FSMContext):
        description = message.text.strip()
        await state.update_data(description=description)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Низкий", callback_data="priority_low"),
             InlineKeyboardButton(text="🟡 Средний", callback_data="priority_medium"),
             InlineKeyboardButton(text="🔴 Высокий", callback_data="priority_high")]
        ])
        await message.answer("⚡ <b>Шаг 3:</b> Выберите приоритет задачи:", reply_markup=kb)
        await state.set_state(JiraFSM.waiting_priority)

    # ======= Приоритет =======
    @dp.callback_query(JiraFSM.waiting_priority)
    async def jira_priority_handler(callback: CallbackQuery, state: FSMContext):
        mapping = {"priority_low": "Low", "priority_medium": "Medium", "priority_high": "High"}
        await state.update_data(priority=mapping.get(callback.data, "Medium"))

        kb_links = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_links")]
        ])
        await callback.message.answer("🔗 <b>Шаг 4:</b> Введите ссылки через пробел или нажмите 'Пропустить'", reply_markup=kb_links)
        await state.set_state(JiraFSM.waiting_links_input)
        await callback.answer()

    # ======= Ввод ссылок =======
    @dp.message(JiraFSM.waiting_links_input)
    async def jira_links_input_handler(message: Message, state: FSMContext):
        links_text = message.text.strip()
        links = [] if links_text.lower() in ("пропустить", "skip") else links_text.split()
        await state.update_data(links=links)

        kb_screenshots = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_screenshots")]
        ])
        await message.answer("📸 <b>Шаг 5:</b> Прикрепите скриншоты или нажмите 'Пропустить'", reply_markup=kb_screenshots)
        await state.set_state(JiraFSM.waiting_screenshots)

    # ======= Пропустить ссылки =======
    @dp.callback_query(F.data == "skip_links")
    async def skip_links(callback: CallbackQuery, state: FSMContext):
        await state.update_data(links=[])
        kb_screenshots = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_screenshots")]
        ])
        await callback.message.answer("📸 <b>Шаг 5:</b> Прикрепите скриншоты или нажмите 'Пропустить'", reply_markup=kb_screenshots)
        await state.set_state(JiraFSM.waiting_screenshots)
        await callback.answer()

    # ======= Пропустить скриншоты =======
    @dp.callback_query(F.data == "skip_screenshots")
    async def skip_screenshots(callback: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        issue_key = await create_jira_ticket_fsm(bot, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY,
                                                 JIRA_PARENT_KEY, JIRA_URL, data, author=callback.from_user.full_name)
        await state.clear()
        if issue_key:
            text_notify = f"✅ <b>Подзадача создана!</b>\n🔑 <b>{issue_key}</b>\n👤 Автор: <b>{callback.from_user.full_name}</b>\n"
            if data.get("links"):
                text_notify += "🔗 Ссылки:\n" + "\n".join(data["links"]) + "\n"
            files = data.get("files", [])
            if files:
                text_notify += f"📎 Прикреплено файлов: {len(files)}\n"
            text_notify += f"\n<a href=\"{JIRA_URL}/browse/{issue_key}\">Открыть задачу в Jira</a>"
            await callback.message.answer(text_notify, reply_markup=ReplyKeyboardRemove())
        else:
            await callback.message.answer("❌ Ошибка при создании подзадачи.", reply_markup=ReplyKeyboardRemove())
        await callback.answer()

    # ======= Скриншоты =======
    @dp.message(JiraFSM.waiting_screenshots)
    async def jira_screenshots_handler(message: Message, state: FSMContext):
        data = await state.get_data()
        files = data.get("files", [])

        kb_skip = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="skip_screenshots")]
        ])

        if message.photo:
            for photo in message.photo[-1:]:
                if photo.file_id not in files:
                    files.append(photo.file_id)
            await state.update_data(files=files)
            await message.answer(
                f"✅ Скриншот добавлен. Всего файлов: {len(files)}\nПрикрепите ещё или нажмите 'Пропустить'.",
                reply_markup=kb_skip
            )
            return
        else:
            await message.answer(
                "⚠️ Пожалуйста, отправьте фото или нажмите 'Пропустить'.",
                reply_markup=kb_skip
            )
            return
