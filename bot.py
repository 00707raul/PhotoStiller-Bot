import asyncio
import secrets
import threading
import time
from html import escape
from pathlib import Path

from flask import Flask, Response, jsonify, request
from telethon import Button, TelegramClient, events
from telethon.errors import RPCError
from telethon.sessions import StringSession

from channel_downloader import ChannelImageDownloader
from config import (
    ADMIN_IDS,
    ALLOW_PRIVATE_LINKS_FOR_PUBLIC,
    API_HASH,
    API_ID,
    BOT_NAME,
    BOT_TOKEN,
    DATA_DIR,
    DASHBOARD_ENABLED,
    DASHBOARD_PUBLIC,
    DASHBOARD_REFRESH_SECONDS,
    DASHBOARD_SECRET,
    DOWNLOAD_ROOT,
    LOG_FILE,
    MAX_ACTIVE_JOBS,
    MAX_URLS_PER_MESSAGE,
    MIN_SECONDS_BETWEEN_JOBS,
    OWNER_ID,
    OWNER_MAX_PHOTOS_PER_JOB,
    PORT,
    PUBLIC_MAX_PHOTOS_PER_JOB,
    PUBLIC_MODE,
    SESSION_NAME,
    STRING_SESSION,
    USER_DAILY_CHANNEL_LIMIT,
    USER_DAILY_DIRECT_LIMIT,
    WEB_BASE_URL,
)
from database import DownloadDB
from logger_setup import setup_logging
from utils import (
    cleanup_paths,
    disk_report,
    download_direct_image,
    ensure_dirs,
    extract_og_image,
    extract_urls,
    format_bytes,
    folder_size_bytes,
    is_telegram_channel_link,
    is_valid_http_url,
    is_web_telegram_url,
    normalize_channel_input,
)

logger = setup_logging()
db = DownloadDB()
STARTED_AT = time.time()
last_channel_start: dict[int, float] = {}
pending_channel_links: dict[str, dict] = {}

# Python 3.14 no longer creates a default event loop automatically.
# Telethon checks the current loop during client creation, so we create one
# explicitly and run the whole bot on the same loop. This keeps Render deploys
# working even if Render selects Python 3.14.
MAIN_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(MAIN_LOOP)

# Bot client: receives commands and sends files.
bot_client = TelegramClient(SESSION_NAME, API_ID, API_HASH, loop=MAIN_LOOP)

# Reader client: reads channel history. STRING_SESSION allows private invite links.
uses_user_session = bool(STRING_SESSION)
reader_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH, loop=MAIN_LOOP) if uses_user_session else bot_client

downloader = ChannelImageDownloader(bot_client, reader_client, db, logger, uses_user_session=uses_user_session)

health_app = Flask(__name__)


@health_app.route("/")
def index():
    # Render primary URL now opens the full statistics website.
    # If the dashboard is private, this route shows a small login page instead of plain text.
    if DASHBOARD_ENABLED:
        return _render_dashboard_or_login()
    return Response(_status_page_html(), mimetype="text/html")


@health_app.route("/favicon.ico")
def favicon():
    svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop stop-color='#29b6ff'/><stop offset='1' stop-color='#0b5cff'/></linearGradient></defs><rect width='64' height='64' rx='16' fill='url(#g)'/><path d='M18 22h28a4 4 0 0 1 4 4v20a4 4 0 0 1-4 4H18a4 4 0 0 1-4-4V26a4 4 0 0 1 4-4Z' fill='white' opacity='.95'/><path d='M20 44l9-10 7 7 5-6 8 9H20Z' fill='#0b5cff'/><path d='M32 10v22m0 0l-8-8m8 8l8-8' stroke='white' stroke-width='6' stroke-linecap='round' stroke-linejoin='round'/></svg>"""
    return Response(svg, mimetype="image/svg+xml")


@health_app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": BOT_NAME,
        "reader": "user_session" if uses_user_session else "bot_session",
        "public_mode": get_public_mode(),
        "active_jobs": len(downloader.active_jobs()),
    }


@health_app.route("/api/stats")
def api_stats():
    if not _dashboard_allowed():
        return jsonify({"ok": False, "error": "dashboard locked"}), 401
    return jsonify(_dashboard_payload())


@health_app.route("/dashboard")
def dashboard():
    return _render_dashboard_or_login()


def _render_dashboard_or_login():
    if not DASHBOARD_ENABLED:
        return Response(_status_page_html("Dashboard disabled"), status=404, mimetype="text/html")
    if not _dashboard_allowed():
        return Response(_dashboard_login_html(), status=200, mimetype="text/html")
    return Response(_dashboard_html(_dashboard_payload()), mimetype="text/html")


def _dashboard_allowed() -> bool:
    if not DASHBOARD_ENABLED:
        return False
    if DASHBOARD_PUBLIC:
        return True
    supplied_key = request.args.get("key", "") or request.headers.get("X-Dashboard-Key", "")
    return bool(DASHBOARD_SECRET and secrets.compare_digest(supplied_key, DASHBOARD_SECRET))


def _dashboard_link(include_secret: bool = True) -> str:
    base = (WEB_BASE_URL or "").rstrip("/")
    url = f"{base}/dashboard" if base else "/dashboard"
    if include_secret and DASHBOARD_SECRET and not DASHBOARD_PUBLIC:
        url += f"?key={DASHBOARD_SECRET}"
    return url


def _dashboard_payload() -> dict:
    today_direct, today_channels, today_images = db.usage_totals_today()
    total_direct, total_channels, total_images = db.usage_totals_all_time()
    recent_users = db.list_users(8)
    recent_events = db.recent_events(12)
    daily_rows = db.daily_usage_last_days(14)
    active_jobs = downloader.active_jobs()
    downloads_size = folder_size_bytes(DOWNLOAD_ROOT)
    return {
        "bot": BOT_NAME,
        "status": "online",
        "uptime": uptime_text(),
        "public_mode": get_public_mode(),
        "reader": "user_session" if uses_user_session else "bot_session",
        "unique_users": db.user_count(),
        "active_users_24h": db.active_users_since(1),
        "active_users_7d": db.active_users_since(7),
        "last_user_seen": db.last_user_seen_iso(),
        "total_direct_urls": int(total_direct or 0),
        "total_channel_jobs": int(total_channels or 0),
        "total_downloaded_files": int(total_images or 0),
        "today_direct_urls": int(today_direct or 0),
        "today_channel_jobs": int(today_channels or 0),
        "today_downloaded_files": int(today_images or 0),
        "active_jobs": len(active_jobs),
        "max_active_jobs": MAX_ACTIVE_JOBS,
        "downloads_folder_size": format_bytes(downloads_size),
        "disk_report": disk_report(),
        "recent_users": [
            {
                "user_id": row[0],
                "username": row[1] or "",
                "first_name": row[2] or "",
                "banned": bool(row[3]),
                "first_seen": row[4] or "",
                "last_seen": row[5] or "",
            }
            for row in recent_users
        ],
        "recent_events": [
            {"user_id": row[0], "type": row[1], "detail": row[2], "created_at": row[3]}
            for row in recent_events
        ],
        "daily_usage": [
            {"day": row[0], "direct": int(row[1] or 0), "channels": int(row[2] or 0), "files": int(row[3] or 0)}
            for row in daily_rows
        ],
        "jobs": [
            {
                "user_id": job.user_id,
                "channel": job.channel_key or job.channel_input,
                "status": job.status,
                "phase": job.phase,
                "downloaded": job.downloaded_count,
                "processed": job.processed_count,
                "total": job.total_photos,
            }
            for job in active_jobs
        ],
    }


def _card(title: str, value: str, sub: str = "") -> str:
    return f"""
    <div class=\"card\">
      <div class=\"label\">{escape(title)}</div>
      <div class=\"value\">{escape(str(value))}</div>
      <div class=\"sub\">{escape(sub)}</div>
    </div>
    """


def _status_page_html(message: str = "Bot is running") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(BOT_NAME)} Status</title>
  <style>
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; font-family:Inter,Segoe UI,Arial,sans-serif; background:linear-gradient(135deg,#06101d,#0a2442); color:#eef6ff; }}
    .box {{ width:min(560px,92vw); background:rgba(14,27,46,.9); border:1px solid #223750; border-radius:24px; padding:28px; box-shadow:0 25px 80px rgba(0,0,0,.28); }}
    h1 {{ margin:0 0 10px; }} p {{ color:#9db3c9; line-height:1.6; }} a {{ color:#2aa7ff; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>📥 {escape(BOT_NAME)}</h1>
    <p>{escape(message)}</p>
    <p>Open <a href="/dashboard">/dashboard</a> to view statistics.</p>
  </div>
</body>
</html>"""


def _dashboard_login_html() -> str:
    public_hint = "Set DASHBOARD_PUBLIC=true in Render if you want the root URL to show stats without a password."
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(BOT_NAME)} Dashboard Login</title>
  <style>
    :root {{ --bg:#07111f; --panel:#0e1b2e; --text:#eef6ff; --muted:#8ea4bd; --blue:#2aa7ff; --border:#223750; --danger:#ff5d6c; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; font-family:Inter,Segoe UI,Arial,sans-serif; background:radial-gradient(circle at 20% 20%,#123b68 0,#07111f 42%,#050b14 100%); color:var(--text); padding:18px; }}
    .box {{ width:min(560px,100%); background:rgba(14,27,46,.92); border:1px solid var(--border); border-radius:26px; padding:28px; box-shadow:0 26px 100px rgba(0,0,0,.34); }}
    .logo {{ width:68px;height:68px;border-radius:22px; display:grid; place-items:center; font-size:34px; background:linear-gradient(135deg,#29b6ff,#0b5cff); margin-bottom:16px; }}
    h1 {{ margin:0 0 8px; font-size:28px; }}
    p {{ color:var(--muted); line-height:1.6; margin:0 0 18px; }}
    label {{ display:block; color:#c7d7e8; margin-bottom:8px; font-weight:700; }}
    input {{ width:100%; border:1px solid var(--border); background:#07111f; color:var(--text); border-radius:14px; padding:14px 15px; font-size:15px; outline:none; }}
    button {{ width:100%; margin-top:14px; border:0; border-radius:14px; padding:14px 16px; background:linear-gradient(135deg,#2aa7ff,#0b5cff); color:white; font-size:16px; font-weight:800; cursor:pointer; }}
    .hint {{ margin-top:16px; font-size:13px; color:var(--muted); }}
    .err {{ color:var(--danger); min-height:20px; margin-top:12px; font-size:14px; }}
  </style>
</head>
<body>
  <div class="box">
    <div class="logo">📊</div>
    <h1>{escape(BOT_NAME)} Dashboard</h1>
    <p>This dashboard is private. Enter your <b>DASHBOARD_SECRET</b> from Render to view live bot statistics.</p>
    <label for="key">Dashboard secret</label>
    <input id="key" type="password" placeholder="Paste DASHBOARD_SECRET here" autocomplete="off">
    <button onclick="openDashboard()">Open Dashboard</button>
    <div id="err" class="err"></div>
    <div class="hint">{escape(public_hint)}</div>
  </div>
  <script>
    const existing = localStorage.getItem('dashboardKey');
    if (existing) document.getElementById('key').value = existing;
    function openDashboard() {{
      const key = document.getElementById('key').value.trim();
      if (!key) {{ document.getElementById('err').textContent = 'Enter your DASHBOARD_SECRET first.'; return; }}
      localStorage.setItem('dashboardKey', key);
      window.location.href = '/dashboard?key=' + encodeURIComponent(key);
    }}
    document.getElementById('key').addEventListener('keydown', (e) => {{ if (e.key === 'Enter') openDashboard(); }});
  </script>
</body>
</html>"""


def _dashboard_html(payload: dict) -> str:
    # Full self-contained HTML/CSS/JS dashboard.
    # It is served at both / and /dashboard, so the Render primary URL opens the stats website.
    initial_json = escape(__import__('json').dumps(payload), quote=False)
    refresh_ms = max(3, int(DASHBOARD_REFRESH_SECONDS or 15)) * 1000
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(payload['bot'])} Dashboard</title>
  <style>
    :root {{
      --bg:#07111f; --panel:#0e1b2e; --panel2:#13233a; --text:#eef6ff; --muted:#8ea4bd;
      --blue:#2aa7ff; --blue2:#0b5cff; --green:#37d67a; --yellow:#f8c23a; --red:#ff5d6c; --border:#223750;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter,Segoe UI,Arial,sans-serif; background:linear-gradient(135deg,#06101d,#0a2442 55%,#06101d); color:var(--text); }}
    .wrap {{ max-width:1240px; margin:0 auto; padding:26px 18px 58px; }}
    .hero {{ display:flex; justify-content:space-between; gap:18px; align-items:center; margin-bottom:20px; }}
    .brand {{ display:flex; gap:14px; align-items:center; }}
    .logo {{ width:62px; height:62px; border-radius:20px; display:grid; place-items:center; background:linear-gradient(135deg,var(--blue),var(--blue2)); box-shadow:0 16px 42px rgba(42,167,255,.28); font-size:30px; }}
    h1 {{ margin:0 0 6px; font-size:31px; letter-spacing:-.6px; }}
    .muted {{ color:var(--muted); }}
    .toolbar {{ display:flex; flex-wrap:wrap; gap:10px; justify-content:flex-end; }}
    .pill,.btn {{ display:inline-flex; align-items:center; gap:8px; padding:10px 14px; border:1px solid var(--border); background:rgba(255,255,255,.05); border-radius:999px; color:#c7d7e8; text-decoration:none; font-weight:700; }}
    .btn {{ cursor:pointer; }}
    .btn:hover {{ border-color:var(--blue); color:white; }}
    .dot {{ width:10px; height:10px; background:var(--green); border-radius:999px; box-shadow:0 0 12px var(--green); }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:16px; }}
    .card {{ position:relative; overflow:hidden; background:linear-gradient(180deg,var(--panel),var(--panel2)); border:1px solid var(--border); border-radius:20px; padding:19px; box-shadow:0 14px 40px rgba(0,0,0,.24); }}
    .card::after {{ content:''; position:absolute; right:-28px; top:-30px; width:95px; height:95px; border-radius:999px; background:rgba(42,167,255,.10); }}
    .label {{ color:var(--muted); font-size:13px; margin-bottom:10px; position:relative; z-index:1; }}
    .value {{ font-size:33px; font-weight:900; letter-spacing:-1px; position:relative; z-index:1; }}
    .sub {{ color:var(--muted); margin-top:8px; font-size:13px; min-height:18px; position:relative; z-index:1; }}
    .section {{ background:rgba(14,27,46,.86); border:1px solid var(--border); border-radius:20px; padding:18px; margin-top:16px; overflow:auto; box-shadow:0 12px 38px rgba(0,0,0,.18); }}
    .section h2 {{ margin:0 0 14px; font-size:18px; }}
    .two {{ display:grid; grid-template-columns:1.15fr .85fr; gap:16px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th,td {{ text-align:left; padding:10px 8px; border-bottom:1px solid rgba(255,255,255,.08); vertical-align:top; }}
    th {{ color:#b6c9dd; font-weight:800; }}
    td {{ color:#e8f2fb; }}
    pre {{ white-space:pre-wrap; color:#c7d7e8; background:#07111f; border:1px solid var(--border); border-radius:14px; padding:12px; margin:0; }}
    .statusLine {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:10px; }}
    .small {{ font-size:12px; color:var(--muted); }}
    .ok {{ color:var(--green); }} .warn {{ color:var(--yellow); }} .bad {{ color:var(--red); }}
    .bar {{ height:10px; background:#07111f; border:1px solid var(--border); border-radius:999px; overflow:hidden; margin-top:10px; }}
    .bar > div {{ height:100%; width:0%; background:linear-gradient(90deg,var(--blue),var(--green)); transition:width .25s ease; }}
    @media (max-width:1000px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .two {{ grid-template-columns:1fr; }} }}
    @media (max-width:700px) {{ .grid {{ grid-template-columns:1fr; }} .hero {{ flex-direction:column; align-items:flex-start; }} .toolbar {{ justify-content:flex-start; }} h1 {{ font-size:25px; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="brand">
        <div class="logo">📥</div>
        <div>
          <h1 id="title">{escape(payload['bot'])} Dashboard</h1>
          <div class="muted">Live statistics website for your Telegram bot</div>
          <div class="statusLine">
            <span class="pill"><span class="dot"></span><span id="onlineText">Online</span></span>
            <span class="pill">Last update: <span id="lastUpdate">now</span></span>
          </div>
        </div>
      </div>
      <div class="toolbar">
        <button class="btn" onclick="loadStats(true)">↻ Refresh</button>
        <a class="btn" href="/health" target="_blank">Health</a>
        <a class="btn" href="/api/stats" id="apiLink" target="_blank">API</a>
      </div>
    </div>

    <div class="grid">
      <div class="card"><div class="label">Unique users</div><div class="value" id="unique_users">0</div><div class="sub" id="active_users">24h: 0 • 7d: 0</div></div>
      <div class="card"><div class="label">Downloaded files</div><div class="value" id="total_downloaded_files">0</div><div class="sub" id="today_downloaded_files">Today: 0</div></div>
      <div class="card"><div class="label">Channel jobs</div><div class="value" id="total_channel_jobs">0</div><div class="sub" id="today_channel_jobs">Today: 0</div></div>
      <div class="card"><div class="label">Direct URL downloads</div><div class="value" id="total_direct_urls">0</div><div class="sub" id="today_direct_urls">Today: 0</div></div>
      <div class="card"><div class="label">Active jobs</div><div class="value" id="active_jobs">0</div><div class="sub" id="active_jobs_sub">Max: 0</div></div>
      <div class="card"><div class="label">Server status</div><div class="value" id="status">online</div><div class="sub" id="uptime">Uptime: -</div></div>
      <div class="card"><div class="label">Public mode</div><div class="value" id="public_mode">-</div><div class="sub" id="reader">Reader: -</div></div>
      <div class="card"><div class="label">Downloads folder</div><div class="value" id="downloads_folder_size">0 B</div><div class="sub">Temporary storage usage</div></div>
    </div>

    <div class="section"><h2>Active downloads</h2><table><thead><tr><th>User</th><th>Channel</th><th>Status</th><th>Phase</th><th>Files</th><th>Progress</th></tr></thead><tbody id="jobsRows"></tbody></table></div>

    <div class="two">
      <div class="section"><h2>Last 14 days</h2><table><thead><tr><th>Day</th><th>Downloaded files</th><th>Channel jobs</th><th>Direct URLs</th></tr></thead><tbody id="dailyRows"></tbody></table></div>
      <div class="section"><h2>Disk</h2><pre id="diskReport">Loading...</pre></div>
    </div>

    <div class="section"><h2>Recent users</h2><table><thead><tr><th>User ID</th><th>Username</th><th>Name</th><th>Banned</th><th>Last seen</th></tr></thead><tbody id="usersRows"></tbody></table></div>
    <div class="section"><h2>Recent events</h2><table><thead><tr><th>User ID</th><th>Type</th><th>Detail</th><th>Time</th></tr></thead><tbody id="eventsRows"></tbody></table></div>
    <p class="small">This page refreshes automatically every {int(refresh_ms/1000)} seconds using JavaScript.</p>
  </div>

  <script id="initial" type="application/json">{initial_json}</script>
  <script>
    const refreshMs = {refresh_ms};
    const params = new URLSearchParams(location.search);
    const keyFromUrl = params.get('key') || '';
    if (keyFromUrl) localStorage.setItem('dashboardKey', keyFromUrl);
    const dashboardKey = keyFromUrl || localStorage.getItem('dashboardKey') || '';
    const apiUrl = '/api/stats' + (dashboardKey ? ('?key=' + encodeURIComponent(dashboardKey)) : '');
    document.getElementById('apiLink').href = apiUrl;

    function esc(v) {{ return String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
    function set(id, value) {{ const el = document.getElementById(id); if (el) el.textContent = value; }}
    function int(v) {{ return Number(v || 0).toLocaleString(); }}
    function row(html, colspan) {{ return html || `<tr><td colspan="${{colspan}}">No data yet</td></tr>`; }}

    function render(data) {{
      set('title', `${{data.bot || 'PhotoSnatcher'}} Dashboard`);
      set('unique_users', int(data.unique_users));
      set('active_users', `24h: ${{int(data.active_users_24h)}} • 7d: ${{int(data.active_users_7d)}}`);
      set('total_downloaded_files', int(data.total_downloaded_files));
      set('today_downloaded_files', `Today: ${{int(data.today_downloaded_files)}}`);
      set('total_channel_jobs', int(data.total_channel_jobs));
      set('today_channel_jobs', `Today: ${{int(data.today_channel_jobs)}}`);
      set('total_direct_urls', int(data.total_direct_urls));
      set('today_direct_urls', `Today: ${{int(data.today_direct_urls)}}`);
      set('active_jobs', `${{int(data.active_jobs)}}/${{int(data.max_active_jobs)}}`);
      set('active_jobs_sub', `Reader: ${{data.reader || '-'}}`);
      set('status', data.status || 'online');
      set('uptime', `Uptime: ${{data.uptime || '-'}}`);
      set('public_mode', String(data.public_mode));
      set('reader', `Reader: ${{data.reader || '-'}}`);
      set('downloads_folder_size', data.downloads_folder_size || '0 B');
      set('diskReport', data.disk_report || 'No disk report');
      set('lastUpdate', new Date().toLocaleTimeString());
      document.getElementById('onlineText').textContent = 'Online';

      const jobs = (data.jobs || []).map(j => {{
        const total = Number(j.total || 0);
        const downloaded = Number(j.downloaded || 0);
        const pct = total ? Math.min(100, Math.round((downloaded / total) * 100)) : 0;
        return `<tr><td>${{esc(j.user_id)}}</td><td>${{esc(j.channel)}}</td><td>${{esc(j.status)}}</td><td>${{esc(j.phase)}}</td><td>${{int(downloaded)}}/${{total ? int(total) : '?'}}</td><td><div class="bar"><div style="width:${{pct}}%"></div></div><span class="small">${{pct}}%</span></td></tr>`;
      }}).join('');
      document.getElementById('jobsRows').innerHTML = row(jobs, 6);

      const daily = (data.daily_usage || []).map(d => `<tr><td>${{esc(d.day)}}</td><td>${{int(d.files)}}</td><td>${{int(d.channels)}}</td><td>${{int(d.direct)}}</td></tr>`).join('');
      document.getElementById('dailyRows').innerHTML = row(daily, 4);

      const users = (data.recent_users || []).map(u => `<tr><td>${{esc(u.user_id)}}</td><td>@${{esc(u.username || '-')}}</td><td>${{esc(u.first_name || '-')}}</td><td>${{u.banned ? '<span class="bad">Yes</span>' : '<span class="ok">No</span>'}}</td><td>${{esc(u.last_seen || '-')}}</td></tr>`).join('');
      document.getElementById('usersRows').innerHTML = row(users, 5);

      const events = (data.recent_events || []).map(e => `<tr><td>${{esc(e.user_id)}}</td><td>${{esc(e.type)}}</td><td>${{esc(e.detail)}}</td><td>${{esc(e.created_at)}}</td></tr>`).join('');
      document.getElementById('eventsRows').innerHTML = row(events, 4);
    }}

    async function loadStats(manual=false) {{
      try {{
        const res = await fetch(apiUrl, {{ cache: 'no-store' }});
        if (!res.ok) throw new Error('Dashboard API locked or unavailable: ' + res.status);
        const data = await res.json();
        render(data);
      }} catch (err) {{
        document.getElementById('onlineText').textContent = 'Connection problem';
        console.error(err);
        if (manual) alert(err.message);
      }}
    }}

    try {{ render(JSON.parse(document.getElementById('initial').textContent)); }} catch (e) {{}}
    loadStats(false);
    setInterval(loadStats, refreshMs);
  </script>
</body>
</html>"""


def run_health_server():
    health_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


def uptime_text() -> str:
    seconds = int(time.time() - STARTED_AT)
    mins, sec = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    days, hrs = divmod(hrs, 24)
    if days:
        return f"{days}d {hrs}h {mins}m"
    if hrs:
        return f"{hrs}h {mins}m"
    return f"{mins}m {sec}s"


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID and user_id == OWNER_ID)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_public_mode() -> bool:
    value = db.get_setting("PUBLIC_MODE")
    if value is None:
        return PUBLIC_MODE
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_private_invite(channel_link: str) -> bool:
    return "+" in channel_link or "/joinchat/" in channel_link


def is_allowed_user(user_id: int) -> bool:
    return get_public_mode() or is_admin(user_id)


async def touch_user(event) -> None:
    try:
        sender = await event.get_sender()
        db.touch_user(
            event.sender_id,
            getattr(sender, "username", "") or "",
            getattr(sender, "first_name", "") or "",
            getattr(sender, "last_name", "") or "",
        )
    except Exception:
        db.touch_user(event.sender_id)


def access_control(func):
    async def wrapper(event):
        await touch_user(event)
        sender_id = event.sender_id
        if db.is_banned(sender_id):
            await event.reply("❌ You are blocked from using this bot.")
            logger.warning("Blocked banned user: %s", sender_id)
            return
        if not is_allowed_user(sender_id):
            await event.reply("❌ This bot is private right now. Send /whoami to get your user ID and ask the owner for access.")
            logger.warning("Blocked unauthorized user: %s", sender_id)
            return
        return await func(event)

    return wrapper


def admin_only(func):
    async def wrapper(event):
        await touch_user(event)
        sender_id = event.sender_id
        if not is_admin(sender_id):
            await event.reply("❌ This command is only available to the bot owner/admins.")
            logger.warning("Blocked non-admin from admin command: %s", sender_id)
            return
        return await func(event)

    return wrapper


def make_start_button(channel_link: str, user_id: int):
    key = secrets.token_urlsafe(8)
    pending_channel_links[key] = {"link": channel_link, "user_id": user_id, "created_at": time.time()}
    return Button.inline("▶️ Start Download", data=f"start:{key}".encode("utf-8"))


def check_config() -> None:
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not get_public_mode() and not OWNER_ID:
        missing.append("OWNER_ID")
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")


def max_photos_for_user(user_id: int) -> int:
    if is_admin(user_id):
        return OWNER_MAX_PHOTOS_PER_JOB
    return PUBLIC_MAX_PHOTOS_PER_JOB


def quota_text(user_id: int) -> str:
    direct, channel, images = db.get_usage_today(user_id)
    return (
        f"Today usage:\n"
        f"Direct URLs: {direct}/{USER_DAILY_DIRECT_LIMIT}\n"
        f"Channel jobs: {channel}/{USER_DAILY_CHANNEL_LIMIT}\n"
        f"Images delivered: {images}\n"
        f"Public channel photo cap: {PUBLIC_MAX_PHOTOS_PER_JOB if PUBLIC_MAX_PHOTOS_PER_JOB else 'unlimited'}"
    )


def can_start_channel_job(user_id: int) -> tuple[bool, str]:
    if is_admin(user_id):
        return True, ""
    _, channel_count, _ = db.get_usage_today(user_id)
    if channel_count >= USER_DAILY_CHANNEL_LIMIT:
        return False, f"Daily channel download limit reached: {channel_count}/{USER_DAILY_CHANNEL_LIMIT}. Try again tomorrow."
    last = last_channel_start.get(user_id, 0)
    remaining = int(MIN_SECONDS_BETWEEN_JOBS - (time.time() - last))
    if remaining > 0:
        return False, f"Please wait {remaining}s before starting another download."
    return True, ""


START_TEXT = f"""
📥 **{BOT_NAME}**

I can save images in two ways:

1) Send me a direct image URL.
2) Send a Telegram channel link or use `/download <channel_link>` to download accessible photos from channel history.

Commands:
/start - welcome message
/help - usage instructions
/whoami - show your Telegram user ID
/limits - show your limits
/download <channel_link> - download channel photos
/status - show your current progress
/pause - pause/resume your download
/cancel - stop your download
/cleanup - owner cleanup

Telegram links must look like:
`https://t.me/SomeChannel`
`t.me/SomeChannel`
`@SomeChannel`
`https://t.me/+PRIVATE_INVITE_LINK`

Do not send `web.telegram.org/...` browser links. Use a real `t.me` link.
""".strip()

HELP_TEXT = f"""
**How to use {BOT_NAME}**

Direct image:
Send: `https://site.com/image.jpg`

Telegram channel:
`/download https://t.me/SomeChannel`

Private invite links:
`/download https://t.me/+XXXX`

Private links need `STRING_SESSION` in Render env. Public users cannot use private links unless the owner enables it.

Useful commands:
/status - progress
/pause - pause/resume
/cancel - stop
/limits - show daily limits
/whoami - show your user ID

Admin commands:
/admin - control panel
/stats - bot statistics
/dashboard - private Render stats website link
/queue - active downloads
/users - recent users
/ban <id> - block user
/unban <id> - unblock user
/banlist - blocked users
/publicmode on|off|status - change public mode without Render
/broadcast <message> - message all users
/logs [lines] - send recent logs
/cleanup - delete temporary server files
/cancelall - stop every active job

Locked/paid Telegram media cannot be bypassed. The bot downloads only accessible photos.
""".strip()


@bot_client.on(events.NewMessage(pattern=r"^/whoami$"))
async def whoami_handler(event):
    await touch_user(event)
    await event.reply(f"Your Telegram numeric ID is:\n`{event.sender_id}`", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/start$"))
@access_control
async def start_handler(event):
    await event.reply(
        START_TEXT,
        buttons=[
            [Button.inline("📊 My status", data=b"my_status"), Button.inline("📘 Help", data=b"help")],
        ],
        parse_mode="md",
    )


@bot_client.on(events.NewMessage(pattern=r"^/help$"))
@access_control
async def help_handler(event):
    await event.reply(HELP_TEXT, parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/limits$"))
@access_control
async def limits_handler(event):
    await event.reply(quota_text(event.sender_id))


@bot_client.on(events.NewMessage(pattern=r"^/ping$"))
@access_control
async def ping_handler(event):
    await event.reply(f"✅ Pong. Uptime: {uptime_text()}")



@bot_client.on(events.NewMessage(pattern=r"^/dashboard$"))
@admin_only
async def dashboard_link_handler(event):
    if not DASHBOARD_ENABLED:
        await event.reply("❌ Dashboard is disabled. Set DASHBOARD_ENABLED=true in Render env.")
        return
    if not DASHBOARD_PUBLIC and not DASHBOARD_SECRET:
        await event.reply("⚠️ Dashboard needs DASHBOARD_SECRET in Render env. Add it, redeploy, then use /dashboard again.")
        return
    await event.reply(f"📊 Web dashboard:\n{_dashboard_link(include_secret=True)}")

@bot_client.on(events.NewMessage(pattern=r"^/sessionstatus$"))
@admin_only
async def session_status_handler(event):
    if uses_user_session:
        await event.reply("✅ STRING_SESSION is active. Private invite links can work if your account has access.")
    else:
        await event.reply(
            "⚠️ STRING_SESSION is missing. Public links may work, but private invite links like `https://t.me/+XXXX` will not work.",
            parse_mode="md",
        )


@bot_client.on(events.NewMessage(pattern=r"^/status$"))
@access_control
async def status_handler(event):
    await event.reply(downloader.status_text(event.sender_id), parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/cancel$"))
@access_control
async def cancel_handler(event):
    text = await downloader.cancel(event.sender_id)
    await event.reply(text)


@bot_client.on(events.NewMessage(pattern=r"^/pause$"))
@access_control
async def pause_handler(event):
    text = await downloader.toggle_pause(event.sender_id)
    await event.reply(text)


@bot_client.on(events.NewMessage(pattern=r"^/cleanup$"))
@admin_only
async def cleanup_handler(event):
    active_job = downloader.get_job(event.sender_id)
    if active_job and active_job.status in {"validating", "scanning", "running", "paused", "zipping", "delivering"}:
        await event.reply("❌ A download is active. Use /cancel first, then /cleanup.")
        return
    cleanup_paths([DOWNLOAD_ROOT])
    ensure_dirs()
    await event.reply("✅ Cleanup complete. Temporary download files were deleted.\n\n" + disk_report())


@bot_client.on(events.NewMessage(pattern=r"^/admin$"))
@admin_only
async def admin_handler(event):
    await event.reply(
        f"🛠 **{BOT_NAME} Admin Panel**\nChoose an action:",
        buttons=[
            [Button.inline("📊 Stats", data=b"admin_stats"), Button.inline("🌐 Dashboard", data=b"admin_dashboard")],
            [Button.inline("📥 Queue", data=b"admin_queue"), Button.inline("👥 Users", data=b"admin_users")],
            [Button.inline("💾 Disk", data=b"admin_disk"), Button.inline("🌍 Public mode", data=b"admin_public")],
            [Button.inline("🧹 Cleanup", data=b"admin_cleanup")],
        ],
        parse_mode="md",
    )


@bot_client.on(events.NewMessage(pattern=r"^/stats$"))
@admin_only
async def stats_handler(event):
    direct, channels, images = db.usage_totals_today()
    text = (
        f"📊 **{BOT_NAME} stats**\n"
        f"Uptime: {uptime_text()}\n"
        f"Public mode: {get_public_mode()}\n"
        f"Reader: {'user session' if uses_user_session else 'bot session'}\n"
        f"Active jobs: {len(downloader.active_jobs())}/{MAX_ACTIVE_JOBS}\n"
        f"Known users: {db.user_count()}\n"
        f"Today direct URLs: {direct}\n"
        f"Today channel jobs: {channels}\n"
        f"Today images delivered: {images}\n\n"
        f"{disk_report()}"
    )
    await event.reply(text, parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/queue$"))
@admin_only
async def queue_handler(event):
    await event.reply(downloader.queue_text(), parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/users(?:\s+(\d+))?$"))
@admin_only
async def users_handler(event):
    limit = int(event.pattern_match.group(1) or 15)
    limit = max(1, min(limit, 50))
    rows = db.list_users(limit)
    if not rows:
        await event.reply("No users saved yet.")
        return
    lines = [f"Recent users ({len(rows)}):"]
    for user_id, username, first_name, banned, first_seen, last_seen in rows:
        name = f"@{username}" if username else (first_name or "no name")
        ban = " 🚫" if banned else ""
        lines.append(f"• `{user_id}` {name}{ban} | last: {last_seen}")
    await event.reply("\n".join(lines), parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/ban\s+(\d+)$"))
@admin_only
async def ban_handler(event):
    user_id = int(event.pattern_match.group(1))
    if is_admin(user_id):
        await event.reply("❌ You cannot ban the owner/admin.")
        return
    db.set_banned(user_id, True)
    await event.reply(f"✅ User `{user_id}` banned.", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/unban\s+(\d+)$"))
@admin_only
async def unban_handler(event):
    user_id = int(event.pattern_match.group(1))
    db.set_banned(user_id, False)
    await event.reply(f"✅ User `{user_id}` unbanned.", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/banlist$"))
@admin_only
async def banlist_handler(event):
    rows = db.list_banned()
    if not rows:
        await event.reply("Ban list is empty.")
        return
    lines = ["Banned users:"]
    for user_id, username, first_name, last_seen in rows:
        name = f"@{username}" if username else (first_name or "no name")
        lines.append(f"• `{user_id}` {name} | last: {last_seen}")
    await event.reply("\n".join(lines), parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/publicmode(?:\s+(on|off|status))?$"))
@admin_only
async def publicmode_handler(event):
    arg = event.pattern_match.group(1)
    if not arg or arg == "status":
        await event.reply(f"Public mode is currently: `{get_public_mode()}`", parse_mode="md")
        return
    db.set_setting("PUBLIC_MODE", "true" if arg == "on" else "false")
    await event.reply(f"✅ Public mode changed to `{arg}`. This change is saved in SQLite and does not need Render redeploy.", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/broadcast\s+([\s\S]+)$"))
@admin_only
async def broadcast_handler(event):
    message = event.pattern_match.group(1).strip()
    if not message:
        await event.reply("Usage: /broadcast your message")
        return
    rows = db.list_users(1000)
    sent = 0
    failed = 0
    for user_id, *_ in rows:
        if db.is_banned(user_id):
            continue
        try:
            await bot_client.send_message(user_id, message)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await event.reply(f"✅ Broadcast finished. Sent: {sent}, failed: {failed}.")


@bot_client.on(events.NewMessage(pattern=r"^/logs(?:\s+(\d+))?$"))
@admin_only
async def logs_handler(event):
    lines_count = int(event.pattern_match.group(1) or 80)
    lines_count = max(10, min(lines_count, 300))
    path = Path(LOG_FILE)
    if not path.exists():
        await event.reply("No log file yet.")
        return
    lines = path.read_text(errors="ignore").splitlines()[-lines_count:]
    text = "\n".join(lines) or "Log is empty."
    if len(text) > 3500:
        await bot_client.send_file(event.chat_id, str(path), caption="bot.log")
    else:
        await event.reply(f"```\n{text}\n```", parse_mode="md")


@bot_client.on(events.NewMessage(pattern=r"^/cancelall$"))
@admin_only
async def cancelall_handler(event):
    count = await downloader.cancel_all()
    await event.reply(f"Cancelled active jobs: {count}")


@bot_client.on(events.NewMessage(pattern=r"^/download(?:\s+(.+))?$"))
@access_control
async def download_handler(event):
    channel_link = event.pattern_match.group(1)
    if not channel_link:
        await event.reply("Usage: `/download https://t.me/SomeChannel`", parse_mode="md")
        return

    channel_link = normalize_channel_input(channel_link)
    await offer_channel_download(event, channel_link)


async def offer_channel_download(event, channel_link: str):
    if is_web_telegram_url(channel_link):
        await event.reply("❌ This is a web.telegram.org browser link. Open the channel → Share/Copy Link and send `https://t.me/...` or `@channelusername`.", parse_mode="md")
        return

    if not is_telegram_channel_link(channel_link):
        await event.reply("❌ Invalid channel link. Use https://t.me/channel, t.me/+invite, t.me/joinchat/hash, or @channel")
        return

    if is_private_invite(channel_link):
        if get_public_mode() and not is_admin(event.sender_id) and not ALLOW_PRIVATE_LINKS_FOR_PUBLIC:
            await event.reply("❌ Private invite links are disabled for public users. Send a public channel link instead.")
            return
        private_note = "\n\n🔐 Private invite link detected. It needs STRING_SESSION active."
    else:
        private_note = ""

    allowed, reason = can_start_channel_job(event.sender_id)
    if not allowed:
        await event.reply(f"❌ {reason}")
        return

    cap = max_photos_for_user(event.sender_id)
    cap_note = f"\nPhoto cap for this job: **{cap}**" if cap else "\nPhoto cap for this job: **unlimited**"
    await event.reply(
        f"Channel detected:\n`{channel_link}`{private_note}{cap_note}\n\nPress Start Download to begin.",
        buttons=[
            [make_start_button(channel_link, event.sender_id)],
            [Button.inline("Cancel", data=b"cancel")],
        ],
        parse_mode="md",
    )


@bot_client.on(events.CallbackQuery)
async def callback_handler(event):
    await touch_user(event)
    if db.is_banned(event.sender_id):
        await event.answer("Blocked", alert=True)
        return
    if not is_allowed_user(event.sender_id):
        await event.answer("Not allowed", alert=True)
        return

    data = event.data.decode("utf-8", errors="ignore")

    if data == "help":
        await event.answer("Help")
        await event.respond(HELP_TEXT, parse_mode="md")
        return

    if data == "my_status":
        await event.answer("Status")
        await event.respond(downloader.status_text(event.sender_id) + "\n\n" + quota_text(event.sender_id), parse_mode="md")
        return

    if data.startswith("admin_"):
        if not is_admin(event.sender_id):
            await event.answer("Admin only", alert=True)
            return
        await event.answer("OK")
        if data == "admin_stats":
            direct, channels, images = db.usage_totals_today()
            await event.respond(f"Stats:\nUptime: {uptime_text()}\nUsers: {db.user_count()}\nToday direct: {direct}\nToday channels: {channels}\nToday images: {images}\nActive jobs: {len(downloader.active_jobs())}/{MAX_ACTIVE_JOBS}")
        elif data == "admin_queue":
            await event.respond(downloader.queue_text(), parse_mode="md")
        elif data == "admin_users":
            rows = db.list_users(10)
            text = "Recent users:\n" + "\n".join([f"• {r[0]} @{r[1] or '-'}" for r in rows]) if rows else "No users yet."
            await event.respond(text)
        elif data == "admin_disk":
            await event.respond(disk_report())
        elif data == "admin_public":
            await event.respond(f"Public mode: {get_public_mode()}\nUse /publicmode on or /publicmode off to change it.")
        elif data == "admin_cleanup":
            cleanup_paths([DOWNLOAD_ROOT])
            ensure_dirs()
            await event.respond("Cleanup complete.\n" + disk_report())
        return

    if data.startswith("start:"):
        key = data.split(":", 1)[1]
        pending = pending_channel_links.pop(key, None)
        if not pending:
            await event.answer("This download button expired. Send the channel link again.", alert=True)
            return
        if pending.get("user_id") != event.sender_id:
            await event.answer("This button belongs to another user.", alert=True)
            return

        channel_link = pending["link"]
        if is_private_invite(channel_link) and get_public_mode() and not is_admin(event.sender_id) and not ALLOW_PRIVATE_LINKS_FOR_PUBLIC:
            await event.answer("Private invite links are disabled for public users.", alert=True)
            return

        allowed, reason = can_start_channel_job(event.sender_id)
        if not allowed:
            await event.answer(reason, alert=True)
            return

        await event.answer("Starting...")
        last_channel_start[event.sender_id] = time.time()
        db.add_usage(event.sender_id, channel=1)
        db.log_event(event.sender_id, "channel_download_start", channel_link)
        text = await downloader.start_download(event.sender_id, channel_link, max_photos=max_photos_for_user(event.sender_id))
        await event.edit(text)
        return

    if data == "cancel":
        await event.answer("Cancelling...")
        text = await downloader.cancel(event.sender_id)
        await event.respond(text)
        return

    if data == "pause":
        await event.answer("Updating...")
        text = await downloader.toggle_pause(event.sender_id)
        await event.respond(text)
        return


@bot_client.on(events.NewMessage)
@access_control
async def text_handler(event):
    text = event.raw_text or ""
    if text.startswith("/"):
        return

    urls = extract_urls(text)
    if urls and any(is_web_telegram_url(url) for url in urls):
        await event.reply("❌ `web.telegram.org/...` links are browser links. Send a real Telegram link like `https://t.me/channelname`, `https://t.me/+invite`, or `@channelname`.", parse_mode="md")
        return

    if not urls:
        await event.reply("Send a direct image URL or use `/download <channel_link>`.", parse_mode="md")
        return

    channel_links = [normalize_channel_input(url) for url in urls if is_telegram_channel_link(normalize_channel_input(url))]
    if channel_links:
        await offer_channel_download(event, channel_links[0])
        return

    direct_count, _, _ = db.get_usage_today(event.sender_id)
    if not is_admin(event.sender_id) and direct_count >= USER_DAILY_DIRECT_LIMIT:
        await event.reply(f"❌ Daily direct URL limit reached: {direct_count}/{USER_DAILY_DIRECT_LIMIT}. Try again tomorrow.")
        return

    selected = urls[:MAX_URLS_PER_MESSAGE]
    temp_folder = DOWNLOAD_ROOT / "direct_urls" / str(event.sender_id)
    temp_folder.mkdir(parents=True, exist_ok=True)
    downloaded_paths = []

    status_msg = await event.reply(f"Found {len(selected)} URL(s). Downloading...")

    for url in selected:
        if not is_valid_http_url(url):
            await event.reply(f"❌ Invalid URL:\n{url}")
            continue

        try:
            image_url = url
            try:
                image_path = await download_direct_image(image_url, temp_folder)
            except Exception:
                og_image = await extract_og_image(url)
                if not og_image:
                    raise
                image_url = og_image
                image_path = await download_direct_image(image_url, temp_folder)

            downloaded_paths.append(image_path)
            await bot_client.send_file(event.chat_id, str(image_path), caption="✅ Downloaded image")
            db.add_usage(event.sender_id, direct=1, images=1)
            db.log_event(event.sender_id, "direct_url", url)
        except Exception as exc:
            await event.reply(f"❌ Failed to download:\n{url}\n\nReason: {exc}")
            logger.warning("Direct URL download failed for %s: %s", url, exc)

    cleanup_paths(downloaded_paths)
    try:
        await status_msg.delete()
    except Exception:
        pass


async def main():
    check_config()
    ensure_dirs()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    threading.Thread(target=run_health_server, daemon=True).start()

    logger.info("Starting %s Telethon bot...", BOT_NAME)
    await bot_client.start(bot_token=BOT_TOKEN)
    bot_me = await bot_client.get_me()
    logger.info("Bot started as @%s", getattr(bot_me, "username", "unknown"))
    logger.info("PUBLIC_MODE=%s | MAX_ACTIVE_JOBS=%s | private_links_for_public=%s", get_public_mode(), MAX_ACTIVE_JOBS, ALLOW_PRIVATE_LINKS_FOR_PUBLIC)

    if uses_user_session:
        await reader_client.start()
        user_me = await reader_client.get_me()
        logger.info(
            "Reader user session started as @%s / %s",
            getattr(user_me, "username", None) or "no_username",
            getattr(user_me, "id", "unknown"),
        )
    else:
        logger.warning("STRING_SESSION missing. Private invite links will not work.")

    await bot_client.run_until_disconnected()


if __name__ == "__main__":
    try:
        MAIN_LOOP.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
    except RPCError as exc:
        logger.exception("Telegram RPC error: %s", exc)
        raise
    finally:
        try:
            pending = asyncio.all_tasks(MAIN_LOOP)
            for task in pending:
                task.cancel()
            if pending:
                MAIN_LOOP.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        MAIN_LOOP.close()
