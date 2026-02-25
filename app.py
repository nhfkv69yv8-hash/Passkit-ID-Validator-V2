import os
import time
import json
import hashlib
import requests
import jwt  # from PyJWT
import pandas as pd
import streamlit as st

# ----------------------------
# Page
# ----------------------------
st.set_page_config(page_title="PassKit 會員批次檢索 (REST + JWT)", page_icon="🔍")
st.title("🔍 PassKit 會員批次檢索（REST + JWT）")
st.caption("每行貼一個 full name（PassKit: person.displayName），最多 50 行。用 REST Filter 查，不掃全量。")

# ----------------------------
# Config helpers
# ----------------------------
def get_config(key: str, default: str | None = None) -> str | None:
    val = st.secrets.get(key) if hasattr(st, "secrets") else None
    if val is None:
        val = os.environ.get(key, default)
    if val is None:
        return None
    # keep \n handling in case someone pastes multi-line values in secrets
    return str(val).replace("\\n", "\n").strip()

PK_API_KEY = get_config("PK_API_KEY")
PK_API_SECRET = get_config("PK_API_SECRET")
PK_API_PREFIX = get_config("PK_API_PREFIX", "https://api.pub1.passkit.io")
PROGRAM_ID = get_config("PROGRAM_ID")

missing_cfg = [k for k, v in {
    "PK_API_KEY": PK_API_KEY,
    "PK_API_SECRET": PK_API_SECRET,
    "PK_API_PREFIX": PK_API_PREFIX,
    "PROGRAM_ID": PROGRAM_ID
}.items() if not v]

if missing_cfg:
    st.error(f"❌ 缺少設定：{', '.join(missing_cfg)}（請在 .env 或 Secrets 補上）")
    st.stop()

# ----------------------------
# JWT auth (PassKit style)
# - payload uses uid, iat, exp
# - optional signature = SHA256(request body) for POST with body
# - header Authorization = <jwt>  (NO 'Bearer ')
# ----------------------------
def make_jwt_for_body(body_text: str) -> str:
    now = int(time.time())
    payload = {
        "uid": PK_API_KEY,
        "iat": now,
        "exp": now + 600,  # 10 minutes is typical for PassKit examples
    }

    if body_text:
        payload["signature"] = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

    token = jwt.encode(payload, PK_API_SECRET, algorithm="HS256")
    # PyJWT may return bytes in older versions; normalize
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token

def post_list_members(filters_payload: dict) -> list[dict]:
    """
    Calls:
      POST {PK_API_PREFIX}/members/member/list/{PROGRAM_ID}
    Returns:
      list of result objects (each line may be a JSON object)
    """
    url = f"{PK_API_PREFIX.rstrip('/')}/members/member/list/{PROGRAM_ID}"
    body_text = json.dumps({"filters": filters_payload}, separators=(",", ":"), ensure_ascii=False)

    token = make_jwt_for_body(body_text)
    headers = {
        "Authorization": token,  # PassKit examples: token directly, not Bearer
        "Content-Type": "application/json",
    }

    resp = requests.post(url, headers=headers, data=body_text, timeout=30)

    # Common failure hints
    if resp.status_code == 404:
        raise RuntimeError(
            "404 Not Found：多半是 API Prefix 用錯（pub1/pub2），或 endpoint path 拼錯。"
        )
    if resp.status_code in (401, 403):
        raise RuntimeError(
            f"Auth 失敗（{resp.status_code}）：請確認 PK_API_KEY/PK_API_SECRET、以及 API Prefix（pub1/pub2）。"
        )
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    # PassKit list APIs sometimes return NDJSON (one JSON per line)
    text = resp.text.strip()
    if not text:
        return []

    items: list[dict] = []
    # Try NDJSON first
    lines = [ln for ln in text.split("\n") if ln.strip()]
    for ln in lines:
        try:
            items.append(json.loads(ln))
        except json.JSONDecodeError:
            # maybe it's a single JSON
            items = [json.loads(text)]
            break

    return items

def extract_member_rows(list_response_items: list[dict], search_name: str, max_hits: int) -> list[dict]:
    """
    Each item is typically:
      { "result": { ...member... } , ... }
    We'll extract: person.displayName, id
    """
    rows = []
    for item in list_response_items:
        member = item.get("result") or item.get("member") or item
        if not isinstance(member, dict):
            continue

        person = member.get("person") or {}
        display_name = (person.get("displayName") or "").strip()
        member_id = (member.get("id") or "").strip()

        if display_name and member_id:
            rows.append({
                "搜尋姓名": search_name,
                "displayName (person.displayName)": display_name,
                "memberId (member.id)": member_id,
            })

        if len(rows) >= max_hits:
            break
    return rows

def search_by_display_name(name: str, max_hits: int, operator: str) -> list[dict]:
    # REST filter fields: displayName, operators: eq / like, etc. :contentReference[oaicite:2]{index=2}
    filters = {
        "limit": min(max_hits, 1000),
        "offset": 0,
        "filterGroups": [{
            "condition": "AND",
            "fieldFilters": [{
                "filterField": "displayName",
                "filterValue": name,
                "filterOperator": operator,  # "eq" or "like"
            }]
        }]
    }
    items = post_list_members(filters)
    return extract_member_rows(items, name, max_hits=max_hits)

# ----------------------------
# UI
# ----------------------------
with st.form("search_form"):
    input_text = st.text_area(
        "每行一個 full name（person.displayName）— 最多 50 行",
        height=220,
        placeholder="HSIUTING CHOU\nKUANYEN LEE\n..."
    )

    colA, colB, colC = st.columns([1, 1, 2])
    with colA:
        max_hits = st.number_input("同名最多回傳筆數", min_value=1, max_value=50, value=5, step=1)
    with colB:
        operator = st.selectbox("比對方式", options=["eq", "like"], index=0)
    with colC:
        st.caption("eq = 完全相同；like = 包含（較鬆，可能會回更多結果）")

    submitted = st.form_submit_button("Search")

if submitted:
    names = [n.strip() for n in (input_text or "").splitlines() if n.strip()]
    if not names:
        st.warning("請先貼上至少一行姓名。")
        st.stop()

    if len(names) > 50:
        st.warning(f"你貼了 {len(names)} 行，系統只會取前 50 行。")
        names = names[:50]

    all_rows = []
    missing = []

    prog = st.progress(0)
    status = st.empty()

    for i, name in enumerate(names, start=1):
        status.info(f"查詢中 {i}/{len(names)}：{name}")
        try:
            rows = search_by_display_name(name, max_hits=int(max_hits), operator=operator)
            if rows:
                all_rows.extend(rows)
            else:
                missing.append(name)
        except Exception as e:
            st.error(f"❌ 查詢失敗：{name} → {e}")
            missing.append(name)

        prog.progress(i / len(names))

    status.empty()
    prog.empty()

    st.success(f"完成：查詢 {len(names)} 筆，命中 {len(all_rows)} 筆。")

    import streamlit.components.v1 as components

def _copy_js(text: str):
    safe = (
        text.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
    )
    components.html(
        f"""
        <script>
          (async function() {{
            try {{
              await navigator.clipboard.writeText(`{safe}`);
            }} catch (e) {{
              console.log(e);
            }}
          }})();
        </script>
        """,
        height=0,
    )

def render_results_table(display_rows: list[dict]):
    # 表頭
    h1, h2, h3, h4 = st.columns([2.2, 2.2, 3.4, 1.2])
    h1.markdown("**搜尋姓名**")
    h2.markdown("**會員姓名**")
    h3.markdown("**Passkit ID**")
    h4.markdown("")
    st.divider()

    for idx, r in enumerate(display_rows):
        search_name = r.get("搜尋姓名", "")
        member_name = r.get("會員姓名", "")
        pid = r.get("Passkit ID", "")

        row_key = f"copied::{pid}::{idx}"
        copied = st.session_state.get(row_key, False)

        c1, c2, c3, c4 = st.columns([2.2, 2.2, 3.4, 1.2])
        c1.write(search_name)
        c2.write(member_name)

        bg = "#f1f3f5" if copied else "#ffffff"
        border = "#d0d7de"

        c3.markdown(
            f"""
            <div style="
                background:{bg};
                border:1px solid {border};
                padding:10px 12px;
                border-radius:10px;
                font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
                font-size: 14px;
                word-break: break-all;
            ">{pid}</div>
            """,
            unsafe_allow_html=True,
        )

        if c4.button("Copy", key=f"btncopy::{pid}::{idx}"):
            _copy_js(pid)
            st.session_state[row_key] = True
            st.toast("已複製 Passkit ID ✅", icon="📋")
            st.rerun()

# ========= 你的原本 all_rows 結果處理：改成下面這段 =========
    if all_rows:
        # 轉成你要的三欄
        display_rows = []
        for x in all_rows:
        display_rows.append({
            "搜尋姓名": x.get("搜尋姓名", ""),
            "會員姓名": x.get("displayName (person.displayName)", x.get("會員姓名", "")),
            "Passkit ID": x.get("memberId (member.id)", x.get("Passkit ID", "")),
        })

    render_results_table(display_rows)

    # CSV 下載
    df = pd.DataFrame(display_rows)[["搜尋姓名", "會員姓名", "Passkit ID"]]
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "下載 CSV",
        data=csv,
        file_name="passkit_member_ids.csv",
        mime="text/csv",
    )
    else:
    st.warning("沒有找到符合名單的會員。")
