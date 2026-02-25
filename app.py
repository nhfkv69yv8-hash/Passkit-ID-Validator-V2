import streamlit as st
import pandas as pd
import requests
import time
import jwt  # 確保 requirements.txt 有 PyJWT
import os

# 1. 基礎設定
st.set_page_config(page_title="PassKit ID 檢索器", page_icon="🔍")

def get_config(key):
    val = st.secrets.get(key) or os.environ.get(key)
    # 解決截圖中的 TypeError: 'int' object has no attribute 'replace'
    if val is not None:
        return str(val).replace('\\n', '\n').strip()
    return None

# --- 2. 認證 Token 生成 (修正截圖中的 NameError: 'api' is not defined) ---
def build_jwt_token():
    key = get_config("PK_API_KEY")
    secret = get_config("PK_API_SECRET")
    
    if not key or not secret:
        return None
        
    payload = {
        "iss": key,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    # 使用密鑰進行簽署
    return jwt.encode(payload, secret, algorithm="HS256")

# --- 3. REST API 核心搜尋邏輯 ---
def rest_batch_search(name_list):
    results = []
    missing_names = []
    program_id = get_config("PROGRAM_ID")
    
    # 官方 REST 端點路徑
    url = f"https://api.pub1.passkit.io/members/member/list/{program_id}"
    
    token = build_jwt_token()
    if not token:
        st.error("❌ 無法生成認證 Token，請檢查 API Key/Secret")
        return [], name_list
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    search_names = [n.strip() for n in name_list if n.strip()][:50]
    progress_bar = st.progress(0)

    for idx, name in enumerate(search_names):
        try:
            # 構建符合 member_pb2.py 過濾器定義的 JSON
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
                # 根據 SDK 結構提取成員資訊
                members = data.get('members', [])
                if members:
                    for m in members:
                        person = m.get('person', {})
                        # ✅ 符合要求：搜尋姓名、稱謂、系統名、Passkit ID (放最後)
                        results.append({
                            "搜尋姓名": name.upper(),
                            "稱謂 person.salutation": person.get('salutation', ''),
                            "系統名 person.displayName": person.get('displayName', ''),
                            "Passkit ID": m.get('id', '') 
                        })
                else:
                    missing_names.append(name)
            elif resp.status_code == 401:
                st.error("🔑 認證失敗 (401): 請檢查 API Key 和 Secret 是否正確")
                break
            
        except Exception as e:
            st.error(f"搜尋 {name} 時發生異常: {e}")
            
        progress_bar.progress((idx + 1) / len(search_names))

    progress_bar.empty()
    return results, missing_names

# --- 4. 網頁介面 ---
st.title("📑 會員 Passkit ID 批次檢索 (REST)")
st.write("直接呼叫 api.pub1.passkit.io 進行精確過濾。")

input_text = st.text_area("請輸入姓名名單 (每行一個)", height=250, placeholder="CHAN TAI MAN\nWONG SIU MING")

if st.button("執行批次搜尋", type="primary"):
    if not input_text.strip():
        st.warning("請輸入姓名。")
    else:
        names = input_text.split('\n')
        with st.spinner("正在進行 REST API 檢索..."):
            matches, missing = rest_batch_search(names)
            
            if matches:
                st.success(f"✅ 搜尋完成！找到 {len(matches)} 筆相符資料。")
                df = pd.DataFrame(matches)
                # 修正語法錯誤並強制排序欄位
                display_df = df[["搜尋姓名", "稱謂 person.salutation", "系統名 person.displayName", "Passkit ID"]]
                st.dataframe(display_df, use_container_width=True)
            
            if missing:
                with st.expander("❌ 未找到名單"):
                    st.write(", ".join(missing))
