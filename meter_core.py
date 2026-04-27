"""
meter_core.py — Pure-Python data logic for Claude Token Meter.
No AppKit/ObjC dependencies; safe to import in tests.
"""

import json
import glob
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ── Constants ─────────────────────────────────────────────────────────────────
SETTINGS_FILE = os.path.expanduser("~/.claude_meter_settings.json")
PROJECTS_GLOB = os.path.expanduser("~/.claude/projects/**/*.jsonl")
DEFAULT_LIMIT  = 45_000_000    # Claude Pro monthly (approximate)
SESSION_LIMIT  = 12_830_000    # Claude Pro 5-hour session limit (calibrated)
WEEK_LIMIT     = 179_000_000   # Claude Pro weekly limit (calibrated)

# Anthropic's billing week resets on Saturday midnight PT — keep this fixed
# even when we display times to the user in their local zone.
_PT_BILLING_OFFSET = timedelta(hours=7)   # PDT (UTC-7); Anthropic always uses PT

# ── JSONL file cache ──────────────────────────────────────────────────────────
# { path: (mtime_float, [entry_dict, ...]) }
_jsonl_cache: dict = {}


def _load_all_entries() -> list:
    """
    Return all parsed JSONL entry dicts from ~/.claude/projects/.
    Files are re-parsed only when their mtime has changed; otherwise the
    cached result is reused, avoiding redundant disk I/O every refresh tick.
    """
    entries = []
    projects_dir = os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(projects_dir):
        return entries  # no Claude Code data yet — return empty gracefully

    for path in glob.glob(PROJECTS_GLOB, recursive=True):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue

        # Cache hit: file unchanged since last parse
        if path in _jsonl_cache and _jsonl_cache[path][0] == mtime:
            entries.extend(_jsonl_cache[path][1])
            continue

        # Cache miss: parse fresh
        file_entries = []
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if isinstance(d, dict):
                            file_entries.append(d)
                    except (json.JSONDecodeError, ValueError):
                        continue  # skip malformed lines silently
        except OSError:
            continue  # file disappeared between glob and open

        _jsonl_cache[path] = (mtime, file_entries)
        entries.extend(file_entries)

    return entries


def _parse_ts(ts_str: str):
    """Parse an ISO-8601 timestamp string to a UTC-aware datetime, or None."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _extract_tokens(usage) -> int:
    """Safely sum all token fields from a usage dict. Returns 0 on bad input."""
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in ("input_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "output_tokens"):
        val = usage.get(key, 0)
        try:
            total += max(0, int(val))
        except (TypeError, ValueError):
            pass
    return total


# ── Billing week helpers ──────────────────────────────────────────────────────

def _week_start_utc(now_utc: datetime) -> datetime:
    """
    Return the UTC datetime for the start of the current Anthropic billing week.
    Anthropic resets weekly on Saturday midnight PT — this is always PT regardless
    of the user's local timezone, so we keep the fixed PT offset here.
    """
    now_pt = now_utc - _PT_BILLING_OFFSET
    days_since_saturday = (now_pt.weekday() - 5) % 7   # weekday(): Mon=0, Sat=5
    saturday_pt = (now_pt - timedelta(days=days_since_saturday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return saturday_pt + _PT_BILLING_OFFSET  # back to UTC


# ── Reset time formatting (system local timezone) ─────────────────────────────

def _fmt_local_time(utc_dt: datetime) -> str:
    """Format a UTC datetime as the user's local time, e.g. '11:24 PM EDT'."""
    local = utc_dt.astimezone()
    # strftime %I gives zero-padded hour; strip leading zero manually
    h = local.strftime("%I").lstrip("0") or "12"
    ampm = local.strftime("%p")
    tz   = local.strftime("%Z")
    mins = local.strftime("%M")
    return f"{h}:{mins} {ampm} {tz}"


def _fmt_local_date(utc_dt: datetime) -> str:
    """Format a UTC datetime as local date, e.g. 'Apr 18'."""
    return utc_dt.astimezone().strftime("%b %-d")


# ── Session detection ─────────────────────────────────────────────────────────

def detect_session_start() -> datetime:
    """
    Find when the current Anthropic 5h session started.
    Scans recent JSONL timestamps and returns the first message
    after the most recent idle gap >= 30 minutes.
    Falls back to now − 5h if no data is found.
    """
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=8)
    timestamps = []

    for entry in _load_all_entries():
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not _extract_tokens(usage):
            continue
        ts = _parse_ts(entry.get("timestamp", ""))
        if ts and ts >= cutoff:
            timestamps.append(ts)

    if not timestamps:
        return now - timedelta(hours=5)

    timestamps = sorted(set(timestamps))
    session_start = timestamps[0]
    for i in range(1, len(timestamps)):
        if (timestamps[i] - timestamps[i - 1]).total_seconds() / 60 >= 30:
            session_start = timestamps[i]
    return session_start


# ── Core usage calculation ────────────────────────────────────────────────────

def get_usage():
    """
    Parse all local JSONL files and return:
        (total_month, total_week, total_session, daily_dict, session_start)

    daily_dict: { date: token_count } keyed by system local date
    Uses file-mtime caching so unchanged files are never re-parsed.
    """
    now           = datetime.now(timezone.utc)
    month_start   = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_start    = _week_start_utc(now)
    session_start = detect_session_start()

    total_month   = 0
    total_week    = 0
    total_session = 0
    daily         = defaultdict(int)

    for entry in _load_all_entries():
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        tokens = _extract_tokens(usage)
        if not tokens:
            continue

        ts = _parse_ts(entry.get("timestamp", ""))

        # Monthly total + daily chart (bucketed by local date)
        if ts is None or ts >= month_start:
            total_month += tokens
            if ts:
                daily[ts.astimezone().date()] += tokens

        # Weekly billing window (Sat–Sat PT)
        if ts and ts >= week_start:
            total_week += tokens

        # Active session window
        if ts and ts >= session_start:
            total_session += tokens

    return total_month, total_week, total_session, daily, session_start


# ── Settings I/O ─────────────────────────────────────────────────────────────

def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("settings not a dict")
            return data
    except Exception:
        return {"limit": DEFAULT_LIMIT, "interval": 300, "theme": "dark"}


def save_settings(s: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f)
    except OSError as e:
        print(f"save_settings error: {e}")


# ── Live API fetch ────────────────────────────────────────────────────────────

def fetch_claude_ai_usage(settings: dict):
    """
    Fetch real-time usage from claude.ai /usage API.
    Returns a dict on success, None on any failure (expired key, network error, etc.)
    """
    session_key = settings.get("session_key", "").strip()
    if not session_key:
        return None

    headers = {
        "Cookie":     f"sessionKey={session_key}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Accept":     "application/json",
        "Referer":    "https://claude.ai/settings/usage",
    }

    # Cache org_id to avoid extra round-trip on each refresh
    org_id = settings.get("org_id", "").strip()
    if not org_id:
        try:
            req = urllib.request.Request(
                "https://claude.ai/api/organizations", headers=headers)
            with urllib.request.urlopen(req, timeout=8) as r:
                orgs = json.loads(r.read())
            if not orgs or not isinstance(orgs, list):
                return None
            org_id = orgs[0].get("uuid") or orgs[0].get("id") or ""
            if not org_id:
                return None
            settings["org_id"] = org_id
            save_settings(settings)
        except Exception as e:
            print("fetch_claude_ai_usage: orgs error:", e)
            return None

    try:
        url = f"https://claude.ai/api/organizations/{org_id}/usage"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
    except Exception as e:
        print("fetch_claude_ai_usage: usage error:", e)
        return None

    fh = data.get("five_hour") or {}
    sd = data.get("seven_day")  or {}

    def parse_reset(iso):
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt, int(dt.timestamp() * 1000)
        except Exception:
            return None, None

    sess_dt, sess_epoch = parse_reset(fh.get("resets_at", ""))
    week_dt, week_epoch = parse_reset(sd.get("resets_at", ""))

    return {
        "session_pct":         round(float(fh.get("utilization") or 0), 1),
        "session_reset":       _fmt_local_time(sess_dt) if sess_dt else "--",
        "session_reset_epoch": sess_epoch or 0,
        "week_pct":            round(float(sd.get("utilization")  or 0), 1),
        "week_reset":          _fmt_local_date(week_dt) if week_dt else "--",
        "week_reset_epoch":    week_epoch or 0,
        "source":              "live",
    }


# ── Leverage ratio ────────────────────────────────────────────────────────────

def get_leverage_data() -> dict:
    """
    Compute output-to-input leverage ratio for the current and previous
    calendar week (Mon–Sun, local time).

    ratio = output_tokens / (input_tokens + cache_creation_input_tokens)

    Returns:
        ratio      – current-week aggregate ratio (float | None)
        prev_ratio – previous-week aggregate ratio (float | None)
        delta      – ratio − prev_ratio (float | None)
        daily_7    – [{"day", "ratio", "today"}, …] Mon–Sun of current week
    """
    now         = datetime.now(timezone.utc)
    today       = now.astimezone().date()
    monday      = today - timedelta(days=today.weekday())
    last_monday = monday - timedelta(days=7)

    daily_in: dict  = defaultdict(int)
    daily_out: dict = defaultdict(int)

    def _si(val):
        try:
            return max(0, int(val or 0))
        except (TypeError, ValueError):
            return 0

    for entry in _load_all_entries():
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue

        inp = _si(usage.get("input_tokens"))
        cac = _si(usage.get("cache_creation_input_tokens"))
        out = _si(usage.get("output_tokens"))
        if not (inp + out):
            continue

        ts = _parse_ts(entry.get("timestamp", ""))
        if ts is None:
            continue
        d = ts.astimezone().date()
        if d < last_monday:
            continue

        daily_in[d]  += inp + cac
        daily_out[d] += out

    _DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily_7 = []
    for i in range(7):
        d     = monday + timedelta(days=i)
        inp_d = daily_in.get(d, 0)
        out_d = daily_out.get(d, 0)
        ratio_d = round(out_d / inp_d, 3) if inp_d > 0 else None
        daily_7.append({"day": _DAYS[i], "ratio": ratio_d, "today": d == today})

    week_in  = sum(daily_in.get(monday      + timedelta(days=i), 0) for i in range(7))
    week_out = sum(daily_out.get(monday     + timedelta(days=i), 0) for i in range(7))
    prev_in  = sum(daily_in.get(last_monday + timedelta(days=i), 0) for i in range(7))
    prev_out = sum(daily_out.get(last_monday + timedelta(days=i), 0) for i in range(7))

    current_ratio = round(week_out / week_in, 3) if week_in > 0 else None
    prev_ratio    = round(prev_out / prev_in, 3) if prev_in  > 0 else None
    delta         = (round(current_ratio - prev_ratio, 3)
                     if current_ratio is not None and prev_ratio is not None
                     else None)

    return {
        "ratio":      current_ratio,
        "prev_ratio": prev_ratio,
        "delta":      delta,
        "daily_7":    daily_7,
    }


# ── Session leverage (for efficiency card) ────────────────────────────────────

def get_session_leverage(session_start: datetime):
    """
    Compute aggregate output/input leverage ratio for the current session.
    ratio = output_tokens / (input_tokens + cache_creation_input_tokens)

    Returns (ratio, message_count). ratio is None if insufficient data.
    """
    total_in = 0
    total_out = 0
    count = 0

    def _si(val):
        try:
            return max(0, int(val or 0))
        except (TypeError, ValueError):
            return 0

    for entry in _load_all_entries():
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        ts = _parse_ts(entry.get("timestamp", ""))
        if ts is None or ts < session_start:
            continue
        inp = _si(usage.get("input_tokens"))
        cac = _si(usage.get("cache_creation_input_tokens"))
        out = _si(usage.get("output_tokens"))
        if not (inp + out):
            continue
        total_in  += inp + cac
        total_out += out
        count     += 1

    if total_in == 0 or count < 2:
        return None, count

    return round(total_out / total_in, 3), count


# ── Project breakdown ────────────────────────────────────────────────────────

def _project_name(cwd: str) -> str:
    """Extract a short human-readable project name from a cwd path."""
    if not cwd:
        return ""
    parts = [p for p in cwd.replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    name = parts[-1]
    # If the last segment is a generic folder, prepend the parent
    _GENERIC = {"src", "lib", "app", "dist", "build", "test", "tests",
                "node_modules", ".git", "public", "static", "pages",
                "components", "utils", "helpers"}
    if name.lower() in _GENERIC and len(parts) >= 2:
        name = parts[-2] + "/" + name
    return name[:40]


def get_project_breakdown(session_start: datetime, week_start: datetime) -> dict:
    """
    Aggregate token usage by project (cwd) for the session and billing-week windows.
    Returns {"session": [...], "week": [...]} where each list is:
        [{"name": str, "tokens": int, "pct": float}, ...]  (top 6, descending)
    """
    sess: dict = defaultdict(int)
    week: dict = defaultdict(int)

    for entry in _load_all_entries():
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        tokens = _extract_tokens(usage)
        if not tokens:
            continue
        ts  = _parse_ts(entry.get("timestamp", ""))
        cwd = entry.get("cwd", "") or ""
        name = _project_name(cwd)
        if not name:
            continue
        if ts and ts >= session_start:
            sess[name] += tokens
        if ts and ts >= week_start:
            week[name] += tokens

    def _top(d: dict, n: int = 3):
        items = sorted(d.items(), key=lambda x: -x[1])[:n]
        total = sum(v for _, v in items) or 1
        return [{"name": k, "tokens": v, "pct": round(v / total * 100, 1)}
                for k, v in items]

    return {"session": _top(sess), "week": _top(week)}


# ── Hourly heatmap ────────────────────────────────────────────────────────────

def get_hourly_heatmap(days: int = 28) -> list:
    """
    Return a 7×24 matrix of token volumes bucketed by (day-of-week, hour-of-day).
    Rows = Mon(0)…Sun(6), Cols = 0…23 (local time).
    Covers the last `days` calendar days.
    """
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    grid: dict = defaultdict(int)

    for entry in _load_all_entries():
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        usage  = msg.get("usage")
        tokens = _extract_tokens(usage)
        if not tokens:
            continue
        ts = _parse_ts(entry.get("timestamp", ""))
        if ts is None or ts < cutoff:
            continue
        local = ts.astimezone()
        grid[(local.weekday(), local.hour)] += tokens

    return [[grid.get((d, h), 0) for h in range(24)] for d in range(7)]


# ── Payload builder ───────────────────────────────────────────────────────────

def build_payload(settings: dict) -> dict:
    """Build the full data payload sent to the WebView on each refresh tick."""
    total, week_tokens, session_tokens, daily, session_start = get_usage()
    try:
        limit = max(1, int(settings.get("limit", DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    now_utc = datetime.now(timezone.utc)
    now     = datetime.now()

    has_cookie = bool(settings.get("session_key", "").strip())
    live       = fetch_claude_ai_usage(settings) if has_cookie else None
    lev_ratio, lev_count = get_session_leverage(session_start)

    week_start_dt = _week_start_utc(now_utc)
    projects_data = (get_project_breakdown(session_start, week_start_dt)
                     if settings.get("module_project", False) else None)
    heatmap_data  = (get_hourly_heatmap()
                     if settings.get("module_heatmap", False) else None)

    if live:
        session_pct         = live["session_pct"]
        session_reset       = live["session_reset"]
        session_reset_epoch = live["session_reset_epoch"]
        week_pct            = live["week_pct"]
        week_reset          = live["week_reset"]
        week_reset_epoch    = live["week_reset_epoch"]
        data_source         = "live"
    else:
        session_pct         = min(100.0, session_tokens / max(1, SESSION_LIMIT) * 100)
        week_pct            = min(100.0, week_tokens    / max(1, WEEK_LIMIT)    * 100)
        session_reset_utc   = session_start + timedelta(hours=5)
        week_reset_utc      = _week_start_utc(now_utc) + timedelta(days=7)
        session_reset       = _fmt_local_time(session_reset_utc)
        session_reset_epoch = int(session_reset_utc.timestamp() * 1000)
        week_reset          = _fmt_local_date(week_reset_utc)
        week_reset_epoch    = int(week_reset_utc.timestamp() * 1000)
        data_source         = "local"

    pct = min(100.0, total / limit * 100)

    # 7-day chart: Mon–Sun of current local calendar week
    _today  = datetime.now().date()
    _monday = _today - timedelta(days=_today.weekday())
    _DAYS   = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily_7 = [
        {
            "day":    _DAYS[i],
            "tokens": daily.get(_monday + timedelta(days=i), 0),
            "today":  (_monday + timedelta(days=i)) == _today,
        }
        for i in range(7)
    ]

    # Onboarding & empty-state signals
    claude_installed = os.path.isdir(os.path.expanduser("~/.claude/projects"))
    has_data         = total > 0 or bool(live)
    if settings.get("simulate_empty"):
        has_data = False
    first_launch     = not settings.get("onboarding_done", False)

    return {
        "pct":                 round(pct, 1),
        "used":                total,
        "limit":               limit,
        "session_tokens":      session_tokens,
        "session_pct":         round(session_pct, 1),
        "session_reset":       session_reset,
        "session_reset_epoch": session_reset_epoch,
        "week_tokens":         week_tokens,
        "week_pct":            round(week_pct, 1),
        "week_reset":          week_reset,
        "week_reset_epoch":    week_reset_epoch,
        "month":               now.strftime("%b %Y").upper(),
        "updated":             "just now",
        "interval":            settings.get("interval", 300),
        "theme":               settings.get("theme", "system"),
        "source":              data_source,
        "cookie_expired":      has_cookie and not live,
        "daily_7":             daily_7,
        "first_launch":        first_launch,
        "has_data":            has_data,
        "claude_installed":    claude_installed,
        "lev_ratio":           lev_ratio,
        "lev_count":           lev_count,
        "projects":            projects_data,
        "heatmap":             heatmap_data,
    }
