from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F

HR_TOPICS = {
    "attendance": {"title": "Отметки приход/уход", "text": "Все запросы об отметках (приход/уход) необходимо направлять на <b>Адильжана</b>."},
    "bs_order": {"title": "Порядок согласования БС", "text": "Обновлён порядок согласования заявок в Битриксе..."},
    "business_trip": {"title": "Командировка ✈️", "text": "✈️ Инструкция по командировке..."},
    "uvolnenie": {"title": "Обходной лист (увольнение)", "text": "Добрый день, уважаемые коллеги!\n\nСо сегодняшнего дня запускаем в работу электронный обходной лист..."}
}

def get_hr_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=HR_TOPICS["attendance"]["title"], callback_data="hr_attendance")],
        [InlineKeyboardButton(text=HR_TOPICS["bs_order"]["title"], callback_data="hr_bs_order")],
        [InlineKeyboardButton(text=HR_TOPICS["business_trip"]["title"], callback_data="hr_business_trip")],
        [InlineKeyboardButton(text=HR_TOPICS["uvolnenie"]["title"], callback_data="hr_uvolnenie")]
    ])
    return kb
