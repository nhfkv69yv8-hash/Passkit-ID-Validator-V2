import streamlit as st
import pandas as pd
import requests
import time
import jwt  # 請確保 requirements.txt 中有 PyJWT
import os

# 1. 基礎設定
st.set_page_config(page_title="PassKit REST 批次檢索", page_icon="🔍")

# 修正截圖中的 NameError: 初始化 session_state
if 'last_summary' not in st.session_state:
    st.session_state.last_summary = None

def get_config(key):
    val = st.secrets.get(key) or os.environ.get(key)
    # 修正 'int' object has no attribute 'replace' 錯誤
    if val is not None:
        return str(val).replace('\\n', '\n')
    return None

# --- 2. 認證 Token 生成 (修正 build_jwt_token 未定義問題) ---
def build_jwt_token():
    key = get_config("PK_API_KEY")
    secret = get_config("PK_API_SECRET")
    
    if not key or not secret:
        st.error("❌ 缺少 API Key 或 Secret，請檢查 Secrets 設定。")
        return None
        
    payload = {
        "iss": key,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600  # 1 小時有效期
    }
    # 使用 HS256 算法生成 PassKit 要求的 Token
    return jwt.encode(payload, secret, algorithm="HS256")

# --- 3. REST API 核心搜尋邏輯 ---
def rest_batch_search(name_list, limit=1000):
    results = []
    missing_names = []
    program_id = get_config("PROGRAM_ID")
    
    # 官方 REST Prefix
    url = f"https://api.pub2.passkit.io/members/member/list/{program_id}"
    
    token = build_jwt_token()
    if not token: return [], name_list
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # 標準化搜尋名單
    search_names = [n.strip() for n in name_list if n.strip()][:50]
    progress_bar = st.progress(0)

    for idx, name in enumerate(search_names):
        try:
            # 建立符合 member_pb2.py 規範的過濾 JSON
            body = {
                "filters": {
                    "filterGroups": [
                        {
                            "condition": "AND",
                            "fieldFilters": [
                                {
                                    "filterField": "person.displayName",
                                    "filterValue": name,
                                    "filterOperator": "eq"
                                }
                            ]
                        }
                    ]
                }
            }

            resp = requests.post(url, headers=headers, json=body)
            
            if resp.status_code == 200:
                data = resp.json()
                members = data.get('members', [])
                if members:
                    for m in members:
                        # 依照要求排列欄位
                        results.append({
                            "搜尋姓名": name.upper(),
                            "稱謂 person.salutation": m.get('person', {}).get('salutation', ''),
                            "系統名 person.displayName": m.get('person', {}).get('displayName', ''),
                            "Passkit ID": m.get('id', '') # ID 放最後
                        })
                else:
                    missing_names.append(name)
            else:
                st.warning(f"搜尋 {name} 失敗: HTTP {resp.status_code}")
                
        except Exception as e:
            st.error(f"搜尋 {name} 時發生異常: {e}")
            
        progress_bar.progress((idx + 1) / len(search_names))

    progress_bar.empty()
    return results, missing_names

# --- 4. 網頁介面 ---
st.title("🔍 批次查詢 PassKit Member ID")
st.markdown("用 REST filter 一次查，不掃全量。")

# 模擬截圖中的環境變數檢查 Expander
with st.expander("✅ 目前環境變數檢查"):
    st.write(f"Program ID: `{get_config('PROGRAM_ID')}`")
    st.write(f"API Key: `{get_config('PK_API_KEY')[:5]}...` (已遮蔽)")

input_text = st.text_area("每行一個 full name (displayName)", height=250, placeholder="SUHAN CHAN\nYUCHUN LEE")

col1, col2 = st.columns(2)
with col1:
    search_mode = st.selectbox("比對方式", ["eq", "startsWith", "contains"])
with col2:
    limit_val = st.number_input("limit (<=1000)", value=1000, max_value=1000)

if st.button("Search", type="primary"):
    if not input_text.strip():
        st.warning("請輸入內容。")
    else:
        names = input_text.split('\n')
        with st.spinner("REST API 檢索中..."):
            matches, missing = rest_batch_search(names, limit=limit_val)
            
            if matches:
                st.success(f"✅ 完成：找到 {len(matches)} 筆資料。")
                df = pd.DataFrame(matches)[["搜尋姓名", "稱謂 person.salutation", "系統名 person.displayName", "Passkit ID"]]
                st.dataframe(df, use_container_width=True)
            
            if missing:
                with st.expander("❌ 未找到名單", expanded=True):
                    st.write(", ".join(missing))
