import aiohttp
import ssl
import logging
import os
from typing import Optional

from aiogram import Bot, F
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
                try:
                    file = await bot.get_file(file_id)
                    file_bytes = await bot.download_file(file.file_path)
                    attach_url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments"
                    data_attach = aiohttp.FormData()
                    data_attach.add_field('file', file_bytes.read(), filename=f"screenshot_{i+1}.jpg", content_type='image/jpeg')
                    headers = {"X-Atlassian-Token": "no-check"}
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
    
    # ID из твоего Main
    TARGET_GROUP_ID = -1002196628724
    TARGET_THREAD_ID = 42896

    @dp.message(F.text == "/jira")
    async def start_jira_fsm(message: Message, state: FSMContext):
        await state.clear()
        await state.update_data(files=[])
        await message.answer("🚀 <b>Регистрация дефекта</b>\n\n📌 <b>Шаг 1:</b> Введите заголовок (коротко):")
        await state.set_state(JiraFSM.waiting_title)

    @dp.message(JiraFSM.waiting_title)
    async def jira_title_handler(message: Message, state: FSMContext):
        title = message.text.strip()
        if not title:
            await message.answer("⚠️ Заголовок пуст. Попробуйте ещё раз:")
            return
        await state.update_data(title=title)
        await message.answer("📝 <b>Шаг 2:</b> Введите описание дефекта:")
        await state.set_state(JiraFSM.waiting_description)

    @dp.message(JiraFSM.waiting_description)
    async def jira_description_handler(message: Message, state: FSMContext):
        await state.update_data(description=message.text.strip())
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Низкий", callback_data="priority_low"),
             InlineKeyboardButton(text="🟡 Средний", callback_data="priority_medium"),
             InlineKeyboardButton(text="🔴 Высокий", callback_data="priority_high")]
        ])
        await message.answer("⚡ <b>Шаг 3:</b> Выберите приоритет:", reply_markup=kb)
        await state.set_state(JiraFSM.waiting_priority)

    @dp.callback_query(JiraFSM.waiting_priority)
    async def jira_priority_handler(callback: CallbackQuery, state: FSMContext):
        mapping = {"priority_low": "Low", "priority_medium": "Medium", "priority_high": "High"}
        await state.update_data(priority=mapping.get(callback.data, "Medium"))
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="skip_links")]])
        await callback.message.answer("🔗 <b>Шаг 4:</b> Введите ссылки через пробел или нажмите 'Пропустить'", reply_markup=kb)
        await state.set_state(JiraFSM.waiting_links_input)
        await callback.answer()

    @dp.message(JiraFSM.waiting_links_input)
    async def jira_links_input_handler(message: Message, state: FSMContext):
        links = message.text.strip().split()
        await state.update_data(links=links)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="skip_screenshots")]])
        await message.answer("📸 <b>Шаг 5:</b> Прикрепите скриншоты или нажмите 'Пропустить'", reply_markup=kb)
        await state.set_state(JiraFSM.waiting_screenshots)

    @dp.callback_query(F.data == "skip_links")
    async def skip_links(callback: CallbackQuery, state: FSMContext):
        await state.update_data(links=[])
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="skip_screenshots")]])
        await callback.message.answer("📸 <b>Шаг 5:</b> Прикрепите скриншоты или нажмите 'Пропустить'", reply_markup=kb)
        await state.set_state(JiraFSM.waiting_screenshots)
        await callback.answer()

    @dp.callback_query(F.data == "skip_screenshots")
    async def finish_jira_fsm(callback: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        author = callback.from_user.full_name
        issue_key = await create_jira_ticket_fsm(bot, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_PARENT_KEY, JIRA_URL, data, author)
        
        await state.clear()
        
        if issue_key:
            jira_link = f"{JIRA_URL}/browse/{issue_key}"
            # Сообщение в топик (TARGET_THREAD_ID)
            channel_text = (
                f"📣 <b>Зарегистрирован новый дефект!</b>\n\n"
                f"🔑 <b>Ключ:</b> <a href='{jira_link}'>{issue_key}</a>\n"
                f"👤 <b>Автор:</b> {author}\n"
                f"📝 <b>Суть:</b> {data.get('title')}\n"
                f"⚡ <b>Приоритет:</b> {data.get('priority')}\n"
            )
            
            await bot.send_message(
                chat_id=TARGET_GROUP_ID,
                message_thread_id=TARGET_THREAD_ID,
                text=channel_text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            await callback.message.answer(f"✅ Готово! Задача {issue_key} создана и отправлена в топик.", reply_markup=ReplyKeyboardRemove())
        else:
            await callback.message.answer("❌ Ошибка при создании задачи в Jira.", reply_markup=ReplyKeyboardRemove())
        await callback.answer()

    @dp.message(JiraFSM.waiting_screenshots, F.photo)
    async def jira_screenshots_handler(message: Message, state: FSMContext):
        data = await state.get_data()
        files = data.get("files", [])
        files.append(message.photo[-1].file_id)
        await state.update_data(files=files)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Завершить", callback_data="skip_screenshots")]])
        await message.answer(f"✅ Скриншот добавлен (всего: {len(files)}). Отправьте еще или нажмите 'Завершить'.", reply_markup=kb)
