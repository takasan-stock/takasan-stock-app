import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="4系統サマリー", page_icon="🧭", layout="wide")

st.title("🧭 4系統サマリー")
st.caption("底打ち転換・押し目・ブレイク・決算モメンタムを4系統で整理し、総合スコアで優先順位を確認できます。")


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
    numeric_cols = [
        "該当系統数", "元パターン合計", "終値", "総合スコア", "テクニカル総合",
        "TURNAROUNDスコア", "PULLBACKスコア", "BREAKOUTスコア", "EARNINGSスコア",
    ]
    for col in numeric_cols:
        if col in summary.columns:
            summary[col] = pd.to_numeric(summary[col], errors="coerce")

    turn_count = int((summary.get("TURNAROUNDスコア", pd.Series(dtype=float)).fillna(0) > 0).sum())
    pull_count = int((summary.get("PULLBACKスコア", pd.Series(dtype=float)).fillna(0) > 0).sum())
    break_count = int((summary.get("BREAKOUTスコア", pd.Series(dtype=float)).fillna(0) > 0).sum())
    earn_count = int((summary.get("EARNINGSスコア", pd.Series(dtype=float)).fillna(0) > 0).sum())

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("🏆 総合80点以上", int((summary.get("総合スコア", pd.Series(dtype=float)).fillna(0) >= 80).sum()))
    m2.metric("🔄 TURNAROUND", turn_count, help="週足A + GC底打ちF")
    m3.metric("🎯 PULLBACK", pull_count, help="日足B1 + 日足B2 + 初押しD")
    m4.metric("🚀 BREAKOUT", break_count, help="ボリバンC + 出来高E + ポケットピボットG")
    m5.metric("🔥 EARNINGS", earn_count, help="決算モメンタム")

    with st.expander("📐 総合スコアの計算方法"):
        st.markdown(
            "- テクニカル3系統は、1条件ヒットを60点の起点にし、同系統で確認材料が増えるほど80〜100点へ上げます。\n"
            "- 最も強いテクニカル系統を主軸に、別系統にも同時ヒットしていれば1系統につき+5点します（上限100）。\n"
            "- 決算モメンタムがある銘柄は、**テクニカル80% + EARNINGS20%** で総合化します。\n"
            "- 決算モメンタムが無い銘柄は、決算データ不足を理由に減点せずテクニカル総合をそのまま使います。\n"
            "- テクニカル該当が無く決算モメンタムだけある銘柄は、EARNINGSスコアを総合スコアとして使います。"
        )

    st.divider()

    c1, c2, c3, c4 = st.columns([1.2, 1.1, 1.1, 1.8])
    with c1:
        category = st.selectbox(
            "表示系統",
            ["すべて", "TURNAROUND", "PULLBACK", "BREAKOUT", "EARNINGS", "複数系統のみ"],
        )
    with c2:
        min_score = st.slider("最低総合スコア", 0, 100, 0, 5)
    with c3:
        rank_filter = st.selectbox("総合ランク", ["すべて", "S", "A", "B", "C", "D", "E"])
    with c4:
        query = st.text_input("銘柄コード・銘柄名検索", placeholder="例: 4063 / 信越")

    out = summary.copy()
    score_col_map = {
        "TURNAROUND": "TURNAROUNDスコア",
        "PULLBACK": "PULLBACKスコア",
        "BREAKOUT": "BREAKOUTスコア",
        "EARNINGS": "EARNINGSスコア",
    }
    if category in score_col_map and score_col_map[category] in out.columns:
        out = out[out[score_col_map[category]].fillna(0) > 0]
    elif category == "複数系統のみ":
        active_cols = [c for c in score_col_map.values() if c in out.columns]
        if active_cols:
            active_count = sum((out[c].fillna(0) > 0).astype(int) for c in active_cols)
            out = out[active_count >= 2]

    if "総合スコア" in out.columns:
        out = out[out["総合スコア"].fillna(0) >= min_score]
    if rank_filter != "すべて" and "総合ランク" in out.columns:
        out = out[out["総合ランク"].astype(str) == rank_filter]

    if query:
        q = query.strip()
        mask = pd.Series(False, index=out.index)
        for col in ["証券コード", "Ticker", "銘柄名"]:
            if col in out.columns:
                mask |= out[col].astype(str).str.contains(q, case=False, na=False)
        out = out[mask]

    sort_cols = [c for c in ["総合スコア", "テクニカル総合", "元パターン合計"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols, ascending=[False] * len(sort_cols))

    st.subheader("🏆 総合ランキング")
    st.caption(f"該当 {len(out)} 銘柄。総合スコアの高い順に表示します。")
    show_cols = [
        "証券コード", "Ticker", "銘柄名", "終値", "総合スコア", "総合ランク",
        "TURNAROUNDスコア", "PULLBACKスコア", "BREAKOUTスコア", "EARNINGSスコア",
        "テクニカル総合", "4系統該当", "元パターン合計",
    ]
    show_cols = [c for c in show_cols if c in out.columns]
    st.dataframe(out[show_cols].reset_index(drop=True), use_container_width=True, height=560, hide_index=True)

    csv = out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("📥 総合ランキングCSV", csv, "4系統_総合ランキング.csv", "text/csv")

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

st.info("既存A〜Gの個別スクリーナーは元画面に残しています。Overviewでは総合スコアで候補を絞り、詳細確認は個別画面で行えます。")
