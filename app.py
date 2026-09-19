import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

EXPECTED_SCORE_VERSION="v5-actionability"

st.set_page_config(page_title="日本株スクリーナー", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
div[data-testid="stMetric"] {background:#f8f9fb;border:1px solid #e4e7ee;border-radius:10px;padding:10px 14px;}
div[data-testid="stMetric"] label {font-size:.78rem;color:#667;}
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
        df=pd.DataFrame(values[1:],columns=values[0])
        # Google Sheets側に同名ヘッダーが残っていてもUIを落とさない
        df=df.loc[:,~df.columns.duplicated(keep="first")].copy()
        return df
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def fetch_chart_data(ticker:str,period:str="1y"):
    try:
        df=yf.download(ticker,period=period,interval="1d",auto_adjust=True,progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns,pd.MultiIndex):
            if any(x in df.columns.get_level_values(0) for x in ["Open","High","Low","Close","Volume"]):
                df.columns=df.columns.get_level_values(0)
            else:
                df.columns=df.columns.get_level_values(-1)
        df=df.loc[:,~df.columns.duplicated()].copy()
        return df
    except Exception:
        return pd.DataFrame()


def numeric(df,cols):
    out=df.loc[:,~df.columns.duplicated(keep="first")].copy()
    for c in dict.fromkeys(cols):
        if c in out.columns:
            out[c]=pd.to_numeric(out[c],errors="coerce")
    return out


def filter_search(df,q):
    if df.empty or not q:
        return df
    mask=pd.Series(False,index=df.index)
    for c in ["証券コード","Ticker","銘柄名"]:
        if c in df.columns:
            mask |= df[c].astype(str).str.contains(q.strip(),case=False,na=False)
    return df[mask]


def scalar_value(row,col,default=np.nan):
    try:
        v=row[col]
        if isinstance(v,pd.Series):
            v=v.iloc[0] if len(v) else default
        if isinstance(v,np.ndarray):
            v=v.reshape(-1)[0] if v.size else default
        return v
    except Exception:
        return default


def render_rank_table(df,category=None,key="rank"):
    if df.empty:
        st.info("まだデータがありません。スキャン完了後に表示されます。")
        return

    df=numeric(df,[
        "総合スコア","TURNAROUNDスコア","PULLBACKスコア","BREAKOUTスコア",
        "EARNINGSスコア","実戦スコア","主力品質","テクニカル総合","パターン構造","価格品質",
        "TURNAROUND品質","PULLBACK品質","BREAKOUT品質",
        "20MA乖離%","50MA乖離%","MA傾き","52週高値距離%","20日レンジ位置","20日ブレイク距離%",
        "RS Rating","出来高モメンタム","当日出来高倍率","5日出来高倍率","上昇日出来高比率%","終値"
    ])

    # 今日見るべき: 実戦82以上 + RS80以上 + 出来高M60以上 + 過熱=適正
    practical = pd.to_numeric(df.get("実戦スコア", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    rs = pd.to_numeric(df.get("RS Rating", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    volm = pd.to_numeric(df.get("出来高モメンタム", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    heat = df.get("過熱判定", pd.Series("", index=df.index)).astype(str)
    today_mask = (practical >= 82) & (rs >= 80) & (volm >= 60) & heat.str.contains("適正", na=False)
    df["今日見るべき"] = np.where(today_mask, "🔥 今日見るべき", "")

    if category and f"{category}スコア" in df.columns:
        df=df[df[f"{category}スコア"].fillna(0)>0]

    f1,f2,f3,f4=st.columns([1.8,1.0,1.0,1.0])
    with f1:
        q=st.text_input("銘柄コード・銘柄名で検索",key=f"q_{key}")
    with f2:
        min_practical=st.slider("最低実戦",0,100,0,5,key=f"practical_{key}")
    with f3:
        min_rs=st.slider("最低RS",0,99,0,5,key=f"rs_{key}")
    with f4:
        min_vol=st.slider("最低出来高M",0,100,0,5,key=f"vol_{key}")

    g1,g2,g3,g4=st.columns([1.0,1.0,1.0,1.0])
    with g1:
        signal=st.selectbox("主力シグナル",["すべて","TURNAROUND","PULLBACK","BREAKOUT","EARNINGS"],key=f"signal_{key}")
    with g2:
        heat_filter=st.selectbox("過熱判定",["すべて","🟢 適正","🟡 注意","⚠️ 高"],key=f"heat_{key}")
    with g3:
        today_only=st.checkbox("🔥 今日見るべきだけ",value=False,key=f"today_{key}")
    with g4:
        rank=st.selectbox("総合ランク",["すべて","S","A","B","C","D","E"],key=f"rank_{key}")

    df=filter_search(df,q)
    if "実戦スコア" in df.columns:
        df=df[df["実戦スコア"].fillna(0)>=min_practical]
    if "RS Rating" in df.columns:
        df=df[df["RS Rating"].fillna(0)>=min_rs]
    if "出来高モメンタム" in df.columns:
        df=df[df["出来高モメンタム"].fillna(0)>=min_vol]
    if signal!="すべて" and "主力シグナル" in df.columns:
        df=df[df["主力シグナル"].astype(str)==signal]
    if heat_filter!="すべて" and "過熱判定" in df.columns:
        df=df[df["過熱判定"].astype(str)==heat_filter]
    if today_only and "今日見るべき" in df.columns:
        df=df[df["今日見るべき"].astype(str).ne("")]
    if rank!="すべて" and "総合ランク" in df.columns:
        df=df[df["総合ランク"].astype(str)==rank]

    sort_cols=[c for c in ["実戦スコア","総合スコア","RS Rating","出来高モメンタム"] if c in df.columns]
    if sort_cols:
        df=df.sort_values(sort_cols,ascending=[False]*len(sort_cols))

    cols=[c for c in [
        "今日見るべき","証券コード","Ticker","銘柄名","終値","実戦スコア","実戦ステータス","主力シグナル","主力品質",
        "過熱判定","過熱ペナルティ","総合スコア","総合ランク",
        "RS Rating","出来高モメンタム","テクニカル総合","価格品質","パターン構造",
        "TURNAROUND品質","PULLBACK品質","BREAKOUT品質",
        "20MA乖離%","50MA乖離%","MA傾き","52週高値距離%","20日レンジ位置","20日ブレイク距離%",
        "EARNINGSスコア",
        "TURNAROUNDスコア","PULLBACKスコア","BREAKOUTスコア",
        "当日出来高倍率","5日出来高倍率","上昇日出来高比率%",
        "4系統該当","TURNAROUND","PULLBACK","BREAKOUT"
    ] if c in df.columns]
    # 表示候補側にも同名列が混ざってもArrow変換で落ちないよう最終防御
    cols=list(dict.fromkeys(cols))
    display_df=df.loc[:,~df.columns.duplicated(keep="first")][cols].reset_index(drop=True)

    st.caption(f"該当 {len(df)} 銘柄")
    st.dataframe(display_df,use_container_width=True,height=540,hide_index=True)
    csv=df.to_csv(index=False,encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button("📥 CSVで保存",data=csv,file_name=f"{key}.csv",mime="text/csv",key=f"dl_{key}")


with st.sidebar:
    st.header("🔍 操作")
    if st.button("🔄 最新の結果を再取得",use_container_width=True):
        load_sheet.clear()
        fetch_chart_data.clear()
        st.rerun()
    st.caption("スキャン直後は再取得してください。")
    st.divider()
    st.page_link("pages/99_legacy.py",label="⚙️ A〜G 詳細条件を開く",icon="⚙️")
    st.page_link("pages/01_overview.py",label="🧭 4系統サマリー詳細",icon="🧭")

st.title("📈 日本株スクリーナー v5")
st.caption("4系統 + 決算 + RS Rating + 出来高モメンタムで総合評価します。")

summary=load_sheet("4系統サマリー")
earnings=load_sheet("決算モメンタム")
overlap=load_sheet("重複分析")
log_df=load_sheet("実行ログ")

if not log_df.empty:
    last=log_df.iloc[-1]
    st.caption(f"最終スキャン: {last.get('最終実行日時','不明')} ｜ 対象 {last.get('対象銘柄数','-')} 銘柄 ｜ トリガー: {last.get('トリガー種別','-')}")

if not summary.empty:
    data_version = str(summary.iloc[0].get("スコアバージョン", "")).strip()
    data_updated = str(summary.iloc[0].get("スコア更新日時", "")).strip()
    if data_version != EXPECTED_SCORE_VERSION:
        st.warning(
            "⚠️ 表示UIは最新版ですが、ランキングデータは旧スコアのままです。"
            " GitHub Actionsをmainで1回実行し、完了後に「最新の結果を再取得」を押してください。"
        )
    else:
        st.success(f"✅ スコアデータ: {data_version} ｜ 更新: {data_updated or '不明'}")

    summary=numeric(summary,[
        "総合スコア","TURNAROUNDスコア","PULLBACKスコア","BREAKOUTスコア",
        "EARNINGSスコア","RS Rating","出来高モメンタム"
    ])
    turn=int((summary["TURNAROUNDスコア"].fillna(0)>0).sum()) if "TURNAROUNDスコア" in summary.columns else 0
    pull=int((summary["PULLBACKスコア"].fillna(0)>0).sum()) if "PULLBACKスコア" in summary.columns else 0
    brk=int((summary["BREAKOUTスコア"].fillna(0)>0).sum()) if "BREAKOUTスコア" in summary.columns else 0
    earn=int((summary["EARNINGSスコア"].fillna(0)>0).sum()) if "EARNINGSスコア" in summary.columns else len(earnings)
    high75=int((summary["総合スコア"].fillna(0)>=75).sum()) if "総合スコア" in summary.columns else 0
    strong=int((pd.to_numeric(summary.get("実戦スコア",0),errors="coerce").fillna(0)>=82).sum()) if "実戦スコア" in summary.columns else 0
    m=st.columns(5)
    m[0].metric("🔥 実戦82点以上",strong)
    m[1].metric("🔄 TURNAROUND",turn)
    m[2].metric("🎯 PULLBACK",pull)
    m[3].metric("🚀 BREAKOUT",brk)
    m[4].metric("🔥 EARNINGS",earn)
else:
    st.warning("4系統サマリーがまだ未生成です。次回スキャン完了後に総合スコアが表示されます。")

st.divider()
sections=[
    "🏆 総合ランキング","🔥 決算モメンタム","🔄 底打ち転換","🎯 押し目","🚀 ブレイク",
    "⭐ ウォッチリスト","📊 チャート","🧪 実戦検証","🔎 重複分析","⚙️ 詳細条件"
]
section=st.radio("表示",sections,horizontal=True,label_visibility="collapsed",key="main_section")
st.divider()

if section=="🏆 総合ランキング":
    st.subheader("🏆 総合ランキング")
    st.caption("総合スコアv5：実戦スコア・主力シグナル・過熱判定で優先順位を整理。『今日見るべき』は 実戦82以上・RS80以上・出来高M60以上・過熱適正 です。")
    render_rank_table(summary,key="overall")

elif section=="🔥 決算モメンタム":
    st.subheader("🔥 決算モメンタム")
    if earnings.empty:
        st.info("決算モメンタムの該当銘柄はありません。")
    else:
        out=numeric(earnings,["スコア","決算後騰落率%","RS風","決算反応出来高倍率"])
        if "スコア" in out.columns:
            out=out.sort_values("スコア",ascending=False)
        cols=[c for c in ["順位","証券コード","Ticker","銘柄名","スコア","ランク","シグナル","決算日","決算後騰落率%","決算反応出来高倍率","RS風"] if c in out.columns]
        st.dataframe(out[cols],use_container_width=True,height=540,hide_index=True)

elif section=="🔄 底打ち転換":
    st.subheader("🔄 TURNAROUND｜底打ち転換")
    st.caption("週足A + GC底打ちF。下降トレンド終了〜上昇転換の候補。")
    render_rank_table(summary,"TURNAROUND","turnaround")

elif section=="🎯 押し目":
    st.subheader("🎯 PULLBACK｜上昇トレンド押し目")
    st.caption("B1 + B2 + 初押しD。上昇トレンド中の押し目・反発候補。")
    render_rank_table(summary,"PULLBACK","pullback")

elif section=="🚀 ブレイク":
    st.subheader("🚀 BREAKOUT｜出来高ブレイク")
    st.caption("ボリバンC + 出来高E + ポケットピボットG。値動きが加速し始めた候補。")
    render_rank_table(summary,"BREAKOUT","breakout")

elif section=="⭐ ウォッチリスト":
    st.subheader("⭐ ウォッチリスト")
    wl=load_sheet("ウォッチリスト")
    if wl.empty:
        st.info("ウォッチリストは空です。A〜G詳細画面から登録できます。")
    else:
        st.dataframe(wl,use_container_width=True,height=520,hide_index=True)
        st.caption("登録・解除は『⚙️ A〜G 詳細条件』画面で行えます。")

elif section=="📊 チャート":
    st.subheader("📊 チャート")
    source=summary.copy() if not summary.empty else pd.DataFrame()
    if source.empty or "Ticker" not in source.columns:
        st.info("ランキングデータがありません。")
    else:
        source=source.reset_index(drop=True)
        labels=[f"{r.get('証券コード','')}　{r.get('銘柄名','')}　[総合 {r.get('総合スコア','-')} ]".strip() for _,r in source.iterrows()]
        selected_label=st.selectbox("銘柄",labels,key="chart_ticker")
        idx=labels.index(selected_label)
        row=source.iloc[idx]
        ticker=str(row.get("Ticker","")).strip()
        period_label=st.radio("表示期間",["3ヶ月","6ヶ月","1年","2年"],index=2,horizontal=True,key="chart_period")
        period_map={"3ヶ月":"3mo","6ヶ月":"6mo","1年":"1y","2年":"2y"}
        if not ticker:
            st.warning("Tickerが空です。")
        else:
            with st.spinner(f"{ticker} のチャートを取得中..."):
                dfc=fetch_chart_data(ticker,period_map[period_label])
            if dfc.empty or "Close" not in dfc.columns:
                st.error("チャートデータを取得できませんでした。")
            else:
                try:
                    st.line_chart(dfc["Close"],height=420)
                    latest=dfc.iloc[-1]
                    prev=dfc.iloc[-2] if len(dfc)>1 else latest
                    close_now=float(scalar_value(latest,"Close"))
                    close_prev=float(scalar_value(prev,"Close"))
                    ch=close_now-close_prev
                    pct=(ch/close_prev*100) if close_prev else 0.0
                    cards=st.columns(4)
                    cards[0].metric("終値",f"¥{close_now:,.0f}",f"{ch:+,.0f} ({pct:+.2f}%)")
                    high_v=scalar_value(latest,"High")
                    low_v=scalar_value(latest,"Low")
                    vol_v=scalar_value(latest,"Volume")
                    if pd.notna(high_v): cards[1].metric("高値",f"¥{float(high_v):,.0f}")
                    if pd.notna(low_v): cards[2].metric("安値",f"¥{float(low_v):,.0f}")
                    if pd.notna(vol_v): cards[3].metric("出来高",f"{int(float(vol_v)):,}")
                except Exception as e:
                    st.warning(f"チャート表示だけでエラーが発生しました: {type(e).__name__}")
                    st.caption("ランキング表示には影響ありません。")

elif section=="🧪 実戦検証":
    st.subheader("🧪 実戦スコア検証")
    st.caption("『今日見るべき』銘柄をシグナル日終値で記録し、1・5・20営業日後の騰落率を自動追跡します。")
    perf=load_sheet("実戦検証")
    perf_summary=load_sheet("実戦検証サマリー")

    if perf_summary.empty:
        st.info("まだ検証データがありません。次回GitHub Actions実行後から記録が始まります。")
    else:
        ps=numeric(perf_summary,[
            "登録件数","平均実戦スコア",
            "1日後件数","1日後平均%","1日後中央値%","1日後勝率%",
            "5日後件数","5日後平均%","5日後中央値%","5日後勝率%",
            "20日後件数","20日後平均%","20日後中央値%","20日後勝率%"
        ])
        allrow=ps[ps["区分"].astype(str)=="ALL"] if "区分" in ps.columns else pd.DataFrame()
        if not allrow.empty:
            r=allrow.iloc[0]
            cards=st.columns(4)
            cards[0].metric("累計シグナル",int(r.get("登録件数",0) or 0))
            cards[1].metric("5日後平均",f"{float(r.get('5日後平均%',0) or 0):+.2f}%")
            cards[2].metric("5日後勝率",f"{float(r.get('5日後勝率%',0) or 0):.1f}%")
            cards[3].metric("20日後平均",f"{float(r.get('20日後平均%',0) or 0):+.2f}%")

        st.markdown("#### 主力シグナル別")
        st.dataframe(ps,use_container_width=True,hide_index=True,height=260)

    if not perf.empty:
        st.markdown("#### シグナル履歴")
        show_cols=[c for c in [
            "シグナル日","証券コード","Ticker","銘柄名","主力シグナル","実戦スコア",
            "RS Rating","出来高モメンタム","シグナル終値",
            "1日後騰落率%","5日後騰落率%","20日後騰落率%","スコアバージョン"
        ] if c in perf.columns]
        st.dataframe(perf[show_cols],use_container_width=True,hide_index=True,height=480)

elif section=="🔎 重複分析":
    st.subheader("🔎 重複分析")
    if overlap.empty:
        st.info("重複分析は次回スキャン後に表示されます。")
    else:
        out=overlap.copy()
        for c in ["共通銘柄数","小さい側に対する重複率%","Jaccard重複率%"]:
            if c in out.columns:
                out[c]=pd.to_numeric(out[c],errors="coerce")
        if "小さい側に対する重複率%" in out.columns:
            out=out.sort_values("小さい側に対する重複率%",ascending=False)
        st.dataframe(out,use_container_width=True,height=520,hide_index=True)
        st.caption("重複率が高い組み合わせは統合候補。数週間の実データを見てから削除判断します。")

else:
    st.subheader("⚙️ A〜G 詳細条件")
    st.write("従来の週足A / B1 / B2 / C / D / E / F / G、ウォッチリスト登録、詳細チャートは残してあります。")
    st.page_link("pages/99_legacy.py",label="従来のA〜G詳細画面を開く",icon="⚙️")
