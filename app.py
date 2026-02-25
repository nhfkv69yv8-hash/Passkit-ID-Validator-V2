import os
import time
import requests
import jwt  # PyJWT
import pandas as pd
import streamlit as st


# ---------------------------
# Config helpers
# ---------------------------

# 1. 基礎設定
st.set_page_config(page_title="PassKit 批次檢索 (REST版)", page_icon="🚀")

def get_config(key):
    val = st.secrets.get(key) or os.environ.get(key)
    # 修正截圖中提到的 'int' object has no attribute 'replace' 錯誤
    return str(val).replace('\\n', '\n') if val else None

# --- 2. JWT 認證生成 ---
def get_auth_header():
    key = get_config("PK_API_KEY")
    secret = get_config("PK_API_SECRET")
    if not key or not secret:
        st.error("❌ 請確保 Secrets 中已添加 PK_API_KEY 和 PK_API_SECRET")
        return None
    
    # 建立 PassKit 要求的 JWT 格式
    payload = {
        "iss": key,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def passkit_list_members_filtered(
    rest_base: str,
    program_id: str,
    api_key: str,
    api_secret: str,
    display_names: list[str],
    limit: int = 1000,
    offset: int = 0,
    operator: str = "eq",   # "eq" or "like"
) -> list[dict]:
    """
    Call:
      POST {REST_BASE}/members/member/list/{PROGRAM_ID}
    with filters.filterGroups using OR on displayName.
    """
    token = build_jwt_token(api_key, api_secret, ttl_seconds=60)

    url = f"{rest_base.rstrip('/')}/members/member/list/{program_id}"

    # OR 條件：一次把最多 50 個名字丟進 fieldFilters
    field_filters = []
    for name in display_names:
        field_filters.append({
            "filterField": "displayName",
            "filterValue": name,
            "filterOperator": operator,
        })

    body = {
        "filters": {
            "limit": int(limit),
            "offset": int(offset),
            "orderBy": "created",
            "orderAsc": True,
            "filterGroups": [
                {
                    "condition": "OR",
                    "fieldFilters": field_filters
                }
            ],
        }
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(url, headers=headers, json=body, timeout=60)
    # 直接把錯誤訊息吐清楚，方便你在 Render log 看
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:1200]}")

    data = resp.json()

    # PassKit 的 list response 在不同版本可能是：
    # - {"members":[...], "count":...}
    # - {"results":[...]}
    # 這裡做兼容
    if isinstance(data, dict):
        if "members" in data and isinstance(data["members"], list):
            return data["members"]
        if "results" in data and isinstance(data["results"], list):
            return data["results"]
        if "data" in data and isinstance(data["data"], list):
            return data["data"]

    # 萬一回傳不是上述格式
    raise RuntimeError(f"Unexpected response shape: {str(data)[:800]}")


def normalize_name(s: str) -> str:
    return " ".join(s.strip().upper().split())


# ---------------------------
# UI
# ---------------------------
st.set_page_config(page_title="PassKit ID Validator (REST)", page_icon="🔎")
st.title("🔎 批次查詢 PassKit Member ID（最多 50 個姓名）")
st.caption("每行貼一個 displayName（person.displayName）。用 REST filter 一次查，不掃全量。")

rest_base = get_config("REST_BASE")
api_key = get_config("PK_API_KEY")
api_secret = get_config("PK_API_SECRET")
program_id = get_config("PROGRAM_ID")

with st.expander("✅ 目前環境變數檢查", expanded=False):
    st.write({
        "REST_BASE": rest_base,
        "PROGRAM_ID": program_id,
        "PK_API_KEY": "(set)" if api_key else "(missing)",
        "PK_API_SECRET": "(set)" if api_secret else "(missing)",
    })

input_text = st.text_area(
    "每行一個 full name（displayName）",
    height=260,
    placeholder="HSIUTING CHOU\nKUANYEN LEE\n..."
)

col1, col2, col3 = st.columns([1, 1, 2])
with col1:
    operator = st.selectbox("比對方式", ["eq", "like"], index=0)
with col2:
    limit = st.number_input("limit（<=1000）", min_value=1, max_value=1000, value=1000, step=50)
with col3:
    st.write("")

btn = st.button("Search", type="primary")

if btn:
    if not all([rest_base, api_key, api_secret, program_id]):
        st.error("缺少必要設定：REST_BASE / PK_API_KEY / PK_API_SECRET / PROGRAM_ID")
        st.stop()

    raw_names = [line for line in input_text.splitlines() if line.strip()]
    names = [normalize_name(x) for x in raw_names][:50]

    if not names:
        st.warning("請至少輸入 1 個姓名")
        st.stop()

    with st.spinner("查詢中（REST filter）..."):
        try:
            members = passkit_list_members_filtered(
                rest_base=rest_base,
                program_id=program_id,
                api_key=api_key,
                api_secret=api_secret,
                display_names=names,
                limit=int(limit),
                offset=0,
                operator=operator,
            )
        except Exception as e:
            st.error(f"查詢失敗：{e}")
            st.stop()

    # 解析回傳
    rows = []
    hits = set()

    for m in members:
        # 兼容 key 命名：有的回傳 id / memberId
        mid = m.get("id") or m.get("memberId") or ""
        person = m.get("person") or {}
        display = person.get("displayName") or ""
        sal = person.get("salutation") or ""

        disp_norm = normalize_name(display) if display else ""
        # 只收：剛好命中的名字（eq）或包含（like）也要回
        if operator == "eq":
            if disp_norm in names:
                hits.add(disp_norm)
                rows.append({
                    "person.salutation": sal,
                    "person.displayName": display,
                    "member.id": mid,
                })
        else:
            # like：只要回傳結果裡的 displayName 對任何輸入字串包含即可
            for target in names:
                if target and target in disp_norm:
                    hits.add(target)
                    rows.append({
                        "person.salutation": sal,
                        "person.displayName": display,
                        "member.id": mid,
                    })
                    break

    st.success(f"完成：輸入 {len(names)} 個姓名，回傳 {len(members)} 筆候選，命中 {len(rows)} 筆。")

    if rows:
        df = pd.DataFrame(rows)
        # 欄位順序固定
        df = df[["person.salutation", "person.displayName", "member.id"]]
        st.dataframe(df, use_container_width=True)

    missing = [n for n in names if n not in hits]
    if missing:
        with st.expander(f"❌ 未找到名單（{len(missing)}）"):
            st.write("\n".join(missing))
