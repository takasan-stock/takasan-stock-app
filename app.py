"""
============================================================
日本株スクリーナー結果ビューア（表示専用）v2 - UI改善版
============================================================
このアプリ自体はスキャン処理を行わない。
GitHub Actionsが定期実行した scan.py の結果を、
Googleスプレッドシートから読み込んで表示するだけ。

必要なStreamlit Secrets（Streamlit Cloud側の「Secrets」設定）:
  [gcp_service_account]
  （サービスアカウントJSONの中身をそのままTOML形式で貼る）
  SPREADSHEET_ID = "..."
============================================================
"""

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="日本株スクリーナー",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# カスタムCSS（デフォルト感を減らし密度を上げる）
# ==========================================
st.markdown("""
<style>
div[data-testid="stMetric"] {
    background: #f8f9fb;
    border: 1px solid #e4e7ee;
    border-radius: 10px;
    padding: 10px 14px;
}
div[data-testid="stMetric"] label { font-size: 0.78rem; color: #667; }
button[data-baseweb="tab"] { font-size: 0.95rem; }
div[data-testid="stDataFrame"] { font-size: 0.88rem; }
section[data-testid="stSidebar"] h2 { font-size: 1.0rem; }
</style>
""", unsafe_allow_html=True)

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


@st.cache_data(ttl=300)  # 5分キャッシュ
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


def to_tradingview_txt(df: pd.DataFrame) -> str:
    """
    TradingViewのウォッチリスト用テキストを作る。
    形式: TSE:7203,TSE:9984,... （カンマ区切り、改行なしの1行）
    """
    if df.empty or "証券コード" not in df.columns:
        return ""
    codes = df["証券コード"].astype(str).str.strip()
    codes = [c for c in codes if c and c != "-"]
    return ",".join(f"TSE:{c}" for c in codes)


WATCHLIST_SHEET_NAME = "ウォッチリスト"

@st.cache_data(ttl=60)  # 1分キャッシュ（お気に入り操作の反映を早くする）
def load_watchlist() -> pd.DataFrame:
    """ウォッチリスト（証券コード, Ticker, 銘柄名, 登録日）を読み込む"""
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["SPREADSHEET_ID"])
    try:
        ws = sh.worksheet(WATCHLIST_SHEET_NAME)
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=["証券コード", "Ticker", "銘柄名", "登録日"])

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame(columns=["証券コード", "Ticker", "銘柄名", "登録日"])
    return pd.DataFrame(values[1:], columns=values[0])


def save_watchlist(df: pd.DataFrame):
    """ウォッチリスト全体を書き戻す"""
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["SPREADSHEET_ID"])
    try:
        ws = sh.worksheet(WATCHLIST_SHEET_NAME)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WATCHLIST_SHEET_NAME, rows=500, cols=10)

    if df.empty:
        ws.update([["証券コード", "Ticker", "銘柄名", "登録日"]])
    else:
        values = [df.columns.tolist()] + df.astype(str).values.tolist()
        ws.update(values)
    load_watchlist.clear()


def add_to_watchlist(code: str, ticker: str, name: str):
    wl = load_watchlist()
    if ticker in wl.get("Ticker", pd.Series(dtype=str)).values:
        return  # 既に登録済み
    import datetime
    new_row = pd.DataFrame([{
        "証券コード": code, "Ticker": ticker, "銘柄名": name,
        "登録日": datetime.date.today().strftime("%Y-%m-%d"),
    }])
    wl = pd.concat([wl, new_row], ignore_index=True)
    save_watchlist(wl)


def remove_from_watchlist(ticker: str):
    wl = load_watchlist()
    wl = wl[wl["Ticker"] != ticker]
    save_watchlist(wl)


NUMERIC_HINTS = [
    "終値", "始値", "高値", "安値", "MA", "BAND", "距離", "傾き", "RSI",
    "売買代金", "時価総額", "出来高", "乖離", "σ", "騰落率", "日数",
    "回数", "件数", "パターン数", "比",
]

def to_display_df(df: pd.DataFrame) -> pd.DataFrame:
    """数値らしい列を数値化して表示用に整える"""
    out = df.copy()
    for col in out.columns:
        if any(h in col for h in NUMERIC_HINTS):
            converted = pd.to_numeric(out[col], errors="coerce")
            # 半分以上が数値化できた列だけ置き換える（"-"混在の列を守る）
            if converted.notna().sum() >= len(out) * 0.5:
                out[col] = converted
    return out


def filter_df(df: pd.DataFrame, query: str, first_only: bool) -> pd.DataFrame:
    """サイドバーの検索・絞り込みを適用する"""
    out = df
    if query:
        q = query.strip()
        mask = pd.Series(False, index=out.index)
        for col in ("証券コード", "Ticker", "銘柄名"):
            if col in out.columns:
                mask |= out[col].astype(str).str.contains(q, case=False, na=False)
        out = out[mask]
    if first_only and "前回抽出日" in out.columns:
        out = out[out["前回抽出日"].astype(str) == "初回"]
    return out


def render_sheet_tab(title: str, sheet_name: str, query: str, first_only: bool):
    """1つのシートタブの中身を描画する（行クリックでチャート表示）"""
    df = load_sheet(sheet_name)
    if df.empty or "該当銘柄なし" in df.columns:
        st.info("該当銘柄はありません。")
        return

    df = to_display_df(df)
    df = filter_df(df, query, first_only)

    # ── ウォッチリスト銘柄がヒットしているかチェック ──────
    wl = load_watchlist()
    wl_tickers = set(wl["Ticker"].astype(str)) if not wl.empty else set()

    hit_note = f"該当 {len(df)} 銘柄"
    if query or first_only:
        hit_note += "（絞り込み適用中）"
    st.caption(hit_note + "　💡 行をクリックするとチャートが表示されます")

    if not df.empty and "Ticker" in df.columns and wl_tickers:
        wl_hit = df[df["Ticker"].astype(str).isin(wl_tickers)]
        if not wl_hit.empty:
            names = ", ".join(
                (wl_hit["銘柄名"] if "銘柄名" in wl_hit.columns
                 else wl_hit["Ticker"]).astype(str).tolist()
            )
            st.success(f"⭐ ウォッチリスト銘柄がヒットしています: {names}")

    if df.empty:
        st.info("絞り込み条件に一致する銘柄はありません。")
        return

    df = df.reset_index(drop=True)

    # ── ウォッチリスト銘柄には★マークを付ける ─────────────
    if "Ticker" in df.columns and wl_tickers:
        df.insert(0, "⭐", df["Ticker"].astype(str).apply(
            lambda t: "⭐" if t in wl_tickers else ""))

    # 複数パターン合致のハイライト
    if "合致パターン数" in df.columns:
        df["合致パターン数"] = pd.to_numeric(df["合致パターン数"], errors="coerce")
        table_data = df.style.apply(
            lambda x: ['background-color: #fff3c4'
                       if float(x.get("合致パターン数", 0) or 0) >= 2 else ''
                       for _ in x],
            axis=1,
        )
    else:
        table_data = df

    # 行選択イベント付きのテーブル
    event = st.dataframe(
        table_data,
        use_container_width=True,
        height=460,
        on_select="rerun",
        selection_mode="single-row",
        key=f"table_{sheet_name}",
    )

    dl_col1, dl_col2 = st.columns(2)
    with dl_col1:
        csv = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(
            label="📥 CSVで保存",
            data=csv,
            file_name=f"{sheet_name}.csv",
            mime="text/csv",
            key=f"dl_{sheet_name}",
        )
    with dl_col2:
        tv_txt = to_tradingview_txt(df)
        st.download_button(
            label="📈 TradingView用txt",
            data=tv_txt.encode("utf-8"),
            file_name=f"{sheet_name}_tradingview.txt",
            mime="text/plain",
            key=f"tv_{sheet_name}",
            disabled=(tv_txt == ""),
            help="TradingViewのウォッチリストにインポートできる形式（TSE:コード,...）で保存します",
        )

    # ── 行が選択されたらチャート＋お気に入り操作を表示 ──────
    sel_rows = []
    try:
        sel_rows = event.selection.rows
    except Exception:
        sel_rows = []

    if sel_rows:
        row = df.iloc[sel_rows[0]]
        ticker = str(row.get("Ticker", "")).strip()
        name   = str(row.get("銘柄名", "")).strip()
        code   = str(row.get("証券コード", "")).strip()

        if ticker:
            st.divider()
            head_l, head_r, head_star = st.columns([3, 2, 1])
            with head_l:
                st.markdown(f"#### 📊 {code}　{name}")
            with head_r:
                period_label = st.radio(
                    "表示期間",
                    ["3ヶ月", "6ヶ月", "1年", "2年"],
                    index=1,
                    horizontal=True,
                    label_visibility="collapsed",
                    key=f"period_{sheet_name}",
                )
            with head_star:
                is_fav = ticker in wl_tickers
                if is_fav:
                    if st.button("⭐ 解除", key=f"unfav_{sheet_name}",
                                use_container_width=True):
                        remove_from_watchlist(ticker)
                        st.rerun()
                else:
                    if st.button("☆ お気に入り登録", key=f"fav_{sheet_name}",
                                use_container_width=True):
                        add_to_watchlist(code, ticker, name)
                        st.rerun()
            period_map = {"3ヶ月": "3mo", "6ヶ月": "6mo", "1年": "1y", "2年": "2y"}

            with st.spinner(f"{ticker} のチャートを取得中..."):
                chart_df = fetch_chart_data(ticker, period=period_map[period_label])

            if chart_df.empty:
                st.error(f"{ticker} のデータを取得できませんでした。")
            else:
                fig = build_chart(chart_df, ticker, name)
                st.plotly_chart(fig, use_container_width=True,
                                key=f"plot_{sheet_name}")

                latest = chart_df.iloc[-1]
                prev   = chart_df.iloc[-2] if len(chart_df) >= 2 else latest
                change     = float(latest["Close"]) - float(prev["Close"])
                change_pct = change / float(prev["Close"]) * 100

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("終値",   f"¥{float(latest['Close']):,.0f}",
                          f"{change:+,.0f} ({change_pct:+.2f}%)")
                c2.metric("高値",   f"¥{float(latest['High']):,.0f}")
                c3.metric("安値",   f"¥{float(latest['Low']):,.0f}")
                c4.metric("出来高", f"{int(float(latest['Volume'])):,}")


# ==========================================
# チャート関連
# ==========================================
@st.cache_data(ttl=3600)
def fetch_chart_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    try:
        df = yf.download(ticker, period=period, interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return pd.DataFrame()


def build_chart(df: pd.DataFrame, ticker: str, name: str) -> go.Figure:
    c = df["Close"]
    df["MA21"]  = c.rolling(21).mean()
    df["MA50"]  = c.rolling(50).mean()
    df["MA200"] = c.rolling(200).mean()
    df["BB_MID"] = c.rolling(20).mean()
    df["BB_STD"] = c.rolling(20).std()
    df["BB_U2"]  = df["BB_MID"] + df["BB_STD"] * 2
    df["BB_L2"]  = df["BB_MID"] - df["BB_STD"] * 2
    df["BB_U1"]  = df["BB_MID"] + df["BB_STD"] * 1
    df["BB_L1"]  = df["BB_MID"] - df["BB_STD"] * 1

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04, row_heights=[0.78, 0.22],
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="ローソク足",
        increasing_line_color="#1B75BB", decreasing_line_color="#E94747",
        increasing_fillcolor="#1B75BB", decreasing_fillcolor="#E94747",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df.index, y=df["BB_U2"],
        line=dict(color="rgba(180,130,255,0.4)", width=1),
        name="+2σ",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["BB_L2"],
        line=dict(color="rgba(180,130,255,0.4)", width=1),
        fill="tonexty", fillcolor="rgba(180,130,255,0.07)",
        name="-2σ",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["BB_U1"],
        line=dict(color="rgba(180,130,255,0.25)", width=0.8, dash="dot"),
        name="+1σ", showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["BB_L1"],
        line=dict(color="rgba(180,130,255,0.25)", width=0.8, dash="dot"),
        name="-1σ", showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["BB_MID"],
        line=dict(color="rgba(180,130,255,0.7)", width=1.2, dash="dash"),
        name="BB中心(20MA)",
    ), row=1, col=1)

    for col_name, color, width in [
        ("MA21", "#F4A460", 1.5),
        ("MA50", "#3CB371", 1.5),
        ("MA200", "#FF6347", 2.0),
    ]:
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col_name],
            line=dict(color=color, width=width), name=col_name,
        ), row=1, col=1)

    colors = ["#1B75BB" if r["Close"] >= r["Open"] else "#E94747"
              for _, r in df.iterrows()]
    fig.add_trace(go.Bar(
        x=df.index, y=df["Volume"], name="出来高",
        marker_color=colors, opacity=0.7, showlegend=False,
    ), row=2, col=1)

    name_label = f"　{name}" if name and name != "-" else ""
    fig.update_layout(
        title=dict(text=f"{ticker}{name_label}　日足チャート", font=dict(size=16)),
        height=640,
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.01,
                    xanchor="left", x=0, font=dict(size=11)),
        xaxis_rangeslider_visible=False,
        xaxis2=dict(rangeslider=dict(visible=True, thickness=0.04)),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified",
    )
    fig.update_yaxes(gridcolor="rgba(200,200,200,0.3)")
    fig.update_xaxes(gridcolor="rgba(200,200,200,0.3)")
    return fig


# ==========================================
# サイドバー（共通操作を集約）
# ==========================================
with st.sidebar:
    st.header("🔍 検索・絞り込み")
    query = st.text_input(
        "銘柄コード・銘柄名で検索",
        placeholder="例: 7203 / トヨタ",
        key="sb_query",
    )
    first_only = st.toggle("🆕 初回抽出のみ表示", key="sb_first_only",
                           help="「前回抽出日」が初回の銘柄だけに絞り込みます")

    st.divider()
    if st.button("🔄 最新の結果を再取得", key="btn_refresh", use_container_width=True):
        load_sheet.clear()
        st.rerun()
    st.caption("結果は5分間キャッシュされます。スキャン直後はこのボタンで更新してください。")

    st.divider()
    st.caption(
        "データ更新: GitHub Actionsが毎営業日 18時頃に自動スキャンし、"
        "Googleスプレッドシートへ保存しています。"
    )

# ==========================================
# ヘッダー & サマリーカード
# ==========================================
st.title("📈 日本株スクリーナー")

log_df = load_sheet("実行ログ")
if not log_df.empty:
    last = log_df.iloc[-1]
    st.caption(f"最終スキャン: {last.get('最終実行日時', '不明')}　|　"
               f"対象 {last.get('対象銘柄数', '-')} 銘柄　|　"
               f"トリガー: {last.get('トリガー種別', '-')}")

    m = st.columns(7)
    m[0].metric("⭐ 複数合致",  last.get("複数合致件数", "-"))
    m[1].metric("週足A",        last.get("週足A件数", "-"))
    m[2].metric("日足B1 押し目", last.get("日足B1件数", "-"))
    m[3].metric("日足B2 反発",   last.get("日足B2件数", "-"))
    m[4].metric("ボリバンC",     last.get("ボリバンC件数", "-"))
    m[5].metric("初押しD",       last.get("初押しD件数", "-"))
    m[6].metric("出来高E",       last.get("出来高E件数", "-"))
else:
    st.warning("実行ログが見つかりません。GitHub Actionsがまだ一度も実行されていない可能性があります。")

st.divider()

# ==========================================
# タブ
# ==========================================
tabs = st.tabs([
    "⭐ 複数合致",
    "週足A",
    "B1 押し目🟡",
    "B2 反発🚀",
    "ボリバンC💥",
    "初押しD🎯",
    "出来高E📢",
    "🌟 ウォッチリスト",
    "📊 チャート",
])

sheet_map = [
    (tabs[0], "複数パターン合致",             "複数パターン合致"),
    (tabs[1], "週足パターンA（長期）",         "週足パターンA"),
    (tabs[2], "日足B1 押し目待ち",             "日足B1押し目待ち"),
    (tabs[3], "日足B2 反発エントリー",         "日足B2反発エントリー"),
    (tabs[4], "ボリンジャーバンド +2σ ブレイク", "ボリバンCブレイク"),
    (tabs[5], "初押し・SMA25タッチ 下ひげ陽線", "初押しD下ひげ陽線"),
    (tabs[6], "揉み合い後の出来高急増ブレイク",   "出来高E急増ブレイク"),
]

for tab, title, sheet_name in sheet_map:
    with tab:
        render_sheet_tab(title, sheet_name, query, first_only)

# ==========================================
# ウォッチリストタブ
# ==========================================
with tabs[7]:
    st.subheader("🌟 ウォッチリスト（お気に入り銘柄）")
    st.caption(
        "各タブの銘柄をクリック→「☆ お気に入り登録」で追加できます。"
        "登録した銘柄が他のスクリーニング結果にヒットすると、該当タブの上部に通知が表示されます。"
    )

    wl = load_watchlist()
    if wl.empty:
        st.info("まだお気に入り登録された銘柄がありません。")
    else:
        # 表示用に整形
        wl_disp = wl.reset_index(drop=True)
        event_wl = st.dataframe(
            wl_disp,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            key="table_watchlist",
        )

        del_col1, del_col2 = st.columns([1, 4])
        sel_rows_wl = []
        try:
            sel_rows_wl = event_wl.selection.rows
        except Exception:
            sel_rows_wl = []

        if sel_rows_wl:
            sel_row = wl_disp.iloc[sel_rows_wl[0]]
            sel_ticker = str(sel_row.get("Ticker", ""))
            sel_name   = str(sel_row.get("銘柄名", ""))
            with del_col1:
                if st.button(f"🗑️ 削除", key="btn_remove_watchlist",
                            use_container_width=True):
                    remove_from_watchlist(sel_ticker)
                    st.rerun()
            with del_col2:
                st.caption(f"選択中: {sel_name}（{sel_ticker}）")

            # ウォッチリスト内の銘柄もチャート表示できるように
            st.divider()
            period_label_wl = st.radio(
                "表示期間", ["3ヶ月", "6ヶ月", "1年", "2年"],
                index=1, horizontal=True, key="period_watchlist",
            )
            period_map = {"3ヶ月": "3mo", "6ヶ月": "6mo", "1年": "1y", "2年": "2y"}
            with st.spinner(f"{sel_ticker} のチャートを取得中..."):
                chart_df_wl = fetch_chart_data(sel_ticker, period=period_map[period_label_wl])
            if not chart_df_wl.empty:
                fig_wl = build_chart(chart_df_wl, sel_ticker, sel_name)
                st.plotly_chart(fig_wl, use_container_width=True, key="plot_watchlist")

        # TradingView用の一括エクスポートもここで
        tv_txt_wl = to_tradingview_txt(wl)
        st.download_button(
            label="📈 ウォッチリストをTradingView用txtで保存",
            data=tv_txt_wl.encode("utf-8"),
            file_name="watchlist_tradingview.txt",
            mime="text/plain",
            key="tv_watchlist",
            disabled=(tv_txt_wl == ""),
        )

# ==========================================
# チャートタブ
# ==========================================
with tabs[8]:
    left_col, right_col = st.columns([1, 3])

    with left_col:
        st.markdown("##### 銘柄を選択")

        source_options = {
            "⭐ 複数パターン合致": "複数パターン合致",
            "週足パターンA":       "週足パターンA",
            "日足B1 押し目待ち":   "日足B1押し目待ち",
            "日足B2 反発エントリー": "日足B2反発エントリー",
            "ボリバンC ブレイク":  "ボリバンCブレイク",
            "初押しD 下ひげ陽線":  "初押しD下ひげ陽線",
            "出来高E 急増ブレイク": "出来高E急増ブレイク",
            "🌟 ウォッチリスト":   "__watchlist__",
        }
        selected_source = st.selectbox(
            "表示するリスト",
            list(source_options.keys()),
            key="chart_source",
        )
        source_key = source_options[selected_source]
        if source_key == "__watchlist__":
            df_source = load_watchlist()
        else:
            df_source = load_sheet(source_key)

        ticker_options = []
        if not df_source.empty and "Ticker" in df_source.columns:
            if "銘柄名" in df_source.columns:
                labels = (df_source["証券コード"].astype(str)
                          + "　" + df_source["銘柄名"].astype(str))
            else:
                labels = df_source["Ticker"].astype(str)
            ticker_options = list(zip(labels, df_source["Ticker"].astype(str)))

        selected_ticker = None
        if ticker_options:
            selected_label = st.selectbox(
                "銘柄",
                [lbl for lbl, _ in ticker_options],
                key="chart_ticker",
            )
            selected_ticker = dict(ticker_options)[selected_label]

            selected_period_label = st.radio(
                "表示期間",
                ["3ヶ月", "6ヶ月", "1年", "2年"],
                index=2,
                horizontal=True,
                key="chart_period",
            )
            period_map = {"3ヶ月": "3mo", "6ヶ月": "6mo", "1年": "1y", "2年": "2y"}
            selected_period = period_map[selected_period_label]
        else:
            st.info("このリストに銘柄がありません。")

    with right_col:
        if ticker_options and selected_ticker:
            if "銘柄名" in df_source.columns:
                nr = df_source[df_source["Ticker"] == selected_ticker]
                company_name = nr["銘柄名"].iloc[0] if not nr.empty else ""
            else:
                company_name = ""

            with st.spinner(f"{selected_ticker} のチャートを取得中..."):
                chart_df = fetch_chart_data(selected_ticker, period=selected_period)

            if chart_df.empty:
                st.error(f"{selected_ticker} のデータを取得できませんでした。")
            else:
                fig = build_chart(chart_df, selected_ticker, company_name)
                st.plotly_chart(fig, use_container_width=True)

                latest = chart_df.iloc[-1]
                prev   = chart_df.iloc[-2] if len(chart_df) >= 2 else latest
                change     = float(latest["Close"]) - float(prev["Close"])
                change_pct = change / float(prev["Close"]) * 100

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("終値",   f"¥{float(latest['Close']):,.0f}",
                          f"{change:+,.0f} ({change_pct:+.2f}%)")
                c2.metric("高値",   f"¥{float(latest['High']):,.0f}")
                c3.metric("安値",   f"¥{float(latest['Low']):,.0f}")
                c4.metric("出来高", f"{int(float(latest['Volume'])):,}")
        else:
            st.info("左のリストから銘柄を選ぶとチャートが表示されます。")
