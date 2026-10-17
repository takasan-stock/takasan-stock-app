"""
============================================================
日本株 全銘柄スキャン → Googleスプレッドシート書き込み
GitHub Actions専用（Streamlitに依存しない）
============================================================
実行方法:
  python scan.py

必要な環境変数:
  GCP_SA_KEY            : サービスアカウントJSONの中身（文字列そのまま）
  SPREADSHEET_ID        : 書き込み先スプレッドシートのID
                          (URLの https://docs.google.com/spreadsheets/d/【ここ】/edit)
============================================================
"""

import os
import json
import time
import datetime
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
import pandas as pd
import numpy as np
import gspread

from momentum import build_momentum_results, MOMENTUM_VERSION
from google.oauth2.service_account import Credentials

warnings.filterwarnings("ignore")

# ── バージョン識別子（ファイルが正しく反映されているか確認するため）──
SCAN_PY_VERSION = "2026-10-12-v15-weekly-turnaround-score"
print(f"[診断] scan.py バージョン識別子: {SCAN_PY_VERSION}", flush=True)

# GitHub ActionsのサーバーはUTCで動作するため、日本時間(JST)に明示的に変換する
JST = datetime.timezone(datetime.timedelta(hours=9))

def now_jst() -> datetime.datetime:
    """現在時刻を日本時間(JST)で返す"""
    return datetime.datetime.now(JST)

# ==========================================
# 設定パラメータ
# ==========================================
BAND5_PCT  = 0.025
BAND40_PCT = 0.05

# ── パターンA: 長期下落からの底打ち・40週MA上抜け初動 ──────
A_DECLINE_MIN    = -1.5  # 過去に「明確な下落」とみなす40週MA傾き%(4週)
A_SLOPE_NOW_MIN  = -1.0  # 現在の40週MA傾きの下限。ほぼ横ばい化していることを要求
                         # （V字反発では40週MAがまだ明確に下向きのため、ここで除外される）
A_SLOPE_IMPROVE  = 0.5   # 過去の傾きからの改善幅（下げ止まりの証拠）
A_CROSS_WEEKS    = 10    # 40週MAを上抜けてから何週以内を「初動」とみなすか

# ── ベース形成（V字反発の除外）──────────────────────────
# 上抜け直前に「底値圏で横ばい（ベース）」を作っていたかを見る。
# V字反発は直前に急落を含むためレンジが広くなり、ここで除外される。
A_BASE_WEEKS     = 16    # 上抜け直前の何週をベース期間として見るか
A_BASE_RANGE_MAX = 1.35  # ベース期間の(高値÷安値)の上限。これを超えると「ベースでなくV字」
A_WEEKS_FROM_LOW = 8     # 直近の最安値をつけてから最低何週経過している必要があるか
A_MAX_EXTENDED   = 25.0  # 終値が40週MAから何%以上離れたら「初動でない」として除外
A_RSI_MIN        = 40.0  # 底打ち初動のRSI下限（弱い反発を除外）
A_RSI_MAX        = 85.0  # RSI上限は安全弁のみ。上抜け初動はRSIが高く出るのが自然なため広め
                         # （行き過ぎの抑制は A_MAX_EXTENDED が担当する）

# ── 週足転換H: 画像に近い「ベース反転 / 再加速 / 初動」をスコア化 ──
# A/Fを置き換えず、TURNAROUND内の追加確認シグナルとして使う。
H_SCORE_MIN          = 60.0   # EARLY_TURNを早めに研究候補へ入れる
H_EARLY_CORE_MIN     = 70.0   # 初動は早めに本命へ昇格
H_BASE_CORE_MIN      = 80.0   # ベース反転は従来水準
H_REACCEL_CORE_MIN   = 85.0   # 再加速はかなり厳選
H_BASE_WEEKS      = 16     # 週足ベースの幅を測る期間
H_HIGH_LOOKBACK   = 13     # 直近高値への接近/突破判定
H_MAX_DIST20      = 30.0   # 20週MAから30%以上上方乖離は過熱として除外
H_MIN_WEEKS       = 60     # 最低データ本数

BAND21_PCT = 0.025
BAND50_PCT = 0.04

BATCH_SIZE  = 20
MAX_WORKERS = 2      # GitHub Actionsは比較的余裕があるので2に
SLEEP_SEC   = 1.5
RETRY_MAX   = 3
RETRY_SLEEP = 3.0
MIN_FILL_RATE = 0.9

MIN_AVG_TURNOVER = 3.0   * 100_000_000   # 3億円/日
MIN_MARKET_CAP   = 500.0 * 100_000_000   # 500億円

# ── パターンC: ボリンジャーバンド+2σブレイク ──────────
BB_PERIOD       = 20    # ボリンジャーバンドの期間
BB_SIGMA        = 2     # σ倍率
BREAKOUT_DAYS   = 2     # 直近N日以内に+2σ上抜けがあること
VOL_RATIO_MIN   = 1.5   # ブレイク時の出来高が20日平均の何倍以上か
VOL_MA_PERIOD   = 20    # 出来高平均の計算期間
MOMENTUM_DAYS   = 5     # 直近N日間の上昇率を確認
MOMENTUM_MIN    = 0.05  # 直近N日で何%以上上昇していること（5%）
GAP_EXCLUDE_PCT = 0.08  # 前日比N%以上の急騰翌日はエントリー除外（8%）
USE_DOW_FILTER  = False # Trueで月〜水エントリーに絞る
C_MIN_BARS      = 60    # ボリバン判定に必要な最低本数

# ── パターンE: 揉み合い後の出来高急増ブレイク ──────────
E_LOOKBACK      = 20    # 揉み合い判定期間（当日を除く直近N日）
E_VOL_SURGE     = 3.0   # 当日出来高が揉み合い平均の何倍以上か
E_PRICE_MIN     = 0.03  # 当日の前日比上昇率の下限（3%）
E_MIN_BARS      = 40    # 判定に必要な最低本数

# ── パターンF: 21MA×200MA ゴールデンクロス（底打ち転換）──
F_GC_DAYS       = 15    # 直近N日以内にGCが起きたこと
F_SLOPE_DAYS    = 20    # 200MAの傾きを測る期間
F_DOWN_PREV_MAX = -0.5  # 過去は下向きだった（傾き%がこの値未満）
F_FLAT_NOW_MIN  = -0.8  # 現在は横ばい〜上向き（傾き%がこの値以上）
F_MIN_BARS      = 220   # 200MA計算に必要な最低本数

# ── パターンG: ポケットピボット（オニール系）──────────
G_MA_PERIOD     = 10    # 基準となる移動平均（10日線）
G_LOOKBACK      = 10    # 出来高比較に使う過去日数
G_DIST_LOW      = -0.02 # 終値が10日MAの下方向にどこまで許容するか（-2%）
G_DIST_HIGH     = 0.05  # 終値が10日MAの上方向にどこまで許容するか（+5%）
G_TOUCH_PCT     = 0.03  # 安値が10日MAにどこまで近ければ「タッチ」とみなすか（3%以内）
G_MIN_BARS      = 220   # 上昇トレンド判定に必要な最低本数

# ==========================================
# 1. JPX銘柄リスト取得
# ==========================================
def get_jpx_tickers() -> tuple[list, dict, str]:
    """
    JPX銘柄リストを取得する。
    戻り値: (tickers, name_map, diag)
      - tickers : Tickerのリスト（例: ["7203.T", ...]）
      - name_map: {Ticker: 日本語銘柄名} の辞書（JPX一覧から取得した正式な日本語社名）
      - diag    : 取得方法またはエラー内容
    """
    # JPXは2026年8月頃、配布ファイル形式を .xls から .xlsx に変更した。
    # 将来また変わる可能性があるため、両方の拡張子を順番に試す。
    urls = [
        "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx",
        "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls",
    ]
    df = None
    diag_steps = []

    import requests, urllib3
    from io import BytesIO
    urllib3.disable_warnings()
    headers = {"User-Agent": "Mozilla/5.0"}

    for url in urls:
        try:
            r = requests.get(url, verify=False, timeout=30, headers=headers)
            r.raise_for_status()
            df = pd.read_excel(BytesIO(r.content), header=0)
            diag_steps.append(f"requests+read_excel({url.split('.')[-1]}): 成功")
            break
        except Exception as e:
            diag_steps.append(f"requests+read_excel({url.split('.')[-1]}): 失敗 ({type(e).__name__}: {e})")

    if df is None:
        diag = " | ".join(diag_steps)
        fallback = [f"{c}.T" for c in [
            7203,6758,9984,8035,4063,8306,9432,6861,6920,4502,
            6954,9022,8411,5401,4519,6971,6902,7751,7267,9020,
        ]]
        return fallback, {}, f"⚠️ JPX一覧の取得に失敗: {diag}"

    def norm(c):
        try: return str(int(float(c)))
        except: return str(c).strip()

    def norm_colname(c):
        """列名の表記ゆれ（全角/半角、前後空白、Unicode正規化差異）を吸収する"""
        import unicodedata
        s = str(c)
        s = unicodedata.normalize("NFKC", s)
        return s.strip()

    norm_cols = {col: norm_colname(col) for col in df.columns}

    mc = next((col for col, n in norm_cols.items() if "市場" in n or "区分" in n), None)
    cc = next((col for col, n in norm_cols.items() if "コード" in n or "証券" in n), None)
    nc = next((col for col, n in norm_cols.items() if "銘柄名" in n), None)

    # ── 診断: 実際の列名（正規化前後）を出力（原因調査用）──
    print("[診断] JPXデータの実際の列名一覧:", flush=True)
    for col in df.columns:
        print(f"    repr={repr(col)}  正規化後={repr(norm_colname(col))}", flush=True)
    print(f"[診断] 市場列検出: {repr(mc)} / コード列検出: {repr(cc)} / 銘柄名列検出: {repr(nc)}", flush=True)

    if mc and cc:
        df_f = df[df[mc].astype(str).str.contains("プライム|スタンダード", na=False)]
    else:
        df_f = df
        cc = cc or df.columns[0]

    codes_raw = df_f[cc].dropna().astype(str).str.strip()
    codes = codes_raw.map(norm)

    name_map = {}
    if nc:
        for code_raw, code, name in zip(codes_raw, codes, df_f[nc].fillna("")):
            if 4 <= len(code) <= 5 and code.isalnum():
                name_map[f"{code}.T"] = str(name).strip()
    else:
        print("[診断] ⚠️ 銘柄名列が見つからなかったため name_map は空になります", flush=True)

    tickers = [f"{c}.T" for c in codes if 4 <= len(c) <= 5 and c.isalnum()]

    # ── 診断: name_mapの実際の中身を数件サンプル表示 ──
    print(f"[診断] name_map件数: {len(name_map)}", flush=True)
    if name_map:
        sample = list(name_map.items())[:3]
        print(f"[診断] name_mapサンプル: {sample}", flush=True)

    if len(tickers) < 100:
        diag = " | ".join(diag_steps) + f" | 抽出後{len(tickers)}件のみ"
        return tickers, name_map, f"⚠️ 取得銘柄数が少なすぎます: {diag}"

    return tickers, name_map, " | ".join(diag_steps) + f" | 取得成功: {len(tickers)}銘柄 | name_map: {len(name_map)}件"


# ==========================================
# 2. データ取得（リトライ対応）
# ==========================================
def _download_once(batch: list, period: str, interval: str) -> tuple[dict, str]:
    result = {}
    diag = ""
    try:
        raw = yf.download(
            batch, period=period, interval=interval,
            auto_adjust=True, progress=False, threads=False,
        )
        if raw is None or raw.empty:
            return result, "rawが空"

        if isinstance(raw.columns, pd.MultiIndex):
            lvl0 = raw.columns.get_level_values(0).unique().tolist()
            lvl1 = raw.columns.get_level_values(1).unique().tolist()
            fields = ["Close","Open","High","Low","Volume"]

            if any(f in lvl0 for f in fields):
                diag = "(field,ticker)形式"
                for ticker in batch:
                    if ticker in lvl1:
                        df = raw.xs(ticker, axis=1, level=1).copy()
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(0)
                        if not df.empty and "Close" in df.columns:
                            result[ticker] = df
            else:
                diag = "(ticker,field)形式"
                for ticker in batch:
                    if ticker in lvl0:
                        df = raw[ticker].copy()
                        if not df.empty and "Close" in df.columns:
                            result[ticker] = df
        else:
            diag = "シングルカラム"
            if len(batch) == 1 and "Close" in raw.columns:
                result[batch[0]] = raw.copy()
    except Exception as e:
        diag = f"例外 {type(e).__name__}"
    return result, diag


def download_batch(batch: list, period: str, interval: str, label: str = "") -> tuple[dict, str]:
    result, diag0 = _download_once(batch, period, interval)
    attempts_log = []
    missing = [t for t in batch if t not in result]
    fill_rate = len(result) / len(batch) if batch else 1.0

    attempt = 0
    while missing and attempt < RETRY_MAX and fill_rate < MIN_FILL_RATE:
        attempt += 1
        time.sleep(RETRY_SLEEP * attempt)
        retry_result, retry_diag = _download_once(missing, period, interval)
        result.update(retry_result)
        attempts_log.append(f"retry{attempt}:+{len(retry_result)}/{len(missing)}")
        missing = [t for t in batch if t not in result]
        fill_rate = len(result) / len(batch) if batch else 1.0

    diag = f"{label}: {diag0}"
    if attempts_log: diag += " | " + ", ".join(attempts_log)
    if missing: diag += f" | 最終欠落={len(missing)}件"
    return result, diag


# ==========================================
# 3. インジケーター計算 & パターン判定
#    （Streamlit版 app.py と同一ロジック）
# ==========================================
def calc_weekly(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"]
    df["MA5"]          = c.rolling(5).mean()
    df["MA10W"]        = c.rolling(10).mean()
    df["WMA20"]        = c.rolling(20).mean()   # 週足20SMA（パターンD/H用）
    df["MA40"]         = c.rolling(40).mean()
    df["MA50W"]        = c.rolling(50).mean()
    df["MA150W"]       = c.rolling(150).mean()
    df["MA200W"]       = c.rolling(200).mean()
    df["VOL10W"]       = df["Volume"].rolling(10).mean() if "Volume" in df.columns else np.nan
    df["BAND5_U"]      = df["MA5"]  * (1 + BAND5_PCT)
    df["BAND5_L"]      = df["MA5"]  * (1 - BAND5_PCT)
    df["BAND40_U"]     = df["MA40"] * (1 + BAND40_PCT)
    df["BAND40_L"]     = df["MA40"] * (1 - BAND40_PCT)
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["RSI"]          = 100 - 100 / (1 + gain / (loss + 1e-9))
    df["MA40_s4"]      = df["MA40"].pct_change(4) * 100
    df["MA40_s8"]      = df["MA40"].pct_change(8) * 100
    df["MA40_s8_prev"] = df["MA40_s8"].shift(4)
    df["WMA20_s4"]     = df["WMA20"].pct_change(4) * 100  # 週足20SMAの傾き
    df["MA40_s4b"]      = df["MA40"].pct_change(4) * 100   # 週足40SMAの傾き（別名）
    df["MA5_s4"]        = df["MA5"].pct_change(4) * 100    # 週足5SMAの傾き（パターンA底打ち用）
    df["MA10W_s4"]      = df["MA10W"].pct_change(4) * 100
    df["MA50W_s8"]       = df["MA50W"].pct_change(8) * 100
    df["MA150W_s13"]     = df["MA150W"].pct_change(13) * 100
    df["MA200W_s13"]     = df["MA200W"].pct_change(13) * 100
    return df

def calc_daily(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"]
    df["MA10"]     = c.rolling(10).mean()   # ポケットピボット用（10日線）
    df["MA21"]     = c.rolling(21).mean()
    df["MA25"]     = c.rolling(25).mean()   # SMA25（パターンD用）
    df["MA50"]     = c.rolling(50).mean()
    df["MA200"]    = c.rolling(200).mean()
    df["B21_U"]    = df["MA21"] * (1 + BAND21_PCT)
    df["B21_L"]    = df["MA21"] * (1 - BAND21_PCT)
    df["B50_U"]    = df["MA50"] * (1 + BAND50_PCT)
    df["B50_L"]    = df["MA50"] * (1 - BAND50_PCT)
    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["RSI"]      = 100 - 100 / (1 + gain / (loss + 1e-9))
    df["MA50_s5"]  = df["MA50"].pct_change(5) * 100
    df["MA25_s5"]  = df["MA25"].pct_change(5) * 100   # SMA25の傾き
    df["MA200_sF"]     = df["MA200"].pct_change(F_SLOPE_DAYS) * 100          # 200MAの傾き（現在）
    df["MA200_sF_prev"] = df["MA200_sF"].shift(F_SLOPE_DAYS)                 # 200MAの傾き（過去）
    df["MA10_s5"]      = df["MA10"].pct_change(5) * 100                      # 10日MAの傾き（パターンG用）
    return df

def check_a(df: pd.DataFrame, ctr: dict) -> dict | None:
    """
    パターンA:「長期下落からの底打ち → 40週MA上抜けの初動」
    ══════════════════════════════════════════════════════════
    ① 長期下落の実績: 過去60週で40週MAの傾きが A_DECLINE_MIN 未満だった週がある
    ② 下げ止まり   : 40週MAの傾きが「下向き → 横ばい」に転換
                     （現在の傾き >= A_SLOPE_NOW_MIN、かつ過去より改善）
    ③ 上抜け初動   : 終値が40週MAを上抜けており、その上抜けが
                     直近 A_CROSS_WEEKS 週以内に起きたばかり
    ④ 短期の反転   : 5週MAが上向き、かつ終値が5週MAより上
    ⑤ 過熱でない   : 終値が40週MAから A_MAX_EXTENDED% 以上離れていない
    ⑥ RSI         : A_RSI_MIN 〜 A_RSI_MAX

    上記に当てはまらない場合でも、従来型の「上昇転換後の押し目」
    （40週MAバンドまで押してGCを維持している形）は別状態として拾う。
    ══════════════════════════════════════════════════════════
    """
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None

    need = ["MA5","MA40","BAND5_U","BAND5_L","BAND40_U","BAND40_L",
            "RSI","MA40_s4","MA40_s8_prev","MA5_s4","Close","Low"]
    rec = df.dropna(subset=need).iloc[-60:]
    if len(rec) < 20: return drop("A_データ不足")

    cur = rec.iloc[-1]
    close, low = float(cur["Close"]), float(cur["Low"])
    ma5, ma40  = float(cur["MA5"]), float(cur["MA40"])
    b5u, b40u, b40l = float(cur["BAND5_U"]), float(cur["BAND40_U"]), float(cur["BAND40_L"])
    s_now, s_old = float(cur["MA40_s4"]), float(cur["MA40_s8_prev"])
    ma5_slope = float(cur["MA5_s4"])
    rsi = float(cur["RSI"])
    dist_close = (close - ma40) / ma40 * 100

    # ── ① 長期下落の実績（両パターン共通の前提）──────────
    if not (rec["MA40_s4"] < A_DECLINE_MIN).any():
        return drop("A_①下落実績なし")

    # ── ② 下げ止まり（40週MAが下向き→横ばいに転換）──────
    if s_now < A_SLOPE_NOW_MIN:
        return drop("A_②まだ下落継続")
    if not np.isnan(s_old) and s_now < s_old + A_SLOPE_IMPROVE:
        return drop("A_②改善なし")

    # ══════════════════════════════════════════════════
    # パターンA-1: 底打ち → 40週MA上抜けの初動（画像の形）
    # ══════════════════════════════════════════════════
    breakout_ok = False
    weeks_since_cross = None
    cross_pos = None

    if close > ma40:
        # 直近A_CROSS_WEEKS週以内に「終値が40週MA以下」だった週があるか
        recent = rec.iloc[-(A_CROSS_WEEKS + 1):]
        below = recent[recent["Close"] <= recent["MA40"]]
        if not below.empty:
            weeks_since_cross = int(
                (rec.index[-1] - below.index[-1]).days / 7
            )
            cross_pos = rec.index.get_loc(below.index[-1])
            breakout_ok = True

    if breakout_ok:
        # ── ③ ベース形成（V字反発の除外）─────────────────
        # 上抜け直前 A_BASE_WEEKS 週のレンジが狭いこと＝底で揉み合っていたこと。
        # 急落直後のV字反発は、この期間に急落を含むためレンジが広くなり除外される。
        base = rec.iloc[max(0, cross_pos - A_BASE_WEEKS + 1): cross_pos + 1]
        if len(base) >= 8:
            base_high = float(base["High"].max())
            base_low  = float(base["Low"].min())
            if base_low > 0 and (base_high / base_low) > A_BASE_RANGE_MAX:
                return drop("A_[初動]③ベース未形成(V字反発)")

        # ── ③ 底値から一定期間が経過していること（V字の追加除外）──
        low_win = rec.iloc[-40:]
        low_idx = low_win["Low"].idxmin()
        weeks_from_low = int((rec.index[-1] - low_idx).days / 7)
        if weeks_from_low < A_WEEKS_FROM_LOW:
            return drop("A_[初動]③底値から日が浅い(V字反発)")

        if ma5_slope <= 0:
            return drop("A_[初動]④5週MA上向きでない")
        if close < ma5:
            return drop("A_[初動]④終値が5週MA未満")
        if dist_close > A_MAX_EXTENDED:
            return drop("A_[初動]⑤上昇しすぎ(初動でない)")
        if not (A_RSI_MIN <= rsi <= A_RSI_MAX):
            return drop("A_[初動]⑥RSI範囲外")

        ctr["A_合格"] = ctr.get("A_合格", 0) + 1
        ctr["A_合格-底打ち上抜け"] = ctr.get("A_合格-底打ち上抜け", 0) + 1
        return {
            "status": "🚀上抜け初動",
            "close": round(close, 0), "low": round(low, 0),
            "ma5": round(ma5, 0), "ma40": round(ma40, 0),
            "band40_u": round(b40u, 0), "band40_l": round(b40l, 0),
            "dist_close": round(dist_close, 1),
            "dist_low": round((low - ma40) / ma40 * 100, 1),
            "slope_now": round(s_now, 2),
            "slope_prev": round(s_old, 2) if not np.isnan(s_old) else 0.0,
            "ma5_slope": round(ma5_slope, 2),
            "rsi": round(rsi, 1),
            "weeks_since_cross": weeks_since_cross if weeks_since_cross is not None else 0,
        }

    # ══════════════════════════════════════════════════
    # パターンA-2: 上昇転換後の押し目（従来型）
    # ══════════════════════════════════════════════════
    rec40 = df.dropna(subset=["MA5","MA40"]).iloc[-40:]
    gc_flag = (rec40["MA5"] > rec40["MA40"]).astype(int)
    if not (gc_flag.rolling(2).min() == 1).any(): return drop("A_[押目]③GC実績なし")
    if ma5 < ma40 * 0.97: return drop("A_[押目]③MA5大幅下抜け")
    if not (b40l * 0.97 <= low <= b40u * 1.03): return drop("A_[押目]④安値バンド外")
    if close < ma40 * 0.97: return drop("A_[押目]④終値MA40割れ")
    if b5u <= b40l: return drop("A_[押目]④5SMAバンド潜り込み")
    if not (35 <= rsi <= 65): return drop("A_[押目]⑤RSI範囲外")

    ctr["A_合格"] = ctr.get("A_合格", 0) + 1
    ctr["A_合格-押し目"] = ctr.get("A_合格-押し目", 0) + 1
    return {
        "status": "🔄押し目",
        "close": round(close, 0), "low": round(low, 0),
        "ma5": round(ma5, 0), "ma40": round(ma40, 0),
        "band40_u": round(b40u, 0), "band40_l": round(b40l, 0),
        "dist_close": round(dist_close, 1),
        "dist_low": round((low - ma40) / ma40 * 100, 1),
        "slope_now": round(s_now, 2),
        "slope_prev": round(s_old, 2) if not np.isnan(s_old) else 0.0,
        "ma5_slope": round(ma5_slope, 2),
        "rsi": round(rsi, 1),
        "weeks_since_cross": 999,   # 押し目は上抜け直後ではないため大きい値
    }


def check_h(df: pd.DataFrame, ctr: dict) -> dict | None:
    """
    週足転換H:
    たかさんの参考チャートに共通する「形の良い週足転換」を0〜100で採点する。

    3タイプ:
      EARLY_TURN : 長期調整後、20/40週線が改善して立ち上がる初動
      BASE_TURN  : 8〜20週程度のベースを作り、レンジ上限へ戻る形
      REACCEL    : 上昇トレンド中の調整完了から52週高値方向へ再加速

    厳しいAND条件ではなくスコア方式にすることで、
    エレコム / ベイカレント / SEMITEC / 日本電子 / メイテック型の
    「完全に同じではないが共通して良い週足」をまとめて拾う。
    """
    def drop(k):
        ctr[k] = ctr.get(k, 0) + 1
        return None

    need = ["Open","High","Low","Close","Volume","MA5","MA10W","WMA20","MA40",
            "WMA20_s4","MA40_s4b","RSI","VOL10W"]
    d = df.dropna(subset=need).copy()
    if len(d) < H_MIN_WEEKS:
        return drop("H_データ不足")

    d = d.iloc[-80:]
    cur = d.iloc[-1]
    close = float(cur["Close"])
    open_ = float(cur["Open"])
    ma5 = float(cur["MA5"])
    ma10 = float(cur["MA10W"])
    ma20 = float(cur["WMA20"])
    ma40 = float(cur["MA40"])
    s20 = float(cur["WMA20_s4"])
    s40 = float(cur["MA40_s4b"])
    rsi = float(cur["RSI"])
    vol = float(cur["Volume"])
    vol10 = float(cur["VOL10W"]) if float(cur["VOL10W"]) > 0 else np.nan

    if min(close, ma20, ma40) <= 0:
        return drop("H_価格異常")

    dist20 = (close / ma20 - 1.0) * 100.0
    dist40 = (close / ma40 - 1.0) * 100.0
    if dist20 > H_MAX_DIST20:
        return drop("H_20週線から過熱")
    if dist40 < -12.0:
        return drop("H_40週線より下すぎ")

    w52 = d.iloc[-52:]
    high52 = float(w52["High"].max())
    low52 = float(w52["Low"].min())
    high52_dist = (close / high52 - 1.0) * 100.0 if high52 > 0 else -100.0
    recovery52 = (close / low52 - 1.0) * 100.0 if low52 > 0 else 0.0

    base = d.iloc[-H_BASE_WEEKS:]
    base_hi = float(base["High"].max())
    base_lo = float(base["Low"].min())
    base_range = (base_hi / base_lo - 1.0) * 100.0 if base_lo > 0 else 999.0

    low26 = d.iloc[-26:]["Low"]
    low_pos = int(np.argmin(low26.values))
    weeks_from_low = len(low26) - 1 - low_pos

    prior13 = d.iloc[-(H_HIGH_LOOKBACK + 1):-1]
    prior13_high = float(prior13["High"].max()) if not prior13.empty else high52
    dist13_high = (close / prior13_high - 1.0) * 100.0 if prior13_high > 0 else -100.0

    ret13 = (close / float(d["Close"].iloc[-14]) - 1.0) * 100.0 if len(d) >= 14 else 0.0

    # 週途中の未完成出来高を完成週平均と比較しない。
    # 前週の完成出来高 ÷ その前10週平均を使う。
    completed_vol_ratio = 0.0
    if len(d) >= 12:
        prev_vol = float(d["Volume"].iloc[-2])
        prev10 = pd.to_numeric(d["Volume"].iloc[-12:-2], errors="coerce").dropna()
        if len(prev10) >= 5 and float(prev10.mean()) > 0:
            completed_vol_ratio = prev_vol / float(prev10.mean())

    def _weeks_since_reclaim(ma_col: str, lookback: int = 16) -> int:
        """現在そのMA上なら、最後に終値がMA以下だった週からの経過週数。"""
        try:
            cur_ma = float(d[ma_col].iloc[-1])
            if close < cur_ma:
                return 999
            recent = d.iloc[-(lookback + 1):]
            below = recent[recent["Close"] <= recent[ma_col]]
            if below.empty:
                return 999
            return max(0, int(round((d.index[-1] - below.index[-1]).days / 7)))
        except Exception:
            return 999

    def _weeks_since_signal(flag: pd.Series, lookback: int = 16) -> int:
        try:
            recent = flag.iloc[-lookback:].fillna(False).astype(bool)
            hits = recent[recent]
            if hits.empty:
                return 999
            return max(0, int(round((d.index[-1] - hits.index[-1]).days / 7)))
        except Exception:
            return 999

    weeks20_reclaim = _weeks_since_reclaim("WMA20", 16)
    weeks40_reclaim = _weeks_since_reclaim("MA40", 20)

    s20_prev = float(d["WMA20_s4"].iloc[-5]) if len(d) >= 5 else s20
    s40_prev = float(d["MA40_s4b"].iloc[-5]) if len(d) >= 5 else s40
    s20_improve = s20 - s20_prev
    s40_improve = s40 - s40_prev

    slope20_turn = (d["WMA20_s4"] > 0) & (d["WMA20_s4"].shift(1) <= 0)
    slope40_turn = (d["MA40_s4b"] >= 0) & (d["MA40_s4b"].shift(1) < 0)
    weeks20_slope_turn = _weeks_since_signal(slope20_turn, 16)
    weeks40_slope_turn = _weeks_since_signal(slope40_turn, 20)

    prev13_series = d["High"].shift(1).rolling(H_HIGH_LOOKBACK).max()
    breakout13 = (d["Close"] >= prev13_series) & (d["Close"].shift(1) < prev13_series.shift(1))
    weeks13_break = _weeks_since_signal(breakout13, 16)

    # ─────────────────────────────────────────
    # 急落ダメージ / 反転確認
    # 「損切りが近い」だけで falling knife を高評価しないための安全弁。
    # 現在週は未完成の可能性があるため、急落判定は主に前週の完成足を使う。
    # ─────────────────────────────────────────
    prev = d.iloc[-2]
    prev2 = d.iloc[-3]
    prev_close = float(prev["Close"])
    prev_open = float(prev["Open"])
    prev_high = float(prev["High"])
    prev_low = float(prev["Low"])
    prev2_close = float(prev2["Close"])

    prev_week_ret = (prev_close / prev2_close - 1.0) * 100.0 if prev2_close > 0 else 0.0
    prev_body_ret = (prev_close / prev_open - 1.0) * 100.0 if prev_open > 0 else 0.0
    completed4_ret = (
        (prev_close / float(d["Close"].iloc[-6]) - 1.0) * 100.0
        if len(d) >= 6 and float(d["Close"].iloc[-6]) > 0 else 0.0
    )

    ma5_slope = float(cur["MA5_s4"]) if "MA5_s4" in d.columns and not pd.isna(cur["MA5_s4"]) else 0.0
    below_short_ma = close < ma5 and close < ma10
    short_ma_bearish = ma5 < ma10 and ma5_slope < 0
    heavy_down_week = prev_week_ret <= -6.0 or prev_body_ret <= -5.0
    four_week_damage = completed4_ret <= -8.0
    distribution_damage = heavy_down_week and completed_vol_ratio >= 1.15

    damage_points = 0
    if prev_week_ret <= -10.0:
        damage_points += 45
    elif prev_week_ret <= -6.0:
        damage_points += 30
    elif prev_week_ret <= -4.0:
        damage_points += 15

    if prev_body_ret <= -5.0:
        damage_points += 15
    if four_week_damage:
        damage_points += 15
    if below_short_ma and short_ma_bearish:
        damage_points += 20
    if distribution_damage:
        damage_points += 10

    falling_knife = damage_points >= 30

    # 本命化には「反転した証拠」を最低1つ要求する。
    reclaim_short = close >= ma5 and close >= ma10
    break_prev_high = close >= prev_high
    turn_short_ma = ma5_slope >= 0 and close > prev_close
    constructive_rebound = (
        close > prev_close * 1.02
        and float(cur["Low"]) >= prev_low * 0.99
    )
    reversal_confirmed = reclaim_short or break_prev_high or turn_short_ma or constructive_rebound

    # ─────────────────────────────────────────
    # 形状フィルター
    # EARLY最優先に「高値圏の押し目再始動」や「下降継続中」を混ぜない。
    # 候補には残すが、本命/TOP20からは外す。
    # ─────────────────────────────────────────
    # 直近52週高値からの経過週数
    high52_pos = int(np.argmax(w52["High"].values))
    weeks_since_52w_high = len(w52) - 1 - high52_pos

    # 直近26週で20週MA上にいた割合。長く上にいるほど「底打ち」より押し目再始動寄り。
    recent26 = d.iloc[-26:]
    above20_ratio_26 = float((recent26["Close"] > recent26["WMA20"]).mean()) if not recent26.empty else 0.0
    above40_ratio_26 = float((recent26["Close"] > recent26["MA40"]).mean()) if not recent26.empty else 0.0

    # 高値圏押し目 / 再始動の判定
    high_zone_pullback = (
        weeks_since_52w_high <= 20
        and high52_dist >= -18.0
        and s40 >= 0.0
        and above20_ratio_26 >= 0.55
    )

    # 明確な下降継続
    downtrend_continuation = (
        (close < ma20 and ma20 < ma40 and s20 < 0 and s40 < 0)
        or (ma5 < ma10 < ma20 and ma5_slope < 0 and s20 < 0)
        or (s20 <= -1.0 and s40 <= -0.8 and close < ma20)
    )

    # 「一瞬戻しただけ」を除く定着確認
    above20_2w = bool(
        len(d) >= 2
        and float(d["Close"].iloc[-1]) > float(d["WMA20"].iloc[-1])
        and float(d["Close"].iloc[-2]) > float(d["WMA20"].iloc[-2])
    )
    recent4_high_prev = float(d["High"].iloc[-5:-1].max()) if len(d) >= 5 else prev_high
    break_recent4_high = close >= recent4_high_prev
    stable_reversal = above20_2w or break_recent4_high or break_prev_high

    shape_ok_for_early = (
        not high_zone_pullback
        and not downtrend_continuation
        and stable_reversal
    )

    # ─────────────────────────────────────────
    # 長期ステージ判定
    # 「底打ち初動」「まだStage4」「上昇トレンド中の押し目」を分離する。
    # 150/200週MAが未計算の若い銘柄はUNKNOWN扱いで候補に残し、TOP20だけ慎重に扱う。
    # ─────────────────────────────────────────
    ma50w = float(cur["MA50W"]) if "MA50W" in d.columns and not pd.isna(cur["MA50W"]) else np.nan
    ma150w = float(cur["MA150W"]) if "MA150W" in d.columns and not pd.isna(cur["MA150W"]) else np.nan
    ma200w = float(cur["MA200W"]) if "MA200W" in d.columns and not pd.isna(cur["MA200W"]) else np.nan
    s50w = float(cur["MA50W_s8"]) if "MA50W_s8" in d.columns and not pd.isna(cur["MA50W_s8"]) else np.nan
    s150w = float(cur["MA150W_s13"]) if "MA150W_s13" in d.columns and not pd.isna(cur["MA150W_s13"]) else np.nan
    s200w = float(cur["MA200W_s13"]) if "MA200W_s13" in d.columns and not pd.isna(cur["MA200W_s13"]) else np.nan

    long_stage = "UNKNOWN"
    long_stage_reason = "長期MAデータ不足"

    if not np.isnan(ma50w):
        stage4_wait = (
            s50w <= -2.0
            and close < ma50w
            and (
                np.isnan(ma200w)
                or ma50w < ma200w
                or (not np.isnan(s200w) and s200w < -1.0)
            )
        )

        long_ma_stack_up = (
            (not np.isnan(ma150w) and ma50w >= ma150w)
            or (
                not np.isnan(ma150w)
                and not np.isnan(ma200w)
                and ma150w >= ma200w
            )
        )
        pullback_like_long = (
            close >= ma50w
            and s50w > 0
            and long_ma_stack_up
            and above20_ratio_26 >= 0.55
        )

        early_stage_ok = (
            not stage4_wait
            and not pullback_like_long
            and (
                s50w >= -1.0
                or (s50w > -2.0 and s20_improve > 0 and s40_improve > 0)
            )
            and (
                np.isnan(ma200w)
                or close >= ma200w * 0.92
                or (not np.isnan(s200w) and s200w >= -1.0)
            )
        )

        if stage4_wait:
            long_stage = "STAGE4_WAIT"
            long_stage_reason = "50週MAが強く下向きで長期下降継続"
        elif pullback_like_long:
            long_stage = "PULLBACK_LIKE"
            long_stage_reason = "長期上昇トレンド中の押し目"
        elif early_stage_ok:
            long_stage = "EARLY_STAGE_OK"
            long_stage_reason = "長期下降が緩み短期反転が先行"
        else:
            long_stage = "TRANSITION"
            long_stage_reason = "長期トレンド移行中"

    # TOP20ではSTAGE4_WAIT/PULLBACK_LIKEを除外、TRANSITIONは減点。
    long_stage_ok_for_early = long_stage in ("EARLY_STAGE_OK","TRANSITION","UNKNOWN")

    # ─────────────────────────────────────────
    # EARLY_TURN score（最重要）
    # 「まだ伸び切っていない」「MA近辺」「傾き改善」「高値まで余地」を厚く評価。
    # ─────────────────────────────────────────
    early_score = 0.0

    # 価格位置 30点
    if -2 <= dist20 <= 8: early_score += 14
    elif -5 <= dist20 <= 12: early_score += 10
    elif -8 <= dist20 <= 16: early_score += 5

    if -3 <= dist40 <= 12: early_score += 10
    elif -8 <= dist40 <= 18: early_score += 6
    elif -12 <= dist40 <= 22: early_score += 3

    if ma5 >= ma10: early_score += 3
    if ma10 >= ma20 * 0.98: early_score += 3

    # MAの「向きが良くなっている」こと 25点
    if s20 > 0.5: early_score += 8
    elif s20 >= -0.3: early_score += 6
    elif s20 >= -1.0: early_score += 3

    if s20_improve >= 1.0: early_score += 6
    elif s20_improve >= 0.3: early_score += 4
    elif s20_improve > 0: early_score += 2

    if s40 >= 0: early_score += 5
    elif s40 >= -0.8: early_score += 4
    elif s40 >= -1.5: early_score += 2

    if s40_improve >= 0.6: early_score += 6
    elif s40_improve > 0: early_score += 3

    # 初動の新鮮さ 20点
    if weeks20_reclaim <= 3: early_score += 8
    elif weeks20_reclaim <= 6: early_score += 6
    elif weeks20_reclaim <= 9: early_score += 3

    if weeks40_reclaim <= 5: early_score += 5
    elif weeks40_reclaim <= 10: early_score += 3

    if 3 <= weeks_from_low <= 14: early_score += 7
    elif 2 <= weeks_from_low <= 20: early_score += 4
    elif weeks_from_low <= 24: early_score += 2

    # 上値余地 15点
    # 高値更新済みより「高値までまだ5〜35%ほど余地」がある銘柄をEARLYでは優遇。
    if -35 <= high52_dist <= -8: early_score += 8
    elif -45 <= high52_dist < -35: early_score += 5
    elif -8 < high52_dist <= -4: early_score += 4
    elif -4 < high52_dist <= 0: early_score += 1

    if -10 <= dist13_high <= 2: early_score += 4
    elif -18 <= dist13_high < -10: early_score += 2

    if base_range <= 35: early_score += 3
    elif base_range <= 45: early_score += 2

    # 確認 10点
    if 45 <= rsi <= 68: early_score += 4
    elif 40 <= rsi <= 72: early_score += 2

    if 2 <= ret13 <= 16: early_score += 3
    elif 0 < ret13 < 2: early_score += 2

    if completed_vol_ratio >= 1.2: early_score += 3
    elif completed_vol_ratio >= 0.8: early_score += 2
    elif completed_vol_ratio >= 0.6: early_score += 1

    early_score = min(100.0, early_score)

    early_eligible = (
        2 <= weeks_from_low <= 24
        and -8.0 <= dist20 <= 16.0
        and -12.0 <= dist40 <= 22.0
        and -50.0 <= high52_dist <= -3.0
        and s20 >= -1.0
        and s40 >= -1.8
        and (
            weeks20_reclaim <= 9
            or weeks40_reclaim <= 12
            or weeks20_slope_turn <= 6
            or s20_improve >= 0.6
        )
    )

    # ─────────────────────────────────────────
    # BASE_TURN score
    # ─────────────────────────────────────────
    base_turn_score = 0.0
    if base_range <= 20: base_turn_score += 20
    elif base_range <= 30: base_turn_score += 16
    elif base_range <= 40: base_turn_score += 10
    elif base_range <= 45: base_turn_score += 5

    if -3 <= dist20 <= 8: base_turn_score += 15
    elif -6 <= dist20 <= 13: base_turn_score += 9

    if s20 > 0.5: base_turn_score += 12
    elif s20 > 0: base_turn_score += 9
    elif s20 >= -0.5: base_turn_score += 4

    if s40 >= 0: base_turn_score += 10
    elif s40 >= -0.8: base_turn_score += 6

    if dist13_high >= -3: base_turn_score += 15
    elif dist13_high >= -8: base_turn_score += 10
    elif dist13_high >= -12: base_turn_score += 5

    if -25 <= high52_dist <= -3: base_turn_score += 10
    elif -35 <= high52_dist < -25: base_turn_score += 5

    if 8 <= weeks_from_low <= 24: base_turn_score += 8
    elif weeks_from_low >= 5: base_turn_score += 4

    if 48 <= rsi <= 70: base_turn_score += 5
    elif 42 <= rsi <= 76: base_turn_score += 3

    if completed_vol_ratio >= 1.0: base_turn_score += 5
    elif completed_vol_ratio >= 0.7: base_turn_score += 3

    base_turn_score = min(100.0, base_turn_score)
    base_eligible = (
        base_range <= 45
        and -6 <= dist20 <= 13
        and s20 >= -0.5
        and s40 >= -1.0
        and dist13_high >= -12
        and weeks_from_low >= 5
    )

    # ─────────────────────────────────────────
    # REACCEL score（本命は厳選）
    # ─────────────────────────────────────────
    reaccel_score = 0.0
    if high52_dist >= -5: reaccel_score += 20
    elif high52_dist >= -10: reaccel_score += 15
    elif high52_dist >= -15: reaccel_score += 8

    if dist13_high >= 0: reaccel_score += 20
    elif dist13_high >= -3: reaccel_score += 15
    elif dist13_high >= -6: reaccel_score += 8

    if ma20 >= ma40 and s20 > 0 and s40 >= 0: reaccel_score += 20
    elif s20 > 0 and s40 >= -0.5: reaccel_score += 12

    if 0 <= dist20 <= 10: reaccel_score += 10
    elif -2 <= dist20 <= 15: reaccel_score += 6

    if 0 <= dist40 <= 25: reaccel_score += 10
    elif -3 <= dist40 <= 32: reaccel_score += 5

    if 3 <= ret13 <= 20: reaccel_score += 10
    elif ret13 > 0: reaccel_score += 5

    if 52 <= rsi <= 75: reaccel_score += 5
    elif 45 <= rsi <= 80: reaccel_score += 3

    if completed_vol_ratio >= 1.2: reaccel_score += 5
    elif completed_vol_ratio >= 0.8: reaccel_score += 3

    reaccel_score = min(100.0, reaccel_score)
    reaccel_eligible = (
        close >= ma20
        and ma20 >= ma40
        and s20 > 0
        and s40 >= -0.5
        and high52_dist >= -15
        and dist13_high >= -8
    )

    # EARLY_TURNを最優先で分類。
    # 条件を満たす初動をREACCELへ吸わせない。
    if early_eligible and early_score >= H_SCORE_MIN:
        setup_type = "EARLY_TURN"
        score = early_score
    elif base_eligible and base_turn_score >= H_SCORE_MIN:
        setup_type = "BASE_TURN"
        score = base_turn_score
    elif reaccel_eligible and reaccel_score >= H_SCORE_MIN:
        setup_type = "REACCEL"
        score = reaccel_score
    else:
        # 研究候補として最も近い形を残す
        candidates = {
            "EARLY_TURN": early_score,
            "BASE_TURN": base_turn_score,
            "REACCEL": reaccel_score,
        }
        setup_type = max(candidates, key=candidates.get)
        score = candidates[setup_type]

    # 過熱ペナルティ。EARLYはMA近辺が前提なので通常ほぼ0。
    overheat_penalty = 0.0
    if dist20 > 18:
        overheat_penalty += min(12.0, (dist20 - 18.0) * 1.2)
    if rsi > 80:
        overheat_penalty += min(6.0, (rsi - 80.0) * 0.8)
    score = max(0.0, min(100.0, score - overheat_penalty))

    if score < H_SCORE_MIN:
        return drop("H_スコア不足")

    # タイプ別の本命昇格。
    early_core = (
        setup_type == "EARLY_TURN"
        and score >= H_EARLY_CORE_MIN
        and -4.0 <= dist20 <= 10.0
        and -8.0 <= dist40 <= 18.0
        and s20 >= -0.3
        and s20_improve > 0
        and 3 <= weeks_from_low <= 20
        and -45.0 <= high52_dist <= -5.0
        and (
            weeks20_reclaim <= 6
            or weeks40_reclaim <= 10
            or weeks20_slope_turn <= 4
        )
        and not falling_knife
        and reversal_confirmed
        and shape_ok_for_early
        and long_stage_ok_for_early
        and overheat_penalty <= 3.0
    )

    base_core = (
        setup_type == "BASE_TURN"
        and score >= H_BASE_CORE_MIN
        and -4.0 <= dist20 <= 12.0
        and s20 > 0
        and s40 >= -0.8
        and dist13_high >= -8
        and base_range <= 40
        and overheat_penalty <= 6.0
    )

    # REACCELは「状態」ではなく新しい再加速だけ本命。
    reaccel_core = (
        setup_type == "REACCEL"
        and score >= H_REACCEL_CORE_MIN
        and -2.0 <= dist20 <= 12.0
        and s20 > 0
        and s40 >= 0
        and high52_dist >= -12
        and (
            weeks13_break <= 3
            or weeks20_reclaim <= 4
        )
        and overheat_penalty <= 6.0
    )

    core_ok = early_core or base_core or reaccel_core
    if setup_type == "EARLY_TURN":
        if early_core:
            signal_stage = "🌱初動本命"
        elif falling_knife:
            signal_stage = "⚠️急落後確認待ち"
        elif long_stage == "STAGE4_WAIT":
            signal_stage = "⬇️長期Stage4・待機"
        elif long_stage == "PULLBACK_LIKE":
            signal_stage = "↗️長期押し目・別系統"
        elif downtrend_continuation:
            signal_stage = "⬇️下降継続・除外"
        elif high_zone_pullback:
            signal_stage = "↗️押し目再始動・別系統"
        elif not reversal_confirmed or not stable_reversal:
            signal_stage = "👀反転定着待ち"
        else:
            signal_stage = "👀先回り監視"
    elif setup_type == "BASE_TURN":
        signal_stage = "🧱ベース本命" if base_core else "🧱ベース監視"
    else:
        signal_stage = "🚀再加速本命" if reaccel_core else "➡️再加速継続"

    # ─────────────────────────────────────────
    # EARLY専用「初動優先ランキング」
    # 初動形状だけでなく、損切りの近さ・52週高値までの余地・新鮮さも評価する。
    # これは探索候補を減らすフィルターではなく、見る順番を決めるための順位付け。
    # ─────────────────────────────────────────
    recent8_low = float(d["Low"].iloc[-8:].min()) if len(d) >= 8 else float(cur["Low"])
    stop_candidates = []
    for level in (ma20 * 0.98, ma40 * 0.98, recent8_low * 0.99):
        if level > 0 and level < close:
            stop_candidates.append(level)

    # まずは純粋なテクニカルストップを算出。
    technical_stop_price = max(stop_candidates) if stop_candidates else max(0.01, recent8_low * 0.98)
    technical_stop_distance = (
        max(0.0, (close / technical_stop_price - 1.0) * 100.0)
        if technical_stop_price > 0 else 99.0
    )

    # 実戦用ストップ幅は「近すぎるストップ」を補正する。
    # 週足ATR14の55%を目安にし、最低2.5%、最大5.0%の床を設定。
    prev_close_series = d["Close"].shift(1)
    tr = pd.concat([
        (d["High"] - d["Low"]).abs(),
        (d["High"] - prev_close_series).abs(),
        (d["Low"] - prev_close_series).abs(),
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1]) if len(d) >= 14 else np.nan
    atr14_pct = (atr14 / close * 100.0) if close > 0 and not np.isnan(atr14) else 0.0
    stop_floor_pct = max(2.5, min(5.0, atr14_pct * 0.55)) if atr14_pct > 0 else 2.5

    stop_distance = max(technical_stop_distance, stop_floor_pct)
    stop_price = close / (1.0 + stop_distance / 100.0) if close > 0 else technical_stop_price
    stop_normalized = technical_stop_distance + 0.05 < stop_distance

    upside52 = max(0.0, (high52 / close - 1.0) * 100.0) if close > 0 else 0.0
    rr_proxy = upside52 / stop_distance if stop_distance > 0.1 else 0.0

    # リスク評価も補正後の実戦ストップ幅を使う。
    if 2.5 <= stop_distance <= 4.0:
        risk_score = 100.0
    elif stop_distance <= 6.0:
        risk_score = 90.0
    elif stop_distance <= 8.0:
        risk_score = 75.0
    elif stop_distance <= 10.0:
        risk_score = 55.0
    elif stop_distance <= 12.0:
        risk_score = 35.0
    else:
        risk_score = 10.0

    if 10.0 <= upside52 <= 35.0:
        upside_score = 100.0
    elif 5.0 <= upside52 < 10.0:
        upside_score = 65.0
    elif 35.0 < upside52 <= 50.0:
        upside_score = 85.0
    elif upside52 > 50.0:
        upside_score = 65.0
    else:
        upside_score = 30.0

    freshness_candidates = [
        x for x in (weeks20_reclaim, weeks40_reclaim, weeks20_slope_turn)
        if isinstance(x, (int, float)) and x < 999
    ]
    freshness_weeks = min(freshness_candidates) if freshness_candidates else 20
    if freshness_weeks <= 2:
        freshness_score = 100.0
    elif freshness_weeks <= 4:
        freshness_score = 85.0
    elif freshness_weeks <= 6:
        freshness_score = 70.0
    elif freshness_weeks <= 9:
        freshness_score = 50.0
    else:
        freshness_score = 30.0

    early_priority_score = (
        early_score * 0.45
        + risk_score * 0.25
        + upside_score * 0.20
        + freshness_score * 0.10
    )

    # falling knifeは「ストップが近い」せいで見かけ上高得点になりやすいので強く減点。
    if falling_knife:
        early_priority_score -= 35.0
    elif damage_points >= 20:
        early_priority_score -= 18.0
    if not reversal_confirmed:
        early_priority_score -= 10.0
    if downtrend_continuation:
        early_priority_score -= 30.0
    if high_zone_pullback:
        early_priority_score -= 22.0
    if long_stage == "STAGE4_WAIT":
        early_priority_score -= 35.0
    elif long_stage == "PULLBACK_LIKE":
        early_priority_score -= 22.0
    elif long_stage == "TRANSITION":
        early_priority_score -= 6.0
    if not stable_reversal:
        early_priority_score -= 12.0

    early_priority_score = max(0.0, min(100.0, early_priority_score))

    if early_priority_score >= 85:
        early_rank = "S"
    elif early_priority_score >= 78:
        early_rank = "A"
    elif early_priority_score >= 70:
        early_rank = "B"
    elif early_priority_score >= 62:
        early_rank = "C"
    else:
        early_rank = "D"

    if falling_knife:
        early_rank = "D"
        add_on_room = "× 急落後・反転待ち"
    elif long_stage == "STAGE4_WAIT":
        early_rank = "D"
        add_on_room = "× 長期Stage4"
    elif long_stage == "PULLBACK_LIKE":
        if early_rank in ("S", "A"):
            early_rank = "B"
        add_on_room = "↗️ PULLBACK候補"
    elif downtrend_continuation:
        early_rank = "D"
        add_on_room = "× 下降継続"
    elif high_zone_pullback:
        if early_rank in ("S", "A"):
            early_rank = "B"
        add_on_room = "↗️ PULLBACK/REACCEL候補"
    elif not reversal_confirmed or not stable_reversal:
        if early_rank in ("S", "A"):
            early_rank = "B"
        add_on_room = "△ 反転定着後"
    elif early_priority_score >= 78 and stop_distance <= 8.0 and upside52 >= 12.0:
        add_on_room = "◎ 追撃余地大"
    elif stop_distance <= 10.0 and upside52 >= 8.0:
        add_on_room = "○ 追撃余地あり"
    else:
        add_on_room = "△ 追撃余地限定"

    ctr["H_合格"] = ctr.get("H_合格", 0) + 1
    if core_ok:
        ctr["H_本命"] = ctr.get("H_本命", 0) + 1
    else:
        ctr["H_研究候補"] = ctr.get("H_研究候補", 0) + 1

    return {
        "close": round(close, 1),
        "score": round(score, 1),
        "early_score": round(early_score, 1),
        "base_score": round(base_turn_score, 1),
        "reaccel_score": round(reaccel_score, 1),
        "core_flag": "本命" if core_ok else "研究候補",
        "signal_stage": signal_stage,
        "setup_type": setup_type,
        "early_priority_score": round(early_priority_score, 1),
        "early_rank": early_rank,
        "stop_price": round(stop_price, 1),
        "stop_distance": round(stop_distance, 1),
        "technical_stop_price": round(technical_stop_price, 1),
        "technical_stop_distance": round(technical_stop_distance, 1),
        "stop_floor_pct": round(stop_floor_pct, 1),
        "stop_normalized": "補正あり" if stop_normalized else "補正なし",
        "atr14_pct": round(atr14_pct, 1),
        "upside52": round(upside52, 1),
        "rr_proxy": round(min(rr_proxy, 99.0), 2),
        "freshness_weeks": int(freshness_weeks),
        "add_on_room": add_on_room,
        "damage_points": int(damage_points),
        "damage_flag": "⚠️急落ダメージ" if falling_knife else "✅正常",
        "reversal_confirmed": "✅反転確認" if reversal_confirmed else "⏳未確認",
        "prev_week_ret": round(prev_week_ret, 1),
        "completed4_ret": round(completed4_ret, 1),
        "ma5_slope": round(ma5_slope, 2),
        "weeks_since_52w_high": int(weeks_since_52w_high),
        "above20_ratio_26": round(above20_ratio_26 * 100.0, 1),
        "above40_ratio_26": round(above40_ratio_26 * 100.0, 1),
        "high_zone_pullback": "↗️押し目再始動" if high_zone_pullback else "✅非該当",
        "downtrend_continuation": "⬇️下降継続" if downtrend_continuation else "✅非該当",
        "stable_reversal": "✅定着確認" if stable_reversal else "⏳未定着",
        "shape_ok_for_early": "✅EARLY適合" if shape_ok_for_early else "×別系統/未確定",
        "long_stage": long_stage,
        "long_stage_reason": long_stage_reason,
        "ma50w": None if np.isnan(ma50w) else round(ma50w, 1),
        "ma150w": None if np.isnan(ma150w) else round(ma150w, 1),
        "ma200w": None if np.isnan(ma200w) else round(ma200w, 1),
        "s50w": None if np.isnan(s50w) else round(s50w, 2),
        "s150w": None if np.isnan(s150w) else round(s150w, 2),
        "s200w": None if np.isnan(s200w) else round(s200w, 2),
        "ma5": round(ma5, 1),
        "ma20": round(ma20, 1),
        "ma40": round(ma40, 1),
        "slope20": round(s20, 2),
        "slope40": round(s40, 2),
        "dist20": round(dist20, 2),
        "dist40": round(dist40, 2),
        "base_range": round(base_range, 1),
        "weeks_from_low": int(weeks_from_low),
        "high52_dist": round(high52_dist, 1),
        "recovery52": round(recovery52, 1),
        "dist13_high": round(dist13_high, 1),
        "ret13": round(ret13, 1),
        "vol_ratio": round(completed_vol_ratio, 2),
        "weeks20_reclaim": int(weeks20_reclaim),
        "weeks40_reclaim": int(weeks40_reclaim),
        "weeks20_slope_turn": int(weeks20_slope_turn),
        "weeks40_slope_turn": int(weeks40_slope_turn),
        "weeks13_break": int(weeks13_break),
        "s20_improve": round(s20_improve, 2),
        "s40_improve": round(s40_improve, 2),
        "rsi": round(rsi, 1),
        "overheat_penalty": round(overheat_penalty, 1),
    }


def check_b1(df: pd.DataFrame, ctr: dict) -> dict | None:
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None
    need = ["MA21","MA50","MA200","B21_U","B21_L","B50_U","B50_L","RSI"]
    rec = df.dropna(subset=need).iloc[-60:]
    if len(rec) < 20: return drop("B1_データ不足")

    cur = rec.iloc[-1]
    ma21, ma50, ma200 = float(cur["MA21"]), float(cur["MA50"]), float(cur["MA200"])
    close, rsi = float(cur["Close"]), float(cur["RSI"])
    b21u, b21l, b50u, b50l = float(cur["B21_U"]), float(cur["B21_L"]), float(cur["B50_U"]), float(cur["B50_L"])

    if ma50 <= ma200: return drop("B1_①200MA以下")
    crossed = (rec["B21_L"] > rec["B50_U"]).astype(int)
    if not (crossed.rolling(2).min() == 1).any(): return drop("B1_②上抜け実績なし")
    cr = rec[rec["B21_L"] > rec["B50_U"]]
    days = (rec.index[-1] - cr.index[-1]).days
    if not (3 <= days <= 45): return drop("B1_③クロス日数外")

    dist = (close - b50u) / b50u * 100
    if not (-5.0 <= dist <= 2.0): return drop("B1_④押し目範囲外")
    if b21u <= b50l: return drop("B1_⑤トレンド崩壊")
    if not (35 <= rsi <= 65): return drop("B1_⑥RSI範囲外")

    s50 = float(cur["MA50_s5"]) if "MA50_s5" in df.columns else 0.0
    if s50 < 0: return drop("B1_⑦50MA下向き")

    if -1.5 <= dist <= 0.5: rank = "S"
    elif -3.0 <= dist < -1.5 or 0.5 < dist <= 2.0: rank = "A"
    else: rank = "B"

    ctr["B1_合格"] = ctr.get("B1_合格", 0) + 1
    return {
        "close": round(close,0), "ma21": round(ma21,0), "ma50": round(ma50,0), "ma200": round(ma200,0),
        "b50u": round(b50u,0), "b50l": round(b50l,0), "dist": round(dist,1), "s50": round(s50,2),
        "rsi": round(rsi,1), "days": int(days), "rank": rank,
    }

def check_b2(df: pd.DataFrame, ctr: dict) -> dict | None:
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None
    need = ["MA21","MA50","MA200","B21_U","B21_L","B50_U","B50_L","RSI","Volume"]
    rec30 = df.dropna(subset=need).iloc[-30:]
    if len(rec30) < 10: return drop("B2_データ不足")

    cur = rec30.iloc[-1]
    ma21, ma50, ma200 = float(cur["MA21"]), float(cur["MA50"]), float(cur["MA200"])
    close, rsi = float(cur["Close"]), float(cur["RSI"])
    b21l, b50u = float(cur["B21_L"]), float(cur["B50_U"])

    if ma50 <= ma200: return drop("B2_①200MA以下")
    rec60 = df.dropna(subset=need).iloc[-60:]
    crossed = (rec60["B21_L"] > rec60["B50_U"]).astype(int)
    if not (crossed.rolling(2).min() == 1).any(): return drop("B2_②上抜け実績なし")

    r3 = rec30.iloc[-4:-1]
    if not ((r3["Close"] < r3["B21_L"]).any() and close > b21l): return drop("B2_③21MAバンド上抜けなし")
    if close < b50u * 0.98: return drop("B2_④50BAND未到達")

    vol_avg = float(rec30["Volume"].mean())
    vol_ratio = float(cur["Volume"]) / (vol_avg + 1)
    if vol_ratio < 1.5: return drop("B2_⑤出来高不足")
    if rsi < 45: return drop("B2_⑥RSI不足")

    ctr["B2_合格"] = ctr.get("B2_合格", 0) + 1
    s50 = float(cur["MA50_s5"]) if "MA50_s5" in df.columns else 0.0
    return {
        "close": round(close,0), "ma21": round(ma21,0), "ma50": round(ma50,0), "ma200": round(ma200,0),
        "b21l": round(b21l,0), "b50u": round(b50u,0), "vol_ratio": round(vol_ratio,2),
        "s50": round(s50,2), "rsi": round(rsi,1),
    }


def check_c(df: pd.DataFrame, ctr: dict) -> dict | None:
    """
    パターンC: ボリンジャーバンド +2σ ブレイク
    ──────────────────────────────────────────────────
    ① 直近BREAKOUT_DAYS日以内に終値が+2σを上抜けた実績
    ② ブレイク時の出来高が20日平均のVOL_RATIO_MIN倍以上
    ③ 直近MOMENTUM_DAYS日でMOMENTUM_MIN以上の上昇（モメンタム）
    ④ 前日がGAP_EXCLUDE_PCT以上の急騰なら除外（窓開け失速回避）
    ⑤ 曜日フィルター（USE_DOW_FILTERがTrueなら月〜水のみ）
    """
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None

    if df is None or len(df) < C_MIN_BARS:
        return drop("C_データ不足")

    d = df.copy()
    d["SMA"]    = d["Close"].rolling(BB_PERIOD).mean()
    d["STD"]    = d["Close"].rolling(BB_PERIOD).std()
    d["Upper2"] = d["SMA"] + d["STD"] * BB_SIGMA
    d["VolMA"]  = d["Volume"].rolling(VOL_MA_PERIOD).mean()
    d = d.dropna(subset=["SMA","Upper2","VolMA"])
    if len(d) < 10:
        return drop("C_指標計算後データ不足")

    # ① 直近BREAKOUT_DAYS日以内に+2σ上抜け
    recent = d.iloc[-BREAKOUT_DAYS:]
    breakout_mask = recent["Close"] > recent["Upper2"]
    if not breakout_mask.any():
        return drop("C_①上抜けなし")

    breakout_positions = [i for i, v in enumerate(breakout_mask) if v]
    breakout_iloc_in_recent = breakout_positions[-1]
    breakout_iloc = len(d) - BREAKOUT_DAYS + breakout_iloc_in_recent

    # ② 出来高フィルター
    breakout_vol = float(d["Volume"].iloc[breakout_iloc])
    avg_vol      = float(d["VolMA"].iloc[breakout_iloc])
    if avg_vol <= 0:
        return drop("C_②出来高平均ゼロ")
    vol_ratio = breakout_vol / avg_vol
    if vol_ratio < VOL_RATIO_MIN:
        return drop("C_②出来高不足")

    # ③ モメンタムフィルター
    price_now  = float(d["Close"].iloc[-1])
    price_past = float(d["Close"].iloc[-MOMENTUM_DAYS - 1])
    momentum   = (price_now - price_past) / price_past
    if momentum < MOMENTUM_MIN:
        return drop("C_③モメンタム不足")

    # ④ 急騰翌日除外
    if len(d) >= 3:
        prev_close      = float(d["Close"].iloc[-2])
        prev_prev_close = float(d["Close"].iloc[-3])
        prev_day_change = (prev_close - prev_prev_close) / prev_prev_close
        if prev_day_change >= GAP_EXCLUDE_PCT:
            return drop("C_④急騰翌日のため除外")

    # ⑤ 曜日フィルター
    if USE_DOW_FILTER:
        today_dow = d.index[-1].dayofweek
        if today_dow > 2:
            return drop("C_⑤曜日フィルター対象外")

    upper2_now  = float(d["Upper2"].iloc[-1])
    dist_upper2 = (price_now - upper2_now) / upper2_now
    breakout_date = d.index[breakout_iloc].strftime("%Y-%m-%d")
    days_since_breakout = len(d) - 1 - breakout_iloc

    if days_since_breakout == 0:
        status = "🔥 本日ブレイク"
    elif days_since_breakout == 1:
        status = "⭐ 昨日ブレイク・初動"
    else:
        status = "👀 ブレイク後フォロー"

    ctr["C_合格"] = ctr.get("C_合格", 0) + 1
    return {
        "status"      : status,
        "close"       : round(price_now, 1),
        "upper2"      : round(upper2_now, 1),
        "dist_upper2" : round(dist_upper2 * 100, 2),
        "vol_ratio"   : round(vol_ratio, 1),
        "momentum"    : round(momentum * 100, 2),
        "breakout_date": breakout_date,
        "days_since"  : int(days_since_breakout),
    }


def check_d(dfw: pd.DataFrame, dfd: pd.DataFrame, ctr: dict) -> dict | None:
    """
    パターンD:「週足上昇トレンド + 日足の初押しSMA25タッチ + 下ひげ陽線」
    ══════════════════════════════════════════════════════════
    週足（大局）:
      ① 週足20SMA > 40SMA（パーフェクトオーダー）
      ② 週足20SMA・40SMAがともに上向き

    日足（初押し・エントリー）:
      ③ SMA25が上向き（上昇トレンド）
      ④ 直近で終値がSMA25を明確に上抜けた実績があり、その後の押し目
      ⑤ SMA25タッチ（安値がSMA25の -2%〜+1% に接触）
      ⑥ 下ひげ陽線（当日が陽線、下ひげが実体の0.5倍以上、下ひげ>上ひげ）
    ══════════════════════════════════════════════════════════
    """
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None

    # ── 週足チェック ──────────────────────────────────────
    needw = ["WMA20", "MA40", "WMA20_s4", "MA40_s4b"]
    recw = dfw.dropna(subset=needw)
    if len(recw) < 5:
        return drop("D_週足データ不足")
    cw = recw.iloc[-1]
    wma20 = float(cw["WMA20"]); wma40 = float(cw["MA40"])
    wsl20 = float(cw["WMA20_s4"]); wsl40 = float(cw["MA40_s4b"])

    # ① パーフェクトオーダー
    if wma20 <= wma40:
        return drop("D_①週足PO不成立")
    # ② 両SMAが上向き
    if wsl20 <= 0 or wsl40 <= 0:
        return drop("D_②週足SMA上向きでない")

    # ── 日足チェック ──────────────────────────────────────
    needd = ["MA25", "MA25_s5", "Open", "High", "Low", "Close"]
    recd = dfd.dropna(subset=needd).iloc[-60:]
    if len(recd) < 20:
        return drop("D_日足データ不足")
    cd = recd.iloc[-1]

    o = float(cd["Open"]); h = float(cd["High"])
    l = float(cd["Low"]);  cl = float(cd["Close"])
    ma25 = float(cd["MA25"]); ma25_s5 = float(cd["MA25_s5"])

    # ③ SMA25が上向き
    if ma25_s5 <= 0:
        return drop("D_③SMA25上向きでない")

    # ④ 初押し判定：直近15日以内に終値がSMA25を明確に上抜けた実績があり、
    #    かつ現在はその後の押し目局面（直近数日でSMA25に近づいている）
    recent15 = recd.iloc[-15:]
    above_cnt = (recent15["Close"] > recent15["MA25"] * 1.02).sum()  # 明確に上だった日数
    if above_cnt < 3:
        return drop("D_④上昇実績なし")

    # ⑤ SMA25タッチ：安値がSMA25の -2%〜+1% に接触
    low_ratio = (l - ma25) / ma25
    if not (-0.02 <= low_ratio <= 0.01):
        return drop("D_⑤SMA25タッチなし")

    # ⑥ 下ひげ陽線
    if cl <= o:
        return drop("D_⑥陽線でない")
    body = cl - o
    lower_wick = o - l          # 実体下端(始値)から安値まで
    upper_wick = h - cl         # 実体上端(終値)から高値まで
    if body <= 0:
        return drop("D_⑥実体なし")
    if lower_wick < body * 0.5:
        return drop("D_⑥下ひげ不足")
    if lower_wick <= upper_wick:
        return drop("D_⑥下ひげが上ひげ以下")

    ctr["D_合格"] = ctr.get("D_合格", 0) + 1
    return {
        "close"       : round(cl, 1),
        "open"        : round(o, 1),
        "high"        : round(h, 1),
        "low"         : round(l, 1),
        "ma25"        : round(ma25, 1),
        "low_vs_ma25" : round(low_ratio * 100, 2),
        "lower_wick_r": round(lower_wick / body, 2),
        "wma20"       : round(wma20, 1),
        "wma40"       : round(wma40, 1),
    }


def check_e(df: pd.DataFrame, ctr: dict) -> dict | None:
    """
    パターンE:「揉み合い後の出来高急増ブレイク」
    ══════════════════════════════════════════════════════════
    ① 揉み合い期間（当日を除く直近E_LOOKBACK日）:
       - 出来高が減少傾向（後半10日平均 < 前半10日平均）
       - 値幅が縮小（後半の平均レンジ% < 前半の平均レンジ%）
    ② 出来高急増ブレイク（当日）:
       - 当日出来高が揉み合い期間平均の E_VOL_SURGE 倍以上
       - 前日終値比 +E_PRICE_MIN 以上の上昇 かつ 陽線
    ══════════════════════════════════════════════════════════
    """
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None

    need = ["Open", "High", "Low", "Close", "Volume"]
    d = df.dropna(subset=need)
    if len(d) < E_MIN_BARS:
        return drop("E_データ不足")

    today  = d.iloc[-1]
    consol = d.iloc[-(E_LOOKBACK + 1):-1]   # 当日を除く直近E_LOOKBACK日
    if len(consol) < E_LOOKBACK:
        return drop("E_データ不足")

    half = E_LOOKBACK // 2
    first_half  = consol.iloc[:half]
    second_half = consol.iloc[half:]

    # ── ① 出来高が減少傾向 ──────────────────────────────
    vol_first  = float(first_half["Volume"].mean())
    vol_second = float(second_half["Volume"].mean())
    if vol_first <= 0:
        return drop("E_①出来高データ異常")
    if vol_second >= vol_first:
        return drop("E_①出来高減少傾向なし")

    # ── ① 値幅が縮小 ────────────────────────────────────
    range_first  = float(((first_half["High"] - first_half["Low"])
                          / first_half["Close"]).mean())
    range_second = float(((second_half["High"] - second_half["Low"])
                          / second_half["Close"]).mean())
    if range_second >= range_first:
        return drop("E_①値幅縮小なし")

    # ── ② 当日の出来高急増 ──────────────────────────────
    vol_avg   = float(consol["Volume"].mean())
    vol_today = float(today["Volume"])
    if vol_avg <= 0:
        return drop("E_②出来高平均ゼロ")
    vol_ratio = vol_today / vol_avg
    if vol_ratio < E_VOL_SURGE:
        return drop("E_②出来高急増なし")

    # ── ② 価格上昇（前日比+3%以上 かつ 陽線）────────────
    o  = float(today["Open"]);  cl = float(today["Close"])
    prev_close = float(consol["Close"].iloc[-1])
    if prev_close <= 0:
        return drop("E_②前日終値異常")
    change = (cl - prev_close) / prev_close
    if change < E_PRICE_MIN:
        return drop("E_②価格上昇不足")
    if cl <= o:
        return drop("E_②陰線")

    ctr["E_合格"] = ctr.get("E_合格", 0) + 1

    # 揉み合いの縮小度（後半レンジ/前半レンジ、小さいほど収縮が強い）
    shrink_ratio = round(range_second / (range_first + 1e-9), 2)

    return {
        "close"       : round(cl, 1),
        "change_pct"  : round(change * 100, 2),
        "vol_ratio"   : round(vol_ratio, 1),
        "vol_today"   : int(vol_today),
        "shrink_ratio": shrink_ratio,
        "consol_days" : E_LOOKBACK,
    }


def check_f(df: pd.DataFrame, ctr: dict) -> dict | None:
    """
    パターンF:「21MA×200MA ゴールデンクロス（底打ち・第一ステージ転換）」
    ══════════════════════════════════════════════════════════
    ① 日足21MAが200MAを直近F_GC_DAYS日以内にゴールデンクロス
       （現在は21MA > 200MA、かつ少し前は21MA <= 200MAだった）
    ② 200MAの傾きが「下向き → 横ばい」に転換している
       - 過去（F_SLOPE_DAYS日前基準）は下向き（傾き% < F_DOWN_PREV_MAX）
       - 現在は横ばい〜上向き（傾き% >= F_FLAT_NOW_MIN）
    ※時価総額500億円以上は共通フィルタで担保
    ══════════════════════════════════════════════════════════
    """
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None

    need = ["MA21", "MA200", "MA200_sF", "MA200_sF_prev", "Close"]
    d = df.dropna(subset=need)
    if len(d) < 5:
        return drop("F_データ不足")

    cur = d.iloc[-1]
    ma21  = float(cur["MA21"]);  ma200 = float(cur["MA200"])
    close = float(cur["Close"])
    slope_now  = float(cur["MA200_sF"])
    slope_prev = float(cur["MA200_sF_prev"])

    # ① 現在は21MAが200MAより上
    if ma21 <= ma200:
        return drop("F_①GC未成立")

    # ① 直近F_GC_DAYS日以内にクロスした（それ以前は21MA <= 200MAだった日がある）
    recent = d.iloc[-(F_GC_DAYS + 1):]
    was_below = (recent["MA21"] <= recent["MA200"]).any()
    if not was_below:
        return drop("F_①GCが古すぎ")   # ずっと上＝クロスは古い

    # ② 過去は下向き
    if slope_prev >= F_DOWN_PREV_MAX:
        return drop("F_②過去に下降なし")
    # ② 現在は横ばい〜上向き
    if slope_now < F_FLAT_NOW_MIN:
        return drop("F_②まだ下降中")

    # クロスからの経過日数を求める
    below_idx = recent[recent["MA21"] <= recent["MA200"]].index
    days_since_gc = (d.index[-1] - below_idx[-1]).days if len(below_idx) else 0

    ctr["F_合格"] = ctr.get("F_合格", 0) + 1
    return {
        "close"        : round(close, 1),
        "ma21"         : round(ma21, 1),
        "ma200"        : round(ma200, 1),
        "gc_gap_pct"   : round((ma21 - ma200) / ma200 * 100, 2),
        "slope_prev"   : round(slope_prev, 2),
        "slope_now"    : round(slope_now, 2),
        "days_since_gc": int(days_since_gc),
    }


def check_g(df: pd.DataFrame, ctr: dict) -> dict | None:
    """
    パターンG:「ポケットピボット」（オニール系・Gil Morales / Chris Kacher）
    ══════════════════════════════════════════════════════════
    ① 上昇トレンド: 50日MA > 200日MA
    ② 10日MAが上向き
    ③ 10日MA付近: 終値が10日MAの -2%〜+5%、かつ安値が10日MAの3%以内にタッチ
    ④ 当日は陽線（上昇）
    ⑤ ポケットピボット出来高（核心）:
       当日出来高が、過去10日間の「下落日(陰線)の出来高の最大値」より大きい
    ══════════════════════════════════════════════════════════
    """
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None

    need = ["MA10", "MA50", "MA200", "MA10_s5", "Open", "High", "Low", "Close", "Volume"]
    d = df.dropna(subset=need)
    if len(d) < G_LOOKBACK + 2:
        return drop("G_データ不足")

    cur = d.iloc[-1]
    ma10  = float(cur["MA10"]);  ma50 = float(cur["MA50"]);  ma200 = float(cur["MA200"])
    o = float(cur["Open"]); h = float(cur["High"]); l = float(cur["Low"]); cl = float(cur["Close"])
    ma10_s5 = float(cur["MA10_s5"]); vol_today = float(cur["Volume"])

    # ① 上昇トレンド
    if ma50 <= ma200:
        return drop("G_①上昇トレンドでない")

    # ② 10日MAが上向き
    if ma10_s5 <= 0:
        return drop("G_②10MA上向きでない")

    # ③ 10日MA付近にいる（終値の位置）
    dist = (cl - ma10) / ma10
    if not (G_DIST_LOW <= dist <= G_DIST_HIGH):
        return drop("G_③10MAから離れすぎ")
    # ③ 安値が10日MAにタッチ（近い）
    low_dist = abs(l - ma10) / ma10
    if low_dist > G_TOUCH_PCT and l > ma10:
        # 安値が10日MAより上で、かつ3%以上離れている＝タッチしていない
        return drop("G_③10MAタッチなし")

    # ④ 陽線
    if cl <= o:
        return drop("G_④陰線")

    # ⑤ ポケットピボット出来高: 過去G_LOOKBACK日の「下落日」の出来高の最大値と比較
    past = d.iloc[-(G_LOOKBACK + 1):-1]   # 当日を除く直近10日
    down_days = past[past["Close"] < past["Open"]]   # 陰線の日
    if down_days.empty:
        # 過去10日に下落日がない（ずっと上げ）＝比較対象なし。
        # この場合はポケットピボットの定義上は判定できないが、強い上昇なので
        # 「直近10日の出来高平均」を代替基準にする
        down_vol_max = float(past["Volume"].mean())
    else:
        down_vol_max = float(down_days["Volume"].max())

    if vol_today <= down_vol_max:
        return drop("G_⑤出来高が下落日を超えない")

    ctr["G_合格"] = ctr.get("G_合格", 0) + 1
    vol_ratio = vol_today / (down_vol_max + 1)
    return {
        "close"        : round(cl, 1),
        "ma10"         : round(ma10, 1),
        "dist_ma10"    : round(dist * 100, 2),
        "vol_today"    : int(vol_today),
        "down_vol_max" : int(down_vol_max),
        "vol_vs_down"  : round(vol_ratio, 2),
        "ma10_slope"   : round(ma10_s5, 2),
    }


# ==========================================
# 4. ファンダメンタル情報
# ==========================================
def _fetch_fundamentals_one(ticker: str) -> dict:
    cagr_val, est_val, per_val, fwd_per_val, name_val, err = "-", "-", "-", "-", "-", ""
    for attempt in range(2):
        try:
            tk = yf.Ticker(ticker)
            fins = tk.financials
            if fins is not None and not fins.empty and "Total Revenue" in fins.index:
                revs = fins.loc["Total Revenue"].dropna()
                if len(revs) >= 2:
                    start_val, end_val = revs.iloc[-1], revs.iloc[0]
                    years = len(revs) - 1
                    if start_val and start_val > 0:
                        val = (end_val / start_val) ** (1 / years) - 1
                        cagr_val = f"{val:.1%}"
            info = tk.get_info() if hasattr(tk, "get_info") else tk.info
            if info:
                est = info.get("revenueGrowth")
                if isinstance(est, (float, int)):
                    est_val = f"{est:.1%}"
                # PER（実績）: trailingPE
                per = info.get("trailingPE")
                if isinstance(per, (float, int)) and per > 0:
                    per_val = f"{per:.1f}"
                # PER（予想）: forwardPE
                fwd_per = info.get("forwardPE")
                if isinstance(fwd_per, (float, int)) and fwd_per > 0:
                    fwd_per_val = f"{fwd_per:.1f}"
                # 銘柄名（正式社名 → 取れなければ略称）
                name = info.get("longName") or info.get("shortName")
                if name:
                    name_val = str(name)
            err = ""
            break
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(1.0)
    return {
        "ticker": ticker, "cagr": cagr_val, "est": est_val,
        "per": per_val, "fwd_per": fwd_per_val, "name": name_val, "err": err,
    }

def enrich_with_fundamentals(df: pd.DataFrame, name_map: dict | None = None,
                             max_workers: int = 4) -> pd.DataFrame:
    """
    銘柄名・売上CAGR・売上予想・PERを付与する。
    銘柄名はまずJPX一覧の日本語社名(name_map)を優先し、
    無ければyfinanceのinfoから取得した英語名にフォールバックする。
    """
    name_map = name_map or {}

    if df.empty:
        df["銘柄名"] = []
        df["売上5y CAGR"] = []
        df["売上予想"] = []
        df["PER"] = []
        df["予想PER"] = []
        return df
    tickers = df["Ticker"].tolist()
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_fundamentals_one, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            results[r["ticker"]] = r

    def get_name(t):
        # JPX一覧の日本語社名を優先。無ければyfinance(英語名)にフォールバック
        jp_name = name_map.get(t, "")
        if jp_name:
            return jp_name
        return results.get(t, {}).get("name", "-")

    df["銘柄名"]      = df["Ticker"].map(get_name)

    # ── 診断: 実際に何件JPX名がヒットしたか確認 ──
    hit_count = sum(1 for t in tickers if name_map.get(t, ""))
    print(f"[診断] enrich_with_fundamentals: 対象{len(tickers)}件中 "
          f"name_mapヒット{hit_count}件 / name_map総数{len(name_map)}件", flush=True)
    if tickers:
        sample_t = tickers[0]
        print(f"[診断] サンプルTicker={repr(sample_t)} → "
              f"name_map.get結果={repr(name_map.get(sample_t))} → "
              f"最終的な銘柄名={repr(get_name(sample_t))}", flush=True)

    df["売上5y CAGR"] = df["Ticker"].map(lambda t: results.get(t, {}).get("cagr", "-"))
    df["売上予想"]   = df["Ticker"].map(lambda t: results.get(t, {}).get("est", "-"))
    df["PER"]        = df["Ticker"].map(lambda t: results.get(t, {}).get("per", "-"))
    df["予想PER"]    = df["Ticker"].map(lambda t: results.get(t, {}).get("fwd_per", "-"))

    # 「銘柄名」を証券コードの直後に来るよう列順を入れ替える
    cols = df.columns.tolist()
    cols.remove("銘柄名")
    insert_at = cols.index("Ticker") + 1
    cols = cols[:insert_at] + ["銘柄名"] + cols[insert_at:]
    df = df[cols]
    return df


# ==========================================
# 5. バッチ処理 & スキャン
# ==========================================
def process_batch(batch: list, ctr: dict, min_turnover, min_mktcap) -> tuple:
    ra, rh, rb1, rb2, rc, rd, re_, rf, rg = [], [], [], [], [], [], [], [], []
    rm_candidates = []  # 決算モメンタム候補（業績取得前の軽量プレフィルター）
    cache_w, _ = download_batch(batch, "5y", "1wk", "週足")
    cache_d, _ = download_batch(batch, "2y", "1d",  "日足")

    for ticker in batch:
        code = ticker.replace(".T", "")
        try:
            dfd = cache_d.get(ticker)
            if dfd is None: continue
            tmp = dfd.dropna(subset=["Close","Volume"])
            if len(tmp) < 10: continue

            avg_to = float((tmp.iloc[-20:]["Close"] * tmp.iloc[-20:]["Volume"]).mean())
            if avg_to < min_turnover: continue

            mc = 0.0
            fi_ok = False
            for _ in range(2):
                try:
                    fi = yf.Ticker(ticker).fast_info
                    mc = getattr(fi, "market_cap", None) or 0
                    if getattr(fi, "currency", "JPY") != "JPY":
                        mc *= (yf.Ticker("USDJPY=X").fast_info.last_price or 150.0)
                    fi_ok = True
                    break
                except Exception:
                    time.sleep(1.0)
            if not fi_ok or mc < min_mktcap: continue

            mktcap_oku = round(mc / 1e8, 0)
            avg_to_oku = round(avg_to / 1e8, 2)

            # ── 決算モメンタム候補の軽量プレフィルター ──
            # 4,000銘柄すべてに四半期財務データを取りに行くと遅くなるため、
            # まず株価・出来高の勢いがある銘柄だけを候補として残す。
            # 最終的な決算判定は momentum.py で行う。
            try:
                recent = tmp.copy()
                recent["MA25_M"] = recent["Close"].rolling(25).mean()
                recent["VOL20_M"] = recent["Volume"].rolling(20).mean()
                if len(recent) >= 30:
                    last = recent.iloc[-1]
                    close_m = float(last["Close"])
                    ma25_m = float(last["MA25_M"])
                    vol20_m = float(last["VOL20_M"])
                    ret5_m = ((close_m / float(recent["Close"].iloc[-6])) - 1) * 100
                    ret20_m = ((close_m / float(recent["Close"].iloc[-21])) - 1) * 100
                    vol_ratio_m = float(last["Volume"]) / vol20_m if vol20_m > 0 else 0
                    high52_m = float(recent["High"].tail(252).max()) if "High" in recent.columns else close_m
                    near_high_m = close_m >= high52_m * 0.90 if high52_m > 0 else False
                    prefilter_score = 0
                    if close_m > ma25_m: prefilter_score += 2
                    if ret5_m >= 3: prefilter_score += 2
                    if ret20_m >= 5: prefilter_score += 2
                    if vol_ratio_m >= 1.5: prefilter_score += 2
                    if near_high_m: prefilter_score += 2
                    if prefilter_score >= 4:
                        rm_candidates.append({
                            "ticker": ticker,
                            "code": code,
                            "avg_to": avg_to_oku,
                            "mktcap": mktcap_oku,
                            "prefilter_score": prefilter_score,
                            "dfd": tmp,   # 取得済みの日足データを持ち回り、後で再取得しない
                        })
            except Exception:
                pass

            dfw_calc = None
            dfw = cache_w.get(ticker)
            if dfw is not None:
                if "Low" not in dfw.columns:
                    dfw = dfw.copy(); dfw["Low"] = dfw["Close"]
                dfw = dfw.dropna(subset=["Close"])
                if len(dfw) >= 50:
                    dfw = calc_weekly(dfw)
                    dfw_calc = dfw
                    res = check_a(dfw, ctr)
                    if res:
                        res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                        ra.append(res)

                    # 週足転換H: 画像に近い週足ベース反転/再加速をスコア抽出
                    res_h = check_h(dfw, ctr)
                    if res_h:
                        res_h.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                        rh.append(res_h)

            dfd2_calc = None
            dfd2 = dfd.dropna(subset=["Close"])
            if len(dfd2) >= 60:
                dfd2 = calc_daily(dfd2)
                dfd2_calc = dfd2
                res = check_b1(dfd2, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    rb1.append(res)
                res = check_b2(dfd2, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    rb2.append(res)

            # ── パターンC（ボリバンブレイク）。日足の元データ(dfd)を使う ──
            if len(tmp) >= C_MIN_BARS:
                res = check_c(tmp, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    rc.append(res)

            # ── パターンD（週足PO＋日足初押し下ひげ陽線）。週足・日足の両方が必要 ──
            if dfw_calc is not None and dfd2_calc is not None:
                res = check_d(dfw_calc, dfd2_calc, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    rd.append(res)

            # ── パターンE（揉み合い後の出来高急増ブレイク）。日足の元データ(tmp)を使う ──
            if len(tmp) >= E_MIN_BARS:
                res = check_e(tmp, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    re_.append(res)

            # ── パターンF（21MA×200MA GC底打ち）。日足のcalc_daily済みデータを使う ──
            if dfd2_calc is not None and len(dfd2_calc) >= F_MIN_BARS:
                res = check_f(dfd2_calc, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    rf.append(res)

            # ── パターンG（ポケットピボット）。日足のcalc_daily済みデータを使う ──
            if dfd2_calc is not None and len(dfd2_calc) >= G_MIN_BARS:
                res = check_g(dfd2_calc, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    rg.append(res)
        except Exception:
            pass

    return ra, rh, rb1, rb2, rc, rd, re_, rf, rg, rm_candidates


def format_a(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","status","close","low","ma5","ma40","band40_u","band40_l",
                            "dist_close","dist_low","slope_now","slope_prev","ma5_slope","rsi",
                            "weeks_since_cross","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","状態","終値","安値","5週MA","40週MA","40BANDu","40BANDl",
                 "終値とMA40距離%","安値とMA40距離%","40MA傾き%(4週)","40MA傾き%(前期)","5MA傾き%(4週)","RSI",
                 "上抜けからの週数","売買代金(億円)","時価総額(億円)"]
    # 上抜け初動（週数が浅いもの）を優先して上に並べる
    return d.sort_values(["上抜けからの週数", "終値とMA40距離%"],
                         ascending=[True, True]).reset_index(drop=True)


def format_h(rows):
    if not rows:
        return pd.DataFrame()

    rename_map = {
        "code":"証券コード","ticker":"Ticker","score":"週足転換スコア",
        "early_score":"EARLYスコア","base_score":"BASEスコア","reaccel_score":"REACCELスコア",
        "early_priority_score":"初動優先スコア","early_rank":"初動ランク",
        "core_flag":"本命判定","signal_stage":"初動ステージ","setup_type":"週足転換タイプ",
        "stop_price":"実戦想定損切り価格","stop_distance":"実戦想定損切り距離%",
        "technical_stop_price":"テクニカル損切り価格","technical_stop_distance":"テクニカル損切り距離%",
        "stop_floor_pct":"最低ストップ幅%","stop_normalized":"ストップ補正","atr14_pct":"週足ATR14%",
        "upside52":"52週高値まで上値余地%","rr_proxy":"RR目安","freshness_weeks":"初動鮮度週","add_on_room":"追撃余地",
        "damage_points":"急落ダメージ点","damage_flag":"急落判定","reversal_confirmed":"反転確認",
        "prev_week_ret":"前週騰落率%","completed4_ret":"完成4週騰落率%","ma5_slope":"5週MA傾き%(4週)",
        "weeks_since_52w_high":"52週高値から週数","above20_ratio_26":"26週20MA上滞在率%",
        "above40_ratio_26":"26週40MA上滞在率%","high_zone_pullback":"押し目再始動判定",
        "downtrend_continuation":"下降継続判定","stable_reversal":"反転定着","shape_ok_for_early":"EARLY形状判定",
        "long_stage":"長期ステージ","long_stage_reason":"長期ステージ理由",
        "ma50w":"50週MA","ma150w":"150週MA","ma200w":"200週MA",
        "s50w":"50週MA傾き%(8週)","s150w":"150週MA傾き%(13週)","s200w":"200週MA傾き%(13週)",
        "close":"終値","ma5":"5週MA","ma20":"20週MA","ma40":"40週MA",
        "slope20":"20週MA傾き%(4週)","slope40":"40週MA傾き%(4週)",
        "s20_improve":"20週MA傾き改善","s40_improve":"40週MA傾き改善",
        "dist20":"20週MA乖離%","dist40":"40週MA乖離%","base_range":"16週ベース幅%",
        "weeks_from_low":"26週安値から週数","weeks20_reclaim":"20週MA再奪回から週数",
        "weeks40_reclaim":"40週MA再奪回から週数","weeks20_slope_turn":"20週MA上向き転換から週数",
        "weeks40_slope_turn":"40週MA上向き転換から週数","high52_dist":"52週高値距離%",
        "recovery52":"52週安値回復率%","dist13_high":"13週高値距離%",
        "weeks13_break":"13週高値ブレイクから週数","ret13":"13週騰落率%",
        "vol_ratio":"完成週出来高倍率","rsi":"週RSI","overheat_penalty":"過熱ペナルティ",
        "avg_to":"売買代金(億円)","mktcap":"時価総額(億円)"
    }

    d=pd.DataFrame(rows).copy()
    wanted=[c for c in rename_map.keys() if c in d.columns]
    d=d[wanted].rename(columns=rename_map)

    type_order={"EARLY_TURN":0,"BASE_TURN":1,"REACCEL":2}
    if "週足転換タイプ" in d.columns:
        d["_type_order"]=d["週足転換タイプ"].map(type_order).fillna(9)
    else:
        d["_type_order"]=9

    sort_cols=[]
    ascending=[]
    for col,asc in [("_type_order",True),("初動優先スコア",False),("週足転換スコア",False),("完成週出来高倍率",False)]:
        if col in d.columns:
            sort_cols.append(col); ascending.append(asc)
    if sort_cols:
        d=d.sort_values(sort_cols,ascending=ascending)
    return d.drop(columns=["_type_order"],errors="ignore").reset_index(drop=True)

def format_b1(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","close","ma21","ma50","ma200","b50u","b50l","dist","rank","s50","rsi","days","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","終値","21日MA","50日MA","200日MA","50BANDu","50BANDl","50BAND上限距離%","ランク","50MA傾き%","RSI","クロスから日数","売買代金(億円)","時価総額(億円)"]
    return d.sort_values("クロスから日数", ascending=True).reset_index(drop=True)

def format_b2(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","close","ma21","ma50","ma200","b21l","b50u","vol_ratio","s50","rsi","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","終値","21日MA","50日MA","200日MA","21BANDl","50BANDu","出来高比","50MA傾き%","RSI","売買代金(億円)","時価総額(億円)"]
    return d.sort_values("出来高比", ascending=False).reset_index(drop=True)

def format_c(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","status","close","upper2","dist_upper2","vol_ratio","momentum","breakout_date","days_since","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","状態","終値","+2σ","+2σ乖離%","出来高倍率",f"直近{MOMENTUM_DAYS}日騰落率%","ブレイク日","ブレイクからの日数","売買代金(億円)","時価総額(億円)"]
    return d.sort_values(["ブレイクからの日数","出来高倍率"], ascending=[True, False]).reset_index(drop=True)

def format_d(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","close","open","high","low","ma25","low_vs_ma25","lower_wick_r","wma20","wma40","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","終値","始値","高値","安値","日足SMA25","安値のSMA25乖離%","下ひげ/実体比","週足20SMA","週足40SMA","売買代金(億円)","時価総額(億円)"]
    return d.sort_values("安値のSMA25乖離%", key=abs).reset_index(drop=True)

def format_e(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","close","change_pct","vol_ratio","vol_today","shrink_ratio","consol_days","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","終値","前日比%","出来高倍率","当日出来高","値幅縮小率","揉み合い日数","売買代金(億円)","時価総額(億円)"]
    # 出来高倍率が高い順（勢いの強い順）に並べる
    return d.sort_values("出来高倍率", ascending=False).reset_index(drop=True)

def format_f(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","close","ma21","ma200","gc_gap_pct","slope_prev","slope_now","days_since_gc","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","終値","21日MA","200日MA","GC乖離%","200MA傾き(過去)%","200MA傾き(現在)%","GCからの日数","売買代金(億円)","時価総額(億円)"]
    # クロスから日数が浅い順（新鮮な転換順）に並べる
    return d.sort_values("GCからの日数", ascending=True).reset_index(drop=True)

def format_g(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","close","ma10","dist_ma10","vol_today","down_vol_max","vol_vs_down","ma10_slope","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","終値","10日MA","10MA乖離%","当日出来高","下落日最大出来高","出来高/下落日比","10MA傾き%","売買代金(億円)","時価総額(億円)"]
    # 下落日出来高に対する当日出来高の比が高い順（買いの勢いが強い順）
    return d.sort_values("出来高/下落日比", ascending=False).reset_index(drop=True)

def build_multi_hit(dfa, dfh, dfb1, dfb2, dfc, dfd, dfe, dff, dfg):
    hits = {}
    def register(df, pattern):
        if df.empty: return
        for _, row in df.iterrows():
            t = row["Ticker"]
            if t not in hits:
                hits[t] = {"証券コード": row["証券コード"], "Ticker": t, "終値": row["終値"], "パターン": set()}
                if "銘柄名" in row: hits[t]["銘柄名"] = row["銘柄名"]
                if "売上5y CAGR" in row: hits[t]["売上5y CAGR"] = row["売上5y CAGR"]
                if "売上予想" in row: hits[t]["売上予想"] = row["売上予想"]
                if "PER" in row: hits[t]["PER"] = row["PER"]
                if "予想PER" in row: hits[t]["予想PER"] = row["予想PER"]
            hits[t]["パターン"].add(pattern)
            hits[t]["終値"] = row["終値"]
    register(dfa,  "週A")
    register(dfh,  "週H")
    register(dfb1, "日B1")
    register(dfb2, "日B2")
    register(dfc,  "ボリバンC")
    register(dfd,  "初押しD")
    register(dfe,  "出来高E")
    register(dff,  "GC底打ちF")
    register(dfg,  "ポケピG")
    if not hits: return pd.DataFrame()
    rows = []
    for t, v in hits.items():
        r = {"証券コード": v["証券コード"], "Ticker": t, "終値": v["終値"],
             "合致パターン数": len(v["パターン"]), "合致パターン": " + ".join(sorted(v["パターン"]))}
        if "銘柄名" in v: r["銘柄名"] = v["銘柄名"]
        if "売上5y CAGR" in v: r["売上5y CAGR"] = v["売上5y CAGR"]
        if "売上予想" in v: r["売上予想"] = v["売上予想"]
        if "PER" in v: r["PER"] = v["PER"]
        if "予想PER" in v: r["予想PER"] = v["予想PER"]
        rows.append(r)
    df_out = pd.DataFrame(rows).sort_values(["合致パターン数", "Ticker"], ascending=[False, True]).reset_index(drop=True)

    # 「銘柄名」を Ticker の直後に来るよう列順を入れ替える
    if "銘柄名" in df_out.columns:
        cols = df_out.columns.tolist()
        cols.remove("銘柄名")
        insert_at = cols.index("Ticker") + 1
        cols = cols[:insert_at] + ["銘柄名"] + cols[insert_at:]
        df_out = df_out[cols]
    return df_out


# ==========================================
# 6. Googleスプレッドシート書き込み
# ==========================================
def get_gspread_client() -> gspread.Client:
    """環境変数 GCP_SA_KEY からサービスアカウント認証情報を読み込む"""
    sa_key_str = os.environ.get("GCP_SA_KEY")
    if not sa_key_str:
        raise RuntimeError("環境変数 GCP_SA_KEY が設定されていません")
    sa_info = json.loads(sa_key_str)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    return gspread.authorize(creds)


def write_df_to_sheet(gc: gspread.Client, spreadsheet_id: str,
                      sheet_name: str, df: pd.DataFrame):
    """指定シートをクリアしてDataFrameを書き込む（シートが無ければ作成）"""
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(sheet_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_name, rows=2000, cols=30)

    if df.empty:
        ws.update([["該当銘柄なし"]])
        return

    # NaN/Infは df.astype(str) だけでは float のまま残ることがあり、
    # Google Sheets APIへの送信時に「Out of range float values are not
    # JSON compliant」で失敗するため、先に明示的な文字列へ置き換える。
    safe_df = df.replace([np.inf, -np.inf], np.nan)
    safe_df = safe_df.astype(object).where(pd.notna(safe_df), "")
    safe_df = safe_df.astype(str)

    values = [safe_df.columns.tolist()] + safe_df.values.tolist()
    ws.update(values)


# ==========================================
# 6.5 抽出履歴の管理（前回抽出日・年間回数の集計用）
# ==========================================
HISTORY_SHEET_NAME = "抽出履歴"
HISTORY_RETENTION_DAYS = 365  # この日数より古い履歴は削除する

def load_history(gc: gspread.Client, spreadsheet_id: str) -> pd.DataFrame:
    """
    履歴シート（日付, Ticker, パターン）を読み込む。
    シートが無ければ空のDataFrameを返す。
    """
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(HISTORY_SHEET_NAME)
    except gspread.WorksheetNotFound:
        return pd.DataFrame(columns=["日付", "Ticker", "パターン"])

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame(columns=["日付", "Ticker", "パターン"])

    df = pd.DataFrame(values[1:], columns=values[0])

    # シート構造が想定と異なる場合（過去の異常書き込みなど）は安全に空扱いにする
    required_cols = {"日付", "Ticker", "パターン"}
    if not required_cols.issubset(set(df.columns)):
        print(f"⚠️ 履歴シートの列構造が想定外です: {list(df.columns)} → 履歴をリセットします", flush=True)
        return pd.DataFrame(columns=["日付", "Ticker", "パターン"])

    df["日付"] = pd.to_datetime(df["日付"], errors="coerce")
    df = df.dropna(subset=["日付"])
    return df


def append_today_to_history(gc: gspread.Client, spreadsheet_id: str,
                            today_str: str,
                            dfa: pd.DataFrame, dfh: pd.DataFrame,
                            dfb1: pd.DataFrame, dfb2: pd.DataFrame, dfc: pd.DataFrame,
                            dfd: pd.DataFrame = None,
                            dfe: pd.DataFrame = None,
                            dff: pd.DataFrame = None,
                            dfg: pd.DataFrame = None) -> pd.DataFrame:
    """
    今回ヒットした銘柄を履歴に追記し、1年より古い行を削除した上で
    スプレッドシートに書き戻す。戻り値は更新後の履歴DataFrame。
    """
    history = load_history(gc, spreadsheet_id)

    new_rows = []
    def collect(df, pattern_label):
        if df is None or df.empty:
            return
        for t in df["Ticker"]:
            new_rows.append({"日付": today_str, "Ticker": t, "パターン": pattern_label})

    collect(dfa,  "週A")
    collect(dfh,  "週H")
    collect(dfb1, "日B1")
    collect(dfb2, "日B2")
    collect(dfc,  "ボリバンC")
    collect(dfd,  "初押しD")
    collect(dfe,  "出来高E")
    collect(dff,  "GC底打ちF")
    collect(dfg,  "ポケピG")

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        new_df["日付"] = pd.to_datetime(new_df["日付"])
        history = pd.concat([history, new_df], ignore_index=True)

    # ── 型を明示的に統一（空DataFrameとの結合でobject型に戻ることがあるため）──
    if not history.empty:
        history["日付"] = pd.to_datetime(history["日付"], errors="coerce")
        history = history.dropna(subset=["日付"])

    # ── 1年より古い履歴を削除 ──
    if not history.empty:
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=HISTORY_RETENTION_DAYS)
        history = history[history["日付"] >= cutoff]
        # 同日・同銘柄・同パターンの重複（再実行などで生じる）は1件に統一
        history = history.drop_duplicates(subset=["日付", "Ticker", "パターン"])

    # ── 書き戻し ──
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(HISTORY_SHEET_NAME)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=HISTORY_SHEET_NAME, rows=20000, cols=5)

    if history.empty:
        ws.update([["日付", "Ticker", "パターン"]])
    else:
        out = history.copy()
        # .dt アクセサに依存せず、要素ごとに安全に文字列化する
        # （型がdatetime64でなくても、各要素がTimestamp/strのどちらでも対応できる）
        def _to_date_str(v):
            try:
                ts = pd.Timestamp(v)
                if pd.isna(ts):
                    return None
                return ts.strftime("%Y-%m-%d")
            except Exception:
                return None
        out["日付"] = out["日付"].apply(_to_date_str)
        out = out.dropna(subset=["日付"])
        out = out.sort_values("日付")
        values = [out.columns.tolist()] + out.astype(str).values.tolist()
        ws.update(values)

    return history


def compute_history_stats(history: pd.DataFrame, today_str: str) -> pd.DataFrame:
    """
    Tickerごとに「前回抽出日」「過去1年の抽出回数」を集計する。
    今日のヒットは集計対象から除く（「前回」なので今回より前の記録のみ見る）。
    戻り値: 列 [Ticker, 前回抽出日, 年間抽出回数]
    """
    if history.empty:
        return pd.DataFrame(columns=["Ticker", "前回抽出日", "年間抽出回数"])

    # 念のため日付型を保証（呼び出し元で型が崩れていても安全に動くように）
    history = history.copy()
    history["日付"] = pd.to_datetime(history["日付"], errors="coerce")
    history = history.dropna(subset=["日付"])
    if history.empty:
        return pd.DataFrame(columns=["Ticker", "前回抽出日", "年間抽出回数"])

    today_ts = pd.to_datetime(today_str)
    past = history[history["日付"] < today_ts]  # 今日より前の記録のみ

    if past.empty:
        return pd.DataFrame(columns=["Ticker", "前回抽出日", "年間抽出回数"])

    # 年間抽出回数: パターンを問わず「その銘柄が何らかの形でヒットした日数」を数える
    # （同日に複数パターンでヒットしても1日としてカウント）
    daily_hits = past.drop_duplicates(subset=["日付", "Ticker"])
    counts = daily_hits.groupby("Ticker").size().rename("年間抽出回数")
    last_dates = past.groupby("Ticker")["日付"].max().rename("前回抽出日")

    stats = pd.concat([last_dates, counts], axis=1).reset_index()

    def _to_date_str(v):
        try:
            ts = pd.Timestamp(v)
            if pd.isna(ts):
                return "-"
            return ts.strftime("%Y-%m-%d")
        except Exception:
            return "-"
    stats["前回抽出日"] = stats["前回抽出日"].apply(_to_date_str)
    return stats


def attach_history_stats(df: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    """結果DataFrameに「前回抽出日」「年間抽出回数」列をTicker経由で結合する"""
    if df.empty:
        df["前回抽出日"] = []
        df["年間抽出回数"] = []
        return df
    if stats.empty:
        df["前回抽出日"] = "初回"
        df["年間抽出回数"] = 0
        return df

    merged = df.merge(stats, on="Ticker", how="left")
    merged["前回抽出日"]   = merged["前回抽出日"].fillna("初回")
    merged["年間抽出回数"] = merged["年間抽出回数"].fillna(0).astype(int)
    return merged


# ==========================================
# 7. メイン処理
# ==========================================
def scheduled_session_date(now: datetime.datetime, schedule_expr: str) -> datetime.date:
    """
    GitHub ActionsのcronはUTC。実行が遅れてJSTの日付をまたいでも、
    cron本来の対象取引日を返す。
    """
    if not schedule_expr:
        return now.date()
    try:
        fields = schedule_expr.strip().split()
        if len(fields) != 5:
            return now.date()
        minute = int(fields[0])
        hour = int(fields[1])
        dow = fields[4]
        if dow == "1-5":
            allowed_py_weekdays = {0, 1, 2, 3, 4}
        else:
            allowed_py_weekdays = set()
            for part in dow.split(","):
                cron_d = int(part)
                allowed_py_weekdays.add(6 if cron_d in (0, 7) else cron_d - 1)

        now_utc = now.astimezone(datetime.timezone.utc)
        for days_back in range(0, 8):
            d = now_utc.date() - datetime.timedelta(days=days_back)
            if d.weekday() not in allowed_py_weekdays:
                continue
            candidate_utc = datetime.datetime(
                d.year, d.month, d.day, hour, minute,
                tzinfo=datetime.timezone.utc,
            )
            if candidate_utc <= now_utc:
                return candidate_utc.astimezone(JST).date()
    except Exception as e:
        print(f"⚠️ cron対象日解析に失敗: {schedule_expr} / {e}", flush=True)
    return now.date()


def already_scanned_for_session(gc, spreadsheet_id: str, session_date_str: str) -> bool:
    """対象取引日がすでに完了済みかを実行ログから判定する。"""
    try:
        sh = gc.open_by_key(spreadsheet_id)
        ws = sh.worksheet("実行ログ")
        values = ws.get_all_values()
        if not values or len(values) < 2:
            return False
        header = values[0]
        last_row = values[-1]

        if "対象取引日" in header:
            idx = header.index("対象取引日")
            if idx < len(last_row):
                return str(last_row[idx]).strip() == session_date_str

        if "最終実行日時" in header:
            idx = header.index("最終実行日時")
            if idx < len(last_row):
                return str(last_row[idx]).startswith(session_date_str)
        return False
    except Exception:
        return False


def main():
    now = now_jst()
    weekday_jp = ["月","火","水","木","金","土","日"][now.weekday()]
    trigger = os.environ.get("GITHUB_EVENT_NAME", "不明（ローカル実行など）")
    schedule_expr = os.environ.get("GITHUB_EVENT_SCHEDULE", "")
    session_date = scheduled_session_date(now, schedule_expr) if trigger == "schedule" else now.date()
    today_str = session_date.strftime("%Y-%m-%d")

    try:
        if os.path.exists(".scan_skipped"):
            os.remove(".scan_skipped")
    except Exception:
        pass

    print(f"=== スキャン開始: {now.strftime('%Y-%m-%d %H:%M:%S')} ({weekday_jp}曜日, JST) "
          f"| トリガー: {trigger} | 対象取引日: {today_str} | cron: {schedule_expr or '-'} ===", flush=True)

    # scheduleだけ対象取引日で重複排除。workflow_dispatchは検証のため常に実行可能。
    spreadsheet_id_early = os.environ.get("SPREADSHEET_ID")
    if trigger == "schedule" and spreadsheet_id_early:
        try:
            gc_early = get_gspread_client()
            if already_scanned_for_session(gc_early, spreadsheet_id_early, today_str):
                with open(".scan_skipped", "w", encoding="utf-8") as fp:
                    fp.write(today_str)
                print(f"対象取引日（{today_str}）はすでにスキャン済みのため、この予約実行はスキップします。", flush=True)
                print("=== 重複スキップ終了 ===", flush=True)
                return
        except Exception as e:
            print(f"対象取引日スキャン済みチェックに失敗（処理は続行）: {e}", flush=True)

    tickers_all, name_map, jpx_diag = get_jpx_tickers()
    print(f"JPX取得診断: {jpx_diag}", flush=True)
    print(f"対象銘柄数: {len(tickers_all)}", flush=True)

    batches = [tickers_all[i:i+BATCH_SIZE] for i in range(0, len(tickers_all), BATCH_SIZE)]
    total = len(batches)
    print(f"バッチ数: {total}", flush=True)

    all_a, all_h, all_b1, all_b2, all_c, all_d, all_e, all_f, all_g = [], [], [], [], [], [], [], [], []
    all_m_candidates = []
    ctr = {}
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(process_batch, b, ctr, MIN_AVG_TURNOVER, MIN_MARKET_CAP): i
                   for i, b in enumerate(batches)}
        completed = 0
        for future in as_completed(futures):
            try:
                ra, rh, rb1, rb2, rc, rd, re_, rf, rg, rm_candidates = future.result()
                all_a.extend(ra); all_h.extend(rh); all_b1.extend(rb1); all_b2.extend(rb2)
                all_c.extend(rc); all_d.extend(rd); all_e.extend(re_)
                all_f.extend(rf); all_g.extend(rg)
                all_m_candidates.extend(rm_candidates)
            except Exception as e:
                print(f"バッチエラー: {e}", flush=True)
            completed += 1
            if completed % 10 == 0 or completed == total:
                elapsed = time.time() - t0
                print(f"  進捗 {completed}/{total} バッチ完了 ({elapsed/60:.1f}分経過)", flush=True)
            time.sleep(SLEEP_SEC)

    elapsed = time.time() - t0
    print(f"=== スキャン完了 ({elapsed/60:.1f}分) ===", flush=True)
    print(f"週足A: {len(all_a)}件 / 週足転換H: {len(all_h)}件 / 日足B1: {len(all_b1)}件 / 日足B2: {len(all_b2)}件 / "
          f"ボリバンC: {len(all_c)}件 / 初押しD: {len(all_d)}件 / 出来高E: {len(all_e)}件 / "
          f"GC底打ちF: {len(all_f)}件 / ポケピG: {len(all_g)}件", flush=True)

    # ── 診断カウンター（各パターンがどの条件で何銘柄落ちたか）──
    if ctr:
        print("\n" + "─" * 60, flush=True)
        print("📋 診断レポート（条件別の脱落・合格銘柄数）", flush=True)
        print("─" * 60, flush=True)
        # パターンごとにプレフィックスでグループ分けして表示
        groups = {
            "A_": "【週足パターンA】",
            "H_": "【週足転換H 画像マッチ】",
            "B1_": "【日足B1 押し目待ち】",
            "B2_": "【日足B2 反発エントリー】",
            "C_": "【ボリバンC ブレイク】",
            "D_": "【初押しD 下ひげ陽線】",
            "E_": "【出来高E 急増ブレイク】",
            "F_": "【GC底打ちF 21×200GC】",
            "G_": "【ポケットピボットG】",
        }
        for prefix, title in groups.items():
            keys = sorted(k for k in ctr if k.startswith(prefix))
            if not keys:
                continue
            print(f"\n{title}", flush=True)
            for k in keys:
                mark = "✅" if "合格" in k else "  ✗"
                print(f"  {mark} {k:32s}: {ctr[k]:5d} 銘柄", flush=True)
        # どのグループにも属さないカウンター（あれば）
        others = sorted(k for k in ctr
                        if not any(k.startswith(p) for p in groups))
        if others:
            print("\n【その他】", flush=True)
            for k in others:
                print(f"     {k:32s}: {ctr[k]:5d} 銘柄", flush=True)
        print("─" * 60, flush=True)
        print("💡 「①週足PO不成立」が多い → 週足が上昇トレンドの銘柄自体が少ない相場", flush=True)
        print("💡 「⑤SMA25タッチなし」が多い → 押し目がまだ浅い（SMA25まで下げていない）", flush=True)
        print("💡 「⑥下ひげ不足」が多い → タッチはしているが下ひげ陽線の形になっていない", flush=True)
        print("─" * 60 + "\n", flush=True)

    dfa  = format_a(all_a)
    dfh  = format_h(all_h)
    dfb1 = format_b1(all_b1)
    dfb2 = format_b2(all_b2)
    dfc  = format_c(all_c)
    dfd  = format_d(all_d)
    dfe  = format_e(all_e)
    dff  = format_f(all_f)
    dfg  = format_g(all_g)

    print("ファンダメンタルズ取得中...", flush=True)
    dfa  = enrich_with_fundamentals(dfa,  name_map)
    dfh  = enrich_with_fundamentals(dfh,  name_map)
    dfh_core = dfh[dfh["本命判定"].astype(str) == "本命"].copy() if (not dfh.empty and "本命判定" in dfh.columns) else pd.DataFrame()
    dfb1 = enrich_with_fundamentals(dfb1, name_map)
    dfb2 = enrich_with_fundamentals(dfb2, name_map)
    dfc  = enrich_with_fundamentals(dfc,  name_map)
    dfd  = enrich_with_fundamentals(dfd,  name_map)
    dfe  = enrich_with_fundamentals(dfe,  name_map)
    dff  = enrich_with_fundamentals(dff,  name_map)
    dfg  = enrich_with_fundamentals(dfg,  name_map)

    dfm = build_multi_hit(dfa, dfh_core, dfb1, dfb2, dfc, dfd, dfe, dff, dfg)

    # ── 決算モメンタム分析 ───────────────────────────────
    # 軽量プレフィルター上位のみを yfinance の四半期決算データへ回す。
    # 日足データは既に cache_d にあるため、ここでは再取得しない。
    print(f"決算モメンタム候補の事前抽出: {len(all_m_candidates)}件", flush=True)
    momentum_candidates = []
    seen_m = set()
    for c in sorted(all_m_candidates, key=lambda x: (x.get("prefilter_score", 0), x.get("avg_to", 0)), reverse=True):
        t = c["ticker"]
        if t in seen_m:
            continue
        seen_m.add(t)
        # 上位候補だけに限定して分析対象にする
        if len(momentum_candidates) >= 180:
            break
        # process_batchで取得済みの日足データ(dfd)を再利用する（yf.downloadの再取得を回避）
        dfx = c.get("dfd")
        if dfx is not None and not dfx.empty and "Close" in dfx.columns:
            momentum_candidates.append((t, c["code"], name_map.get(t, ""), dfx))

    print(f"決算モメンタム分析開始: {len(momentum_candidates)}件 / version={MOMENTUM_VERSION}", flush=True)
    df_momentum = build_momentum_results(momentum_candidates, max_workers=4)
    if not df_momentum.empty:
        print(f"決算モメンタム結果: {len(df_momentum)}件", flush=True)
    else:
        print("決算モメンタム結果: 0件", flush=True)

    # ── スプレッドシートへ書き込み ──
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("環境変数 SPREADSHEET_ID が設定されていません")

    gc = get_gspread_client()

    # ── 履歴に今回のヒットを追記し、1年より古い記録を削除 ──
    print("抽出履歴を更新中...", flush=True)
    history = append_today_to_history(gc, spreadsheet_id, today_str, dfa, dfh_core, dfb1, dfb2, dfc, dfd, dfe, dff, dfg)
    stats = compute_history_stats(history, today_str)

    # ── 各結果に「前回抽出日」「年間抽出回数」を付与 ──
    dfa  = attach_history_stats(dfa,  stats)
    dfh  = attach_history_stats(dfh,  stats)
    dfh_core = attach_history_stats(dfh_core, stats)
    dfb1 = attach_history_stats(dfb1, stats)
    dfb2 = attach_history_stats(dfb2, stats)
    dfc  = attach_history_stats(dfc,  stats)
    dfd  = attach_history_stats(dfd,  stats)
    dfe  = attach_history_stats(dfe,  stats)
    dff  = attach_history_stats(dff,  stats)
    dfg  = attach_history_stats(dfg,  stats)
    if not dfm.empty:
        dfm = attach_history_stats(dfm, stats)

    print("Googleスプレッドシートへ書き込み中...", flush=True)
    write_df_to_sheet(gc, spreadsheet_id, "複数パターン合致", dfm)
    write_df_to_sheet(gc, spreadsheet_id, "決算モメンタム", df_momentum)
    write_df_to_sheet(gc, spreadsheet_id, "週足パターンA",     dfa)
    write_df_to_sheet(gc, spreadsheet_id, "週足転換H",          dfh)
    write_df_to_sheet(gc, spreadsheet_id, "週足転換H本命",      dfh_core)
    write_df_to_sheet(gc, spreadsheet_id, "日足B1押し目待ち",   dfb1)
    write_df_to_sheet(gc, spreadsheet_id, "日足B2反発エントリー", dfb2)
    write_df_to_sheet(gc, spreadsheet_id, "ボリバンCブレイク",   dfc)
    write_df_to_sheet(gc, spreadsheet_id, "初押しD下ひげ陽線",   dfd)
    write_df_to_sheet(gc, spreadsheet_id, "出来高E急増ブレイク",  dfe)
    write_df_to_sheet(gc, spreadsheet_id, "GC底打ちF21x200",     dff)
    write_df_to_sheet(gc, spreadsheet_id, "ポケットピボットG",    dfg)

    # ── メタ情報シート（最終実行日時など）も書いておく ──
    end_now = now_jst()
    weekday_jp_end = ["月","火","水","木","金","土","日"][end_now.weekday()]
    meta_df = pd.DataFrame([{
        "最終実行日時": f"{end_now.strftime('%Y-%m-%d %H:%M:%S')} ({weekday_jp_end}曜日, JST)",
        "対象取引日": today_str,
        "トリガー種別": os.environ.get("GITHUB_EVENT_NAME", "不明（ローカル実行など）"),
        "cron": schedule_expr or "-",
        "対象銘柄数": len(tickers_all),
        "週足A件数": len(dfa),
        "週足転換H件数": len(dfh),
        "週足転換H本命件数": len(dfh_core),
        "日足B1件数": len(dfb1),
        "日足B2件数": len(dfb2),
        "ボリバンC件数": len(dfc),
        "初押しD件数": len(dfd),
        "出来高E件数": len(dfe),
        "GC底打ちF件数": len(dff),
        "ポケピG件数": len(dfg),
        "複数合致件数": len(dfm),
        "履歴保存件数": len(history),
        "JPX取得診断": jpx_diag,
    }])
    write_df_to_sheet(gc, spreadsheet_id, "実行ログ", meta_df)

    print("=== 完了 ===", flush=True)


if __name__ == "__main__":
    main()
