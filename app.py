"""도원결의 (桃園結義) — 정부지원사업 통합 플랫폼 + Supabase 기반 영구 저장"""

import os
import re
import time
import threading
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    redirect,
    url_for,
    session,
    abort,
)
from werkzeug.security import generate_password_hash, check_password_hash
import requests

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dowon-桃園結義-2026-secret-key")
app.permanent_session_lifetime = timedelta(days=30)

# ============================================================
# 환경변수
# ============================================================
KSTARTUP_API_KEY = os.environ.get(
    "KSTARTUP_API_KEY",
    "39af9bc6585e1db2baec17245fe6d556486148d45fe88aa213e10043604d343b",
)
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://ufbfziqebhqhfmjjpnlc.supabase.co",
)
SUPABASE_KEY = os.environ.get(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVmYmZ6aXFlYmhxaGZtampwbmxjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYxMjM3MzEsImV4cCI6MjA5MTY5OTczMX0.hUMijAlIoHC51ddEBZSLWoy4Tg1fMiDlRyG47omIVDo",
)

KSTARTUP_URL = (
    "https://nidapi.k-startup.go.kr/api/kisedKstartupService/v1/getAnnouncementInformation"
)

DEFAULT_CTA_LABEL = "무상지원금 1억 받기 로드맵 무료 세미나 신청하기"
DEFAULT_CTA_URL = "https://example.com"

CACHE_TTL = 1800
cache = {"grants": [], "updated": 0, "loading": False}


# ============================================================
# Supabase REST 헬퍼
# ============================================================
def _sb_headers(prefer=None):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def sb_get(table, params=None):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params=params or {},
        headers=_sb_headers(),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def sb_insert(table, data, prefer="return=representation"):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        json=data,
        headers=_sb_headers(prefer=prefer),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def sb_update(table, params, data, prefer="return=representation"):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params=params,
        json=data,
        headers=_sb_headers(prefer=prefer),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def sb_delete(table, params):
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params=params,
        headers=_sb_headers(),
        timeout=15,
    )
    r.raise_for_status()
    return True


def sb_upsert(table, data, on_conflict):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params={"on_conflict": on_conflict},
        json=data,
        headers=_sb_headers(prefer="resolution=merge-duplicates,return=representation"),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ============================================================
# DB 조회 함수 (Supabase 기반)
# ============================================================
def db_get_user_by_email(email):
    rows = sb_get("users", {"email": f"eq.{email}", "limit": 1})
    return rows[0] if rows else None


def db_get_user_by_id(uid):
    rows = sb_get("users", {"id": f"eq.{uid}", "limit": 1})
    return rows[0] if rows else None


def db_create_user(email, name, company, password_hash, reason):
    data = {
        "email": email,
        "name": name,
        "company": company or None,
        "password_hash": password_hash,
        "role": "user",
        "status": "pending",
        "reason": reason or None,
    }
    result = sb_insert("users", data)
    return result[0] if result else None


def db_update_user(uid, **fields):
    sb_update("users", {"id": f"eq.{uid}"}, fields)


def db_delete_user(uid):
    sb_delete("users", {"id": f"eq.{uid}"})


def db_list_users(status=None):
    params = {"order": "created_at.desc"}
    if status:
        params["status"] = f"eq.{status}"
    return sb_get("users", params)


def db_count_users_by_status():
    rows = sb_get("users", {"select": "status"})
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts


def db_get_setting(key, default=""):
    rows = sb_get("settings", {"key": f"eq.{key}", "limit": 1})
    return rows[0]["value"] if rows else default


def db_set_setting(key, value):
    sb_upsert(
        "settings",
        {"key": key, "value": value, "updated_at": datetime.utcnow().isoformat()},
        on_conflict="key",
    )


# ============================================================
# 인증 헬퍼
# ============================================================
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    try:
        return db_get_user_by_id(uid)
    except Exception as e:
        print(f"[ERROR] current_user: {e}")
        return None


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login", next=request.path))
        if u["status"] != "approved":
            return redirect(url_for("pending"))
        return view(*a, **kw)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        u = current_user()
        if not u:
            return redirect(url_for("login", next=request.path))
        if u["role"] != "admin":
            abort(403)
        return view(*a, **kw)

    return wrapped


@app.context_processor
def inject_user():
    try:
        cta_url = db_get_setting("cta_url", DEFAULT_CTA_URL)
        cta_label = db_get_setting("cta_label", DEFAULT_CTA_LABEL)
    except Exception:
        cta_url, cta_label = DEFAULT_CTA_URL, DEFAULT_CTA_LABEL
    return {
        "current_user": current_user(),
        "cta_url": cta_url,
        "cta_label": cta_label,
    }


# ============================================================
# 라우트: 인증
# ============================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        try:
            user = db_get_user_by_email(email)
        except Exception as e:
            print(f"[ERROR] login: {e}")
            user = None
        if not user or not check_password_hash(user["password_hash"], password):
            error = "이메일 또는 비밀번호가 올바르지 않습니다."
        else:
            session.permanent = True
            session["uid"] = user["id"]
            if user["status"] != "approved":
                return redirect(url_for("pending"))
            nxt = request.args.get("next") or "/"
            if not nxt.startswith("/"):
                nxt = "/"
            return redirect(nxt)
    return render_template("login.html", error=error)


@app.route("/signup")
def signup():
    return redirect(url_for("login"))


@app.route("/pending")
def pending():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    if u["status"] == "approved":
        return redirect(url_for("index"))
    return render_template("pending.html", user=u)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# 관리자
# ============================================================
@app.route("/admin")
@admin_required
def admin():
    tab = request.args.get("tab", "pending")
    if tab not in ("pending", "approved", "rejected", "all"):
        tab = "pending"

    if tab == "all":
        users = db_list_users()
    else:
        users = db_list_users(status=tab)
    counts = db_count_users_by_status()

    return render_template(
        "admin.html",
        users=users,
        tab=tab,
        counts=counts,
        total=sum(counts.values()),
        cta_url_current=db_get_setting("cta_url", DEFAULT_CTA_URL),
        cta_label_current=db_get_setting("cta_label", DEFAULT_CTA_LABEL),
    )


@app.route("/admin/action", methods=["POST"])
@admin_required
def admin_action():
    uid = request.form.get("uid", type=int)
    action = request.form.get("action")
    me = current_user()
    if not uid or action not in (
        "approve", "reject", "delete", "make_admin", "make_user",
    ):
        abort(400)
    if uid == me["id"] and action in ("delete", "make_user"):
        return redirect(url_for("admin", tab=request.form.get("tab", "pending")))

    if action == "approve":
        db_update_user(uid, status="approved", approved_at=datetime.utcnow().isoformat(), approved_by=me["id"])
    elif action == "reject":
        db_update_user(uid, status="rejected")
    elif action == "delete":
        db_delete_user(uid)
    elif action == "make_admin":
        db_update_user(uid, role="admin")
    elif action == "make_user":
        db_update_user(uid, role="user")
    return redirect(url_for("admin", tab=request.form.get("tab", "pending")))


@app.route("/admin/create_user", methods=["POST"])
@admin_required
def admin_create_user():
    email = (request.form.get("email") or "").strip().lower()
    name = (request.form.get("name") or "").strip()
    company = (request.form.get("company") or "").strip()
    password = request.form.get("password") or ""
    role = request.form.get("role") or "user"

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return redirect(url_for("admin", tab="approved") + "?err=email")
    if len(password) < 6:
        return redirect(url_for("admin", tab="approved") + "?err=pw")
    if not name:
        return redirect(url_for("admin", tab="approved") + "?err=name")
    if role not in ("user", "admin"):
        role = "user"

    try:
        if db_get_user_by_email(email):
            return redirect(url_for("admin", tab="approved") + "?err=exists")
    except Exception:
        pass

    me = current_user()
    data = {
        "email": email,
        "name": name,
        "company": company or None,
        "password_hash": generate_password_hash(password),
        "role": role,
        "status": "approved",
        "approved_at": datetime.utcnow().isoformat(),
        "approved_by": me["id"] if me else None,
    }
    sb_insert("users", data)
    return redirect(url_for("admin", tab="approved"))


@app.route("/admin/cta", methods=["POST"])
@admin_required
def admin_cta():
    url = (request.form.get("cta_url") or "").strip()
    label = (request.form.get("cta_label") or "").strip()
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    if url:
        db_set_setting("cta_url", url)
    if label:
        db_set_setting("cta_label", label)
    return redirect(url_for("admin", tab=request.form.get("tab", "pending")) + "#cta")


# ============================================================
# 정부지원사업 데이터
# ============================================================
def parse_ymd(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def format_amount(won):
    if not won:
        return None
    if won >= 100_000_000:
        v = won / 100_000_000
        return f"{v:.1f}억원" if v != int(v) else f"{int(v)}억원"
    if won >= 10_000:
        return f"{won // 10_000:,}만원"
    return f"{won:,}원"


AMOUNT_PATTERNS = [
    (re.compile(r"(\d+(?:[,\.]\d+)?)\s*억\s*원"), 100_000_000),
    (re.compile(r"(\d+(?:,\d{3})*)\s*만\s*원"), 10_000),
    (re.compile(r"(\d+(?:,\d{3})*)\s*천\s*만\s*원"), 10_000_000),
]


def extract_amount(text):
    if not text:
        return 0
    best = 0
    for pat, mult in AMOUNT_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                v = float(raw)
                won = int(v * mult)
                if won > best:
                    best = won
            except ValueError:
                pass
    return best


def compute_dday(end_date):
    if not end_date:
        return None
    return (end_date - date.today()).days


def status_from(end_date, recruit_flag):
    dday = compute_dday(end_date)
    if recruit_flag == "N" and (dday is None or dday < 0):
        return "마감", dday
    if dday is None:
        return "상시", None
    if dday < 0:
        return "마감", dday
    if dday <= 3:
        return "마감임박", dday
    return "모집중", dday


def clean_html_entities(s):
    if not s:
        return ""
    return (
        s.replace("&#40;", "(")
        .replace("&#41;", ")")
        .replace("&amp;", "&")
        .replace("\r\n", "\n")
    )


def fetch_kstartup_page(page=1, per_page=100):
    params = {
        "serviceKey": KSTARTUP_API_KEY,
        "page": page,
        "perPage": per_page,
        "returnType": "JSON",
    }
    try:
        r = requests.get(KSTARTUP_URL, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ERROR] K-Startup page {page}: {e}")
        return {}


def fetch_all_kstartup(max_pages=5):
    all_items = []
    for page in range(1, max_pages + 1):
        data = fetch_kstartup_page(page=page, per_page=100)
        items = data.get("data", [])
        if not items:
            break
        all_items.extend(items)
        if len(items) < 100:
            break
        time.sleep(0.2)
    return all_items


def normalize(item):
    title = clean_html_entities(
        item.get("intg_pbanc_biz_nm") or item.get("biz_pbanc_nm") or ""
    )
    content = clean_html_entities(item.get("pbanc_ctnt") or "")
    target_content = clean_html_entities(item.get("aply_trgt_ctnt") or "")
    category_raw = clean_html_entities(item.get("supt_biz_clsfc") or "기타")

    start_date = parse_ymd(item.get("pbanc_rcpt_bgng_dt"))
    end_date = parse_ymd(item.get("pbanc_rcpt_end_dt"))

    status, dday = status_from(end_date, item.get("rcrt_prgs_yn"))

    amount_won = extract_amount(title + " " + content + " " + target_content)
    amount_display = format_amount(amount_won) or "공고 확인 필요"

    category = category_raw
    region = item.get("supt_regin") or "전국"
    enyy = item.get("biz_enyy") or ""

    link = item.get("detl_pg_url") or ""
    apply_url = (
        item.get("biz_aply_url")
        or item.get("aply_mthd_onli_rcpt_istc")
        or item.get("biz_gdnc_url")
        or ""
    )
    if apply_url and not apply_url.startswith("http"):
        apply_url = "https://" + apply_url

    period_raw = ""
    if start_date and end_date:
        period_raw = f"{start_date.isoformat()} ~ {end_date.isoformat()}"
    elif end_date:
        period_raw = f"~ {end_date.isoformat()}"

    hashtags = ", ".join(
        t for t in [category, region, enyy, item.get("sprv_inst") or ""]
        if t and t.strip()
    )

    return {
        "id": f"ks_{item.get('pbanc_sn') or item.get('id')}",
        "title": title,
        "organization": item.get("pbanc_ntrp_nm") or item.get("sprv_inst") or "기관정보없음",
        "description": content[:600],
        "category": category,
        "region": region,
        "enyy": enyy,
        "target": target_content[:200],
        "period_raw": period_raw,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "dday": dday,
        "status": status,
        "amount_won": amount_won,
        "amount_display": amount_display,
        "link": link,
        "apply_url": apply_url or link,
        "hashtags": hashtags,
        "source": "kstartup",
    }


def refresh_cache():
    if cache["loading"]:
        return
    cache["loading"] = True
    try:
        raw = fetch_all_kstartup(max_pages=5)
        seen = set()
        results = []
        for it in raw:
            grant = normalize(it)
            if grant["id"] in seen or not grant["title"]:
                continue
            seen.add(grant["id"])
            results.append(grant)

        def sort_key(g):
            d = g["dday"]
            if d is not None and d >= 0:
                return (0, d)
            if d is None:
                return (1, 0)
            return (2, -d)

        results.sort(key=sort_key)
        cache["grants"] = results
        cache["updated"] = time.time()
        print(f"[INFO] 도원결의 캐시 갱신: {len(results)}건 (원본 {len(raw)}건)")
    finally:
        cache["loading"] = False


def ensure_cache():
    if not cache["grants"] or (time.time() - cache["updated"] > CACHE_TTL):
        refresh_cache()


# ============================================================
# 메인 라우트
# ============================================================
@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/grants")
@login_required
def api_grants():
    ensure_cache()
    grants = list(cache["grants"])

    q = (request.args.get("q") or "").strip().lower()
    category = request.args.get("category") or ""
    status = request.args.get("status") or ""

    if q:
        grants = [
            g
            for g in grants
            if q in g["title"].lower()
            or q in g["description"].lower()
            or q in g["organization"].lower()
            or q in g["hashtags"].lower()
        ]
    if category:
        grants = [g for g in grants if g["category"] == category]
    if status:
        grants = [g for g in grants if g["status"] == status]

    total_budget = sum(g["amount_won"] for g in cache["grants"])
    closing_soon = sum(
        1 for g in cache["grants"] if g["dday"] is not None and 0 <= g["dday"] <= 7
    )
    open_count = sum(
        1 for g in cache["grants"] if g["status"] in ("모집중", "마감임박")
    )
    categories = sorted({g["category"] for g in cache["grants"] if g["category"]})

    return jsonify(
        {
            "grants": grants,
            "total": len(grants),
            "stats": {
                "all_count": len(cache["grants"]),
                "open_count": open_count,
                "closing_soon": closing_soon,
                "total_budget": total_budget,
                "total_budget_display": format_amount(total_budget) or "집계중",
                "updated": cache["updated"],
            },
            "categories": categories,
        }
    )


@app.route("/api/calendar")
@login_required
def api_calendar():
    ensure_cache()
    year = int(request.args.get("year", date.today().year))
    month = int(request.args.get("month", date.today().month))

    events_by_date = {}
    for g in cache["grants"]:
        end = parse_ymd(g["end_date"])
        if not end or end.year != year or end.month != month:
            continue
        key = end.isoformat()
        events_by_date.setdefault(key, []).append(
            {
                "id": g["id"],
                "title": g["title"],
                "organization": g["organization"],
                "amount_display": g["amount_display"],
                "dday": g["dday"],
                "status": g["status"],
                "category": g["category"],
                "link": g["link"],
            }
        )

    for k in events_by_date:
        events_by_date[k].sort(key=lambda e: -(e.get("dday") or 0))

    return jsonify({"year": year, "month": month, "events": events_by_date})


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    threading.Thread(target=refresh_cache, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return jsonify({"ok": True, "supabase": bool(SUPABASE_URL)})


# ============================================================
# 시작
# ============================================================
threading.Thread(target=refresh_cache, daemon=True).start()


if __name__ == "__main__":
    print("\n=== 도원결의 (桃園結義) — Supabase 기반 ===")
    print(f"Supabase: {SUPABASE_URL}")
    print("http://127.0.0.1:5055\n")
    app.run(host="127.0.0.1", port=5055, debug=False)
