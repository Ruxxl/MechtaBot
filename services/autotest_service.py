"""
services/autotest_service.py

Опрашивает GitHub Actions API стороннего репозитория с автотестами
(Cypress, https://github.com/Ruxxl/MechtaATest) и парсит текстовые логи
джобов, чтобы получить разбивку результатов по спекам (файлам тестов) —
сам GitHub Actions API отдает только общий conclusion воркфлоу, без
разбивки по спекам/тестам, поэтому нужную таблицу приходится вытаскивать
из финального вывода Cypress ("Run Finished") в логах джобы.

Ничего не меняет в MechtaATest и не требует правок в его CI — это read-only
опрос публичного GitHub API тем же GITHUB_TOKEN, что уже используется в
web/webhook_handler.py (нужен только доступ на чтение Actions нужного репо).

Используется как services["autotests"] в web/miniapp_api.py. Кэш обновляется
по таймеру из monitors/autotest_runs_monitor.py (см. main.py) — сами
эндпоинты Mini App отдают уже готовый кэш, не дергая GitHub на каждый запрос.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger("bot.autotest_service")

API_BASE = "https://api.github.com"

# Финальная таблица "Run Finished" в выводе Cypress выглядит примерно так:
#   │ ✔  Regress_Test/add_basket.cy.js         00:02        5        5        -        -        - │
# Группы: имя спека, длительность, tests, passing, failing, pending, skipped.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_SPEC_ROW_RE = re.compile(
    r"│\s*[✔✖]?\s*([^\s│]+\.cy\.js)\s+(\d{1,2}:\d{2})\s+(\d+)\s+([\d-]+)\s+([\d-]+)\s+([\d-]+)\s+([\d-]+)\s*│"
)


def _num(value: str) -> int:
    return 0 if value == "-" else int(value)


def _parse_spec_rows(log_text: str) -> list[dict]:
    """Достает построчную сводку по спекам из сырого лога джобы Cypress."""
    clean = _ANSI_RE.sub("", log_text)
    specs = []
    for m in _SPEC_ROW_RE.finditer(clean):
        name, duration, tests, passing, failing, pending, skipped = m.groups()
        specs.append({
            "name": name,
            "duration": duration,
            "tests": _num(tests),
            "passing": _num(passing),
            "failing": _num(failing),
            "pending": _num(pending),
            "skipped": _num(skipped),
        })
    return specs


class AutotestRunsService:
    """Источник данных о прогонах автотестов для Mini App (services["autotests"])."""

    def __init__(self, repo_full_name: str, github_token: Optional[str], workflow_name: str = "Cypress Tests"):
        self.repo_full_name = repo_full_name
        self.github_token = github_token
        self.workflow_name = workflow_name
        self._workflow_id: Optional[int] = None
        self._latest: Optional[dict] = None
        self._history: list[dict] = []  # newest first, максимум 10
        self._last_run_id: Optional[int] = None

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        return headers

    async def _resolve_workflow_id(self, session: aiohttp.ClientSession) -> Optional[int]:
        if self._workflow_id is not None:
            return self._workflow_id
        url = f"{API_BASE}/repos/{self.repo_full_name}/actions/workflows"
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.error(f"Не удалось получить список воркфлоу {self.repo_full_name}: {resp.status}")
                return None
            data = await resp.json()
        workflow = next(
            (w for w in data.get("workflows", []) if w["name"].lower() == self.workflow_name.lower()),
            None,
        )
        if not workflow:
            logger.error(f"Воркфлоу '{self.workflow_name}' не найден в {self.repo_full_name}")
            return None
        self._workflow_id = workflow["id"]
        return self._workflow_id

    async def _fetch_job_log(self, session: aiohttp.ClientSession, job_id: int) -> Optional[str]:
        url = f"{API_BASE}/repos/{self.repo_full_name}/actions/jobs/{job_id}/logs"
        try:
            location = None
            async with session.get(url, allow_redirects=False) as resp:
                if resp.status == 200:
                    return await resp.text()
                if resp.status in (301, 302):
                    location = resp.headers.get("Location")
            if not location:
                return None
            # Редирект ведет на presigned URL blob-хранилища GitHub — его нужно
            # запрашивать без Authorization-заголовка (некоторые presigned URL
            # его не принимают и отвечают ошибкой).
            async with aiohttp.ClientSession() as plain:
                async with plain.get(location) as resp2:
                    if resp2.status != 200:
                        return None
                    return await resp2.text()
        except Exception as e:
            logger.error(f"Ошибка получения лога джобы {job_id}: {e}")
            return None

    async def refresh(self) -> None:
        """Опрашивает GitHub API, парсит логи новых завершенных ранов (если
        появились) и обновляет кэш. Вызывается по таймеру из main.py."""
        if not self.github_token:
            logger.warning("GITHUB_TOKEN не задан — мониторинг автотестов пропущен")
            return

        async with aiohttp.ClientSession(headers=self._headers()) as session:
            workflow_id = await self._resolve_workflow_id(session)
            if workflow_id is None:
                return

            runs_url = f"{API_BASE}/repos/{self.repo_full_name}/actions/workflows/{workflow_id}/runs"
            async with session.get(runs_url, params={"status": "completed", "per_page": 10}) as resp:
                if resp.status != 200:
                    logger.error(f"Ошибка получения ранов {self.repo_full_name}: {resp.status}")
                    return
                data = await resp.json()

            runs = data.get("workflow_runs", [])
            if not runs:
                return

            if self._last_run_id is None:
                # Самый первый опрос после старта бота — не заливаем всю историю
                # разом, только последний завершенный ран.
                new_runs = runs[:1]
            else:
                last_known_number = next(
                    (r["run_number"] for r in runs if r["id"] == self._last_run_id), None
                )
                if last_known_number is None:
                    # Последний известный ран выпал из выдачи (per_page=10) —
                    # подстраховываемся и берем просто самый свежий.
                    new_runs = runs[:1]
                else:
                    new_runs = [r for r in runs if r["run_number"] > last_known_number]

            if not new_runs:
                return

            # От старых к новым, чтобы история осталась в правильном порядке
            for run in sorted(new_runs, key=lambda r: r["run_number"]):
                summary = await self._build_run_summary(session, run)
                if summary is None:
                    continue
                self._latest = summary
                self._last_run_id = summary["_run_id"]
                self._history.insert(0, summary)
                self._history = self._history[:10]
                logger.info(
                    f"🧪 Прогон автотестов #{summary['runNumber']} ({summary['conclusion']}): "
                    f"{summary['totals']['passing']}/{summary['totals']['tests']} passed"
                )
                await self._persist(summary)

    async def _build_run_summary(self, session: aiohttp.ClientSession, run: dict) -> Optional[dict]:
        run_id = run["id"]
        jobs_url = f"{API_BASE}/repos/{self.repo_full_name}/actions/runs/{run_id}/jobs"
        async with session.get(jobs_url) as resp:
            if resp.status != 200:
                return None
            jobs_data = await resp.json()

        specs_by_name: dict = {}
        for job in jobs_data.get("jobs", []):
            log_text = await self._fetch_job_log(session, job["id"])
            if not log_text:
                continue
            for spec in _parse_spec_rows(log_text):
                specs_by_name[spec["name"]] = spec

        specs = sorted(specs_by_name.values(), key=lambda s: s["name"])
        totals = {
            "tests": sum(s["tests"] for s in specs),
            "passing": sum(s["passing"] for s in specs),
            "failing": sum(s["failing"] for s in specs),
            "pending": sum(s["pending"] for s in specs),
            "skipped": sum(s["skipped"] for s in specs),
        }

        head_commit = run.get("head_commit") or {}
        updated_at = run.get("updated_at", "")
        try:
            dt = datetime.fromisoformat(updated_at.replace("Z", ""))
            date_str = dt.strftime("%d.%m.%Y %H:%M")
        except ValueError:
            date_str = updated_at

        return {
            "_run_id": run_id,
            "runNumber": run.get("run_number"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "branch": run.get("head_branch"),
            "actor": (run.get("actor") or {}).get("login", "Unknown"),
            "commit": (head_commit.get("message") or "").split("\n")[0],
            "url": run.get("html_url"),
            "date": date_str,
            "totals": totals,
            "specs": specs,
        }

    async def _persist(self, summary: dict) -> None:
        try:
            from services.db_service import save_autotest_run
            await save_autotest_run(summary)
        except Exception as e:
            logger.error(f"Ошибка сохранения прогона автотестов в БД: {e}")

    async def get_latest(self) -> Optional[dict]:
        if self._latest is None:
            await self._bootstrap_from_db()
        return self._public(self._latest) if self._latest else None

    async def get_runs(self, limit: int = 10) -> list:
        if not self._history:
            await self._bootstrap_from_db()
        return [self._public(r, with_specs=False) for r in self._history[:limit]]

    async def _bootstrap_from_db(self) -> None:
        """После рестарта бота — подхватываем последнее известное состояние из
        Postgres, чтобы Mini App не показывал пустоту, пока не отработает
        первый опрос по таймеру."""
        try:
            from services.db_service import get_recent_autotest_runs
            rows = await get_recent_autotest_runs(limit=10)
        except Exception as e:
            logger.error(f"Ошибка чтения истории автотестов из БД: {e}")
            return
        if rows:
            self._history = rows
            self._latest = rows[0]
            self._last_run_id = rows[0].get("_run_id")

    @staticmethod
    def _public(summary: dict, with_specs: bool = True) -> dict:
        out = {k: v for k, v in summary.items() if not k.startswith("_")}
        if not with_specs:
            out.pop("specs", None)
        return out
