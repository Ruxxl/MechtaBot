"""Хранилище бота в Google Таблице "MechtaBot DB".

Таблица доступна через веб-приложение Apps Script (код — apps_script/db.gs):
POST GSHEETS_DB_URL с JSON {secret, action, ...}. Каждый лист таблицы — бывшая
таблица Postgres с теми же колонками. Все значения в листах хранятся строками,
числа приводятся обратно здесь."""

import logging
import os
from typing import List, Optional

import aiohttp

logger = logging.getLogger("bot.db")

_session: Optional[aiohttp.ClientSession] = None
_url: Optional[str] = None
_secret: Optional[str] = None

_TIMEOUT = aiohttp.ClientTimeout(total=60)


class DBError(Exception):
    pass


async def _call(action: str, **params):
    """Один запрос к веб-приложению. Apps Script отвечает 302 на
    script.googleusercontent.com, aiohttp сам переходит туда GET-ом."""
    async with _session.post(_url, json={"secret": _secret, "action": action, **params}) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    if not data.get("ok"):
        raise DBError(data.get("error"))
    return data.get("result")


def _int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


async def init_db():
    """Открывает HTTP-сессию и проверяет доступ к таблице. Вызывается один раз
    при старте бота. Если GSHEETS_DB_URL/GSHEETS_DB_SECRET не заданы — бот
    продолжает работать как раньше, только на in-memory кэше."""
    global _session, _url, _secret
    url = os.getenv("GSHEETS_DB_URL")
    secret = os.getenv("GSHEETS_DB_SECRET")
    if not url or not secret:
        logger.warning("GSHEETS_DB_URL/GSHEETS_DB_SECRET не заданы — данные в таблицу сохраняться не будут.")
        return

    _url, _secret = url, secret
    _session = aiohttp.ClientSession(timeout=_TIMEOUT)
    try:
        await _call("ping")
        logger.info("✅ Подключение к Google Таблице (MechtaBot DB) установлено.")
    except Exception as e:
        logger.error(f"❌ Не удалось подключиться к Google Таблице: {e}")
        await close_db()


async def close_db():
    global _session
    if _session:
        await _session.close()
        _session = None


async def save_build(stand_key: str, stand_label: str, commit_message: str, actor: str, build_date: str, url: str):
    """Сохраняет запись об успешной заливке на стенд."""
    if not _session:
        return
    try:
        await _call("append", sheet="stand_builds", rows=[{
            "stand_key": stand_key, "stand_label": stand_label, "commit_message": commit_message,
            "actor": actor, "build_date": build_date, "url": url,
        }])
    except Exception as e:
        logger.error(f"Ошибка сохранения заливки в БД (stand_key={stand_key}): {e}")


async def get_latest_builds(stand_key: str, limit: int = 5) -> List[dict]:
    """Последние N заливок по конкретному стенду, свежие первыми.
    Формат записей совпадает с тем, что уже используется в latest_builds
    (webhook_handler.py) и в fetch_latest_build_from_api (GitHub API),
    поэтому main.py может использовать этот результат так же, как и остальные."""
    if not _session:
        return []
    try:
        rows = await _call("select", sheet="stand_builds", where={"stand_key": stand_key},
                           order_by="created_at", desc=True, limit=limit)
        return [
            {"commit": r["commit_message"], "actor": r["actor"], "date": r["build_date"], "url": r["url"]}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Ошибка чтения истории заливок из БД (stand_key={stand_key}): {e}")
        return []


async def get_last_sent_report_month() -> Optional[str]:
    """Месяц (формат "YYYY-MM"), за который ежемесячный отчет уже был отправлен.
    Хранится в таблице (а не в памяти процесса), чтобы рестарт/редеплой бота в
    отчетный день не приводил к повторной отправке отчета."""
    if not _session:
        return None
    try:
        rows = await _call("select", sheet="monthly_report_state", where={"id": "1"}, limit=1)
        return rows[0]["last_sent_month"] if rows else None
    except Exception as e:
        logger.error(f"Ошибка чтения статуса ежемесячного отчета из БД: {e}")
        return None


async def set_last_sent_report_month(month_key: str):
    """Отмечает месяц как отправленный."""
    if not _session:
        return
    try:
        await _call("upsert", sheet="monthly_report_state", key="id",
                    rows=[{"id": "1", "last_sent_month": month_key}])
    except Exception as e:
        logger.error(f"Ошибка сохранения статуса ежемесячного отчета в БД: {e}")


async def get_known_autotest_subtask_keys(parent_key: str) -> Optional[set]:
    """Ключи подзадач (багов автотестов) parent_key, которые уже были отправлены
    в чат.

    Возвращает None, если БД недоступна: это важно, чтобы монитор не считал
    "все текущие подзадачи новыми" при каждом цикле и не спамил в чат после
    недоступности таблицы. Пустой набор означает только то, что в БД еще нет
    записей для этого parent_key, и тогда в чат уйдут все текущие подзадачи."""
    if not _session:
        return None
    try:
        rows = await _call("select", sheet="autotest_bug_subtasks", where={"parent_key": parent_key})
        return {r["subtask_key"] for r in rows}
    except Exception as e:
        logger.error(f"Ошибка чтения отправленных подзадач автотестов из БД (parent_key={parent_key}): {e}")
        return None


async def save_autotest_subtask_keys(parent_key: str, subtask_keys: List[str]):
    """Отмечает подзадачи как отправленные в чат, чтобы при следующих проверках
    (в т.ч. после рестарта/редеплоя бота) они не отправлялись повторно."""
    if not _session or not subtask_keys:
        return
    try:
        await _call("insert_if_absent", sheet="autotest_bug_subtasks", key="subtask_key",
                    rows=[{"subtask_key": key, "parent_key": parent_key} for key in subtask_keys])
    except Exception as e:
        logger.error(f"Ошибка сохранения подзадач автотестов в БД (parent_key={parent_key}): {e}")


async def save_autotest_run(summary: dict):
    """Сохраняет сводку прогона автотестов (см. services/autotest_service.py)
    вместе с разбивкой по спекам. Идемпотентно: повторный опрос того же рана
    просто обновит цифры (один batch-запрос под общей блокировкой скрипта)."""
    if not _session:
        return
    run_id = summary.get("_run_id")
    if run_id is None:
        return
    totals = summary.get("totals", {})
    run_row = {
        "run_id": str(run_id), "run_number": summary.get("runNumber"), "status": summary.get("status"),
        "conclusion": summary.get("conclusion"), "branch": summary.get("branch"), "actor": summary.get("actor"),
        "commit_message": summary.get("commit"), "url": summary.get("url"), "run_date": summary.get("date"),
        "tests": totals.get("tests", 0), "passing": totals.get("passing", 0), "failing": totals.get("failing", 0),
        "pending": totals.get("pending", 0), "skipped": totals.get("skipped", 0),
    }
    spec_rows = [
        {
            "run_id": str(run_id), "spec_name": spec["name"], "duration": spec.get("duration"),
            "tests": spec.get("tests", 0), "passing": spec.get("passing", 0), "failing": spec.get("failing", 0),
            "pending": spec.get("pending", 0), "skipped": spec.get("skipped", 0),
        }
        for spec in summary.get("specs", [])
    ]
    try:
        await _call("batch", ops=[
            {"action": "upsert", "sheet": "autotest_runs", "key": "run_id", "rows": [run_row]},
            {"action": "delete", "sheet": "autotest_run_specs", "where": {"run_id": str(run_id)}},
            {"action": "append", "sheet": "autotest_run_specs", "rows": spec_rows},
        ])
    except Exception as e:
        logger.error(f"Ошибка сохранения прогона автотестов в БД (run_id={run_id}): {e}")


async def get_recent_autotest_runs(limit: int = 10) -> List[dict]:
    """Последние N прогонов автотестов вместе с разбивкой по спекам, свежие
    первыми. Используется для восстановления кэша AutotestRunsService после
    рестарта бота (до первого нового опроса GitHub API)."""
    if not _session:
        return []
    try:
        run_rows = await _call("select", sheet="autotest_runs", order_by="created_at", desc=True, limit=limit)
        if not run_rows:
            return []
        spec_rows = await _call("select", sheet="autotest_run_specs",
                                where={"run_id": [r["run_id"] for r in run_rows]}, order_by="spec_name")
        specs_by_run = {}
        for s in spec_rows:
            specs_by_run.setdefault(s["run_id"], []).append({
                "name": s["spec_name"], "duration": s["duration"], "tests": _int(s["tests"]),
                "passing": _int(s["passing"]), "failing": _int(s["failing"]),
                "pending": _int(s["pending"]), "skipped": _int(s["skipped"]),
            })
        return [
            {
                "_run_id": int(row["run_id"]),
                "runNumber": _int(row["run_number"]) if row["run_number"] is not None else None,
                "status": row["status"],
                "conclusion": row["conclusion"],
                "branch": row["branch"],
                "actor": row["actor"],
                "commit": row["commit_message"],
                "url": row["url"],
                "date": row["run_date"],
                "totals": {
                    "tests": _int(row["tests"]), "passing": _int(row["passing"]), "failing": _int(row["failing"]),
                    "pending": _int(row["pending"]), "skipped": _int(row["skipped"]),
                },
                "specs": specs_by_run.get(row["run_id"], []),
            }
            for row in run_rows
        ]
    except Exception as e:
        logger.error(f"Ошибка чтения истории прогонов автотестов из БД: {e}")
        return []


async def mark_aplaut_review_notified(review_id: str) -> Optional[bool]:
    """Атомарно помечает отзыв Aplaut как отправленный в чат.

    True — отзыв помечен впервые (нужно слать уведомление), False — он уже
    был отправлен раньше (повтор вебхука), None — БД недоступна (решение о
    дедупликации остается за вызывающим кодом)."""
    if not _session:
        return None
    try:
        inserted = await _call("insert_if_absent", sheet="aplaut_notified_reviews", key="review_id",
                               rows=[{"review_id": str(review_id)}])
        return bool(inserted[0])
    except Exception as e:
        logger.error(f"Ошибка сохранения отзыва Aplaut {review_id} в БД: {e}")
        return None


async def has_aplaut_notified_reviews() -> Optional[bool]:
    """Есть ли в БД хоть один отправленный отзыв Aplaut. False означает самый
    первый запуск уведомлений, None — БД недоступна."""
    if not _session:
        return None
    try:
        return (await _call("count", sheet="aplaut_notified_reviews")) > 0
    except Exception as e:
        logger.error(f"Ошибка чтения отправленных отзывов Aplaut из БД: {e}")
        return None
