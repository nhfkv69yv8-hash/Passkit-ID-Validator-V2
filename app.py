import os
import re
import time
from typing import Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st

# ---- Optional OCR (screenshot -> names) ----
# 需要系統有 tesseract binary + pip pytesseract + pillow
OCR_AVAILABLE = False
try:
    from PIL import Image
    import pytesseract

    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False


# =========================
# Config helpers
# =========================
def get_config(key: str, default: str | None = None) -> str | None:
    """
    Read from st.secrets first, then env.
    Also converts '\\n' into '\n' for multi-line secrets (if any).
    """
    val = None
    try:
        val = st.secrets.get(key)
    except Exception:
        val = None

    if val is None:
        val = os.environ.get(key)

    if val is None:
        return default

    return str(val).replace("\\n", "\n")


def normalize_name(s: str) -> str:
    # Normalize for comparison:
    # - upper
    # - collapse multiple spaces
    # - strip
    return re.sub(r"\s+", " ", (s or "").strip().upper())


def split_names_multiline(text: str, limit: int = 50) -> List[str]:
    lines = [normalize_name(x) for x in (text or "").splitlines()]
    lines = [x for x in lines if x]
    return lines[:limit]


# =========================
# PassKit REST + JWT
# =========================
def build_jwt_token(api_key: str, api_secret: str) -> str:
    """
    PassKit REST JWT: HS256, payload typically uses iss/iat/exp.
    """
    import jwt  # PyJWT

    now = int(time.time())
    payload = {
        "iss": api_key,
        "iat": now,
        "exp": now + 3600,  # 1 hour
    }
    token = jwt.encode(payload, api_secret, algorithm="HS256")
    # PyJWT may return bytes in older versions
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def build_auth_headers() -> Dict[str, str]:
    api_key = get_config("PK_API_KEY")
    api_secret = get_config("PK_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("Missing PK_API_KEY / PK_API_SECRET in secrets/env")

    token = build_jwt_token(api_key, api_secret)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def passkit_base_url() -> str:
    """
    Most docs/examples use api.pub1.passkit.io for REST.
    If you are on pub2/pubX, change via PK_API_BASE.
    """
    return (get_config("PK_API_BASE") or "https://api.pub1.passkit.io").rstrip("/")


def list_members_by_display_names(
    program_id: str,
    display_names: List[str],
    operator: str = "eq",   # eq or like
    limit: int = 1000,
    offset: int = 0,
    order_by: str = "created",
    order_asc: bool = True,
) -> List[dict]:
    """
    Use REST endpoint:
      POST {base}/members/member/list/{program_id}

    Build a single OR filter group on displayName.
    """
    if not display_names:
        return []

    base = passkit_base_url()
    url = f"{base}/members/member/list/{program_id}"

    # Filter group: OR across multiple displayName filters
    field_filters = []
    for name in display_names:
        field_filters.append(
            {
                "filterField": "displayName",
                "filterValue": name,
                "filterOperator": operator,  # eq / like
            }
        )

    payload = {
        "filters": {
            "limit": int(limit),
            "offset": int(offset),
            "orderBy": order_by,
            "orderAsc": bool(order_asc),
            "filterGroups": [
                {
                    "condition": "OR",
                    "fieldFilters": field_filters,
                }
            ],
        }
    }

    headers = build_auth_headers()
    resp = requests.post(url, headers=headers, json=payload, timeout=60)

    # Helpful error detail
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()

    # Different tenants/versions might wrap items differently.
    # Try common shapes:
    # - {"members":[...]}
    # - {"results":[...]}
    # - list directly
    if isinstance(data, list):
        return data

    for key in ("members", "results", "data", "items"):
        if key in data and isinstance(data[key], list):
            return data[key]

    # Sometimes nested: {"response": {"members":[...]}}
    if "response" in data and isinstance(data["response"], dict):
        for key in ("members", "results", "items"):
            if key in data["response"] and isinstance(data["response"][key], list):
                return data["response"][key]

    # Unknown shape -> return empty but keep debug available
    return []


def extract_display_name_and_id(member_obj: dict) -> Tuple[str, str]:
    """
    Tries to extract:
    - person.displayName
    - member.id (PassKit internal id)
    Some REST responses might use different key casing; handle common variants.
    """
    # displayName
    display_name = ""
    person = member_obj.get("person") or member_obj.get("Person") or {}
    if isinstance(person, dict):
        display_name = person.get("displayName") or person.get("display_name") or person.get("name") or ""

    # id
    member_id = member_obj.get("id") or member_obj.get("memberId") or member_obj.get("member_id") or ""
    return normalize_name(display_name), str(member_id)


# =========================
# UI helpers: per-row copy with "copied" gray background
# =========================
def init_state():
    if "copied_ids" not in st.session_state:
        st.session_state.copied_ids = set()
    if "names_text" not in st.session_state:
        st.session_state.names_text = ""


def render_results(rows: List[dict]):
    """
    rows schema:
      {
        "搜尋姓名": ...,
        "會員姓名": ...,
        "Passkit ID": ...
      }
    """
    st.subheader("結果")
    if not rows:
        st.info("沒有找到任何符合資料。")
        return

    # Build HTML table with copy buttons
    copied_ids: set = st.session_state.copied_ids

    def esc(s: str) -> str:
        return (
            (s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    # Create table rows
    tr_html = ""
    for idx, r in enumerate(rows):
        search_name = esc(str(r.get("搜尋姓名", "")))
        member_name = esc(str(r.get("會員姓名", "")))
        pid = str(r.get("Passkit ID", ""))

        is_copied = pid in copied_ids
        bg = "#e5e7eb" if is_copied else "white"  # gray if copied

        # unique key for button
        btn_id = f"copybtn_{idx}"

        tr_html += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #eee;">{search_name}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{member_name}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;background:{bg};">
            <div style="display:flex;gap:8px;align-items:center;justify-content:space-between;">
              <code style="font-size:13px;">{esc(pid)}</code>
              <button
                id="{btn_id}"
                style="padding:6px 10px;border:1px solid #ddd;border-radius:8px;background:#fff;cursor:pointer;"
                onclick="navigator.clipboard.writeText('{esc(pid)}').then(() => {{
                    const msg = document.getElementById('{btn_id}_msg');
                    msg.innerText = 'copied';
                    msg.style.opacity = 1;
                }});"
              >Copy</button>
            </div>
            <div id="{btn_id}_msg" style="font-size:12px;color:#6b7280;opacity:0;margin-top:6px;">copied</div>
          </td>
        </tr>
        """

    table_html = f"""
    <div style="border:1px solid #eee;border-radius:12px;overflow:hidden;">
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:#fafafa;">
            <th style="text-align:left;padding:10px;border-bottom:1px solid #eee;">搜尋姓名</th>
            <th style="text-align:left;padding:10px;border-bottom:1px solid #eee;">會員姓名</th>
            <th style="text-align:left;padding:10px;border-bottom:1px solid #eee;">Passkit ID</th>
          </tr>
        </thead>
        <tbody>
          {tr_html}
        </tbody>
      </table>
    </div>
    <div style="font-size:12px;color:#6b7280;margin-top:8px;">
      提示：按 Copy 後，下次重新搜尋仍想保留「已複製」狀態，可以不要重新整理頁面；如需清空，按下方「清除已複製標記」。
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)

    col_a, col_b = st.columns([1, 2])
    with col_a:
        if st.button("清除已複製標記"):
            st.session_state.copied_ids = set()
            st.rerun()

    # CSV download
    df = pd.DataFrame(rows, columns=["搜尋姓名", "會員姓名", "Passkit ID"])
    st.download_button(
        "下載 CSV",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name="passkit_id_results.csv",
        mime="text/csv",
    )


def apply_copied_markers(rows: List[dict]):
    """
    Streamlit can't detect JS copy event, so we provide a manual "Mark copied" UX:
    - user clicks the Copy button (JS copies)
    - user also can click "標記為已複製" next to each row via Streamlit button
    BUT: requirement says "copy過的欄位反灰" — we implement both:
      1) JS copy for actual clipboard
      2) A "Mark copied" column to persist gray state
    """
    if not rows:
        return rows

    st.caption("（可選）若你希望「反灰」狀態能穩定記錄：點 Copy 後，再點同列的「標記已複製」。")
    copied_ids: set = st.session_state.copied_ids

    for i, r in enumerate(rows):
        pid = str(r.get("Passkit ID", ""))
        if not pid:
            continue
        cols = st.columns([6, 2])
        with cols[0]:
            st.write(f"- {r.get('會員姓名','')}  /  {pid}")
        with cols[1]:
            if pid in copied_ids:
                st.write("✅ 已標記")
            else:
                if st.button("標記已複製", key=f"mark_{i}_{pid}"):
                    copied_ids.add(pid)
                    st.session_state.copied_ids = copied_ids
                    st.rerun()

    return rows


# =========================
# OCR: screenshot -> names
# =========================
def ocr_extract_names_from_image(img: "Image.Image") -> List[str]:
    """
    Heuristic OCR:
    - Extract text via pytesseract
    - Pull uppercase-name-looking tokens (e.g. 'HSIUTING CHOU')
    You may tune regex based on your screenshot layout.
    """
    text = pytesseract.image_to_string(img)
    lines = [normalize_name(x) for x in text.splitlines()]
    lines = [x for x in lines if x]

    # Heuristic: names are 2~4 words, uppercase A-Z only
    out = []
    for ln in lines:
        if re.fullmatch(r"[A-Z]+( [A-Z]+){1,3}", ln):
            out.append(ln)

    # De-dup preserve order
    seen = set()
    uniq = []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq[:50]


# =========================
# Streamlit Page
# =========================
def main():
    st.set_page_config(page_title="PassKit ID 批次檢索（REST/JWT）", page_icon="🔎", layout="wide")
    init_state()

    st.title("🔎 PassKit ID 批次檢索（REST/JWT）")
    st.write("功能：以 **person.displayName** 批次搜尋會員，回傳 **Passkit ID（member.id）**。一次最多 50 筆姓名。")

    program_id = get_config("PROGRAM_ID")
    if not program_id:
        st.error("❌ 缺少 PROGRAM_ID（請在 .env 或 Render/Streamlit Secrets 設定）")
        st.stop()

    # ---- Sidebar: settings ----
    with st.sidebar:
        st.header("設定")
        st.text(f"Program ID: {program_id}")

        operator = st.selectbox("比對方式", options=["eq", "like"], index=0,
                                help="eq=完全相同；like=包含（較鬆，可能回更多結果）")
        limit = st.number_input("REST 每次回傳上限 (<=1000)", min_value=1, max_value=1000, value=1000, step=50)
        order_by = st.selectbox("排序欄位", options=["created", "updated"], index=0)
        order_asc = st.checkbox("升序 orderAsc", value=True)

        st.divider()
        st.subheader("已複製反灰")
        st.caption("Copy(JS) 會複製到剪貼簿；如要持久反灰，請用「標記已複製」。")

        st.divider()
        st.subheader("截圖自動帶入姓名（可選）")
        if OCR_AVAILABLE:
            st.success("OCR 可用（pytesseract）")
        else:
            st.warning("OCR 不可用：缺少 pytesseract/Pillow 或系統未安裝 tesseract。")

    # ---- OCR uploader ----
    st.subheader("（可選）上傳截圖，自動提取姓名")
    uploaded = st.file_uploader("上傳截圖（PNG/JPG）", type=["png", "jpg", "jpeg"])
    if uploaded is not None:
        if not OCR_AVAILABLE:
            st.error("此環境未啟用 OCR。請看下方「OCR 啟用方式」。")
        else:
            img = Image.open(uploaded)
            st.image(img, caption="已上傳截圖", use_container_width=True)
            with st.spinner("OCR 解析中..."):
                names = ocr_extract_names_from_image(img)
            if names:
                st.success(f"已從截圖提取 {len(names)} 個姓名（最多 50）")
                st.session_state.names_text = "\n".join(names)
            else:
                st.warning("未從截圖提取到姓名（可能需要調整 OCR 規則或截圖解析度）。")

    # ---- Main input ----
    st.subheader("批次查詢（最多 50 個姓名）")
    names_text = st.text_area(
        "每行一個 full name（對應 PassKit: person.displayName）",
        height=240,
        value=st.session_state.names_text,
        placeholder="例如：\nHSIUTING CHOU\nKUANYEN LEE\n...",
    )
    st.session_state.names_text = names_text

    col1, col2 = st.columns([1, 4])
    with col1:
        do_search = st.button("Search", type="primary")
    with col2:
        st.caption("提示：如果你用 like，請注意可能回傳多筆同名相近結果；你可用「同名最多回傳筆數」自行篩。")

    # ---- Search ----
    if do_search:
        names = split_names_multiline(names_text, limit=50)
        if not names:
            st.warning("請先輸入姓名（每行一個）。")
            st.stop()

        try:
            with st.spinner("向 PassKit REST 查詢中..."):
                raw_members = list_members_by_display_names(
                    program_id=program_id,
                    display_names=names,
                    operator=operator,
                    limit=int(limit),
                    offset=0,
                    order_by=order_by,
                    order_asc=order_asc,
                )
        except Exception as e:
            st.error(f"查詢失敗：{e}")
            st.stop()

        # Build lookup: we want to map input name -> matched members
        # Because like might return extra, we keep all and present.
        rows = []
        hit_count = 0

        for m in raw_members:
            member_name, member_id = extract_display_name_and_id(m)
            if not member_id:
                continue

            # Determine which search name it matches:
            # - eq: exact match with one of the inputs
            # - like: if member_name contains input name OR vice versa, pick first matched
            matched_search = ""
            if operator == "eq":
                if member_name in set(names):
                    matched_search = member_name
            else:
                for sname in names:
                    if sname in member_name or member_name in sname:
                        matched_search = sname
                        break

            if not matched_search:
                continue

            rows.append(
                {
                    "搜尋姓名": matched_search,
                    "會員姓名": member_name,
                    "Passkit ID": member_id,
                }
            )
            hit_count += 1

        st.success(f"完成：輸入 {len(names)} 個姓名，命中 {hit_count} 筆。")

        # Show not found list
        found_inputs = set([r["搜尋姓名"] for r in rows])
        not_found = [n for n in names if n not in found_inputs]
        if not_found:
            with st.expander(f"❌ 未找到名單（{len(not_found)}）", expanded=False):
                st.write("\n".join(not_found))

        render_results(rows)
        apply_copied_markers(rows)

    # ---- OCR enable guide ----
    st.divider()
    st.subheader("OCR 啟用方式（如果你需要「上傳截圖自動帶入姓名」）")
    st.write(
        "你現在部署在 Render 的話，除了 requirements.txt 裝 pytesseract/Pillow，"
        "還需要系統層安裝 tesseract。以下提供最常見做法（擇一）。"
    )
    st.markdown(
        """
**方案 A：Render Docker（推薦）**
- 用 Dockerfile，並在裡面 `apt-get install -y tesseract-ocr`
- requirements.txt 加上 `pytesseract` `pillow`

**方案 B：Render Native（非 Docker）**
- 需要 Render 支援 apt packages（有些 runtime 不支援），不穩定  
- 若不想搞系統依賴，建議先不用 OCR，上傳截圖功能可先保留但提示未啟用
"""
    )


if __name__ == "__main__":
    main()
