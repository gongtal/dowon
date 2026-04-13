"""도원결의 (桃園結義) - 정부지원사업 통합 플랫폼 (Flask 백엔드)"""

import os
import re
import time
import threading
from datetime import datetime, date
from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__, template_folder="templates", static_folder="static")

KSTARTUP_API_KEY = os.environ.get(
    "KSTARTUP_API_KEY",
    "39af9bc6585e1db2baec17245fe6d556486148d45fe88aa213e10043604d343b",
)

KSTARTUP_URL = (
    "https://nidapi.k-startup.go.kr/api/kisedKstartupService/v1/getAnnouncementInformation"
)

CACHE_TTL = 1800
cache = {"grants": [], "updated": 0, "loading": False}


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
        return (f"{v:.1f}억원" if v != int(v) else f"{int(v)}억원")
    if won >= 10_000:
        return f"{won // 10_000:,}만원"
    return f"{won:,}원"


AMOUNT_PATTERNS = [
    (re.compile(r"(\d+(?:[,\.]\d+)?)\s*억\s*원"), 100_000_000),
    (re.compile(r"(\d+(?:,\d{3})*)\s*만\s*원"), 10_000),
    (re.compile(r"(\d+(?:,\d{3})*)\s*천\s*만\s*원"), 10_000_000),
]


def extract_amount(text):
    """공고 텍스트에서 지원금액 숫자를 추출해 원 단위로 반환."""
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
        t
        for t in [category, region, enyy, item.get("sprv_inst") or ""]
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/grants")
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
def api_refresh():
    threading.Thread(target=refresh_cache, daemon=True).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("\n=== 도원결의 (桃園結義) — 정부지원사업 통합 플랫폼 ===")
    print(f"K-Startup API 키: {'설정됨' if KSTARTUP_API_KEY else '미설정'}")
    print("http://127.0.0.1:5055 에서 접속하세요\n")
    threading.Thread(target=refresh_cache, daemon=True).start()
    app.run(host="127.0.0.1", port=5055, debug=False)
