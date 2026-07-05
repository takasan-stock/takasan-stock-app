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
    sh = gc.open_by_key(st.secrets["SPREADSHEET_ID"])
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        return pd.DataFrame()

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame()

    df = pd.DataFrame(values[1:], columns=values[0])
    return df


# ==========================================
# UI
# ==========================================
st.title("📈 日本株スクリーナー 結果ビューア")
st.caption(
    "このアプリはスキャン処理を行いません。"
    "GitHub Actionsが毎営業日に自動実行した結果をスプレッドシートから表示しています。"
)

col1, col2 = st.columns([3, 1])
with col2:
    if st.button("🔄 最新の結果を再取得", use_container_width=True):
        load_sheet.clear()
        st.rerun()

# ── 実行ログ（最終更新日時）を表示 ──
log_df = load_sheet("実行ログ")
if not log_df.empty:
    last_row = log_df.iloc[-1]
    st.info(
        f"最終スキャン日時: {last_row.get('最終実行日時', '不明')}　|　"
        f"対象銘柄数: {last_row.get('対象銘柄数', '-')}　|　"
        f"週A: {last_row.get('週足A件数','-')}件　"
        f"B1: {last_row.get('日足B1件数','-')}件　"
        f"B2: {last_row.get('日足B2件数','-')}件　"
        f"ボリバンC: {last_row.get('ボリバンC件数','-')}件"
    )
else:
    st.warning("実行ログが見つかりません。GitHub Actionsがまだ一度も実行されていない可能性があります。")

# ── 各シートをタブ表示 ──
tabs = st.tabs(["⭐複数パターン合致", "週足パターンA", "日足B1 押し目待ち🟡", "日足B2 反発エントリー🚀", "ボリバンC ブレイク💥"])

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
            if "合致パターン数" in df.columns:
                # 合致パターン数が文字列のままなので数値化してハイライト判定
                try:
                    df["合致パターン数"] = pd.to_numeric(df["合致パターン数"], errors="coerce")
                except Exception:
                    pass
                st.dataframe(
                    df.style.apply(
                        lambda x: ['background-color: #ffeeb0' if str(x.get('合致パターン数','')) and float(x.get('合致パターン数',0) or 0) >= 2 else '' for _ in x],
                        axis=1
                    ),
                    use_container_width=True,
                )
            else:
                st.dataframe(df, use_container_width=True)

            csv = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                label=f"📥 {sheet_name} をダウンロード",
                data=csv,
                file_name=f"{sheet_name}.csv",
                mime="text/csv",
            )
        else:
            st.write("該当銘柄なし")
