"""도원결의 (桃園結義) — 정부지원사업 통합 플랫폼 + 회원/관리자 승인 시스템"""

import os
import re
import time
import sqlite3
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
    flash,
    g,
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
DB_PATH = os.environ.get("DB_PATH", "dowon.db")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@dowon.kr")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "dowon157600!")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "관리자")

KSTARTUP_URL = (
    "https://nidapi.k-startup.go.kr/api/kisedKstartupService/v1/getAnnouncementInformation"
)

CACHE_TTL = 1800
cache = {"grants": [], "updated": 0, "loading": False}


# ============================================================
# DB
# ============================================================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


DEFAULT_CTA_LABEL = "무상지원금 1억 받기 로드맵 무료 세미나 신청하기"
DEFAULT_CTA_URL = "https://example.com"


def init_db():
    """DB 스키마 생성 + 기본 관리자/설정 보정."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            company TEXT,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'pending',
            reason TEXT,
            created_at TEXT NOT NULL,
            approved_at TEXT,
            approved_by INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    now = datetime.utcnow().isoformat()
    for k, v in (("cta_url", DEFAULT_CTA_URL), ("cta_label", DEFAULT_CTA_LABEL)):
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (k, v, now),
        )
    conn.commit()

    cur = conn.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,))
    if not cur.fetchone():
        conn.execute(
            """INSERT INTO users (email, name, password_hash, role, status, created_at, approved_at)
               VALUES (?, ?, ?, 'admin', 'approved', ?, ?)""",
            (
                ADMIN_EMAIL,
                ADMIN_NAME,
                generate_password_hash(ADMIN_PASSWORD),
                datetime.utcnow().isoformat(),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
        print(f"[INFO] 기본 관리자 계정 생성: {ADMIN_EMAIL}")

    conn.close()


# ============================================================
# 인증 헬퍼
# ============================================================
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else None


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


def get_setting(key, default=""):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, datetime.utcnow().isoformat()),
    )
    db.commit()


@app.context_processor
def inject_user():
    return {
        "current_user": current_user(),
        "cta_url": get_setting("cta_url", DEFAULT_CTA_URL),
        "cta_label": get_setting("cta_label", DEFAULT_CTA_LABEL),
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
        row = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            error = "이메일 또는 비밀번호가 올바르지 않습니다."
        else:
            session.permanent = True
            session["uid"] = row["id"]
            if row["status"] != "approved":
                return redirect(url_for("pending"))
            nxt = request.args.get("next") or "/"
            if not nxt.startswith("/"):
                nxt = "/"
            return redirect(nxt)
    return render_template("login.html", error=error)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("index"))

    error = None
    form = {"email": "", "name": "", "company": "", "reason": ""}
    if request.method == "POST":
        form["email"] = (request.form.get("email") or "").strip().lower()
        form["name"] = (request.form.get("name") or "").strip()
        form["company"] = (request.form.get("company") or "").strip()
        form["reason"] = (request.form.get("reason") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", form["email"]):
            error = "올바른 이메일 주소를 입력해주세요."
        elif len(password) < 6:
            error = "비밀번호는 6자 이상이어야 합니다."
        elif password != password2:
            error = "비밀번호가 일치하지 않습니다."
        elif not form["name"]:
            error = "이름을 입력해주세요."
        else:
            db = get_db()
            exists = db.execute("SELECT id FROM users WHERE email = ?", (form["email"],)).fetchone()
            if exists:
                error = "이미 가입된 이메일입니다."
            else:
                db.execute(
                    """INSERT INTO users (email, name, company, password_hash, role, status, reason, created_at)
                       VALUES (?, ?, ?, ?, 'user', 'pending', ?, ?)""",
                    (
                        form["email"],
                        form["name"],
                        form["company"],
                        generate_password_hash(password),
                        form["reason"],
                        datetime.utcnow().isoformat(),
                    ),
                )
                db.commit()
                new_id = db.execute("SELECT id FROM users WHERE email = ?", (form["email"],)).fetchone()["id"]
                session.permanent = True
                session["uid"] = new_id
                return redirect(url_for("pending"))
    return render_template("signup.html", error=error, form=form)


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
# 관리자 페이지
# ============================================================
@app.route("/admin")
@admin_required
def admin():
    tab = request.args.get("tab", "pending")
    if tab not in ("pending", "approved", "rejected", "all"):
        tab = "pending"

    if tab == "all":
        rows = get_db().execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    else:
        rows = (
            get_db()
            .execute(
                "SELECT * FROM users WHERE status = ? ORDER BY created_at DESC",
                (tab,),
            )
            .fetchall()
        )
    counts = {
        r["status"]: r["n"]
        for r in get_db().execute(
            "SELECT status, COUNT(*) AS n FROM users GROUP BY status"
        )
    }
    return render_template(
        "admin.html",
        users=[dict(r) for r in rows],
        tab=tab,
        counts=counts,
        total=sum(counts.values()),
        cta_url_current=get_setting("cta_url", DEFAULT_CTA_URL),
        cta_label_current=get_setting("cta_label", DEFAULT_CTA_LABEL),
    )


@app.route("/admin/cta", methods=["POST"])
@admin_required
def admin_cta():
    url = (request.form.get("cta_url") or "").strip()
    label = (request.form.get("cta_label") or "").strip()
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    if url:
        set_setting("cta_url", url)
    if label:
        set_setting("cta_label", label)
    return redirect(url_for("admin", tab=request.form.get("tab", "pending")) + "#cta")


@app.route("/admin/action", methods=["POST"])
@admin_required
def admin_action():
    uid = request.form.get("uid", type=int)
    action = request.form.get("action")
    me = current_user()
    if not uid or action not in ("approve", "reject", "delete", "make_admin", "make_user"):
        abort(400)
    if uid == me["id"] and action in ("delete", "make_user"):
        flash("본인 계정은 해당 작업을 할 수 없습니다.", "error")
        return redirect(url_for("admin", tab=request.form.get("tab", "pending")))

    db = get_db()
    if action == "approve":
        db.execute(
            "UPDATE users SET status='approved', approved_at=?, approved_by=? WHERE id=?",
            (datetime.utcnow().isoformat(), me["id"], uid),
        )
    elif action == "reject":
        db.execute("UPDATE users SET status='rejected' WHERE id=?", (uid,))
    elif action == "delete":
        db.execute("DELETE FROM users WHERE id=?", (uid,))
    elif action == "make_admin":
        db.execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
    elif action == "make_user":
        db.execute("UPDATE users SET role='user' WHERE id=?", (uid,))
    db.commit()
    return redirect(url_for("admin", tab=request.form.get("tab", "pending")))


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


# ============================================================
# 실행
# ============================================================
init_db()
threading.Thread(target=refresh_cache, daemon=True).start()


if __name__ == "__main__":
    print("\n=== 도원결의 (桃園結義) ===")
    print(f"DB: {DB_PATH}")
    print(f"관리자: {ADMIN_EMAIL}")
    print("http://127.0.0.1:5055\n")
    app.run(host="127.0.0.1", port=5055, debug=False)
