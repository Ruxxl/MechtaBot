from aiogram import Bot, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

CALLBACK_PREFIX = "help_cat:"

MAIN_MENU_TEXT = (
    "🤖 <b>Mechta.kz BOT</b>\n\n"
    "Я помогаю команде с Jira, нагрузочным тестированием, поиском по "
    "документации, релизами и напоминаниями.\n\n"
    "Выбери раздел, чтобы узнать, что я умею:"
)

# =======================
# Контент разделов
# =======================
CATEGORIES = {
    "jira": {
        "label": "🐞 Jira и баги",
        "text": (
            "🐞 <b>Jira и баги</b>\n\n"
            "• <code>#bug</code> или <code>#jira</code> в тексте или подписи к "
            "фото — создам подзадачу в Jira по этому сообщению.\n\n"
            "• <code>/jira</code> — пошаговая форма регистрации дефекта: "
            "заголовок → описание → приоритет → ссылки → скриншоты. В конце "
            "задача появится в чате со ссылкой.\n\n"
            "• <code>#check</code> — быстрая проверка, что я на связи."
        ),
    },
    "stress": {
        "label": "🚦 Нагрузочное тестирование",
        "text": (
            "🚦 <b>Нагрузочное тестирование</b>\n\n"
            "• <code>/stress</code> — выбери стенд (или укажи host вручную), "
            "число пользователей и длительность. Запущу Locust и буду "
            "присылать live-прогресс (RPS, ошибки), а в конце — полный отчет "
            "с разбивкой по типам страниц.\n\n"
            "• Кнопка «⏹ Остановить тест» под сообщением с прогрессом "
            "останавливает тест досрочно.\n\n"
            "• После успешного деплоя нужного стенда я сам запускаю короткий "
            "smoke-тест — ничего нажимать не нужно, просто следи за "
            "уведомлениями."
        ),
    },
    "faq": {
        "label": "📚 FAQ / документация",
        "text": (
            "📚 <b>FAQ / документация</b>\n\n"
            "• <code>/ask вопрос</code> или тег <code>#faq</code> в "
            "сообщении — поищу ответ в Confluence и отвечу своими словами со "
            "ссылками на источники.\n\n"
            "• Если не найду ответа в документации — так и скажу, вместо "
            "того чтобы придумывать."
        ),
    },
    "team": {
        "label": "👥 Команда и спринт",
        "text": (
            "👥 <b>Команда и спринт</b>\n\n"
            "• <code>/team</code> — список участников проекта Jira. Нажми на "
            "имя — покажу все его задачи в активном спринте и подзадачи к "
            "ним (в том числе назначенные на других), со ссылками и "
            "статусами.\n\n"
            "• Задачи в статусах «Готово» и «Ожидает релиза» не показываю — "
            "чтобы видно было только то, что реально в работе."
        ),
    },
    "github": {
        "label": "🚀 GitHub и стенды",
        "text": (
            "🚀 <b>GitHub и стенды</b>\n\n"
            "• <code>/stands</code> — статус последней сборки по каждому "
            "стенду: кто задеплоил, когда, ссылки на коммит и билд.\n\n"
            "• Присылаю уведомление в чат сразу после завершения деплоя "
            "GitHub Actions (успех или ошибка) — можно не проверять вручную."
        ),
    },
    "misc": {
        "label": "📅 Напоминания, релизы и HR",
        "text": (
            "📅 <b>Напоминания, релизы и HR</b>\n\n"
            "• Утром (08:05) и вечером (17:00) по будням — напоминание "
            "отметиться в Clockster.\n\n"
            "• Кнопка «📊 Посмотреть статус будущего релиза» под утренним "
            "напоминанием покажет ближайший невыпущенный релиз и его "
            "задачи.\n\n"
            "• О выходе релиза сообщаю сам — с AI-описанием и списком "
            "задач.\n\n"
            "• Раз в месяц (30-го числа) присылаю отчет по релизам/задачам/"
            "багам за месяц.\n\n"
            "• <code>#hr</code> — меню HR-тем (отметки, командировки, "
            "обходной лист).\n\n"
            "• Фото или текст, присланные мне в личные сообщения, я "
            "пересылаю в основной чат."
        ),
    },
}


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    keys = list(CATEGORIES.keys())
    buttons = []
    row = []
    for key in keys:
        row.append(
            InlineKeyboardButton(
                text=CATEGORIES[key]["label"],
                callback_data=f"{CALLBACK_PREFIX}{key}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{CALLBACK_PREFIX}main")]
    ])


# =======================
# Регистрация хендлеров
# =======================
def register_help_handlers(dp, bot: Bot):

    @dp.message(F.text.in_({"/help", "/start"}))
    async def show_help_menu(message: Message):
        await message.reply(MAIN_MENU_TEXT, reply_markup=_main_menu_keyboard())

    @dp.callback_query(F.data.startswith(CALLBACK_PREFIX))
    async def handle_help_callback(callback: CallbackQuery):
        key = callback.data[len(CALLBACK_PREFIX):]

        if key == "main":
            await callback.message.edit_text(MAIN_MENU_TEXT, reply_markup=_main_menu_keyboard())
            await callback.answer()
            return

        category = CATEGORIES.get(key)
        if not category:
            await callback.answer("Раздел не найден.")
            return

        await callback.message.edit_text(
            category["text"],
            reply_markup=_category_keyboard(),
            disable_web_page_preview=True,
        )
        await callback.answer()