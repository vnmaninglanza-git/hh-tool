# Этот модуль можно использовать как образец для других
import argparse
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from ..api import ApiClient, ApiError, BadRequest
from ..main import BaseOperation
from ..main import Namespace as BaseNamespace
from ..types import ApiListResponse, VacancyItem
from ..utils import print_err, truncate_string

logger = logging.getLogger(__package__)

LOG_DIR = Path(os.environ.get("HH_LOG_DIR", "/app/logs"))
APPLICATIONS_LOG = LOG_DIR / "applications.jsonl"
TEST_VACANCIES_LOG = LOG_DIR / "test_vacancies.jsonl"


def _load_applied_ids() -> set[str]:
    ids: set[str] = set()
    if APPLICATIONS_LOG.exists():
        with APPLICATIONS_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ids.add(str(entry["vacancy_id"]))
                except (json.JSONDecodeError, KeyError):
                    continue
    return ids


def _log_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class Namespace(BaseNamespace):
    resume_id: str | None
    message_list: TextIO


class Operation(BaseOperation):
    """Откликнуться на все подходящие вакансии"""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--resume-id", help="Идентефикатор резюме")
        parser.add_argument(
            "--message-list",
            help="Путь до файла, где хранятся сообщения для отклика на вакансии. Каждое сообщение — с новой строки. В сообщения можно использовать плейсхолдеры типа %%(name)s",
            type=argparse.FileType(),
        )
        parser.add_argument(
            "--force-message",
            help="Всегда отправлять сообщение при отклике",
            default=False,
            action=argparse.BooleanOptionalAction,
        )

    def run(self, args: Namespace) -> None:
        assert args.config["token"]
        api = ApiClient(
            access_token=args.config["token"]["access_token"],
            user_agent=args.config["user_agent"],
        )
        if not (
            resume_id := args.resume_id or args.config["default_resume_id"]
        ):
            resumes: ApiListResponse = api.get("/resumes/mine")
            # Используем id первого резюме
            resume_id = resumes["items"][0]["id"]
        if args.message_list:
            application_messages = list(
                filter(None, map(str.strip, args.message_list))
            )
        else:
            application_messages = [
                "Меня заинтересовала Ваша вакансия %(name)s",
                "Прошу рассмотреть мою кандидатуру на вакансию %(name)s",
            ]
        self._apply_similar(
            api, resume_id, args.force_message, application_messages
        )

    def _get_vacancies(
        self, api: ApiClient, resume_id: str
    ) -> list[VacancyItem]:
        rv = []
        # работает ограничение: глубина возвращаемых результатов не может быть больше 2000
        # Номер страницы (считается от 0, по умолчанию - 0)
        per_page = 100
        for page in range(20):
            res: ApiListResponse = api.get(
                f"/resumes/{resume_id}/similar_vacancies",
                page=page,
                per_page=per_page,
                order_by="relevance",
            )
            rv.extend(res["items"])
            if page >= res["pages"] - 1:
                break
        return rv

    def _apply_similar(
        self,
        api: ApiClient,
        resume_id: str,
        force_message: bool,
        application_messages: list[str],
    ) -> None:
        applied_ids = _load_applied_ids()
        test_vacancies: list[VacancyItem] = []

        item: VacancyItem
        for item in self._get_vacancies(api, resume_id):
            vacancy_id = str(item["id"])

            # Skip already applied
            if vacancy_id in applied_ids:
                logger.debug("Skipping already applied vacancy %s", vacancy_id)
                continue

            # Skip test vacancies — log them so they don't repeat
            if item["has_test"]:
                test_vacancies.append(item)
                applied_ids.add(vacancy_id)
                _log_jsonl(TEST_VACANCIES_LOG, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "vacancy_id": vacancy_id,
                    "name": item["name"],
                    "employer": item.get("employer", {}).get("name", ""),
                    "url": item["alternate_url"],
                })
                _log_jsonl(APPLICATIONS_LOG, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "vacancy_id": vacancy_id,
                    "name": item["name"],
                    "employer": item.get("employer", {}).get("name", ""),
                    "url": item["alternate_url"],
                    "status": "has_test",
                })
                print(
                    "⚠️  Тест:",
                    item["alternate_url"],
                    "(",
                    truncate_string(item["name"]),
                    ")",
                )
                continue

            # Random delay between applications (anti-detection)
            delay = random.uniform(5.0, 10.0)
            logger.debug("Waiting %.2fs before next application", delay)
            time.sleep(delay)

            # Откликаемся на вакансию
            params = {
                "resume_id": resume_id,
                "vacancy_id": item["id"],
                "message": (
                    random.choice(application_messages) % item
                    if force_message or item["response_letter_required"]
                    else ""
                ),
            }
            try:
                res = api.post("/negotiations", params)
                assert res == {}
                print(
                    "📨 Отправили отклик",
                    item["alternate_url"],
                    "(",
                    truncate_string(item["name"]),
                    ")",
                )
                applied_ids.add(vacancy_id)
                _log_jsonl(APPLICATIONS_LOG, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "vacancy_id": vacancy_id,
                    "name": item["name"],
                    "employer": item.get("employer", {}).get("name", ""),
                    "url": item["alternate_url"],
                    "status": "applied",
                })
            except ApiError as ex:
                print_err("❗ Ошибка:", ex)
                _log_jsonl(APPLICATIONS_LOG, {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "vacancy_id": vacancy_id,
                    "name": item["name"],
                    "url": item["alternate_url"],
                    "status": f"error: {ex}",
                })
                if isinstance(ex, BadRequest) and ex.limit_exceeded:
                    break

        # Print test vacancies summary
        if test_vacancies:
            print("\n" + "=" * 70)
            print(f"⚠️  Вакансии с тестами ({len(test_vacancies)} шт.) — требуется ручной отклик:")
            print("=" * 70)
            for v in test_vacancies:
                employer = v.get("employer", {}).get("name", "—")
                print(f"  • {truncate_string(v['name'], 50)}  |  {employer}")
                print(f"    {v['alternate_url']}")
            print("=" * 70)

        print("📝 Отклики на вакансии разосланы!")
