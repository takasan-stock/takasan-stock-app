"""
============================================================
日本株スクリーナー結果ビューア（表示専用）
============================================================
このアプリ自体はスキャン処理を行わない。
GitHub Actionsが定期実行した scan.py の結果を、
Googleスプレッドシートから読み込んで表示するだけ。

必要なStreamlit Secrets（Streamlit Cloud側の「Secrets」設定）:
  [gcp_service_account]
  type = "..."
  project_id = "..."
  private_key = "..."
  client_email = "..."
  （サービスアカウントJSONの中身をそのままTOML形式で貼る）

  SPREADSHEET_ID = "..."
============================================================
"""

import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 追加 ---
import yfinance as yf
import plotly.graph_objects as go

st.set_page_config(page_title="日本株スクリーナー結果ビューア", layout="wide")

# ==========================================
# Googleスプレッドシート読み込み
# ==========================================
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=300)  # 5分キャッシュ（毎回API呼ばないように）
def load_sheet(sheet_name: str) -> pd.DataFrame:
    gc = get_gspread_client()
    spreadsheet_id = st.secrets["SPREADSHEET_ID"]
    try:
        sheet = gc.open_by_key(spreadsheet_id).worksheet(sheet_name)
        data = sheet.get_all_values()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data[1:], columns=data[0])
        return df
    except Exception as e:
        st.error(f"シート '{sheet_name}' の読み込みエラー: {e}")
        return pd.DataFrame()

# ==========================================
# チャート描画関数（追加）
# ==========================================
def draw_chart(ticker_code, ticker_name=""):
    try:
        # 日本株の場合はコードの末尾に .T をつける
        symbol = f"{ticker_code}.T"
        # 過去半年分のデータを取得
        df_chart = yf.download(symbol, period="6mo", progress=False)
        
        if df_chart.empty:
            st.warning(f"【{ticker_code}】の株価データが取得できませんでした。")
            return
            
        fig = go.Figure(data=[go.Candlestick(
            x=df_chart.index,
            open=df_chart['Open'],
            high=df_chart['High'],
            low=df_chart['Low'],
            close=df_chart['Close'],
            name="ローソク足"
        )])
        
        # 25日SMAも追加（トレンド把握用）
        sma25 = df_chart['Close'].rolling(window=25).mean()
        fig.add_trace(go.Scatter(x=df_chart.index, y=sma25, mode='lines', name='25SMA', line=dict(color='orange', width=1.5)))
        
        fig.update_layout(
            title=f"【{ticker_code}】{ticker_name} (過去半年)",
            yaxis_title="株価",
            xaxis_rangeslider_visible=False,
            height=450,
            margin=dict(l=0, r=0, t=40, b=0)
        )
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error(f"チャート描画エラー: {e}")

# ==========================================
# メイン画面表示
# ==========================================
st.title("📊 日本株スクリーナー結果ビューア")

tabs = st.tabs(["複数パターン合致", "週足パターンA", "日足B1 押し目待ち🟡", "日足B2 反発エントリー🚀", "ボリバンC ブレイク💥"])

sheet_map = [
    (tabs[0], "複数パターン合致",      "複数パターン合致"),
    (tabs[1], "週足パターンA（長期）",  "週足パターンA"),
    (tabs[2], "日足B1-押し目待ち",      "日足B1押し目待ち"),
    (tabs[3], "日足B2-反発エントリー",  "日足B2反発エントリー"),
    (tabs[4], "ボリバン+2σブレイク",    "ボリバンCブレイク"),
]

for tab, title, sheet_name in sheet_map:
    with tab:
        df = load_sheet(sheet_name)
        st.subheader(f"{title} (該当: {len(df)} 銘柄)")
        
        if not df.empty and "該当銘柄なし" not in df.columns:
            
            # --- 左右に分割：左(比率1.5)に表、右(比率1.0)にチャート ---
            col1, col2 = st.columns([1.5, 1.0])
            
            with col1:
                # 既存のデータフレームハイライト処理
                if "合致パターン数" in df.columns:
                    try:
                        df["合致パターン数"] = pd.to_numeric(df["合致パターン数"], errors="coerce")
                    except Exception:
                        pass
                    st.dataframe(
                        df.style.apply(
                            lambda x: ['background-color: #ffeeb0' if str(x.get('合致パターン数','')) and float(x.get('合致パターン数',0) or 0) >= 2 else '' for _ in x],
                            axis=1
                        ),
                        height=450 # チャートの高さと合わせる
                    )
                else:
                    st.dataframe(df, height=450)
            
            with col2:
                # 表に「コード」列がある場合のみプルダウンを表示
                if "コード" in df.columns:
                    # プルダウンの選択肢を作成 (例: "7203 : トヨタ自動車")
                    options = []
                    for _, row in df.iterrows():
                        code = str(row.get("コード", ""))
                        name = str(row.get("銘柄名", ""))
                        options.append(f"{code} : {name}")
                    
                    # ユーザーがプルダウンから銘柄を選択
                    selected = st.selectbox("📊 チャートを表示する銘柄を選択", options, key=f"sb_{sheet_name}")
                    
                    if selected:
                        # "7203 : トヨタ自動車" からコードと銘柄名を分割して取得
                        selected_code = selected.split(" : ")[0]
                        selected_name = selected.split(" : ")[1] if " : " in selected else ""
                        
                        # チャートを描画
                        draw_chart(selected_code, selected_name)
