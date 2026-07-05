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
from google.oauth2.service_account import Credentials

warnings.filterwarnings("ignore")

# ── バージョン識別子（ファイルが正しく反映されているか確認するため）──
SCAN_PY_VERSION = "2026-07-05-v3-pattern-D"
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
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    df = None
    diag_steps = []

    try:
        df = pd.read_html(url, header=0)[0]
        diag_steps.append("read_html: 成功")
    except Exception as e:
        diag_steps.append(f"read_html: 失敗 ({type(e).__name__}: {e})")

    if df is None:
        try:
            import requests, urllib3
            from io import BytesIO
            urllib3.disable_warnings()
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, verify=False, timeout=30, headers=headers)
            r.raise_for_status()
            df = pd.read_excel(BytesIO(r.content), header=0)
            diag_steps.append("requests+read_excel: 成功")
        except Exception as e:
            diag_steps.append(f"requests+read_excel: 失敗 ({type(e).__name__}: {e})")

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
    df["WMA20"]        = c.rolling(20).mean()   # 週足20SMA（パターンD用）
    df["MA40"]         = c.rolling(40).mean()
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
    df["MA40_s4b"]     = df["MA40"].pct_change(4) * 100   # 週足40SMAの傾き（別名）
    return df

def calc_daily(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"]
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
    return df

def check_a(df: pd.DataFrame, ctr: dict) -> dict | None:
    def drop(k): ctr[k] = ctr.get(k, 0) + 1; return None
    need = ["MA5","MA40","BAND5_U","BAND5_L","BAND40_U","BAND40_L","RSI","MA40_s4","MA40_s8_prev","Close","Low"]
    rec = df.dropna(subset=need).iloc[-60:]
    if len(rec) < 20: return drop("A_データ不足")

    cur = rec.iloc[-1]
    close, low, ma5, ma40 = float(cur["Close"]), float(cur["Low"]), float(cur["MA5"]), float(cur["MA40"])
    b5u, b40u, b40l = float(cur["BAND5_U"]), float(cur["BAND40_U"]), float(cur["BAND40_L"])
    s_now, s_old = float(cur["MA40_s4"]), float(cur["MA40_s8_prev"])

    if not (rec["MA40_s4"] < -1.5).any(): return drop("A_①下落実績なし")
    if s_now < -1.5: return drop("A_②急落継続")
    if not np.isnan(s_old) and s_now < s_old + 0.3: return drop("A_②改善なし")

    rec40 = df.dropna(subset=["MA5","MA40"]).iloc[-40:]
    gc_flag = (rec40["MA5"] > rec40["MA40"]).astype(int)
    if not (gc_flag.rolling(2).min() == 1).any(): return drop("A_③GC実績なし")
    if ma5 < ma40 * 0.97: return drop("A_③MA5大幅下抜け")
    if not (b40l * 0.97 <= low <= b40u * 1.03): return drop("A_④安値バンド外")
    if close < ma40 * 0.97: return drop("A_④終値MA40割れ")
    if b5u <= b40l: return drop("A_④5SMAバンド潜り込み")

    rsi = float(cur["RSI"])
    if not (35 <= rsi <= 65): return drop("A_⑤RSI範囲外")

    ctr["A_合格"] = ctr.get("A_合格", 0) + 1
    return {
        "close": round(close, 0), "low": round(low, 0), "ma5": round(ma5, 0), "ma40": round(ma40, 0),
        "band40_u": round(b40u, 0), "band40_l": round(b40l, 0), "dist_close": round((close - ma40) / ma40 * 100, 1),
        "dist_low": round((low - ma40) / ma40 * 100, 1), "slope_now": round(s_now, 2),
        "slope_prev": round(s_old, 2) if not np.isnan(s_old) else 0.0, "rsi": round(rsi, 1),
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
    ra, rb1, rb2, rc, rd = [], [], [], [], []
    cache_w, _ = download_batch(batch, "3y", "1wk", "週足")
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
        except Exception:
            pass

    return ra, rb1, rb2, rc, rd


def format_a(rows):
    if not rows: return pd.DataFrame()
    d = pd.DataFrame(rows)[["code","ticker","close","low","ma5","ma40","band40_u","band40_l","dist_close","dist_low","slope_now","slope_prev","rsi","avg_to","mktcap"]]
    d.columns = ["証券コード","Ticker","終値","安値","5週MA","40週MA","40BANDu","40BANDl","終値とMA40距離%","安値とMA40距離%","40MA傾き%(4週)","40MA傾き%(前期)","RSI","売買代金(億円)","時価総額(億円)"]
    return d.sort_values("安値とMA40距離%", key=abs).reset_index(drop=True)

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
    # SMA25への近さ（絶対値が小さい順）で並べる
    return d.sort_values("安値のSMA25乖離%", key=abs).reset_index(drop=True)

def build_multi_hit(dfa, dfb1, dfb2, dfc, dfd):
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
    register(dfb1, "日B1")
    register(dfb2, "日B2")
    register(dfc,  "ボリバンC")
    register(dfd,  "初押しD")
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

    values = [df.columns.tolist()] + df.astype(str).values.tolist()
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
                            dfa: pd.DataFrame, dfb1: pd.DataFrame,
                            dfb2: pd.DataFrame, dfc: pd.DataFrame,
                            dfd: pd.DataFrame = None) -> pd.DataFrame:
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
    collect(dfb1, "日B1")
    collect(dfb2, "日B2")
    collect(dfc,  "ボリバンC")
    collect(dfd,  "初押しD")

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
def main():
    now = now_jst()
    weekday_jp = ["月","火","水","木","金","土","日"][now.weekday()]
    trigger = os.environ.get("GITHUB_EVENT_NAME", "不明（ローカル実行など）")
    print(f"=== スキャン開始: {now.strftime('%Y-%m-%d %H:%M:%S')} ({weekday_jp}曜日, JST) "
          f"| トリガー: {trigger} ===", flush=True)

    tickers_all, name_map, jpx_diag = get_jpx_tickers()
    print(f"JPX取得診断: {jpx_diag}", flush=True)
    print(f"対象銘柄数: {len(tickers_all)}", flush=True)

    batches = [tickers_all[i:i+BATCH_SIZE] for i in range(0, len(tickers_all), BATCH_SIZE)]
    total = len(batches)
    print(f"バッチ数: {total}", flush=True)

    all_a, all_b1, all_b2, all_c, all_d = [], [], [], [], []
    ctr = {}
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(process_batch, b, ctr, MIN_AVG_TURNOVER, MIN_MARKET_CAP): i
                   for i, b in enumerate(batches)}
        completed = 0
        for future in as_completed(futures):
            try:
                ra, rb1, rb2, rc, rd = future.result()
                all_a.extend(ra); all_b1.extend(rb1); all_b2.extend(rb2)
                all_c.extend(rc); all_d.extend(rd)
            except Exception as e:
                print(f"バッチエラー: {e}", flush=True)
            completed += 1
            if completed % 10 == 0 or completed == total:
                elapsed = time.time() - t0
                print(f"  進捗 {completed}/{total} バッチ完了 ({elapsed/60:.1f}分経過)", flush=True)
            time.sleep(SLEEP_SEC)

    elapsed = time.time() - t0
    print(f"=== スキャン完了 ({elapsed/60:.1f}分) ===", flush=True)
    print(f"週足A: {len(all_a)}件 / 日足B1: {len(all_b1)}件 / 日足B2: {len(all_b2)}件 / "
          f"ボリバンC: {len(all_c)}件 / 初押しD: {len(all_d)}件", flush=True)

    dfa  = format_a(all_a)
    dfb1 = format_b1(all_b1)
    dfb2 = format_b2(all_b2)
    dfc  = format_c(all_c)
    dfd  = format_d(all_d)

    print("ファンダメンタルズ取得中...", flush=True)
    dfa  = enrich_with_fundamentals(dfa,  name_map)
    dfb1 = enrich_with_fundamentals(dfb1, name_map)
    dfb2 = enrich_with_fundamentals(dfb2, name_map)
    dfc  = enrich_with_fundamentals(dfc,  name_map)
    dfd  = enrich_with_fundamentals(dfd,  name_map)

    dfm = build_multi_hit(dfa, dfb1, dfb2, dfc, dfd)

    # ── スプレッドシートへ書き込み ──
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("環境変数 SPREADSHEET_ID が設定されていません")

    gc = get_gspread_client()
    today_str = now_jst().strftime("%Y-%m-%d")

    # ── 履歴に今回のヒットを追記し、1年より古い記録を削除 ──
    print("抽出履歴を更新中...", flush=True)
    history = append_today_to_history(gc, spreadsheet_id, today_str, dfa, dfb1, dfb2, dfc, dfd)
    stats = compute_history_stats(history, today_str)

    # ── 各結果に「前回抽出日」「年間抽出回数」を付与 ──
    dfa  = attach_history_stats(dfa,  stats)
    dfb1 = attach_history_stats(dfb1, stats)
    dfb2 = attach_history_stats(dfb2, stats)
    dfc  = attach_history_stats(dfc,  stats)
    dfd  = attach_history_stats(dfd,  stats)
    if not dfm.empty:
        dfm = attach_history_stats(dfm, stats)

    print("Googleスプレッドシートへ書き込み中...", flush=True)
    write_df_to_sheet(gc, spreadsheet_id, "複数パターン合致", dfm)
    write_df_to_sheet(gc, spreadsheet_id, "週足パターンA",     dfa)
    write_df_to_sheet(gc, spreadsheet_id, "日足B1押し目待ち",   dfb1)
    write_df_to_sheet(gc, spreadsheet_id, "日足B2反発エントリー", dfb2)
    write_df_to_sheet(gc, spreadsheet_id, "ボリバンCブレイク",   dfc)
    write_df_to_sheet(gc, spreadsheet_id, "初押しD下ひげ陽線",   dfd)

    # ── メタ情報シート（最終実行日時など）も書いておく ──
    end_now = now_jst()
    weekday_jp_end = ["月","火","水","木","金","土","日"][end_now.weekday()]
    meta_df = pd.DataFrame([{
        "最終実行日時": f"{today_str} {end_now.strftime('%H:%M:%S')} ({weekday_jp_end}曜日, JST)",
        "トリガー種別": os.environ.get("GITHUB_EVENT_NAME", "不明（ローカル実行など）"),
        "対象銘柄数": len(tickers_all),
        "週足A件数": len(dfa),
        "日足B1件数": len(dfb1),
        "日足B2件数": len(dfb2),
        "ボリバンC件数": len(dfc),
        "初押しD件数": len(dfd),
        "複数合致件数": len(dfm),
        "履歴保存件数": len(history),
        "JPX取得診断": jpx_diag,
    }])
    write_df_to_sheet(gc, spreadsheet_id, "実行ログ", meta_df)

    print("=== 完了 ===", flush=True)


if __name__ == "__main__":
    main()
