import argparse
import json
import logging
import os
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from ..main import BaseOperation
from ..main import Namespace as BaseNamespace
from ..utils import print_err

logger = logging.getLogger(__package__)

LOG_DIR = Path(os.environ.get("HH_LOG_DIR", "/app/logs"))
ANALYSIS_LOG = LOG_DIR / "vacancy_analysis.jsonl"


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HH Vacancy Analysis Report</title>
<style>
  :root {{
    --good: #16a34a; --good-bg: #f0fdf4; --good-border: #bbf7d0;
    --neutral: #ca8a04; --neutral-bg: #fefce8; --neutral-border: #fef08a;
    --skip: #dc2626; --skip-bg: #fef2f2; --skip-border: #fecaca;
    --bg: #f8fafc; --card-bg: #fff; --text: #1e293b; --muted: #64748b;
    --border: #e2e8f0; --link: #2563eb;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); line-height: 1.5; padding: 20px; }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 20px; }}
  .stats {{ display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }}
  .stat {{ padding: 12px 20px; border-radius: 10px; font-weight: 600; font-size: 0.95rem; }}
  .stat-good {{ background: var(--good-bg); color: var(--good); border: 1px solid var(--good-border); }}
  .stat-neutral {{ background: var(--neutral-bg); color: var(--neutral); border: 1px solid var(--neutral-border); }}
  .stat-skip {{ background: var(--skip-bg); color: var(--skip); border: 1px solid var(--skip-border); }}
  .stat-total {{ background: var(--card-bg); color: var(--text); border: 1px solid var(--border); }}
  .section-title {{ font-size: 1.15rem; font-weight: 700; margin: 28px 0 12px; padding-left: 4px; }}
  .card {{ background: var(--card-bg); border-radius: 12px; padding: 16px 20px; margin-bottom: 12px;
           border-left: 4px solid var(--border); box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .card-good {{ border-left-color: var(--good); }}
  .card-neutral {{ border-left-color: var(--neutral); }}
  .card-skip {{ border-left-color: var(--skip); }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }}
  .card-title {{ font-weight: 700; font-size: 1.05rem; }}
  .card-title a {{ color: var(--text); text-decoration: none; }}
  .card-title a:hover {{ color: var(--link); text-decoration: underline; }}
  .card-employer {{ color: var(--muted); font-size: 0.9rem; }}
  .badges {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: 0.78rem; font-weight: 600; }}
  .badge-remote {{ background: #dbeafe; color: #1d4ed8; }}
  .badge-hybrid {{ background: #fef3c7; color: #92400e; }}
  .badge-office {{ background: #fee2e2; color: #991b1b; }}
  .badge-unknown {{ background: #f1f5f9; color: #475569; }}
  .badge-salary {{ background: #ecfdf5; color: #065f46; }}
  .badge-test {{ background: #fef3c7; color: #92400e; }}
  .badge-tracker {{ background: #fee2e2; color: #991b1b; }}
  .badge-verdict {{ font-size: 0.75rem; }}
  .badge-good {{ background: var(--good-bg); color: var(--good); border: 1px solid var(--good-border); }}
  .badge-neutral-v {{ background: var(--neutral-bg); color: var(--neutral); border: 1px solid var(--neutral-border); }}
  .badge-skip-v {{ background: var(--skip-bg); color: var(--skip); border: 1px solid var(--skip-border); }}
  .summary {{ margin-top: 8px; font-size: 0.92rem; color: #334155; }}
  .skills {{ margin-top: 8px; font-size: 0.85rem; }}
  .skills-label {{ font-weight: 600; color: var(--muted); }}
  .skills-list {{ color: #334155; }}
  .nice {{ color: #059669; }}
  .red-flags {{ margin-top: 6px; font-size: 0.85rem; color: var(--skip); }}
  .reason {{ margin-top: 6px; font-size: 0.85rem; color: var(--muted); font-style: italic; }}
  .salary-body {{ font-size: 0.85rem; color: #065f46; margin-top: 4px; }}
  .filters {{ margin-bottom: 20px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .filter-btn {{ padding: 6px 16px; border-radius: 20px; border: 1px solid var(--border);
                 background: var(--card-bg); cursor: pointer; font-size: 0.85rem; font-weight: 500; }}
  .filter-btn:hover {{ background: #f1f5f9; }}
  .filter-btn.active {{ background: var(--text); color: #fff; border-color: var(--text); }}
  @media (max-width: 600px) {{
    body {{ padding: 10px; }}
    .card {{ padding: 12px 14px; }}
    .card-header {{ flex-direction: column; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>HH Vacancy Analysis</h1>
  <div class="subtitle">Generated {generated_at} &bull; {total} vacancies analyzed</div>
  <div class="stats">
    <div class="stat stat-total">{total} total</div>
    <div class="stat stat-good">{good_count} recommended</div>
    <div class="stat stat-neutral">{neutral_count} neutral</div>
    <div class="stat stat-skip">{skip_count} skip</div>
  </div>
  <div class="filters">
    <button class="filter-btn active" onclick="filterCards('all')">All</button>
    <button class="filter-btn" onclick="filterCards('good')">Recommended</button>
    <button class="filter-btn" onclick="filterCards('neutral')">Neutral</button>
    <button class="filter-btn" onclick="filterCards('skip')">Skip</button>
    <button class="filter-btn" onclick="filterCards('remote')">Remote only</button>
  </div>
  {cards_html}
</div>
<script>
function filterCards(f) {{
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.card').forEach(c => {{
    if (f === 'all') {{ c.style.display = ''; return; }}
    if (f === 'remote') {{ c.style.display = c.dataset.format === 'REMOTE' ? '' : 'none'; return; }}
    c.style.display = c.dataset.verdict === f.toUpperCase() ? '' : 'none';
  }});
}}
</script>
</body>
</html>
"""


def _build_card(entry: dict) -> str:
    a = entry.get("analysis", {})
    verdict = a.get("verdict", "NEUTRAL").upper()
    work_format = a.get("work_format", "UNKNOWN").upper()
    verdict_lower = verdict.lower()
    card_class = f"card-{verdict_lower}" if verdict_lower in ("good", "neutral", "skip") else ""

    # Format badges
    fmt_class = {"REMOTE": "badge-remote", "HYBRID": "badge-hybrid",
                 "OFFICE": "badge-office"}.get(work_format, "badge-unknown")
    verdict_badge = {"GOOD": "badge-good", "NEUTRAL": "badge-neutral-v",
                     "SKIP": "badge-skip-v"}.get(verdict, "badge-neutral-v")

    badges = f'<span class="badge {fmt_class}">{work_format}</span>'
    badges += f'<span class="badge badge-verdict {verdict_badge}">{verdict}</span>'

    # Salary
    sal = entry.get("salary_structured", entry.get("salary", ""))
    if sal and sal != "не указана":
        badges += f'<span class="badge badge-salary">{sal}</span>'

    if entry.get("has_test"):
        badges += '<span class="badge badge-test">HAS TEST</span>'
    if a.get("has_time_tracker"):
        badges += '<span class="badge badge-tracker">TIME TRACKER</span>'

    # Salary from body
    salary_body_html = ""
    if a.get("salary_in_body"):
        salary_body_html = f'<div class="salary-body">Salary in description: {a["salary_in_body"]}</div>'

    # Summary
    summary_html = ""
    if a.get("summary"):
        summary_html = f'<div class="summary">{a["summary"]}</div>'

    # Skills
    skills_html = ""
    req = a.get("required_skills", [])
    if req:
        skills_html += f'<div class="skills"><span class="skills-label">Required: </span><span class="skills-list">{", ".join(req)}</span></div>'
    nice = a.get("nice_to_have", [])
    if nice:
        skills_html += f'<div class="skills"><span class="skills-label">Nice to have: </span><span class="skills-list nice">{", ".join(nice)}</span></div>'

    # Red flags
    flags_html = ""
    flags = a.get("red_flags", [])
    if flags:
        flags_html = f'<div class="red-flags">Red flags: {", ".join(flags)}</div>'

    # Reason
    reason_html = ""
    if a.get("reason"):
        reason_html = f'<div class="reason">{a["reason"]}</div>'

    url = entry.get("url", "")
    name = entry.get("name", "—")
    employer = entry.get("employer", "—")
    schedule = entry.get("schedule", "")

    return f"""
    <div class="card {card_class}" data-verdict="{verdict}" data-format="{work_format}">
      <div class="card-header">
        <div>
          <div class="card-title"><a href="{url}" target="_blank">{name}</a></div>
          <div class="card-employer">{employer}{(' &bull; ' + schedule) if schedule else ''}</div>
        </div>
      </div>
      <div class="badges">{badges}</div>
      {salary_body_html}
      {summary_html}
      {skills_html}
      {flags_html}
      {reason_html}
    </div>"""


class Namespace(BaseNamespace):
    pass


class Operation(BaseOperation):
    """Генерация HTML-отчёта по результатам анализа вакансий"""

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--output", "-o",
            default=str(LOG_DIR / "report.html"),
            help="Путь для сохранения HTML-отчёта (по умолчанию: %(default)s)",
        )
        parser.add_argument(
            "--no-open",
            default=False,
            action="store_true",
            help="Не открывать отчёт в браузере",
        )

    def run(self, args: Namespace) -> None:
        if not ANALYSIS_LOG.exists():
            print_err(f"❗ Лог анализа не найден: {ANALYSIS_LOG}")
            print_err("   Сначала запустите: hh-applicant-tool analyze-vacancies")
            return 1

        # Load entries, deduplicate by vacancy_id (keep latest)
        seen = {}
        with ANALYSIS_LOG.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    vid = entry.get("vacancy_id", "")
                    seen[vid] = entry
                except json.JSONDecodeError:
                    continue

        entries = list(seen.values())
        if not entries:
            print_err("❗ Нет данных для отчёта")
            return 1

        # Sort: GOOD first, then NEUTRAL, then SKIP
        order = {"GOOD": 0, "NEUTRAL": 1, "SKIP": 2}
        entries.sort(key=lambda e: order.get(e.get("analysis", {}).get("verdict", "NEUTRAL").upper(), 1))

        good_count = sum(1 for e in entries if e.get("analysis", {}).get("verdict", "").upper() == "GOOD")
        neutral_count = sum(1 for e in entries if e.get("analysis", {}).get("verdict", "").upper() == "NEUTRAL")
        skip_count = sum(1 for e in entries if e.get("analysis", {}).get("verdict", "").upper() == "SKIP")

        cards_html = "\n".join(_build_card(e) for e in entries)

        html = HTML_TEMPLATE.format(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            total=len(entries),
            good_count=good_count,
            neutral_count=neutral_count,
            skip_count=skip_count,
            cards_html=cards_html,
        )

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

        print(f"📄 Отчёт сохранён: {output_path}")
        print(f"   {len(entries)} вакансий: ✅ {good_count} | ➡️ {neutral_count} | 🚫 {skip_count}")

        if not args.no_open:
            webbrowser.open(f"file://{output_path.resolve()}")
