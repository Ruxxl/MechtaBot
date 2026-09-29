"""
services/market_tests_service.py

Запуск E2E-тестов market_site (Playwright/pytest, репозиторий
mechta-market/microservices-tests-automation) через GitHub Actions.

Сами тесты в боте не гоняются — на free-инстансе Render нет ни памяти, ни
браузера для Playwright. Бот только дергает workflow_dispatch воркфлоу
.github/workflows/market_site_tests.yml, ждет завершения рана и разбирает
JUnit-отчет, который воркфлоу выкладывает артефактом `junit-report`.

Токену (MARKET_TESTS_GITHUB_TOKEN, по умолчанию GITHUB_TOKEN) нужны права
Actions: read & write на репозиторий с тестами.
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional

import aiohttp

logger = logging.getLogger("bot.market_tests")

API_BASE = "https://api.github.com"
ARTIFACT_NAME = "junit-report"


def parse_junit(xml_bytes: bytes) -> dict:
    """Сводка по JUnit XML от pytest: итоги + список упавших тестов."""
    root = ET.fromstring(xml_bytes)
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    failed = []
    for suite in suites:
        totals["tests"] += int(suite.get("tests", 0))
        totals["failures"] += int(suite.get("failures", 0))
        totals["errors"] += int(suite.get("errors", 0))
        totals["skipped"] += int(suite.get("skipped", 0))
        totals["time"] += float(suite.get("time", 0) or 0)
        for case in suite.iter("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                continue
            # classname у pytest — модуль через точки: tests.e2e.cart.test_add_basket
            module = (case.get("classname") or "").rsplit(".", 1)[-1]
            failed.append({
                "name": f"{module}::{case.get('name')}" if module else case.get("name"),
                "message": (problem.get("message") or "").strip().split("\n")[0][:200],
            })

    totals["passed"] = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    return {"totals": totals, "failed": failed}


class MarketTestsService:
    def __init__(
        self,
        repo_full_name: str,
        github_token: Optional[str],
        workflow_file: str = "market_site_tests.yml",
        ref: str = "market_site",
        base_url: str = "https://pp.yc.mechta.kz",
    ):
        self.repo_full_name = repo_full_name
        self.github_token = github_token
        self.workflow_file = workflow_file
        self.ref = ref
        self.base_url = base_url

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    async def dispatch(self, suite: str, test_path: str = "") -> dict:
        """Запускает воркфлоу и возвращает {"run_id", "url"}.
        Бросает RuntimeError с понятным текстом, если запустить не удалось."""
        if not self.github_token:
            raise RuntimeError("не задан GITHUB_TOKEN / MARKET_TESTS_GITHUB_TOKEN")

        request_id = uuid.uuid4().hex[:10]
        url = f"{API_BASE}/repos/{self.repo_full_name}/actions/workflows/{self.workflow_file}/dispatches"
        payload = {
            "ref": self.ref,
            "inputs": {
                "suite": suite,
                "test_path": test_path,
                "base_url": self.base_url,
                "request_id": request_id,
            },
            "return_run_details": True,
        }
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"run_id": data["workflow_run_id"], "url": data.get("html_url")}
                if resp.status != 204:
                    body = await resp.text()
                    raise RuntimeError(f"GitHub ответил {resp.status}: {body[:300]}")

            # Старое поведение API (204 без деталей) — ищем свой ран по run-name,
            # в который воркфлоу подставляет request_id.
            return await self._find_run_by_request_id(session, request_id)

    async def _find_run_by_request_id(self, session: aiohttp.ClientSession, request_id: str) -> dict:
        runs_url = f"{API_BASE}/repos/{self.repo_full_name}/actions/workflows/{self.workflow_file}/runs"
        for _ in range(12):
            await asyncio.sleep(5)
            async with session.get(runs_url, params={"event": "workflow_dispatch", "per_page": 20}) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
            for run in data.get("workflow_runs", []):
                if request_id in (run.get("display_title") or ""):
                    return {"run_id": run["id"], "url": run.get("html_url")}
        raise RuntimeError("воркфлоу запущен, но ран не найден за минуту — проверь вкладку Actions")

    async def get_run(self, run_id: int) -> Optional[dict]:
        url = f"{API_BASE}/repos/{self.repo_full_name}/actions/runs/{run_id}"
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()

    async def cancel_run(self, run_id: int) -> bool:
        url = f"{API_BASE}/repos/{self.repo_full_name}/actions/runs/{run_id}/cancel"
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.post(url) as resp:
                return resp.status == 202

    async def get_report(self, run_id: int) -> Optional[dict]:
        """Скачивает артефакт junit-report и возвращает parse_junit(), либо None."""
        url = f"{API_BASE}/repos/{self.repo_full_name}/actions/runs/{run_id}/artifacts"
        try:
            async with aiohttp.ClientSession(headers=self._headers()) as session:
                async with session.get(url, params={"name": ARTIFACT_NAME}) as resp:
                    if resp.status != 200:
                        return None
                    artifacts = (await resp.json()).get("artifacts", [])
                if not artifacts:
                    return None

                location = None
                async with session.get(artifacts[0]["archive_download_url"], allow_redirects=False) as resp:
                    if resp.status in (301, 302):
                        location = resp.headers.get("Location")
                    elif resp.status == 200:
                        archive = await resp.read()
                if location:
                    # Presigned URL blob-хранилища — без Authorization-заголовка
                    async with aiohttp.ClientSession() as plain:
                        async with plain.get(location) as resp2:
                            if resp2.status != 200:
                                return None
                            archive = await resp2.read()
                elif resp.status != 200:
                    return None

            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                xml_name = next((n for n in zf.namelist() if n.endswith(".xml")), None)
                if not xml_name:
                    return None
                return parse_junit(zf.read(xml_name))
        except Exception as e:
            logger.error(f"Ошибка получения JUnit-отчета рана {run_id}: {e}")
            return None
