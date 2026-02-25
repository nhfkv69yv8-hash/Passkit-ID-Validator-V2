import streamlit as st
import pandas as pd
import requests
import time
import jwt  # pip install PyJWT
import os

# 1. 基礎設定
st.set_page_config(page_title="PassKit REST 檢索工具", page_icon="⚡")

def get_config(key):
    val = st.secrets.get(key) or os.environ.get(key)
    return str(val).replace('\\n', '\n') if val else None

# --- 2. 認證 Token 生成 (JWT) ---
def get_auth_header():
    key = get_config("PK_API_KEY")
    secret = get_config("PK_API_SECRET")
    if not key or not secret:
        st.error("❌ 缺少 PK_API_KEY 或 PK_API_SECRET")
        return None
    
    # 建立 PassKit 要求的 JWT Payload
    payload = {
        "iss": key,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600  # 1小時後過期
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# --- 3. REST API 搜尋邏輯 ---
def rest_batch_search(name_list):
    results = []
    missing_names = []
    program_id = get_config("PROGRAM_ID")
    base_url = f"https://api.pub1.passkit.io/members/member/list/{program_id}"
    
    headers = get_auth_header()
    if not headers: return [], []

    clean_names = [n.strip() for n in name_list if n.strip()][:50]
    progress_bar = st.progress(0)

    for idx, name in enumerate(clean_names):
        try:
            # 建立符合 PassKit 規範的 Filter Body
            # 參考文件：https://help.passkit.com/en/articles/4133757
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

            response = requests.post(base_url, headers=headers, json=body)
            
            if response.status_code == 200:
                data = response.json()
                # API 回傳結構通常在 'members' 或直接是清單
                members = data.get('members', [])
                
                if members:
                    for m in members:
                        # 依照要求排列：搜尋姓名、稱謂、系統名、Passkit ID
                        results.append({
                            "搜尋姓名": name.upper(),
                            "稱謂 person.salutation": m.get('person', {}).get('salutation', ''),
                            "系統名 person.displayName": m.get('person', {}).get('displayName', ''),
                            "Passkit ID": m.get('id', '')
                        })
                else:
                    missing_names.append(name)
            else:
                st.error(f"API 錯誤 ({name}): {response.status_code} - {response.text}")
            
            progress_bar.progress((idx + 1) / len(clean_names))
        except Exception as e:
            st.error(f"連線異常 ({name}): {e}")

    progress_bar.empty()
    return results, missing_names

# --- 4. 網頁介面 ---
st.title("⚡ PassKit REST 批次檢索 (v4.0)")
st.info("使用官方 REST API Prefix: api.pub1.passkit.io，支援精確過濾。")

with st.form("rest_form"):
    input_text = st.text_area("請輸入姓名名單 (每行一個)", height=250, placeholder="CHAN TAI MAN\nWONG SIU MING")
    submitted = st.form_submit_button("執行閃電搜尋")

if submitted:
    if not input_text.strip():
        st.warning("請輸入姓名。")
    else:
        with st.spinner("正在與 PassKit 雲端進行 REST 通訊..."):
            matches, missing = rest_batch_search(input_text.split('\n'))
            
            if matches:
                st.success(f"✅ 成功找到 {len(matches)} 筆相符資料。")
                df = pd.DataFrame(matches)[["搜尋姓名", "稱謂 person.salutation", "系統名 person.displayName", "Passkit ID"]]
                st.dataframe(df, use_container_width=True)
            
            if missing:
                with st.expander("❌ 未找到名單 (請確認與系統內的 displayName 完全一致)"):
                    st.write(", ".join(missing))
