import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="4系統サマリー", page_icon="🧭", layout="wide")

st.title("🧭 4系統サマリー")
st.caption("A〜Gの個別パターンを、底打ち転換・押し目・ブレイク・決算モメンタムの4系統で見やすく整理します。")


@st.cache_resource
def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    return gspread.authorize(creds)


@st.cache_data(ttl=300)
def load_sheet(sheet_name: str) -> pd.DataFrame:
    try:
        gc = get_client()
        sh = gc.open_by_key(st.secrets["SPREADSHEET_ID"])
        ws = sh.worksheet(sheet_name)
        values = ws.get_all_values()
        if not values or len(values) < 2:
            return pd.DataFrame()
        if values[0] in (["該当銘柄なし"], ["該当データなし"]):
            return pd.DataFrame()
        return pd.DataFrame(values[1:], columns=values[0])
    except Exception:
        return pd.DataFrame()


if st.button("🔄 最新データを再取得"):
    load_sheet.clear()
    st.rerun()

summary = load_sheet("4系統サマリー")
earnings = load_sheet("決算モメンタム")
overlap = load_sheet("重複分析")

if summary.empty:
    st.warning("4系統サマリーがまだありません。GitHub Actionsのスキャン完了後に表示されます。")
else:
    for col in ["該当系統数", "元パターン合計", "終値"]:
        if col in summary.columns:
            summary[col] = pd.to_numeric(summary[col], errors="coerce")

    turn_count = int(summary["TURNAROUND"].astype(str).str.strip().ne("").sum()) if "TURNAROUND" in summary.columns else 0
    pull_count = int(summary["PULLBACK"].astype(str).str.strip().ne("").sum()) if "PULLBACK" in summary.columns else 0
    break_count = int(summary["BREAKOUT"].astype(str).str.strip().ne("").sum()) if "BREAKOUT" in summary.columns else 0
    earn_count = len(earnings) if not earnings.empty else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🔄 TURNAROUND", turn_count, help="週足A + GC底打ちF")
    m2.metric("🎯 PULLBACK", pull_count, help="日足B1 + 日足B2 + 初押しD")
    m3.metric("🚀 BREAKOUT", break_count, help="ボリバンC + 出来高E + ポケットピボットG")
    m4.metric("🔥 EARNINGS", earn_count, help="決算モメンタム")

    st.divider()

    c1, c2, c3 = st.columns([1.2, 1.2, 1.8])
    with c1:
        category = st.selectbox(
            "表示系統",
            ["すべて", "TURNAROUND", "PULLBACK", "BREAKOUT", "複数系統のみ"],
        )
    with c2:
        min_sources = st.selectbox("元パターン合計", [1, 2, 3, 4], index=0)
    with c3:
        query = st.text_input("銘柄コード・銘柄名検索", placeholder="例: 4063 / 信越")

    out = summary.copy()
    if category in ("TURNAROUND", "PULLBACK", "BREAKOUT") and category in out.columns:
        out = out[out[category].astype(str).str.strip().ne("")]
    elif category == "複数系統のみ" and "該当系統数" in out.columns:
        out = out[out["該当系統数"].fillna(0) >= 2]

    if "元パターン合計" in out.columns:
        out = out[out["元パターン合計"].fillna(0) >= min_sources]

    if query:
        q = query.strip()
        mask = pd.Series(False, index=out.index)
        for col in ["証券コード", "Ticker", "銘柄名"]:
            if col in out.columns:
                mask |= out[col].astype(str).str.contains(q, case=False, na=False)
        out = out[mask]

    sort_cols = [c for c in ["該当系統数", "元パターン合計"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols, ascending=[False] * len(sort_cols))

    st.subheader("📋 4系統一覧")
    st.caption(f"該当 {len(out)} 銘柄。複数系統・複数元パターンに重なる銘柄を上位に表示します。")
    show_cols = [
        "証券コード", "Ticker", "銘柄名", "終値", "該当系統数", "該当系統",
        "TURNAROUND", "PULLBACK", "BREAKOUT", "元パターン合計",
    ]
    show_cols = [c for c in show_cols if c in out.columns]
    st.dataframe(out[show_cols].reset_index(drop=True), use_container_width=True, height=520, hide_index=True)

    csv = out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("📥 4系統サマリーCSV", csv, "4系統サマリー.csv", "text/csv")

st.divider()

left, right = st.columns([1.4, 1])
with left:
    st.subheader("🔥 決算モメンタム")
    if earnings.empty:
        st.info("決算モメンタムの結果はまだありません。")
    else:
        if "スコア" in earnings.columns:
            earnings["スコア"] = pd.to_numeric(earnings["スコア"], errors="coerce")
            earnings = earnings.sort_values("スコア", ascending=False)
        cols = [c for c in ["順位", "証券コード", "銘柄名", "スコア", "ランク", "シグナル", "決算後騰落率%", "RS風"] if c in earnings.columns]
        st.dataframe(earnings[cols].head(30), use_container_width=True, hide_index=True, height=420)

with right:
    st.subheader("🔎 重複分析")
    if overlap.empty:
        st.info("重複分析は次回スキャン後に表示されます。")
    else:
        for col in ["件数1", "件数2", "共通銘柄数", "小さい側に対する重複率%", "Jaccard重複率%"]:
            if col in overlap.columns:
                overlap[col] = pd.to_numeric(overlap[col], errors="coerce")
        if "小さい側に対する重複率%" in overlap.columns:
            overlap = overlap.sort_values("小さい側に対する重複率%", ascending=False)
        cols = [c for c in ["パターン1", "パターン2", "共通銘柄数", "小さい側に対する重複率%", "Jaccard重複率%"] if c in overlap.columns]
        st.dataframe(overlap[cols].head(15), use_container_width=True, hide_index=True, height=420)
        st.caption("重複率が高い組み合わせは、今後の整理・統合候補です。すぐ削除せず数週間の実データで判断します。")

st.info("既存A〜Gの個別スクリーナーは元画面に残しています。このページを普段使いの入口にし、個別条件は必要な時だけ確認する運用を想定しています。")
