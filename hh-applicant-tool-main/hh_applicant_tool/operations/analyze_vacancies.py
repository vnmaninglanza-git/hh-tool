import argparse
import json
import logging
import os
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..api import ApiClient
from ..main import BaseOperation
from ..main import Namespace as BaseNamespace
from ..types import ApiListResponse, VacancyItem
from ..utils import print_err, truncate_string

logger = logging.getLogger(__package__)

LOG_DIR = Path(os.environ.get("HH_LOG_DIR", "/app/logs"))
ANALYSIS_LOG = LOG_DIR / "vacancy_analysis.jsonl"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:12b")

ANALYSIS_PROMPT = """You are a job vacancy analyst. Analyze this vacancy and respond STRICTLY as valid JSON (no markdown, no ```).

CRITICAL RULES:
- "work_format": determine if REMOTE, OFFICE, HYBRID, or UNKNOWN. If they mention time trackers, keystroke loggers, or screenshot monitoring — note it.
- "salary_in_body": extract any salary/compensation mentioned in the description text (even if not in the structured salary field). Return as string or null.
- "required_skills": list of MANDATORY requirements from the job posting.
- "nice_to_have": list of PREFERRED/optional/"будет плюсом"/"желательно" requirements. These are often easier than the main requirements!
- "has_time_tracker": true if they mention any time tracking, screenshot monitoring, Hubstaff, TimeDoctor, etc.
- "verdict": GOOD (remote-friendly, fair requirements), NEUTRAL (hybrid or unclear), SKIP (office-only, has time tracker, red flags)

Respond with this exact JSON structure:
{{
  "summary": "1-2 sentence summary in Russian",
  "work_format": "REMOTE / OFFICE / HYBRID / UNKNOWN",
  "salary_in_body": "salary from description text or null",
  "required_skills": ["skill1", "skill2"],
  "nice_to_have": ["skill1", "skill2"],
  "has_time_tracker": false,
  "red_flags": ["list of red flags if any"],
  "verdict": "GOOD / NEUTRAL / SKIP",
  "reason": "1 sentence why in Russian"
}}

VACANCY:
Title: {name}
Company: {employer}
Structured salary: {salary}
Schedule: {schedule}
Description:
{description}
"""


def _query_ollama(prompt: str, retries: int = 2) -> dict | None:
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.05, "num_predict": 1024},
                },
                timeout=180,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
                text = text.strip()
            # Try to find JSON object in the response
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                return json.loads(match.group())
            return json.loads(text)
        except requests.RequestException as e:
            logger.error("Ollama request failed (attempt %d): %s", attempt + 1, e)
            if attempt < retries:
                time.sleep(2)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse Ollama response (attempt %d): %s\nRaw: %s", attempt + 1, e, text[:200])
            if attempt < retries:
                time.sleep(1)
    return None


def _load_analyzed_ids() -> set[str]:
    ids: set[str] = set()
    if ANALYSIS_LOG.exists():
        for line in ANALYSIS_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                ids.add(str(json.loads(line)["vacancy_id"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return ids


def _log_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _format_salary(salary: dict | None) -> str:
    if not salary:
        return "не указана"
    parts = []
    if salary.get("from"):
        parts.append(f"от {salary['from']}")
    if salary.get("to"):
        parts.append(f"до {salary['to']}")
    if salary.get("currency"):
        parts.append(salary["currency"])
    return " ".join(parts) if parts else "не указана"


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class Namespace(BaseNamespace):
    resume_id: str | None
    limit: int


class Operation(BaseOperation):
    """Анализ вакансий с помощью локальной AI (Ollama)"""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--resume-id", help="ID резюме")
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help="Макс. кол-во вакансий для анализа (по умолчанию: %(default)d)",
        )

    def run(self, args: Namespace) -> None:
        # Check Ollama is running
        try:
            r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            if not any(OLLAMA_MODEL in m for m in models):
                print_err(f"❗ Модель {OLLAMA_MODEL} не найдена в Ollama. Доступные: {models}")
                return 1
        except requests.RequestException:
            print_err(f"❗ Ollama недоступен по адресу {OLLAMA_URL}. Запустите: ollama serve")
            return 1

        assert args.config["token"]
        api = ApiClient(
            access_token=args.config["token"]["access_token"],
            user_agent=args.config["user_agent"],
        )
        if not (
            resume_id := args.resume_id or args.config["default_resume_id"]
        ):
            resumes: ApiListResponse = api.get("/resumes/mine")
            resume_id = resumes["items"][0]["id"]

        # Load custom prompt from config if set
        custom_prompt = args.config.get("analysis_prompt")
        prompt_template = custom_prompt if custom_prompt else ANALYSIS_PROMPT

        # Fetch vacancies
        print(f"🔍 Загружаем вакансии...")
        vacancies = self._get_vacancies(api, resume_id, args.limit)

        # Skip already analyzed
        analyzed_ids = _load_analyzed_ids()
        before = len(vacancies)
        vacancies = [v for v in vacancies if str(v["id"]) not in analyzed_ids]
        skipped = before - len(vacancies)
        if skipped:
            print(f"⏭  Пропущено {skipped} уже проанализированных")

        if not vacancies:
            print("✅ Все вакансии уже проанализированы!")
            return

        print(f"📋 {len(vacancies)} новых вакансий. Анализ через {OLLAMA_MODEL}...\n")

        good = []
        neutral = []
        skip = []

        for i, item in enumerate(vacancies, 1):
            name = item["name"]
            employer = item.get("employer", {}).get("name", "—")
            print(f"[{i}/{len(vacancies)}] {truncate_string(name, 45)} ({employer})...", end=" ", flush=True)

            # Fetch full vacancy details
            full_data = self._get_vacancy_full(api, str(item["id"]))
            description = _strip_html(full_data.get("description", ""))
            schedule = full_data.get("schedule", {}).get("name", "не указан")

            salary_str = _format_salary(item.get("salary"))
            prompt = prompt_template.format(
                name=name,
                employer=employer,
                salary=salary_str,
                schedule=schedule,
                description=description[:4000],
            )

            analysis = _query_ollama(prompt)
            if not analysis:
                print("⚠️  ошибка")
                continue

            verdict = analysis.get("verdict", "NEUTRAL").upper()
            work_fmt = analysis.get("work_format", "?")
            tracker = " 🕐TRACKER" if analysis.get("has_time_tracker") else ""
            print(f"→ {verdict} [{work_fmt}]{tracker}")

            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "vacancy_id": str(item["id"]),
                "name": name,
                "employer": employer,
                "url": item["alternate_url"],
                "salary_structured": salary_str,
                "salary_in_body": analysis.get("salary_in_body"),
                "schedule": schedule,
                "has_test": item.get("has_test", False),
                "analysis": analysis,
            }
            _log_jsonl(ANALYSIS_LOG, entry)

            if verdict == "GOOD":
                good.append(entry)
            elif verdict == "SKIP":
                skip.append(entry)
            else:
                neutral.append(entry)

            # Delay between requests
            time.sleep(random.uniform(1.0, 2.0))

        # Print detailed summary
        print("\n" + "=" * 70)
        print(f"📊 РЕЗУЛЬТАТЫ АНАЛИЗА ({len(vacancies)} вакансий)")
        print(f"   ✅ {len(good)} рекомендуемых  |  ➡️  {len(neutral)} нейтральных  |  🚫 {len(skip)} пропустить")
        print("=" * 70)

        if good:
            print(f"\n✅ РЕКОМЕНДУЕМЫЕ ({len(good)}):")
            for e in good:
                a = e["analysis"]
                sal = e["salary_structured"]
                if a.get("salary_in_body"):
                    sal = f"{sal} (в тексте: {a['salary_in_body']})"
                print(f"\n  📌 {e['name']}  |  {e['employer']}")
                print(f"     💰 {sal}  |  📍 {a.get('work_format', '?')}  |  📅 {e['schedule']}")
                print(f"     {a.get('summary', '')}")
                if a.get("required_skills"):
                    print(f"     📋 Требуется: {', '.join(a['required_skills'][:6])}")
                if a.get("nice_to_have"):
                    print(f"     ⭐ Плюсом: {', '.join(a['nice_to_have'][:6])}")
                print(f"     🔗 {e['url']}")

        if neutral:
            print(f"\n➡️  НЕЙТРАЛЬНЫЕ ({len(neutral)}):")
            for e in neutral:
                a = e["analysis"]
                sal = e["salary_structured"]
                if a.get("salary_in_body"):
                    sal = f"{sal} (в тексте: {a['salary_in_body']})"
                print(f"  • {truncate_string(e['name'], 40)}  |  {e['employer']}  |  💰 {sal}  |  📍 {a.get('work_format', '?')}")
                if a.get("nice_to_have"):
                    print(f"    ⭐ Плюсом: {', '.join(a['nice_to_have'][:4])}")
                print(f"    {e['url']}")

        if skip:
            print(f"\n🚫 ПРОПУСТИТЬ ({len(skip)}):")
            for e in skip:
                a = e["analysis"]
                flags = a.get("red_flags", [])
                reason = a.get("reason", "—")
                tracker = " | 🕐 TIME TRACKER" if a.get("has_time_tracker") else ""
                print(f"  ✗ {truncate_string(e['name'], 40)}  |  {e['employer']}  |  📍 {a.get('work_format', '?')}{tracker}")
                print(f"    Причина: {reason}")
                if flags:
                    print(f"    ⚠️  {', '.join(flags)}")

        print("\n" + "=" * 70)
        print(f"📁 Подробный лог: {ANALYSIS_LOG}")

    def _get_vacancies(
        self, api: ApiClient, resume_id: str, limit: int
    ) -> list[VacancyItem]:
        rv = []
        per_page = min(limit, 100)
        for page in range(20):
            res: ApiListResponse = api.get(
                f"/resumes/{resume_id}/similar_vacancies",
                page=page,
                per_page=per_page,
                order_by="relevance",
            )
            rv.extend(res["items"])
            if len(rv) >= limit or page >= res["pages"] - 1:
                break
        return rv[:limit]

    def _get_vacancy_full(self, api: ApiClient, vacancy_id: str) -> dict:
        try:
            return api.get(f"/vacancies/{vacancy_id}")
        except Exception:
            return {}
