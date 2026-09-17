import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="日本株スクリーナー", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
div[data-testid="stMetric"] {background:#f8f9fb;border:1px solid #e4e7ee;border-radius:10px;padding:10px 14px;}
div[data-testid="stMetric"] label {font-size:.78rem;color:#667;}
button[data-baseweb="tab"] {font-size:.95rem;}
div[data-testid="stDataFrame"] {font-size:.88rem;}
</style>
""", unsafe_allow_html=True)

@st.cache_resource
def get_gspread_client():
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    creds=Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    return gspread.authorize(creds)

@st.cache_data(ttl=300)
def load_sheet(sheet_name:str)->pd.DataFrame:
    try:
        sh=get_gspread_client().open_by_key(st.secrets["SPREADSHEET_ID"])
        ws=sh.worksheet(sheet_name)
        values=ws.get_all_values()
        if not values or len(values)<2:
            return pd.DataFrame()
        if values[0] in (["該当銘柄なし"],["該当データなし"]):
            return pd.DataFrame()
        return pd.DataFrame(values[1:],columns=values[0])
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def fetch_chart_data(ticker:str,period:str="1y"):
    try:
        df=yf.download(ticker,period=period,interval="1d",auto_adjust=True,progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns,pd.MultiIndex):
            df.columns=df.columns.get_level_values(0)
        return df
    except Exception:
        return pd.DataFrame()


def numeric(df, cols):
    out=df.copy()
    for c in cols:
        if c in out.columns:
            out[c]=pd.to_numeric(out[c],errors="coerce")
    return out


def filter_search(df, q):
    if df.empty or not q:
        return df
    mask=pd.Series(False,index=df.index)
    for c in ["証券コード","Ticker","銘柄名"]:
        if c in df.columns:
            mask |= df[c].astype(str).str.contains(q.strip(),case=False,na=False)
    return df[mask]


def render_rank_table(df, category=None, key="rank"):
    if df.empty:
        st.info("まだデータがありません。スキャン完了後に表示されます。")
        return
    df=numeric(df,["総合スコア","TURNAROUNDスコア","PULLBACKスコア","BREAKOUTスコア","EARNINGSスコア","テクニカル総合","終値"])
    if category and f"{category}スコア" in df.columns:
        df=df[df[f"{category}スコア"].fillna(0)>0]
    q=st.text_input("銘柄コード・銘柄名で検索",key=f"q_{key}")
    df=filter_search(df,q)
    min_score=st.slider("最低総合スコア",0,100,0,5,key=f"min_{key}")
    if "総合スコア" in df.columns:
        df=df[df["総合スコア"].fillna(0)>=min_score]
    if "総合ランク" in df.columns:
        rank=st.multiselect("総合ランク",["S","A","B","C","D","E"],default=[],key=f"rank_{key}")
        if rank:
            df=df[df["総合ランク"].isin(rank)]
    sort_cols=[c for c in ["総合スコア","テクニカル総合"] if c in df.columns]
    if sort_cols:
        df=df.sort_values(sort_cols,ascending=[False]*len(sort_cols))
    cols=[c for c in ["証券コード","Ticker","銘柄名","終値","総合スコア","総合ランク","TURNAROUNDスコア","PULLBACKスコア","BREAKOUTスコア","EARNINGSスコア","4系統該当","TURNAROUND","PULLBACK","BREAKOUT"] if c in df.columns]
    st.caption(f"該当 {len(df)} 銘柄")
    st.dataframe(df[cols].reset_index(drop=True),use_container_width=True,height=540,hide_index=True)
    csv=df.to_csv(index=False,encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("📥 CSVで保存",csv,f"{key}.csv","text/csv",key=f"dl_{key}")


with st.sidebar:
    st.header("🔍 操作")
    if st.button("🔄 最新の結果を再取得",use_container_width=True):
        load_sheet.clear(); st.rerun()
    st.caption("スキャン直後は再取得してください。")
    st.divider()
    st.page_link("pages/99_legacy.py",label="⚙️ A〜G 詳細条件を開く",icon="⚙️")
    st.page_link("pages/01_overview.py",label="🧭 4系統サマリー詳細",icon="🧭")

st.title("📈 日本株スクリーナー v3")
st.caption("普段使いは4系統＋総合スコア。A〜Gの細かい条件は『詳細条件』へ整理しました。")

summary=load_sheet("4系統サマリー")
earnings=load_sheet("決算モメンタム")
overlap=load_sheet("重複分析")
log_df=load_sheet("実行ログ")

if not log_df.empty:
    last=log_df.iloc[-1]
    st.caption(f"最終スキャン: {last.get('最終実行日時','不明')} ｜ 対象 {last.get('対象銘柄数','-')} 銘柄 ｜ トリガー: {last.get('トリガー種別','-')}")

if not summary.empty:
    summary=numeric(summary,["総合スコア","TURNAROUNDスコア","PULLBACKスコア","BREAKOUTスコア","EARNINGSスコア"])
    turn=int((summary.get("TURNAROUNDスコア",pd.Series(dtype=float)).fillna(0)>0).sum()) if "TURNAROUNDスコア" in summary.columns else 0
    pull=int((summary.get("PULLBACKスコア",pd.Series(dtype=float)).fillna(0)>0).sum()) if "PULLBACKスコア" in summary.columns else 0
    brk=int((summary.get("BREAKOUTスコア",pd.Series(dtype=float)).fillna(0)>0).sum()) if "BREAKOUTスコア" in summary.columns else 0
    earn=int((summary.get("EARNINGSスコア",pd.Series(dtype=float)).fillna(0)>0).sum()) if "EARNINGSスコア" in summary.columns else len(earnings)
    high=int((summary.get("総合スコア",pd.Series(dtype=float)).fillna(0)>=80).sum()) if "総合スコア" in summary.columns else 0
    m=st.columns(5)
    m[0].metric("🏆 総合80点以上",high)
    m[1].metric("🔄 TURNAROUND",turn)
    m[2].metric("🎯 PULLBACK",pull)
    m[3].metric("🚀 BREAKOUT",brk)
    m[4].metric("🔥 EARNINGS",earn)
else:
    st.warning("4系統サマリーがまだ未生成です。次回スキャン完了後に総合スコアが表示されます。")

st.divider()

tabs=st.tabs(["🏆 総合ランキング","🔥 決算モメンタム","🔄 底打ち転換","🎯 押し目","🚀 ブレイク","⭐ ウォッチリスト","📊 チャート","🔎 重複分析","⚙️ 詳細条件"])

with tabs[0]:
    st.subheader("🏆 総合ランキング")
    st.caption("4系統の強さを0〜100点でまとめた普段使いのランキングです。")
    render_rank_table(summary,key="overall")

with tabs[1]:
    st.subheader("🔥 決算モメンタム")
    if earnings.empty:
        st.info("決算モメンタムの該当銘柄はありません。")
    else:
        earnings=numeric(earnings,["スコア","決算後騰落率%","RS風"])
        if "スコア" in earnings.columns:
            earnings=earnings.sort_values("スコア",ascending=False)
        cols=[c for c in ["順位","証券コード","Ticker","銘柄名","スコア","ランク","シグナル","決算日","決算後騰落率%","決算反応出来高倍率","RS風"] if c in earnings.columns]
        st.dataframe(earnings[cols],use_container_width=True,height=540,hide_index=True)

with tabs[2]:
    st.subheader("🔄 TURNAROUND｜底打ち転換")
    st.caption("週足A + GC底打ちF。下降トレンド終了〜上昇転換の候補。")
    render_rank_table(summary,"TURNAROUND","turnaround")

with tabs[3]:
    st.subheader("🎯 PULLBACK｜上昇トレンド押し目")
    st.caption("B1 + B2 + 初押しD。上昇トレンド中の押し目・反発候補。")
    render_rank_table(summary,"PULLBACK","pullback")

with tabs[4]:
    st.subheader("🚀 BREAKOUT｜出来高ブレイク")
    st.caption("ボリバンC + 出来高E + ポケットピボットG。値動きが加速し始めた候補。")
    render_rank_table(summary,"BREAKOUT","breakout")

with tabs[5]:
    st.subheader("⭐ ウォッチリスト")
    wl=load_sheet("ウォッチリスト")
    if wl.empty:
        st.info("ウォッチリストは空です。A〜G詳細画面から登録できます。")
    else:
        st.dataframe(wl,use_container_width=True,height=520,hide_index=True)
        st.caption("登録・解除は『⚙️ A〜G 詳細条件』画面で行えます。")

with tabs[6]:
    st.subheader("📊 チャート")
    source=summary.copy() if not summary.empty else pd.DataFrame()
    if source.empty or "Ticker" not in source.columns:
        st.info("ランキングデータがありません。")
    else:
        labels=[]
        for _,r in source.iterrows():
            labels.append(f"{r.get('証券コード','')}　{r.get('銘柄名','')}　[{r.get('総合スコア','-')}]".strip())
        idx=st.selectbox("銘柄",range(len(labels)),format_func=lambda i:labels[i])
        row=source.iloc[idx]
        ticker=str(row.get("Ticker","")).strip()
        period_label=st.radio("表示期間",["3ヶ月","6ヶ月","1年","2年"],index=2,horizontal=True)
        period_map={"3ヶ月":"3mo","6ヶ月":"6mo","1年":"1y","2年":"2y"}
        dfc=fetch_chart_data(ticker,period_map[period_label])
        if dfc.empty:
            st.error("チャートデータを取得できませんでした。")
        else:
            chart=dfc[[c for c in ["Close"] if c in dfc.columns]].copy()
            st.line_chart(chart,use_container_width=True,height=420)
            latest=dfc.iloc[-1]
            prev=dfc.iloc[-2] if len(dfc)>1 else latest
            ch=float(latest["Close"])-float(prev["Close"])
            pct=ch/float(prev["Close"])*100 if float(prev["Close"]) else 0
            c=st.columns(4)
            c[0].metric("終値",f"¥{float(latest['Close']):,.0f}",f"{ch:+,.0f} ({pct:+.2f}%)")
            if "High" in latest:c[1].metric("高値",f"¥{float(latest['High']):,.0f}")
            if "Low" in latest:c[2].metric("安値",f"¥{float(latest['Low']):,.0f}")
            if "Volume" in latest:c[3].metric("出来高",f"{int(float(latest['Volume'])):,}")

with tabs[7]:
    st.subheader("🔎 重複分析")
    if overlap.empty:
        st.info("重複分析は次回スキャン後に表示されます。")
    else:
        for c in ["共通銘柄数","小さい側に対する重複率%","Jaccard重複率%"]:
            if c in overlap.columns: overlap[c]=pd.to_numeric(overlap[c],errors="coerce")
        if "小さい側に対する重複率%" in overlap.columns:
            overlap=overlap.sort_values("小さい側に対する重複率%",ascending=False)
        st.dataframe(overlap,use_container_width=True,height=520,hide_index=True)
        st.caption("重複率が高い組み合わせは統合候補。数週間の実データを見てから削除判断します。")

with tabs[8]:
    st.subheader("⚙️ A〜G 詳細条件")
    st.write("従来の週足A / B1 / B2 / C / D / E / F / G、ウォッチリスト登録、詳細チャートは残してあります。")
    st.page_link("pages/99_legacy.py",label="従来のA〜G詳細画面を開く",icon="⚙️")
