import streamlit as st
import pandas as pd
import requests
import time
import jwt  # 請確保 requirements.txt 中有 PyJWT
import os

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

# --- 3. REST API 檢索邏輯 ---
def rest_search(names):
    results = []
    missing = []
    program_id = get_config("PROGRAM_ID")
    # 對應您提到的官方 REST Prefix
    url = f"https://api.pub1.passkit.io/members/member/list/{program_id}"
    
    headers = get_auth_header()
    if not headers: return [], names

    progress_bar = st.progress(0)
    
    # 每次搜尋一個名字以確保精確度
    for idx, name in enumerate(names):
        name = name.strip()
        if not name: continue
        
        try:
            # 根據 member_pb2.py 結構構建過濾 JSON
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
                        # 依照您的要求排列欄位
                        results.append({
                            "搜尋姓名": name.upper(),
                            "稱謂 person.salutation": m.get('person', {}).get('salutation', ''),
                            "系統名 person.displayName": m.get('person', {}).get('displayName', ''),
                            "Passkit ID": m.get('id', '') # ID 放最後
                        })
                else:
                    missing.append(name)
            else:
                st.warning(f"搜尋 {name} 失敗: {resp.status_code}")
        except Exception as e:
            st.error(f"連線錯誤: {e}")
            
        progress_bar.progress((idx + 1) / len(names))
    
    progress_bar.empty()
    return results, missing

# --- 4. 網頁介面 ---
st.title("📑 PassKit 會員 ID 批次提取")
st.markdown("使用 REST API 進行精確比對，第一欄為搜尋姓名，最後一欄為 Passkit ID。")

with st.form("search_form"):
    input_text = st.text_area("貼上姓名名單 (每行一個)", height=250)
    submitted = st.form_submit_button("開始搜尋")

if submitted:
    if not input_text.strip():
        st.warning("請輸入內容")
    else:
        name_list = input_text.split('\n')
        with st.spinner("正在檢索中..."):
            matches, missing = rest_search(name_list)
            
            if matches:
                st.success(f"✅ 找到 {len(matches)} 筆結果")
                df = pd.DataFrame(matches)
                # 強制欄位排序
                df = df[["搜尋姓名", "稱謂 person.salutation", "系統名 person.displayName", "Passkit ID"]]
                st.dataframe(df, use_container_width=True)
            
            if missing:
                with st.expander("❌ 未匹配名單"):
                    st.write(", ".join(missing))
