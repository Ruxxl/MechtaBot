"""
handlers/market_tests_handler.py

/autotest — запуск E2E-тестов market_site через GitHub Actions
(см. services/market_tests_service.py). Выбираешь набор кнопкой, бот
запускает воркфлоу, следит за раном и присылает итоги с упавшими тестами.
"""

import asyncio
import html
import logging
import time
from typing import Dict

from aiogram import Bot, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from services.market_tests_service import MarketTestsService

logger = logging.getLogger("bot.market_tests")

# Ключ — маркер pytest из market_site/pyproject.toml ("all" — все, кроме regress)
SUITES = {
    "all": "🧪 Все тесты",
    "home": "🏠 Главная",
    "catalog": "📂 Каталог/фильтры",
    "search": "🔍 Поиск",
    "product_page": "📦 Страница товара",
    "defectives": "🏷 Уценка",
    "shopwindow": "🪟 На витрине",
    "actions": "🎁 Акции",
    "site_blocks": "🔥 Горячие предложения",
    "basket": "🛒 Корзина",
    "checkout": "💳 Чекаут",
    "favorites": "❤️ Избранное",
    "compare": "⚖️ Сравнение",
    "cabinet": "👤 Личный кабинет",
    "eproducts": "💾 Цифровые товары",
    "footer": "📎 Футер",
    "api": "🔌 API-проверки",
}

POLL_INTERVAL = 30
MAX_WAIT_SECONDS = 3 * 60 * 60
MAX_FAILED_SHOWN = 15

# run_id -> suite; не даем запустить тот же набор повторно, пока он идет
active_runs: Dict[int, str] = {}


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _suites_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=label, callback_data=f"mtest_run:{key}") for key, label in SUITES.items()]
    rows = [buttons[:1]] + [buttons[i:i + 2] for i in range(1, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _run_keyboard(run_id: int, url: str) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text="🛠 Ран в GitHub", url=url)])
    rows.append([InlineKeyboardButton(text="⏹ Отменить", callback_data=f"mtest_cancel:{run_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_duration(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes} мин {sec} сек" if minutes else f"{sec} сек"


def _format_result(suite_label: str, run: dict, report: dict | None, user: str) -> str:
    conclusion = run.get("conclusion")
    icon = {"success": "✅", "failure": "❌", "cancelled": "⏹"}.get(conclusion, "⚠️")
    lines = [f"{icon} <b>Автотесты market_site: {_esc(suite_label)}</b>", f"👤 Запустил: {_esc(user)}"]

    if report is None:
        lines.append(f"\nИтог рана: <b>{_esc(conclusion)}</b>. JUnit-отчет не найден — смотри лог в GitHub.")
        return "\n".join(lines)

    t = report["totals"]
    lines.append(
        f"\n✔️ Прошло: <b>{t['passed']}</b>   ❌ Упало: <b>{t['failures'] + t['errors']}</b>   "
        f"⏭ Пропущено: <b>{t['skipped']}</b>   Всего: {t['tests']}"
    )
    lines.append(f"⏱ Время тестов: {_fmt_duration(t['time'])}")

    failed = report["failed"]
    if failed:
        lines.append("\n<b>Упавшие тесты:</b>")
        for item in failed[:MAX_FAILED_SHOWN]:
            lines.append(f"• <code>{_esc(item['name'])}</code>")
            if item["message"]:
                lines.append(f"  <i>{_esc(item['message'])}</i>")
        if len(failed) > MAX_FAILED_SHOWN:
            lines.append(f"…и еще {len(failed) - MAX_FAILED_SHOWN}")
    return "\n".join(lines)


async def _watch_run(bot: Bot, service: MarketTestsService, message: Message, run_id: int,
                     url: str, suite: str, user: str):
    suite_label = SUITES[suite]
    started = time.monotonic()
    last_status = None
    try:
        while time.monotonic() - started < MAX_WAIT_SECONDS:
            await asyncio.sleep(POLL_INTERVAL)
            run = await service.get_run(run_id)
            if run is None:
                continue
            status = run.get("status")
            if status == "completed":
                report = await service.get_report(run_id)
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛠 Ран в GitHub", url=url)]]) if url else None
                await message.edit_text(
                    _format_result(suite_label, run, report, user),
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
                return
            if status != last_status:
                last_status = status
                state = "в очереди на раннер" if status in ("queued", "waiting", "pending") else "выполняется"
                await message.edit_text(
                    f"🟡 <b>Автотесты market_site: {_esc(suite_label)}</b>\n"
                    f"👤 Запустил: {_esc(user)}\n\nСтатус: {state}…",
                    reply_markup=_run_keyboard(run_id, url),
                    disable_web_page_preview=True,
                )
        await message.edit_text(
            f"⚠️ <b>Автотесты market_site: {_esc(suite_label)}</b>\n"
            f"Ран идет дольше {_fmt_duration(MAX_WAIT_SECONDS)} — перестал следить, смотри в GitHub.",
            reply_markup=_run_keyboard(run_id, url),
        )
    except Exception as e:
        logger.exception(f"Ошибка отслеживания рана {run_id}: {e}")
    finally:
        active_runs.pop(run_id, None)


def register_market_tests_handlers(dp, bot: Bot, service: MarketTestsService):

    @dp.message(F.text.in_({"/autotest", "/autotests"}))
    async def autotest_menu(message: Message):
        await message.reply(
            "🧪 <b>E2E-тесты market_site</b>\n"
            f"Стенд: <code>{_esc(service.base_url)}</code>, ветка <code>{_esc(service.ref)}</code>\n\n"
            "Выбери набор (живой регресс <code>regress</code> не запускается):",
            reply_markup=_suites_keyboard(),
        )

    @dp.callback_query(F.data.startswith("mtest_run:"))
    async def autotest_run(callback: CallbackQuery):
        suite = callback.data.split(":", 1)[1]
        if suite not in SUITES:
            await callback.answer("Неизвестный набор", show_alert=True)
            return
        if suite in active_runs.values():
            await callback.answer("Этот набор уже выполняется — дождись результата", show_alert=True)
            return

        user = callback.from_user.full_name or callback.from_user.username or "—"
        await callback.answer("Запускаю…")
        await callback.message.edit_text(f"⏳ Запускаю «{_esc(SUITES[suite])}» в GitHub Actions…")

        try:
            run = await service.dispatch(suite)
        except Exception as e:
            logger.error(f"Не удалось запустить автотесты ({suite}): {e}")
            await callback.message.edit_text(f"❌ Не удалось запустить автотесты: {_esc(e)}")
            return

        run_id, url = run["run_id"], run.get("url")
        active_runs[run_id] = suite
        await callback.message.edit_text(
            f"🟡 <b>Автотесты market_site: {_esc(SUITES[suite])}</b>\n"
            f"👤 Запустил: {_esc(user)}\n\nСтатус: запущено, жду раннер…",
            reply_markup=_run_keyboard(run_id, url),
            disable_web_page_preview=True,
        )
        asyncio.create_task(_watch_run(bot, service, callback.message, run_id, url, suite, user))

    @dp.callback_query(F.data.startswith("mtest_cancel:"))
    async def autotest_cancel(callback: CallbackQuery):
        try:
            run_id = int(callback.data.split(":", 1)[1])
        except ValueError:
            await callback.answer()
            return
        ok = await service.cancel_run(run_id)
        await callback.answer("Отмена отправлена, итог придет сюда" if ok else "Не удалось отменить ран", show_alert=not ok)
