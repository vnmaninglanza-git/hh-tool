# HH Tool

Web UI for automating job applications on [hh.ru](https://hh.ru).

## Features

- **Auth** — Phone+SMS or OAuth browser login
- **Analyze Vacancies** — AI analysis via local Ollama (remote/office, salary, red flags)
- **Auto-Apply** — Apply to matching vacancies with duplicate prevention
- **Test Detection** — Vacancies requiring tests are logged separately with links
- **Reports** — HTML report with filters (recommended / neutral / skip)
- **Live Output** — Watch operations run in real time, cancel anytime
- **Logs** — API calls, applications, activity feed
- **Model Picker** — Choose Ollama model by RAM: gemma3:1b (4GB), gemma3:4b (8GB), gemma3:12b (16GB)

## Requirements

- **Python 3.10+** or **Docker**
- **Ollama** — for AI vacancy analysis ([install](https://ollama.ai))

## Quick Start

### Docker (just works)

```bash
git clone https://github.com/vnmaninglanza-git/hh-tool.git
cd hh-tool
docker compose up --build -d
```

### Native Python

```bash
git clone https://github.com/vnmaninglanza-git/hh-tool.git
cd hh-tool
bash setup.sh
```

Open **http://localhost:5050**

## Setup Ollama

Ollama runs locally on your machine (not inside Docker).

```bash
# Install from https://ollama.ai, then:
ollama serve

# Pull a model (pick one):
ollama pull gemma3:1b     # 4 GB RAM — fast, lower quality
ollama pull gemma3:4b     # 8 GB RAM — good balance
ollama pull gemma3:12b    # 16 GB RAM — best quality
```

You can also pull models from the Settings page in the web UI.

## Authentication

Go to http://localhost:5050/auth

1. Open **hh.ru** in your browser and login normally
2. Click **"Get Auth Code"** on the auth page
3. You'll see an error page — that's normal
4. Open **DevTools** (F12) → **Console** tab
5. Find the line: `Failed to launch 'hhandroid://oauthresponse?code=...'`
6. Copy that URL, paste it in the field, click **Save Token**

## Usage

| Page | What it does |
|------|-------------|
| Dashboard | Auth status, stats, quick actions |
| Settings | Model picker, AI prompt editor, clear analysis history |
| Analyze | Fetches similar vacancies and analyzes with AI |
| Apply | Auto-applies to matching vacancies (skips tests & duplicates) |
| Report | Generates filterable HTML report |
| Logs | API calls, applications, activity feed (auto-refresh) |

## Data

All data stays inside the project folder (portable):

```
config.json               # Tokens and settings (gitignored)
logs/
  api_calls.jsonl          # Every API request
  applications.jsonl       # Applied vacancies (prevents duplicates)
  vacancy_analysis.jsonl   # AI analysis results
  test_vacancies.jsonl     # Vacancies requiring manual test
  report.html              # Generated HTML report
```

## Commands

```bash
# Native
bash setup.sh              # Start
Ctrl+C                     # Stop

# Docker
bash setup.sh docker       # Start
docker compose down        # Stop
docker compose logs -f     # View logs
```
