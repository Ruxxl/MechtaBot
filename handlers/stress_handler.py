import asyncio
import csv
import html
import logging
import os
import time
import uuid
from typing import Dict, Optional

from aiogram import Bot, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from web.admin_handler import monitor

logger = logging.getLogger("bot.stress")

LOCUSTFILE_PATH = "loadtest/locustfile.py"
REPORTS_DIR = "loadtest/reports"

# Доступные среды для выбора кнопками (ключ: (подпись, host))
ENVIRONMENTS = {
    "pp": ("🟢 Preprod (pp)", "https://pp.yc.mechta.kz"),
    "pp_ext": ("🟢 Preprod Integrations", "http://pp.im.mdev.kz"),
    "d2": ("🔵 D2", "http://d2.im.mdev.kz"),
    "d3": ("🔵 D3", "http://d3.im.mdev.kz"),
    "d4": ("🔵 D4", "http://d4.im.mdev.kz"),
    "1c": ("🟣 1C", "http://1c.im.mdev.kz"),
    "prod": ("🔴 Production", "https://mechta.kz"),
}

os.makedirs(REPORTS_DIR, exist_ok=True)


class StressFSM(StatesGroup):
    waiting_environment = State()
    waiting_custom_host = State()
    waiting_users = State()
    waiting_duration = State()


class StressSession:
    def __init__(self, run_id, chat_id, thread_id, users, duration_seconds, host, env_label):
        self.run_id = run_id
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.users = users
        self.duration_seconds = duration_seconds
        self.host = host
        self.env_label = env_label
        self.csv_prefix = os.path.join(REPORTS_DIR, run_id)
        self.process: Optional[asyncio.subprocess.Process] = None
        self.message_id: Optional[int] = None
        self.started_at: Optional[float] = None
        self.stopped_by_user = False


# одна активная сессия теста на чат
active_sessions: Dict[int, StressSession] = {}


def _env_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for key, (label, _url) in ENVIRONMENTS.items():
        row.append(InlineKeyboardButton(text=label, callback_data=f"stress_env:{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="✏️ Указать вручную", callback_data="stress_env:custom")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _stop_keyboard(run_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ Остановить тест", callback_data=f"stress_stop:{run_id}")]
    ])


def _esc(value) -> str:
    """Экранирует текст перед вставкой в HTML-сообщение Telegram.
    Нужно для всего, что не задано нами в коде: ошибки из CSV locust,
    кастомный host/название среды, введенные пользователем."""
    return html.escape(str(value), quote=False)


def _progress_bar(percent: float, width: int = 12) -> str:
    filled = int(width * percent / 100)
    return "▓" * filled + "░" * (width - filled)


def _fmt_seconds(total_seconds: float) -> str:
    minutes, seconds = divmod(int(total_seconds), 60)
    if minutes:
        return f"{minutes} мин {seconds} сек"
    return f"{seconds} сек"


def _read_aggregated_stats(csv_prefix: str) -> Optional[dict]:
    """Читает агрегированную строку из {csv_prefix}_stats.csv, который locust обновляет на ходу."""
    path = f"{csv_prefix}_stats.csv"
    if not os.path.exists(path):
        return None
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for row in reversed(rows):
            if row.get("Name", "").strip() == "Aggregated":
                return row
    except Exception as e:
        logger.warning(f"Не удалось прочитать stats csv: {e}")
    return None


def _read_breakdown_stats(csv_prefix: str) -> list:
    """Читает по-строчную статистику {csv_prefix}_stats.csv (одна строка на
    каждый stat_name из locustfile, без агрегированной строки) — теперь, когда
    locustfile группирует запросы по типу страницы (product/section/...),
    это дает осмысленную разбивку в отчете, а не 2000+ строк по каждому URL."""
    path = f"{csv_prefix}_stats.csv"
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        logger.warning(f"Не удалось прочитать stats csv: {e}")
        return []
    rows = [r for r in rows if r.get("Name", "").strip() != "Aggregated"]
    rows.sort(key=lambda r: float(r.get("Request Count", 0) or 0), reverse=True)
    return rows


def _read_failures(csv_prefix: str) -> list:
    path = f"{csv_prefix}_failures.csv"
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        logger.warning(f"Не удалось прочитать failures csv: {e}")
        return []


async def _progress_loop(bot: Bot, session: StressSession):
    while session.process and session.process.returncode is None:
        elapsed = time.monotonic() - session.started_at
        percent = min(100, elapsed / session.duration_seconds * 100) if session.duration_seconds else 0
        stats = _read_aggregated_stats(session.csv_prefix)

        lines = [
            "🚦 <b>Нагрузочный тест выполняется...</b>",
            "",
            f"{_progress_bar(percent)} {percent:.0f}%",
            f"⏱ Прошло: {_fmt_seconds(elapsed)} из {_fmt_seconds(session.duration_seconds)}",
            f"👥 Пользователей: {session.users}",
            f"🌐 Среда: {_esc(session.env_label)} ({_esc(session.host)})",
        ]

        if stats:
            lines += [
                "",
                f"📈 Запросов: {stats.get('Request Count', '0')}",
                f"❌ Ошибок: {stats.get('Failure Count', '0')}",
                f"⚡ RPS: {stats.get('Requests/s', '0')}",
            ]

        try:
            await bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.message_id,
                text="\n".join(lines),
                reply_markup=_stop_keyboard(session.run_id),
            )
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                logger.debug(f"Не удалось обновить прогресс: {e}")

        await asyncio.sleep(5)


def _build_report_text(session: StressSession) -> str:
    elapsed = time.monotonic() - session.started_at
    stats = _read_aggregated_stats(session.csv_prefix)
    failures = _read_failures(session.csv_prefix)

    status_line = "⏹ Остановлен пользователем" if session.stopped_by_user else "✅ Завершен по таймеру"

    lines = [
        "📊 <b>Отчет нагрузочного теста</b>",
        "",
        f"Статус: {status_line}",
        f"🌐 Среда: {_esc(session.env_label)} ({_esc(session.host)})",
        f"👥 Пользователей: {session.users}",
        f"⏱ Запланировано: {_fmt_seconds(session.duration_seconds)}",
        f"⏱ Фактически: {_fmt_seconds(elapsed)}",
    ]

    if stats:
        total = int(float(stats.get("Request Count", 0) or 0))
        fail = int(float(stats.get("Failure Count", 0) or 0))
        fail_pct = (fail / total * 100) if total else 0
        lines += [
            "",
            f"📈 Всего запросов: {total}",
            f"❌ Ошибок: {fail} ({fail_pct:.1f}%)",
            f"⚡ RPS (среднее): {stats.get('Requests/s', '0')}",
            f"🕐 Среднее время ответа: {stats.get('Average Response Time', '0')} мс",
            f"🕐 Медиана: {stats.get('Median Response Time', '0')} мс",
            f"🕐 Мин / Макс: {stats.get('Min Response Time', '0')} / {stats.get('Max Response Time', '0')} мс",
        ]
    else:
        lines.append("\n⚠️ Не удалось прочитать статистику теста (возможно, тест остановлен слишком рано).")

    breakdown = _read_breakdown_stats(session.csv_prefix)
    if breakdown:
        lines.append("\n📊 <b>По типам страниц:</b>")
        for row in breakdown:
            name = _esc(row.get("Name", "?"))
            total = row.get("Request Count", "0")
            fail = row.get("Failure Count", "0")
            rps = row.get("Requests/s", "0")
            avg = row.get("Average Response Time", "0")
            lines.append(f"• {name}: {total} запросов, {fail} ошибок, {rps} RPS, {avg} мс")

    if failures:
        lines.append("\n🐞 <b>Топ ошибок:</b>")
        for f in failures[:10]:
            name = _esc(f.get("Name", "?"))
            error = _esc(str(f.get("Error", "?"))[:200])
            occurrences = f.get("Occurrences", "?")
            lines.append(f"• {name} — {error} ({occurrences} раз)")

    return "\n".join(lines)


async def _run_stress_test(bot: Bot, session: StressSession):
    monitor.update_status("Stress Test", "RUNNING")

    spawn_rate = max(1, session.users // 10)
    cmd = [
        "locust",
        "-f", LOCUSTFILE_PATH,
        "--headless",
        "--host", session.host,
        "-u", str(session.users),
        "-r", str(spawn_rate),
        "-t", f"{session.duration_seconds}s",
        "--csv", session.csv_prefix,
        "--csv-full-history",
        "--only-summary",
    ]
    logger.info(f"Запуск нагрузочного теста: {' '.join(cmd)}")

    try:
        session.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        await bot.send_message(
            chat_id=session.chat_id,
            message_thread_id=session.thread_id,
            text="❌ Не удалось запустить locust. Проверь, что пакет 'locust' установлен (requirements.txt).",
        )
        active_sessions.pop(session.chat_id, None)
        monitor.update_status("Stress Test", "ERROR")
        return

    session.started_at = time.monotonic()
    progress_task = asyncio.create_task(_progress_loop(bot, session))

    await session.process.wait()
    progress_task.cancel()

    report_text = _build_report_text(session)

    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.message_id,
            text="🏁 Тест завершен. Отчет ниже 👇",
            reply_markup=None,
        )
    except Exception:
        pass

    await bot.send_message(
        chat_id=session.chat_id,
        message_thread_id=session.thread_id,
        text=report_text,
    )

    monitor.update_status("Stress Test", "OK")
    active_sessions.pop(session.chat_id, None)


def register_stress_handlers(dp, bot: Bot):

    @dp.message(F.text == "/stress")
    async def start_stress(message: Message, state: FSMContext):
        if message.chat.id in active_sessions:
            await message.reply(
                "⚠️ В этом чате уже выполняется нагрузочный тест. "
                "Останови его кнопкой в сообщении выше перед запуском нового."
            )
            return
        await state.clear()
        await message.reply("🌐 Выбери среду для нагрузочного теста:", reply_markup=_env_keyboard())
        await state.set_state(StressFSM.waiting_environment)

    @dp.callback_query(StressFSM.waiting_environment, F.data.startswith("stress_env:"))
    async def stress_env_handler(callback: CallbackQuery, state: FSMContext):
        key = callback.data.split(":", 1)[1]

        if key == "custom":
            await callback.message.edit_text(
                "✏️ Введи host вручную (например https://pp.yc.mechta.kz):"
            )
            await state.set_state(StressFSM.waiting_custom_host)
            await callback.answer()
            return

        env = ENVIRONMENTS.get(key)
        if not env:
            await callback.answer("Неизвестная среда.")
            return

        label, host = env
        await state.update_data(host=host, env_label=label)
        await callback.message.edit_text(
            f"🌐 Среда: {label} ({host})\n👥 Сколько пользователей сэмулировать? Введи число:"
        )
        await state.set_state(StressFSM.waiting_users)
        await callback.answer()

    @dp.message(StressFSM.waiting_custom_host)
    async def stress_custom_host_handler(message: Message, state: FSMContext):
        if not message.text:
            await message.reply("⚠️ Нужно отправить host текстом (например https://pp.yc.mechta.kz). Попробуй еще раз:")
            return
        host = message.text.strip()
        if not (host.startswith("http://") or host.startswith("https://")):
            await message.reply("⚠️ Host должен начинаться с http:// или https://. Попробуй еще раз:")
            return
        await state.update_data(host=host, env_label=host)
        await message.reply(f"🌐 Среда: {_esc(host)}\n👥 Сколько пользователей сэмулировать? Введи число:")
        await state.set_state(StressFSM.waiting_users)

    @dp.message(StressFSM.waiting_users)
    async def stress_users_handler(message: Message, state: FSMContext):
        if not message.text:
            await message.reply("⚠️ Нужно отправить число текстом. Попробуй еще раз:")
            return
        try:
            users = int(message.text.strip())
            if users <= 0:
                raise ValueError
        except ValueError:
            await message.reply("⚠️ Нужно целое положительное число. Попробуй еще раз:")
            return
        await state.update_data(users=users)
        await message.reply("⏱ На сколько минут запустить тест? Введи число:")
        await state.set_state(StressFSM.waiting_duration)

    @dp.message(StressFSM.waiting_duration)
    async def stress_duration_handler(message: Message, state: FSMContext):
        if not message.text:
            await message.reply("⚠️ Нужно отправить число минут текстом. Попробуй еще раз:")
            return
        try:
            minutes = float(message.text.strip().replace(",", "."))
            if minutes <= 0:
                raise ValueError
        except ValueError:
            await message.reply("⚠️ Нужно положительное число минут. Попробуй еще раз:")
            return

        data = await state.get_data()
        users = data["users"]
        host = data["host"]
        env_label = data.get("env_label", host)
        duration_seconds = int(minutes * 60)
        await state.clear()

        run_id = uuid.uuid4().hex[:8]
        session = StressSession(
            run_id=run_id,
            chat_id=message.chat.id,
            thread_id=message.message_thread_id,
            users=users,
            duration_seconds=duration_seconds,
            host=host,
            env_label=env_label,
        )

        sent = await message.reply(
            "🚦 <b>Запускаю нагрузочный тест...</b>\n\n"
            f"🌐 Среда: {_esc(env_label)} ({_esc(host)})\n"
            f"👥 Пользователей: {users}\n"
            f"⏱ Длительность: {_fmt_seconds(duration_seconds)}",
            reply_markup=_stop_keyboard(run_id),
        )
        session.message_id = sent.message_id
        active_sessions[message.chat.id] = session

        asyncio.create_task(_run_stress_test(bot, session))

    @dp.callback_query(F.data.startswith("stress_stop:"))
    async def stress_stop_handler(callback: CallbackQuery):
        run_id = callback.data.split(":", 1)[1]
        session = active_sessions.get(callback.message.chat.id)
        if not session or session.run_id != run_id:
            await callback.answer("Этот тест уже завершен.")
            return
        if session.process and session.process.returncode is None:
            session.stopped_by_user = True
            try:
                session.process.terminate()
            except ProcessLookupError:
                pass
            await callback.answer("⏹ Останавливаю тест...")
        else:
            await callback.answer("Тест уже завершается.")
