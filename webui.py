#!/usr/bin/env python3
"""
Web UI for hh-applicant-tool.
Run: python webui.py
Open: http://localhost:5050
"""

import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests as http_requests
from flask import Flask, Response, redirect, render_template_string, request, stream_with_context

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = Path(os.environ.get("HH_CONFIG", str(BASE_DIR / "config.json")))
LOG_DIR = Path(os.environ.get("HH_LOG_DIR", str(BASE_DIR / "logs")))
ANALYSIS_LOG = LOG_DIR / "vacancy_analysis.jsonl"
APPLICATIONS_LOG = LOG_DIR / "applications.jsonl"
REPORT_HTML = LOG_DIR / "report.html"

ANDROID_CLIENT_ID = "HIOMIAS39CA9DICTA7JIO64LQKQJF5AGIK74G9ITJKLNEDAOH5FHS5G1JI7FOEGD"
ANDROID_CLIENT_SECRET = "V9M870DE342BGHFRUJ5FTCGCUA1482AN0DI8C5TFI9ULMA89H10N60NOP8I4JMVS"

HH_TOOL = Path(os.environ.get("HH_TOOL_DIR", str(BASE_DIR / "hh-applicant-tool-main")))

ANALYSIS_PROMPT_DEFAULT = """\
You are a job vacancy analyst. Analyze this vacancy and respond STRICTLY as valid JSON (no markdown, no ```).

CRITICAL RULES:
- "work_format": determine if REMOTE, OFFICE, HYBRID, or UNKNOWN.
- "salary_in_body": extract any salary mentioned in the description. Return as string or null.
- "required_skills": list of MANDATORY requirements.
- "nice_to_have": list of PREFERRED/optional requirements.
- "has_time_tracker": true if they mention time tracking, screenshot monitoring, etc.
- "verdict": GOOD (remote-friendly, fair), NEUTRAL (hybrid or unclear), SKIP (office-only, tracker, red flags)

Respond with JSON:
{{"summary":"...","work_format":"...","salary_in_body":"...","required_skills":[...],"nice_to_have":[...],"has_time_tracker":false,"red_flags":[...],"verdict":"...","reason":"..."}}

VACANCY:
Title: {name}
Company: {employer}
Salary: {salary}
Schedule: {schedule}
Description:
{description}
"""

app = Flask(__name__)
app.secret_key = os.urandom(24)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("webui")

# ---------------------------------------------------------------------------
# Running operations state
# ---------------------------------------------------------------------------
running_ops = {}  # op_name -> {"thread", "output", "status", "started_at"}

def _run_op_thread(op_key, cmd_name, display_name):
    """Run an operation in background thread, collecting output line by line."""
    running_ops[op_key]["status"] = "running"
    running_ops[op_key]["output"] = ""
    cfg = load_config()
    env = os.environ.copy()
    env["HH_LOG_DIR"] = str(LOG_DIR)
    env["OLLAMA_MODEL"] = cfg.get("ollama_model", os.environ.get("OLLAMA_MODEL", "gemma3:12b"))
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "hh_applicant_tool", "-c", str(CONFIG_PATH), cmd_name],
            cwd=str(HH_TOOL), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1)
        running_ops[op_key]["proc"] = proc
        for line in proc.stdout:
            running_ops[op_key]["output"] += line
        proc.wait()
        running_ops[op_key]["status"] = "done" if proc.returncode == 0 else "error"
        running_ops[op_key]["exit_code"] = proc.returncode
    except Exception as e:
        running_ops[op_key]["output"] += f"\nError: {e}"
        running_ops[op_key]["status"] = "error"
    finally:
        running_ops[op_key].pop("proc", None)

# ---------------------------------------------------------------------------
# Auth session state
# ---------------------------------------------------------------------------
auth_session = {
    "http": None,
    "step": "idle",
    "phone": "",
    "captcha_key": None,
    "captcha_state": None,
    "otp_state": None,
    "message": "",
    "msg_type": "info",
    "captcha_img_b64": None,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}

def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

def api_get(endpoint: str, token: str) -> dict | None:
    try:
        r = http_requests.get(
            f"https://api.hh.ru{endpoint}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": "hh-webui/1.0"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def render_page(title, active, body_html):
    return render_template_string(LAYOUT, title=title, active=active, body=body_html)

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
LAYOUT = """\
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
  :root {
    --bg:#0f172a;--card:#1e293b;--card2:#334155;
    --text:#e2e8f0;--muted:#94a3b8;--accent:#38bdf8;
    --green:#22c55e;--yellow:#eab308;--red:#ef4444;
    --border:#475569;--input-bg:#0f172a;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
  a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
  .wrap{max-width:960px;margin:0 auto;padding:20px}
  nav{background:var(--card);border-bottom:1px solid var(--border);padding:12px 0;margin-bottom:24px}
  nav .wrap{display:flex;gap:20px;align-items:center;padding-top:0;padding-bottom:0}
  nav .logo{font-weight:700;font-size:1.1rem;color:var(--accent)}
  nav a{color:var(--muted);font-size:.9rem}nav a:hover,nav a.active{color:var(--text);text-decoration:none}
  h1{font-size:1.4rem;margin-bottom:16px}h2{font-size:1.1rem;margin-bottom:12px;color:var(--accent)}
  .card{background:var(--card);border-radius:12px;padding:20px;margin-bottom:16px;border:1px solid var(--border)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .stat{text-align:center}.stat-num{font-size:2rem;font-weight:700}.stat-label{font-size:.85rem;color:var(--muted)}
  .green{color:var(--green)}.yellow{color:var(--yellow)}.red{color:var(--red)}.muted{color:var(--muted)}
  input[type=text],input[type=password],textarea{width:100%;padding:10px 14px;border-radius:8px;border:1px solid var(--border);background:var(--input-bg);color:var(--text);font-size:.95rem;margin-bottom:12px}
  textarea{min-height:120px;font-family:monospace;font-size:.85rem}
  button,.btn{display:inline-block;padding:10px 24px;border-radius:8px;border:none;background:var(--accent);color:#0f172a;font-weight:600;font-size:.95rem;cursor:pointer;text-decoration:none}
  button:hover,.btn:hover{opacity:.9}
  .btn-outline{background:transparent;border:1px solid var(--accent);color:var(--accent)}
  .btn-red{background:var(--red);color:#fff}.btn-green{background:var(--green);color:#0f172a}
  .msg{padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:.9rem}
  .msg-ok{background:#052e16;border:1px solid #166534;color:#86efac}
  .msg-err{background:#450a0a;border:1px solid #991b1b;color:#fca5a5}
  .msg-info{background:#172554;border:1px solid #1e40af;color:#93c5fd}
  .captcha-img{border-radius:8px;margin:12px 0;max-width:100%;background:#fff;padding:8px}
  table{width:100%;border-collapse:collapse;font-size:.9rem}
  th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
  th{color:var(--muted);font-weight:600}
  pre{background:var(--card2);padding:12px;border-radius:8px;overflow-x:auto;font-size:.82rem;margin:8px 0;white-space:pre-wrap;word-break:break-all}
  .tag{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.78rem;font-weight:600}
  .tag-green{background:#052e16;color:#86efac}.tag-yellow{background:#422006;color:#fde047}
  .tag-red{background:#450a0a;color:#fca5a5}.tag-blue{background:#172554;color:#93c5fd}
  @keyframes spin{to{transform:rotate(360deg)}}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .spinner{display:inline-block;width:18px;height:18px;border:2px solid var(--accent);border-top-color:transparent;border-radius:50%;animation:spin .8s linear infinite;vertical-align:middle;margin-right:8px}
  .pulse{animation:pulse 1.5s ease-in-out infinite}
  .live-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 1s ease-in-out infinite;margin-right:6px}
  .log-box{background:#0a0f1a;border:1px solid var(--border);border-radius:8px;padding:12px;font-family:monospace;font-size:.82rem;max-height:500px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;line-height:1.6}
  .log-line{padding:1px 0;border-bottom:1px solid #1a2035}
  .log-ts{color:#4b5563;margin-right:8px}
  .running-bar{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:12px 18px;margin-bottom:16px;display:flex;align-items:center;gap:12px}
  .running-bar .name{font-weight:600;font-size:.95rem}
  .running-bar .elapsed{color:var(--muted);font-size:.82rem}
  @media(max-width:640px){.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<nav><div class="wrap">
  <span class="logo">HH Tool</span>
  <a href="/" {{'class=active' if active=='dash' else ''}}>Dashboard</a>
  <a href="/auth" {{'class=active' if active=='auth' else ''}}>Auth</a>
  <a href="/settings" {{'class=active' if active=='settings' else ''}}>Settings</a>
  <a href="/report" {{'class=active' if active=='report' else ''}}>Report</a>
  <a href="/logs" {{'class=active' if active=='logs' else ''}}>Logs</a>
</div></nav>
<div class="wrap">{{ body | safe }}</div>
</body></html>
"""

# ---------------------------------------------------------------------------
# Routes — Dashboard
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    cfg = load_config()
    token = cfg.get("token", {}).get("access_token", "")
    user = api_get("/me", token) if token else None

    stats = {"good": 0, "neutral": 0, "skip": 0, "applied": 0}
    recent = []

    if ANALYSIS_LOG.exists():
        seen = {}
        for line in ANALYSIS_LOG.read_text().splitlines():
            if not line.strip(): continue
            try:
                e = json.loads(line)
                seen[e.get("vacancy_id")] = e
            except json.JSONDecodeError:
                continue
        for e in seen.values():
            v = e.get("analysis", {}).get("verdict", "NEUTRAL").upper()
            stats["good" if v == "GOOD" else "skip" if v == "SKIP" else "neutral"] += 1
        for e in list(seen.values())[-10:]:
            a = e.get("analysis", {})
            recent.append({"name": e.get("name", ""), "employer": e.get("employer", ""),
                           "url": e.get("url", ""), "verdict": a.get("verdict", "NEUTRAL").upper(),
                           "work_format": a.get("work_format", "?").upper()})
        recent.reverse()

    if APPLICATIONS_LOG.exists():
        stats["applied"] = sum(1 for l in APPLICATIONS_LOG.read_text().splitlines() if l.strip())

    # Running ops banner
    running_html = ""
    for key, op in running_ops.items():
        if op["status"] == "running":
            elapsed = int(time.time() - op["started_at"])
            running_html += f'''<div class="running-bar">
                <span class="spinner"></span>
                <span class="name">{op["display_name"]}</span>
                <span class="elapsed">{elapsed}s elapsed</span>
                <a href="/run/{key}/live" class="btn" style="padding:6px 14px;font-size:.82rem;margin-left:auto">View Live</a>
            </div>'''

    # Build HTML
    if user:
        auth_html = f'<p class="green" style="font-weight:700">Authenticated</p>'
        auth_html += f'<p style="margin-top:8px">{user.get("first_name","")} {user.get("last_name","")}</p>'
        auth_html += f'<p class="muted">{user.get("email","")}</p>'
    elif token:
        auth_html = '<p class="yellow" style="font-weight:700">Token exists (may be expired)</p>'
        auth_html += '<a href="/auth" class="btn" style="margin-top:12px;display:inline-block">Re-login</a>'
    else:
        auth_html = '<p class="red" style="font-weight:700">Not authenticated</p>'
        auth_html += '<a href="/auth" class="btn" style="margin-top:12px;display:inline-block">Login</a>'

    recent_html = ""
    if recent:
        rows = ""
        for e in recent:
            vc = "green" if e["verdict"]=="GOOD" else "yellow" if e["verdict"]=="NEUTRAL" else "red"
            rows += f'<tr><td><a href="{e["url"]}" target="_blank">{e["name"][:50]}</a></td>'
            rows += f'<td>{e["employer"]}</td>'
            rows += f'<td><span class="tag tag-{vc}">{e["verdict"]}</span></td>'
            rows += f'<td><span class="tag tag-blue">{e["work_format"]}</span></td></tr>'
        recent_html = f'''<div class="card"><h2>Recent Analyses</h2>
            <table><tr><th>Vacancy</th><th>Employer</th><th>Verdict</th><th>Format</th></tr>{rows}</table></div>'''

    body = f'''
    <h1>Dashboard</h1>
    {running_html}
    <div class="grid2">
      <div class="card"><h2>Auth Status</h2>{auth_html}</div>
      <div class="card"><h2>Statistics</h2>
        <div class="grid2">
          <div class="stat"><div class="stat-num green">{stats["good"]}</div><div class="stat-label">Recommended</div></div>
          <div class="stat"><div class="stat-num yellow">{stats["neutral"]}</div><div class="stat-label">Neutral</div></div>
          <div class="stat"><div class="stat-num red">{stats["skip"]}</div><div class="stat-label">Skip</div></div>
          <div class="stat"><div class="stat-num">{stats["applied"]}</div><div class="stat-label">Applied</div></div>
        </div>
      </div>
    </div>
    <div class="card"><h2>Quick Actions</h2>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <a href="/run/analyze" class="btn btn-outline">Analyze Vacancies</a>
        <a href="/run/apply" class="btn btn-outline">Apply Similar</a>
        <a href="/run/report" class="btn btn-outline">Generate Report</a>
        <a href="/report" class="btn btn-outline">View Report</a>
      </div>
    </div>
    {recent_html}
    '''
    return render_page("Dashboard", "dash", body)

# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------
@app.route("/auth")
def auth_page():
    s = auth_session
    msg_html = ""
    if s.get("message"):
        cls = "msg-ok" if s["msg_type"]=="ok" else "msg-err" if s["msg_type"]=="err" else "msg-info"
        msg_html = f'<div class="msg {cls}">{s["message"]}</div>'

    step = s["step"]
    phone = s.get("phone", "")

    # Token status card
    cfg = load_config()
    token = cfg.get("token", {}).get("access_token", "")
    token_card = ""
    if token:
        token_short = token[:20] + "..."
        token_card = f'''<div class="card">
          <h2>Current Token</h2>
          <p><code>{token_short}</code></p>
          <div style="display:flex;gap:10px;margin-top:12px">
            <a href="/auth/check" class="btn btn-outline">Check Auth</a>
            <a href="/auth/clear-token" class="btn btn-red" onclick="return confirm('Remove saved token?')">Clear Token</a>
          </div>
        </div>'''
    else:
        token_card = '<div class="card"><p class="red" style="font-weight:600">No token saved</p></div>'

    if step in ("idle", "error"):
        body = f'''
        <h1>Authentication</h1>
        {msg_html}
        {token_card}
        <div class="card">
          <h2>Login with Phone (hh.ru)</h2>
          <p class="muted" style="margin-bottom:16px">Enter your phone number. We'll show captcha if needed, then SMS code.</p>
          <form method="POST" action="/auth/start">
            <label class="muted">Phone number</label>
            <input type="text" name="phone" placeholder="+7XXXXXXXXXX" value="{phone}">
            <button type="submit">Send Code</button>
          </form>
        </div>
        <div class="card">
          <h2>Browser Login</h2>
          <div style="font-size:.92rem;line-height:2">
            <p><strong style="color:var(--accent)">1.</strong> Open hh.ru and login normally (any way you prefer):</p>
            <a href="https://hh.ru/account/login" class="btn btn-outline" target="_blank" style="margin:8px 0 16px;display:inline-block">Open hh.ru Login</a>
            <p><strong style="color:var(--accent)">2.</strong> After you're logged in, click this link:</p>
            <a href="/auth/oauth-start" class="btn" target="_blank" style="margin:8px 0 16px;display:inline-block">Get Auth Code</a>
            <p><strong style="color:var(--accent)">3.</strong> You'll see an error page — <span class="green" style="font-weight:700">that's OK!</span></p>
            <p>Open <strong>DevTools</strong> (F12 or Cmd+Option+I) → <strong>Console</strong> tab.</p>
            <p>You'll see a line like:</p>
            <div style="background:var(--card2);border-radius:8px;padding:12px;margin:8px 0;font-family:monospace;font-size:.82rem;word-break:break-all;color:var(--red)">
              Failed to launch 'hhandroid://oauthresponse?code=<span class="green" style="font-weight:700">ABCDEF123456...</span>' because the scheme does not have a registered handler.
            </div>
            <p>Copy that <span class="green" style="font-weight:700">whole URL</span> from the console (or from the address bar).</p>
          </div>
          <form method="POST" action="/auth/oauth-code" style="margin-top:12px">
            <label class="muted">Paste the URL or just the code:</label>
            <input type="text" name="redirect_url" placeholder="hhandroid://oauthresponse?code=... or just the code">
            <button type="submit" class="btn-green">Save Token</button>
          </form>
        </div>
        <div class="card">
          <h2>Manual Token Entry</h2>
          <form method="POST" action="/auth/manual">
            <label class="muted">Access Token</label>
            <input type="text" name="access_token" placeholder="USER...">
            <label class="muted">Refresh Token</label>
            <input type="text" name="refresh_token" placeholder="USER...">
            <button type="submit" class="btn-outline">Save Token</button>
          </form>
        </div>'''

    elif step == "captcha":
        img = s.get("captcha_img_b64", "")
        img_tag = f'<img src="data:image/png;base64,{img}" class="captcha-img" alt="captcha">' if img else ""
        body = f'''
        <h1>Authentication</h1>
        {msg_html}
        <div class="card">
          <h2>Solve Captcha</h2>
          <p class="muted">Phone: {phone}</p>
          {img_tag}
          <form method="POST" action="/auth/captcha">
            <label class="muted">Enter text from image</label>
            <input type="text" name="captcha_text" placeholder="Type captcha text..." autofocus>
            <div style="display:flex;gap:10px">
              <button type="submit">Submit</button>
              <a href="/auth/reset" class="btn btn-outline">Start Over</a>
            </div>
          </form>
        </div>'''

    elif step == "otp":
        body = f'''
        <h1>Authentication</h1>
        {msg_html}
        <div class="card">
          <h2>Enter SMS Code</h2>
          <p class="muted">Code sent to {phone}</p>
          <form method="POST" action="/auth/otp">
            <label class="muted">4-digit code from SMS</label>
            <input type="text" name="otp_code" maxlength="4" placeholder="1234" autofocus
                   style="font-size:2rem;text-align:center;letter-spacing:12px;max-width:200px">
            <br><button type="submit">Verify</button>
          </form>
        </div>'''

    elif step == "done":
        body = f'''
        <h1>Authentication</h1>
        {msg_html}
        {token_card}
        <div class="card">
          <h2 class="green">Authenticated!</h2>
          <p><a href="/">Go to Dashboard</a></p>
        </div>'''
    else:
        body = f"<h1>Auth</h1>{msg_html}"

    return render_page("Auth", "auth", body)


@app.route("/auth/check")
def auth_check():
    cfg = load_config()
    token = cfg.get("token", {}).get("access_token", "")
    if not token:
        auth_session.update(message="No token saved", msg_type="err", step="idle")
        return redirect("/auth")
    user = api_get("/me", token)
    if user:
        name = f'{user.get("first_name", "")} {user.get("last_name", "")}'.strip()
        email = user.get("email", "")
        auth_session.update(message=f"Token valid! {name} ({email})", msg_type="ok", step="idle")
    else:
        auth_session.update(message="Token expired or invalid", msg_type="err", step="idle")
    return redirect("/auth")


@app.route("/auth/clear-token")
def auth_clear_token():
    cfg = load_config()
    cfg.pop("token", None)
    save_config(cfg)
    auth_session.update(message="Token removed", msg_type="ok", step="idle")
    return redirect("/auth")


@app.route("/auth/reset")
def auth_reset():
    auth_session.update(step="idle", message="", msg_type="info", phone="",
                        captcha_img_b64=None, captcha_key=None, captcha_state=None, http=None)
    return redirect("/auth")


@app.route("/auth/start", methods=["GET", "POST"])
def auth_start():
    phone = (request.form.get("phone") or request.args.get("phone", "")).strip().replace(" ", "").replace("-", "")
    if not phone:
        auth_session.update(message="Phone number required", msg_type="err", step="idle")
        return redirect("/auth")

    if not phone.startswith("+"):
        if not phone.startswith("7"):
            phone = "7" + phone
        phone = "+" + phone
    auth_session["phone"] = phone

    s = http_requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "x-requested-with": "XMLHttpRequest",
        "Referer": "https://hh.ru/account/login",
    })

    try:
        s.get("https://hh.ru/account/login", timeout=15)
        xsrf = s.cookies.get("_xsrf", "")
        if xsrf:
            s.headers["x-xsrftoken"] = xsrf
    except Exception as e:
        auth_session.update(message=f"Failed to connect: {e}", msg_type="err", step="error")
        return redirect("/auth")

    auth_session["http"] = s

    try:
        r = s.post("https://hh.ru/account/otp_generate", data={
            "login": phone, "otpType": "phone", "operationType": "applicant_otp_auth",
            "isSignupPage": "false", "captchaText": "",
        }, timeout=15)
        data = r.json()
    except Exception as e:
        auth_session.update(message=f"otp_generate failed: {e}", msg_type="err", step="error")
        return redirect("/auth")

    hc = data.get("hhcaptcha", {})
    if hc.get("isBot"):
        auth_session["captcha_state"] = hc.get("captchaState", "")
        try:
            r2 = s.post("https://hh.ru/captcha?lang=RU", timeout=10)
            captcha_key = r2.json().get("key", "")
            auth_session["captcha_key"] = captcha_key
            r3 = s.get(f"https://hh.ru/captcha/picture?key={captcha_key}", timeout=10)
            auth_session["captcha_img_b64"] = base64.b64encode(r3.content).decode()
            auth_session.update(step="captcha", message="Solve the captcha", msg_type="info")
        except Exception as e:
            auth_session.update(message=f"Captcha fetch failed: {e}", msg_type="err", step="error")
    elif data.get("success") and data.get("key") == "CODE_SEND_OK":
        auth_session.update(step="otp", message="SMS code sent!", msg_type="ok",
                            otp_state=data.get("state", ""))
    else:
        auth_session.update(message=f"Unexpected: {json.dumps(data, ensure_ascii=False)[:300]}",
                            msg_type="err", step="error")
    return redirect("/auth")


@app.route("/auth/captcha", methods=["POST"])
def auth_captcha():
    captcha_text = request.form.get("captcha_text", "").strip()
    if not captcha_text:
        auth_session.update(message="Captcha text required", msg_type="err")
        return redirect("/auth")

    s = auth_session.get("http")
    if not s:
        auth_session.update(message="Session expired, start over", msg_type="err", step="idle")
        return redirect("/auth")

    try:
        r = s.post("https://hh.ru/account/otp_generate", data={
            "login": auth_session["phone"], "otpType": "phone",
            "operationType": "applicant_otp_auth", "isSignupPage": "false",
            "captchaText": captcha_text,
            "captchaKey": auth_session.get("captcha_key", ""),
            "captchaState": auth_session.get("captcha_state", ""),
        }, timeout=15)
        data = r.json()
    except Exception as e:
        auth_session.update(message=f"Request failed: {e}", msg_type="err", step="error")
        return redirect("/auth")

    if data.get("success") and data.get("key") == "CODE_SEND_OK":
        auth_session.update(step="otp", message="SMS code sent!", msg_type="ok",
                            otp_state=data.get("state", ""))
    else:
        # Wrong captcha or still blocked — refresh captcha
        hc = data.get("hhcaptcha", {})
        auth_session["captcha_state"] = hc.get("captchaState", auth_session.get("captcha_state"))
        try:
            r2 = s.post("https://hh.ru/captcha?lang=RU", timeout=10)
            captcha_key = r2.json().get("key", "")
            auth_session["captcha_key"] = captcha_key
            r3 = s.get(f"https://hh.ru/captcha/picture?key={captcha_key}", timeout=10)
            auth_session["captcha_img_b64"] = base64.b64encode(r3.content).decode()
        except Exception:
            pass
        msg = "Wrong captcha, try again." if hc.get("captchaError") else "Captcha required. Try again."
        auth_session.update(message=msg, msg_type="err", step="captcha")

    return redirect("/auth")


@app.route("/auth/otp", methods=["POST"])
def auth_otp():
    code = request.form.get("otp_code", "").strip()
    if not code:
        auth_session.update(message="Code required", msg_type="err")
        return redirect("/auth")

    s = auth_session.get("http")
    if not s:
        auth_session.update(message="Session expired, start over", msg_type="err", step="idle")
        return redirect("/auth")

    try:
        r = s.post("https://hh.ru/account/login/by_code", data={
            "username": auth_session["phone"], "code": code,
            "operationType": "applicant_otp_auth",
            "backurl": "https://hh.ru/", "isApplicantSignup": "false", "remember": "true",
        }, timeout=15)
        data = r.json()
    except Exception as e:
        auth_session.update(message=f"OTP verify failed: {e}", msg_type="err")
        return redirect("/auth")

    if data.get("success"):
        # Login OK — try OAuth flow
        try:
            oauth_url = (f"https://hh.ru/oauth/authorize?response_type=code"
                         f"&client_id={ANDROID_CLIENT_ID}&redirect_uri=hhandroid://oauthresponse")
            r2 = s.get(oauth_url, allow_redirects=False, timeout=10)

            if r2.status_code in (301, 302):
                location = r2.headers.get("Location", "")
                if "code=" in location:
                    parsed = urlsplit(location)
                    oauth_code = parse_qs(parsed.query).get("code", [None])[0]
                    if oauth_code:
                        r3 = http_requests.post("https://hh.ru/oauth/token", data={
                            "grant_type": "authorization_code",
                            "client_id": ANDROID_CLIENT_ID,
                            "client_secret": ANDROID_CLIENT_SECRET,
                            "code": oauth_code,
                            "redirect_uri": "hhandroid://oauthresponse",
                        }, timeout=10)
                        if r3.status_code == 200:
                            token = r3.json()
                            token["created_at"] = int(time.time())
                            cfg = load_config()
                            cfg["token"] = token
                            save_config(cfg)
                            auth_session.update(step="done", message="Authentication successful! Tokens saved.", msg_type="ok")
                            return redirect("/auth")
                        auth_session.update(message=f"Token exchange failed: {r3.status_code}", msg_type="err")
                    else:
                        auth_session.update(message=f"No code in redirect", msg_type="err")
                else:
                    auth_session.update(message=f"Redirect without code: {location[:200]}", msg_type="err")
            else:
                auth_session.update(
                    message=f"OAuth returned {r2.status_code}. Login succeeded but OAuth needs browser. Use the OAuth Browser method.",
                    msg_type="info", step="idle")
        except Exception as e:
            auth_session.update(message=f"OAuth failed: {e}. Try browser method.", msg_type="err", step="idle")
    else:
        vkey = data.get("verification", {}).get("key", "")
        if vkey == "WRONG_CODE":
            auth_session.update(message="Wrong code. Try again.", msg_type="err")
        elif vkey == "CODE_EXPIRED":
            auth_session.update(message="Code expired. Start over.", msg_type="err", step="idle")
        else:
            auth_session.update(message=f"Error: {json.dumps(data, ensure_ascii=False)[:200]}", msg_type="err")
    return redirect("/auth")


@app.route("/auth/oauth-start")
def auth_oauth_start():
    return redirect(f"https://hh.ru/oauth/authorize?response_type=code"
                    f"&client_id={ANDROID_CLIENT_ID}"
                    f"&redirect_uri=hhandroid://oauthresponse")


@app.route("/auth/oauth-code", methods=["POST"])
def auth_oauth_code():
    raw = request.form.get("redirect_url", "").strip()
    if not raw:
        auth_session.update(message="Paste the URL from the address bar", msg_type="err", step="idle")
        return redirect("/auth")
    try:
        # Accept full URL or just the code
        if "code=" in raw:
            code = parse_qs(urlsplit(raw).query).get("code", [None])[0]
        else:
            code = raw  # user pasted just the code
        if not code:
            raise ValueError("No code found")
        r = http_requests.post("https://hh.ru/oauth/token", data={
            "grant_type": "authorization_code",
            "client_id": ANDROID_CLIENT_ID,
            "client_secret": ANDROID_CLIENT_SECRET,
            "code": code,
            "redirect_uri": "hhandroid://oauthresponse",
        }, timeout=10)
        if r.status_code == 200:
            token = r.json()
            token["created_at"] = int(time.time())
            cfg = load_config()
            cfg["token"] = token
            save_config(cfg)
            auth_session.update(step="done", message="Tokens saved!", msg_type="ok")
        else:
            auth_session.update(message=f"Exchange failed: {r.status_code} {r.text[:200]}", msg_type="err", step="idle")
    except Exception as e:
        auth_session.update(message=f"Error: {e}", msg_type="err", step="idle")
    return redirect("/auth")


@app.route("/auth/manual", methods=["POST"])
def auth_manual():
    access = request.form.get("access_token", "").strip()
    refresh = request.form.get("refresh_token", "").strip()
    if not access:
        auth_session.update(message="Access token required", msg_type="err", step="idle")
        return redirect("/auth")
    cfg = load_config()
    cfg["token"] = {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "created_at": int(time.time())}
    save_config(cfg)
    auth_session.update(step="done", message="Tokens saved!", msg_type="ok")
    return redirect("/auth")


# ---------------------------------------------------------------------------
# Routes — Settings
# ---------------------------------------------------------------------------
@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    msg_html = ""
    if request.method == "POST":
        try:
            new_cfg = json.loads(request.form.get("config", "{}"))
            save_config(new_cfg)
            msg_html = '<div class="msg msg-ok">Settings saved!</div>'
        except json.JSONDecodeError as e:
            msg_html = f'<div class="msg msg-err">Invalid JSON: {e}</div>'

    cfg = load_config()
    token = cfg.get("token", {})
    token_html = '<p class="muted">No token configured</p>'
    if token.get("access_token"):
        a = token["access_token"][:30]
        r = token.get("refresh_token", "")[:30]
        token_html = f'''<table>
            <tr><td class="muted">Access Token</td><td><code>{a}...</code></td></tr>
            <tr><td class="muted">Refresh Token</td><td><code>{r}...</code></td></tr>
            <tr><td class="muted">Type</td><td>{token.get("token_type","bearer")}</td></tr></table>'''

    config_text = json.dumps(cfg, indent=2, ensure_ascii=False)
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    ollama_model = cfg.get("ollama_model", os.environ.get("OLLAMA_MODEL", "gemma3:12b"))

    # Recommended models by RAM
    MODEL_PRESETS = [
        ("gemma3:1b",   "~1 GB",  "Fast, low quality. 4 GB RAM."),
        ("gemma3:4b",   "~3 GB",  "Good balance. 8 GB RAM."),
        ("gemma3:12b",  "~8 GB",  "Best quality. 16 GB RAM."),
    ]

    # Check Ollama status
    ollama_status = "offline"
    ollama_models = []
    try:
        r = http_requests.get(f"{ollama_url}/api/tags", timeout=3)
        if r.status_code == 200:
            ollama_status = "online"
            ollama_models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass

    model_active = any(ollama_model in m for m in ollama_models)
    status_dot = "green" if ollama_status == "online" else "red"
    model_dot = "green" if model_active else "yellow"
    models_list = ""
    for m in ollama_models:
        is_active = ollama_model in m
        models_list += f'<span class="tag {"tag-green" if is_active else "tag-blue"}" style="margin:2px">{m}</span> '

    # Build model selector rows
    preset_rows = ""
    for name, size, desc in MODEL_PRESETS:
        installed = any(name in m for m in ollama_models)
        selected = ollama_model == name
        sel_style = "background:var(--card2);border:1px solid var(--accent)" if selected else ""
        badge = '<span class="tag tag-green" style="margin-left:6px">installed</span>' if installed else ""
        active_badge = '<span class="tag tag-yellow" style="margin-left:6px">active</span>' if selected else ""
        preset_rows += f'''<tr style="cursor:pointer;{sel_style}" onclick="document.getElementById('model-select').value='{name}';this.closest('form').submit()">
          <td><strong>{name}</strong>{badge}{active_badge}</td>
          <td class="muted">{size}</td>
          <td class="muted" style="font-size:.82rem">{desc}</td>
        </tr>'''

    ollama_html = f'''
    <div class="card">
      <h2>Ollama (Local AI)</h2>
      <table>
        <tr><td class="muted">Status</td><td><span style="color:var(--{status_dot})">{"Online" if ollama_status=="online" else "Offline"}</span></td></tr>
        <tr><td class="muted">URL</td><td><code>{ollama_url}</code></td></tr>
        <tr><td class="muted">Active Model</td><td><code>{ollama_model}</code> <span style="color:var(--{model_dot})">{"(installed)" if model_active else "(not installed)"}</span></td></tr>
        <tr><td class="muted">Installed</td><td>{models_list if models_list else "<span class=muted>none</span>"}</td></tr>
      </table>
    </div>
    <div class="card">
      <h2>Choose Model</h2>
      <p class="muted" style="margin-bottom:12px">Click a row to select. Smaller models need less RAM but give lower quality analysis.</p>
      <form method="POST" action="/settings/select-model">
        <input type="hidden" name="model" id="model-select" value="{ollama_model}">
        <table>{preset_rows}</table>
      </form>
      <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <form method="POST" action="/settings/ollama-pull" style="display:flex;gap:8px;align-items:center;margin:0">
          <input type="text" name="model" value="{ollama_model}" style="margin:0;width:250px">
          <button type="submit">Pull Model</button>
        </form>
        <span class="muted" style="font-size:.82rem">Downloads the model if not installed</span>
      </div>
      {f'<p class="muted" style="margin-top:12px;font-size:.82rem">Ollama is offline. Start with: <code>ollama serve</code></p>' if ollama_status=="offline" else ""}
    </div>'''

    # Prompt editor
    current_prompt = cfg.get("analysis_prompt", "")
    prompt_escaped = current_prompt.replace("<", "&lt;").replace(">", "&gt;")
    default_preview = ANALYSIS_PROMPT_DEFAULT[:300].replace("<", "&lt;").replace(">", "&gt;")

    # Analysis data count
    analysis_count = 0
    if ANALYSIS_LOG.exists():
        analysis_count = sum(1 for l in ANALYSIS_LOG.read_text().splitlines() if l.strip())

    body = f'''
    <h1>Settings</h1>{msg_html}
    <div class="card"><h2>Token Info</h2>{token_html}</div>
    {ollama_html}
    <div class="card">
      <h2>AI Analysis Prompt</h2>
      <p class="muted" style="margin-bottom:8px">Customize the prompt sent to Ollama. Leave empty to use default.</p>
      <form method="POST" action="/settings/prompt">
        <textarea name="prompt" style="min-height:200px" placeholder="Leave empty for default prompt...">{prompt_escaped}</textarea>
        <div style="display:flex;gap:10px">
          <button type="submit">Save Prompt</button>
          <a href="/settings/prompt-reset" class="btn btn-outline">Reset to Default</a>
        </div>
      </form>
      <details style="margin-top:12px"><summary class="muted" style="cursor:pointer;font-size:.85rem">Default prompt (preview)</summary>
        <pre style="font-size:.75rem;margin-top:8px">{default_preview}...</pre>
      </details>
    </div>
    <div class="card">
      <h2>Analysis Data</h2>
      <table><tr><td class="muted">Analyzed vacancies</td><td>{analysis_count}</td></tr></table>
      <p class="muted" style="margin-top:8px;font-size:.82rem">Clearing lets you re-analyze all vacancies from scratch.</p>
      <form method="POST" action="/settings/clear-analysis" style="margin-top:12px"
            onsubmit="return confirm('Clear all {analysis_count} analysis entries?')">
        <button type="submit" class="btn btn-red">Clear Analysis History</button>
      </form>
    </div>
    <div class="card"><h2>Config (config.json)</h2>
      <form method="POST" action="/settings">
        <textarea name="config">{config_text}</textarea>
        <button type="submit">Save</button>
      </form>
    </div>
    <div class="card"><h2>Environment</h2><table>
      <tr><td class="muted">Config Path</td><td><code>{CONFIG_PATH}</code></td></tr>
      <tr><td class="muted">Log Dir</td><td><code>{LOG_DIR}</code></td></tr>
    </table></div>'''
    return render_page("Settings", "settings", body)


@app.route("/settings/select-model", methods=["POST"])
def select_model():
    model = request.form.get("model", "").strip()
    if model:
        cfg = load_config()
        cfg["ollama_model"] = model
        save_config(cfg)
    return redirect("/settings")


@app.route("/settings/prompt", methods=["POST"])
def save_prompt():
    prompt = request.form.get("prompt", "").strip()
    cfg = load_config()
    if prompt:
        cfg["analysis_prompt"] = prompt
    else:
        cfg.pop("analysis_prompt", None)
    save_config(cfg)
    return redirect("/settings")


@app.route("/settings/prompt-reset")
def reset_prompt():
    cfg = load_config()
    cfg.pop("analysis_prompt", None)
    save_config(cfg)
    return redirect("/settings")


@app.route("/settings/clear-analysis", methods=["POST"])
def clear_analysis():
    if ANALYSIS_LOG.exists():
        ANALYSIS_LOG.unlink()
    return redirect("/settings")


@app.route("/settings/ollama-pull", methods=["POST"])
def ollama_pull():
    model = request.form.get("model", "").strip()
    if not model:
        return redirect("/settings")
    # Start pull as a background operation
    op_key = "ollama-pull"
    running_ops[op_key] = {
        "display_name": f"Pull {model}",
        "cmd_name": "ollama-pull",
        "status": "running",
        "output": f"Pulling model: {model}...\n",
        "started_at": time.time(),
        "exit_code": None,
    }
    def _pull_thread():
        ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        try:
            r = http_requests.post(f"{ollama_url}/api/pull",
                                   json={"name": model, "stream": True},
                                   stream=True, timeout=600)
            for line in r.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        status = data.get("status", "")
                        total = data.get("total", 0)
                        completed = data.get("completed", 0)
                        if total > 0:
                            pct = int(completed / total * 100)
                            running_ops[op_key]["output"] += f"{status} {pct}%\n"
                        else:
                            running_ops[op_key]["output"] += f"{status}\n"
                    except json.JSONDecodeError:
                        running_ops[op_key]["output"] += line.decode() + "\n"
            running_ops[op_key]["status"] = "done"
            running_ops[op_key]["exit_code"] = 0
            running_ops[op_key]["output"] += f"\nModel {model} pulled successfully!\n"
        except Exception as e:
            running_ops[op_key]["output"] += f"\nError: {e}\n"
            running_ops[op_key]["status"] = "error"

    t = threading.Thread(target=_pull_thread, daemon=True)
    t.start()
    running_ops[op_key]["thread"] = t

    # Reuse the live view for ollama pull
    return redirect(f"/run/ollama-pull/live")


@app.route("/run/ollama-pull/live")
def ollama_pull_live():
    info = running_ops.get("ollama-pull", {})
    status = info.get("status", "idle")
    display_name = info.get("display_name", "Pull Model")

    if status == "running":
        status_html = f'<div class="running-bar"><span class="spinner"></span><span class="name">{display_name}</span><span class="elapsed pulse" id="elapsed">pulling...</span></div>'
    elif status == "done":
        status_html = '<div class="msg msg-ok">Model pulled successfully!</div>'
    elif status == "error":
        status_html = '<div class="msg msg-err">Pull failed.</div>'
    else:
        status_html = '<div class="msg msg-info">Not started.</div>'

    body = f'''
    <h1>{display_name}</h1>
    {status_html}
    <div class="card">
      <h2><span class="live-dot" id="live-dot" style="{"display:inline-block" if status=="running" else "display:none"}"></span>Output</h2>
      <div class="log-box" id="log-box"></div>
    </div>
    <a href="/settings" class="btn btn-outline" style="margin-top:12px">Back to Settings</a>
    <script>
    var logBox = document.getElementById('log-box');
    function poll() {{
      fetch('/api/op/ollama-pull/output')
        .then(r => r.json())
        .then(data => {{
          logBox.textContent = data.output;
          logBox.scrollTop = logBox.scrollHeight;
          if (data.status === 'running') setTimeout(poll, 1000);
          else setTimeout(() => location.reload(), 500);
        }})
        .catch(() => setTimeout(poll, 2000));
    }}
    poll();
    </script>'''
    return render_page(display_name, "settings", body)


# ---------------------------------------------------------------------------
# Routes — Report
# ---------------------------------------------------------------------------
@app.route("/report")
def report_page():
    if REPORT_HTML.exists():
        return REPORT_HTML.read_text(encoding="utf-8")
    body = '''<h1>Report</h1><div class="card">
        <p class="muted">No report generated yet.</p>
        <a href="/run/report" class="btn" style="margin-top:12px">Generate Report</a></div>'''
    return render_page("Report", "report", body)


# ---------------------------------------------------------------------------
# Routes — Logs
# ---------------------------------------------------------------------------
@app.route("/logs")
def logs_page():
    api_log_path = LOG_DIR / "api_calls.jsonl"
    api_rows = ""
    api_count = 0
    if api_log_path.exists():
        lines = api_log_path.read_text().splitlines()
        api_count = len(lines)
        for line in lines[-30:]:
            try:
                e = json.loads(line)
                url_short = e.get("url", "").split("?")[0].replace("https://api.hh.ru", "")
                api_rows += f'<tr><td class="muted">{e.get("ts","")[11:19]}</td><td>{e.get("method","")}</td>'
                api_rows += f'<td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">{url_short}</td>'
                api_rows += f'<td>{e.get("status","")}</td></tr>'
            except json.JSONDecodeError:
                continue

    app_rows = ""
    apps_count = 0
    if APPLICATIONS_LOG.exists():
        lines = APPLICATIONS_LOG.read_text().splitlines()
        apps_count = len(lines)
        for line in lines[-20:]:
            try:
                a = json.loads(line)
                app_rows += f'<tr><td class="muted">{a.get("ts","")[:10]}</td>'
                app_rows += f'<td><a href="{a.get("url","")}" target="_blank">{a.get("name","")[:40]}</a></td>'
                app_rows += f'<td>{a.get("status","")}</td></tr>'
            except json.JSONDecodeError:
                continue

    # Activity log (combined, sorted by time)
    activity = []
    if api_log_path.exists():
        for line in api_log_path.read_text().splitlines()[-50:]:
            try:
                e = json.loads(line)
                url_short = e.get("url", "").split("?")[0].replace("https://api.hh.ru", "")
                activity.append({"ts": e.get("ts", ""), "type": "api",
                                 "detail": f'{e.get("method","")} {url_short} → {e.get("status","")}'})
            except json.JSONDecodeError:
                continue
    if APPLICATIONS_LOG.exists():
        for line in APPLICATIONS_LOG.read_text().splitlines()[-50:]:
            try:
                a = json.loads(line)
                activity.append({"ts": a.get("ts", ""), "type": "apply",
                                 "detail": f'Applied: {a.get("name","")[:40]} ({a.get("status","")})'})
            except json.JSONDecodeError:
                continue
    if ANALYSIS_LOG.exists():
        for line in ANALYSIS_LOG.read_text().splitlines()[-50:]:
            try:
                e = json.loads(line)
                v = e.get("analysis", {}).get("verdict", "?")
                activity.append({"ts": e.get("ts", ""), "type": "analysis",
                                 "detail": f'Analyzed: {e.get("name","")[:40]} → {v}'})
            except json.JSONDecodeError:
                continue
    activity.sort(key=lambda x: x["ts"], reverse=True)

    type_colors = {"api": "#64748b", "apply": "#22c55e", "analysis": "#38bdf8"}
    activity_rows = ""
    for a in activity[:60]:
        c = type_colors.get(a["type"], "#94a3b8")
        ts = a["ts"][11:19] if len(a["ts"]) > 19 else a["ts"][:19]
        activity_rows += f'<div class="log-line"><span class="log-ts">{ts}</span><span style="color:{c};font-weight:600;margin-right:6px">[{a["type"]}]</span>{a["detail"]}</div>'

    # Running ops
    running_html = ""
    for key, op in running_ops.items():
        if op["status"] == "running":
            elapsed = int(time.time() - op["started_at"])
            running_html += f'''<div class="running-bar">
                <span class="spinner"></span><span class="name">{op["display_name"]}</span>
                <span class="elapsed">{elapsed}s</span>
                <a href="/run/{key}/live" class="btn" style="padding:6px 14px;font-size:.82rem;margin-left:auto">View Live</a>
            </div>'''

    body = f'''
    <h1>Logs & Activity</h1>
    {running_html}
    <div class="grid2">
      <div class="card"><h2>API Calls</h2><p class="muted">{api_count} total calls</p>
        <table><tr><th>Time</th><th>Method</th><th>URL</th><th>Status</th></tr>{api_rows}</table></div>
      <div class="card"><h2>Applications</h2><p class="muted">{apps_count} total applications</p>
        <table><tr><th>Date</th><th>Vacancy</th><th>Status</th></tr>{app_rows}</table></div>
    </div>
    <div class="card">
      <h2>Activity Feed <span class="muted" style="font-size:.8rem;font-weight:400">(last 60 events, auto-refresh)</span></h2>
      <div class="log-box" id="activity-log" style="max-height:400px">{activity_rows}</div>
    </div>
    <script>
    setInterval(function() {{
      fetch('/api/activity').then(r=>r.text()).then(html=>{{
        document.getElementById('activity-log').innerHTML = html;
      }});
    }}, 5000);
    </script>'''
    return render_page("Logs", "logs", body)


@app.route("/api/activity")
def api_activity():
    """Return recent activity HTML for auto-refresh."""
    activity = []
    api_log_path = LOG_DIR / "api_calls.jsonl"
    if api_log_path.exists():
        for line in api_log_path.read_text().splitlines()[-50:]:
            try:
                e = json.loads(line)
                url_short = e.get("url", "").split("?")[0].replace("https://api.hh.ru", "")
                activity.append({"ts": e.get("ts", ""), "type": "api",
                                 "detail": f'{e.get("method","")} {url_short} → {e.get("status","")}'})
            except json.JSONDecodeError:
                continue
    if APPLICATIONS_LOG.exists():
        for line in APPLICATIONS_LOG.read_text().splitlines()[-50:]:
            try:
                a = json.loads(line)
                activity.append({"ts": a.get("ts", ""), "type": "apply",
                                 "detail": f'Applied: {a.get("name","")[:40]} ({a.get("status","")})'})
            except json.JSONDecodeError:
                continue
    if ANALYSIS_LOG.exists():
        for line in ANALYSIS_LOG.read_text().splitlines()[-50:]:
            try:
                e = json.loads(line)
                v = e.get("analysis", {}).get("verdict", "?")
                activity.append({"ts": e.get("ts", ""), "type": "analysis",
                                 "detail": f'Analyzed: {e.get("name","")[:40]} → {v}'})
            except json.JSONDecodeError:
                continue
    activity.sort(key=lambda x: x["ts"], reverse=True)
    type_colors = {"api": "#64748b", "apply": "#22c55e", "analysis": "#38bdf8"}
    html = ""
    for a in activity[:60]:
        c = type_colors.get(a["type"], "#94a3b8")
        ts = a["ts"][11:19] if len(a["ts"]) > 19 else a["ts"][:19]
        html += f'<div class="log-line"><span class="log-ts">{ts}</span><span style="color:{c};font-weight:600;margin-right:6px">[{a["type"]}]</span>{a["detail"]}</div>'
    return html


# ---------------------------------------------------------------------------
# Routes — Run operations (async with live output)
# ---------------------------------------------------------------------------
VALID_OPS = {"analyze": ("analyze-vacancies", "Analyze Vacancies"),
             "apply": ("apply-similar", "Apply Similar"),
             "report": ("report", "Generate Report")}

@app.route("/run/<op>")
def run_operation(op):
    if op not in VALID_OPS:
        return "Unknown operation", 404

    cmd_name, display_name = VALID_OPS[op]

    # If already running, redirect to live view
    if op in running_ops and running_ops[op]["status"] == "running":
        return redirect(f"/run/{op}/live")

    # Start operation in background thread
    running_ops[op] = {
        "display_name": display_name,
        "cmd_name": cmd_name,
        "status": "running",
        "output": "",
        "started_at": time.time(),
        "exit_code": None,
    }
    t = threading.Thread(target=_run_op_thread, args=(op, cmd_name, display_name), daemon=True)
    t.start()
    running_ops[op]["thread"] = t

    return redirect(f"/run/{op}/live")


@app.route("/run/<op>/live")
def run_live(op):
    if op not in VALID_OPS:
        return "Unknown operation", 404
    display_name = VALID_OPS[op][1]
    info = running_ops.get(op, {})
    status = info.get("status", "idle")

    if status == "running":
        status_html = f'<div class="running-bar"><span class="spinner"></span><span class="name">{display_name}</span><span class="elapsed pulse" id="elapsed">running...</span></div>'
    elif status == "done":
        status_html = f'<div class="msg msg-ok">Operation completed successfully.</div>'
    elif status == "error":
        status_html = f'<div class="msg msg-err">Operation failed (exit code {info.get("exit_code","?")}).</div>'
    else:
        status_html = f'<div class="msg msg-info">Operation not started. <a href="/run/{op}">Start it</a></div>'

    body = f'''
    <h1>{display_name}</h1>
    {status_html}
    <div class="card">
      <h2><span class="live-dot" id="live-dot" style="{'display:inline-block' if status=='running' else 'display:none'}"></span>Live Output</h2>
      <div class="log-box" id="log-box"></div>
    </div>
    <div style="margin-top:12px;display:flex;gap:10px">
      <a href="/" class="btn btn-outline">Dashboard</a>
      {'<a href="/run/'+op+'/cancel" class="btn btn-red">Cancel</a>' if status == 'running' else '<a href="/run/'+op+'" class="btn">Restart</a>'}
    </div>
    <script>
    var logBox = document.getElementById('log-box');
    var liveDot = document.getElementById('live-dot');
    var elapsedEl = document.getElementById('elapsed');
    var startTime = {int(info.get("started_at", time.time()) * 1000)};
    function updateElapsed() {{
      if (!elapsedEl) return;
      var s = Math.floor((Date.now() - startTime) / 1000);
      var m = Math.floor(s / 60);
      elapsedEl.textContent = m > 0 ? m + 'm ' + (s%60) + 's' : s + 's elapsed';
    }}
    function poll() {{
      fetch('/api/op/{op}/output')
        .then(r => r.json())
        .then(data => {{
          logBox.textContent = data.output;
          logBox.scrollTop = logBox.scrollHeight;
          if (data.status === 'running') {{
            updateElapsed();
            setTimeout(poll, 1000);
          }} else {{
            if (liveDot) liveDot.style.display = 'none';
            if (elapsedEl) elapsedEl.textContent = data.status === 'done' ? 'completed' : 'failed';
            if (elapsedEl) elapsedEl.classList.remove('pulse');
            // Refresh page to show final status
            setTimeout(() => location.reload(), 500);
          }}
        }})
        .catch(() => setTimeout(poll, 2000));
    }}
    poll();
    if ('{status}' === 'running') setInterval(updateElapsed, 1000);
    </script>
    '''
    return render_page(f"{display_name}", "", body)


@app.route("/api/op/<op>/output")
def api_op_output(op):
    info = running_ops.get(op, {})
    return {"status": info.get("status", "idle"),
            "output": info.get("output", ""),
            "exit_code": info.get("exit_code")}


@app.route("/run/<op>/cancel")
def cancel_operation(op):
    info = running_ops.get(op)
    if info and info.get("status") == "running":
        proc = info.get("proc")
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            info["status"] = "error"
            info["output"] += "\n--- CANCELLED BY USER ---\n"
            info["exit_code"] = -15
    return redirect(f"/run/{op}/live")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  HH Tool Web UI: http://localhost:{port}\n")
    if os.environ.get("HH_NO_BROWSER") != "1":
        webbrowser.open(f"http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
