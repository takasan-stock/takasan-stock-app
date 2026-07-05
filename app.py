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
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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
# チャートデータ取得
# ==========================================
@st.cache_data(ttl=3600)  # 1時間キャッシュ
def fetch_chart_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """yfinanceから日足データを取得する"""
    try:
        df = yf.download(ticker, period=period, interval="1d",
                         auto_adjust=True, progress=False)
        if df is None or df.empty:
            return pd.DataFrame()
        # MultiIndexの場合はフラット化
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return pd.DataFrame()


def build_chart(df: pd.DataFrame, ticker: str, name: str) -> go.Figure:
    """
    ローソク足 + 出来高 + ボリンジャーバンド(20日) + 移動平均線(21/50/200日)
    を含むplotlyチャートを作成する
    """
    # ── インジケーター計算 ──────────────────────────────
    c = df["Close"]

    # 移動平均線
    df["MA21"]  = c.rolling(21).mean()
    df["MA50"]  = c.rolling(50).mean()
    df["MA200"] = c.rolling(200).mean()

    # ボリンジャーバンド（20日、±2σ）
    df["BB_MID"] = c.rolling(20).mean()
    df["BB_STD"] = c.rolling(20).std()
    df["BB_U2"]  = df["BB_MID"] + df["BB_STD"] * 2
    df["BB_L2"]  = df["BB_MID"] - df["BB_STD"] * 2
    df["BB_U1"]  = df["BB_MID"] + df["BB_STD"] * 1
    df["BB_L1"]  = df["BB_MID"] - df["BB_STD"] * 1

    # ── サブプロット（価格エリア：出来高エリア = 4:1）──────
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.78, 0.22],
    )

    # ── ローソク足 ────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["Open"], high=df["High"],
        low=df["Low"],   close=df["Close"],
        name="ローソク足",
        increasing_line_color="#E94747",
        decreasing_line_color="#1B75BB",
        increasing_fillcolor="#E94747",
        decreasing_fillcolor="#1B75BB",
    ), row=1, col=1)

    # ── ボリンジャーバンド ────────────────────────────────
    # ±2σ 塗りつぶし
    fig.add_trace(go.Scatter(
        x=df.index, y=df["BB_U2"],
        line=dict(color="rgba(180,130,255,0.4)", width=1),
        name="+2σ", showlegend=True,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["BB_L2"],
        line=dict(color="rgba(180,130,255,0.4)", width=1),
        fill="tonexty",
        fillcolor="rgba(180,130,255,0.07)",
        name="-2σ", showlegend=True,
    ), row=1, col=1)
    # ±1σ
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
    # 中心線（20日MA）
    fig.add_trace(go.Scatter(
        x=df.index, y=df["BB_MID"],
        line=dict(color="rgba(180,130,255,0.7)", width=1.2, dash="dash"),
        name="BB中心(20MA)", showlegend=True,
    ), row=1, col=1)

    # ── 移動平均線 ────────────────────────────────────────
    ma_styles = [
        ("MA21",  "#F4A460", 1.5, "solid"),
        ("MA50",  "#3CB371", 1.5, "solid"),
        ("MA200", "#FF6347", 2.0, "solid"),
    ]
    for col_name, color, width, dash in ma_styles:
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col_name],
            line=dict(color=color, width=width, dash=dash),
            name=col_name,
        ), row=1, col=1)

    # ── 出来高バー ────────────────────────────────────────
    colors = ["#E94747" if row["Close"] >= row["Open"] else "#1B75BB"
              for _, row in df.iterrows()]
    fig.add_trace(go.Bar(
        x=df.index, y=df["Volume"],
        name="出来高",
        marker_color=colors,
        opacity=0.7,
        showlegend=False,
    ), row=2, col=1)

    # ── レイアウト ────────────────────────────────────────
    name_label = f"　{name}" if name and name != "-" else ""
    fig.update_layout(
        title=dict(
            text=f"{ticker}{name_label}　日足チャート",
            font=dict(size=16),
        ),
        height=680,
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.01,
            xanchor="left",   x=0,
            font=dict(size=11),
        ),
        xaxis_rangeslider_visible=False,
        xaxis2=dict(
            rangeslider=dict(visible=True, thickness=0.04),
        ),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
    )
    fig.update_yaxes(gridcolor="rgba(200,200,200,0.3)", row=1, col=1)
    fig.update_yaxes(gridcolor="rgba(200,200,200,0.3)", row=2, col=1)
    fig.update_xaxes(gridcolor="rgba(200,200,200,0.3)")

    return fig


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
    if st.button("🔄 最新の結果を再取得", key="btn_refresh", use_container_width=True):
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
tabs = st.tabs([
    "⭐複数パターン合致",
    "週足パターンA",
    "日足B1 押し目待ち🟡",
    "日足B2 反発エントリー🚀",
    "ボリバンC ブレイク💥",
    "📊 チャート表示",
])

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
                key=f"dl_{sheet_name}",
            )
        else:
            st.write("該当銘柄なし")


# ==========================================
# 📊 チャート表示タブ
# ==========================================
with tabs[5]:
    st.subheader("📊 ローソク足チャート")
    st.caption("スクリーニング結果から銘柄を選んでチャートを表示します。")

    # ── 左：銘柄リスト　右：チャート の2カラムレイアウト ──
    left_col, right_col = st.columns([1, 3])

    with left_col:
        st.markdown("#### 銘柄を選択")

        # どのシートから銘柄を選ぶか
        source_options = {
            "⭐ 複数パターン合致": "複数パターン合致",
            "週足パターンA":       "週足パターンA",
            "日足B1 押し目待ち":   "日足B1押し目待ち",
            "日足B2 反発エントリー": "日足B2反発エントリー",
            "ボリバンC ブレイク":  "ボリバンCブレイク",
        }
        selected_source = st.selectbox(
            "表示するリスト",
            list(source_options.keys()),
            label_visibility="collapsed",
            key="chart_source",
        )
        sheet_name = source_options[selected_source]
        df_source = load_sheet(sheet_name)

        # Ticker列・銘柄名列から選択肢を構築
        if df_source.empty or "Ticker" not in df_source.columns:
            st.warning("このシートに銘柄がありません。")
            ticker_options = []
        else:
            # 「証券コード 銘柄名」形式でラベルを作る
            if "銘柄名" in df_source.columns:
                labels = (df_source["証券コード"].astype(str)
                          + "　" + df_source["銘柄名"].astype(str))
            else:
                labels = df_source["Ticker"].astype(str)
            ticker_options = list(zip(labels, df_source["Ticker"].astype(str)))

        if ticker_options:
            selected_label = st.radio(
                "銘柄",
                [lbl for lbl, _ in ticker_options],
                label_visibility="collapsed",
                key="chart_ticker",
            )
            selected_ticker = dict(ticker_options)[selected_label]

            # 表示期間の選択
            st.markdown("#### 表示期間")
            period_map = {
                "3ヶ月": "3mo",
                "6ヶ月": "6mo",
                "1年":   "1y",
                "2年":   "2y",
            }
            selected_period_label = st.radio(
                "期間",
                list(period_map.keys()),
                index=2,
                horizontal=True,
                label_visibility="collapsed",
                key="chart_period",
            )
            selected_period = period_map[selected_period_label]

    with right_col:
        if ticker_options and selected_ticker:
            # 銘柄名を取得
            if "銘柄名" in df_source.columns:
                name_row = df_source[df_source["Ticker"] == selected_ticker]
                company_name = name_row["銘柄名"].iloc[0] if not name_row.empty else ""
            else:
                company_name = ""

            with st.spinner(f"{selected_ticker} のチャートを取得中..."):
                chart_df = fetch_chart_data(selected_ticker, period=selected_period)

            if chart_df.empty:
                st.error(f"{selected_ticker} のデータを取得できませんでした。")
            else:
                fig = build_chart(chart_df, selected_ticker, company_name)
                st.plotly_chart(fig, use_container_width=True)

                # ── 基本情報をチャート下に表示 ──────────────
                latest = chart_df.iloc[-1]
                prev   = chart_df.iloc[-2] if len(chart_df) >= 2 else latest
                change     = float(latest["Close"]) - float(prev["Close"])
                change_pct = change / float(prev["Close"]) * 100
                color = "🔴" if change >= 0 else "🔵"

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("終値",   f"¥{float(latest['Close']):,.0f}",
                          f"{color} {change:+,.0f} ({change_pct:+.2f}%)")
                m2.metric("高値",   f"¥{float(latest['High']):,.0f}")
                m3.metric("安値",   f"¥{float(latest['Low']):,.0f}")
                m4.metric("出来高", f"{int(float(latest['Volume'])):,}")
        else:
            st.info("左のリストから銘柄を選ぶとチャートが表示されます。")


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
                key=f"dl_{sheet_name}",
            )
        else:
            st.write("該当銘柄なし")
