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

# ==========================================
# 1. JPX銘柄リスト取得
# ==========================================
def get_jpx_tickers() -> tuple[list, str]:
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
        return fallback, f"⚠️ JPX一覧の取得に失敗: {diag}"

    def norm(c):
        try: return str(int(float(c)))
        except: return str(c).strip()

    mc = next((c for c in df.columns if "市場" in str(c) or "区分" in str(c)), None)
    cc = next((c for c in df.columns if "コード" in str(c) or "証券" in str(c)), None)
    if mc and cc:
        df_f = df[df[mc].str.contains("プライム|スタンダード", na=False)]
        codes = df_f[cc].dropna().astype(str).str.strip().map(norm)
    else:
        cc = next((c for c in df.columns if "コード" in str(c)), df.columns[0])
        codes = df[cc].dropna().astype(str).str.strip().map(norm)

    tickers = [f"{c}.T" for c in codes if 4 <= len(c) <= 5 and c.isalnum()]

    if len(tickers) < 100:
        diag = " | ".join(diag_steps) + f" | 抽出後{len(tickers)}件のみ"
        return tickers, f"⚠️ 取得銘柄数が少なすぎます: {diag}"

    return tickers, " | ".join(diag_steps) + f" | 取得成功: {len(tickers)}銘柄"


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
    return df

def calc_daily(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"]
    df["MA21"]     = c.rolling(21).mean()
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


# ==========================================
# 4. ファンダメンタル情報
# ==========================================
def _fetch_fundamentals_one(ticker: str) -> dict:
    cagr_val, est_val, err = "-", "-", ""
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
            err = ""
            break
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(1.0)
    return {"ticker": ticker, "cagr": cagr_val, "est": est_val, "err": err}

def enrich_with_fundamentals(df: pd.DataFrame, max_workers: int = 4) -> pd.DataFrame:
    if df.empty:
        df["売上5y CAGR"] = []
        df["売上予想"] = []
        return df
    tickers = df["Ticker"].tolist()
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_fundamentals_one, t): t for t in tickers}
        for fut in as_completed(futures):
            r = fut.result()
            results[r["ticker"]] = r
    df["売上5y CAGR"] = df["Ticker"].map(lambda t: results.get(t, {}).get("cagr", "-"))
    df["売上予想"]   = df["Ticker"].map(lambda t: results.get(t, {}).get("est", "-"))
    return df


# ==========================================
# 5. バッチ処理 & スキャン
# ==========================================
def process_batch(batch: list, ctr: dict, min_turnover, min_mktcap) -> tuple:
    ra, rb1, rb2 = [], [], []
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

            dfw = cache_w.get(ticker)
            if dfw is not None:
                if "Low" not in dfw.columns:
                    dfw = dfw.copy(); dfw["Low"] = dfw["Close"]
                dfw = dfw.dropna(subset=["Close"])
                if len(dfw) >= 50:
                    dfw = calc_weekly(dfw)
                    res = check_a(dfw, ctr)
                    if res:
                        res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                        ra.append(res)

            dfd2 = dfd.dropna(subset=["Close"])
            if len(dfd2) >= 60:
                dfd2 = calc_daily(dfd2)
                res = check_b1(dfd2, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    rb1.append(res)
                res = check_b2(dfd2, ctr)
                if res:
                    res.update({"ticker":ticker,"code":code,"avg_to":avg_to_oku,"mktcap":mktcap_oku})
                    rb2.append(res)
        except Exception:
            pass

    return ra, rb1, rb2


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

def build_multi_hit(dfa, dfb1, dfb2):
    hits = {}
    def register(df, pattern):
        if df.empty: return
        for _, row in df.iterrows():
            t = row["Ticker"]
            if t not in hits:
                hits[t] = {"証券コード": row["証券コード"], "Ticker": t, "終値": row["終値"], "パターン": set()}
                if "売上5y CAGR" in row: hits[t]["売上5y CAGR"] = row["売上5y CAGR"]
                if "売上予想" in row: hits[t]["売上予想"] = row["売上予想"]
            hits[t]["パターン"].add(pattern)
            hits[t]["終値"] = row["終値"]
    register(dfa,  "週A")
    register(dfb1, "日B1")
    register(dfb2, "日B2")
    if not hits: return pd.DataFrame()
    rows = []
    for t, v in hits.items():
        r = {"証券コード": v["証券コード"], "Ticker": t, "終値": v["終値"],
             "合致パターン数": len(v["パターン"]), "合致パターン": " + ".join(sorted(v["パターン"]))}
        if "売上5y CAGR" in v: r["売上5y CAGR"] = v["売上5y CAGR"]
        if "売上予想" in v: r["売上予想"] = v["売上予想"]
        rows.append(r)
    return pd.DataFrame(rows).sort_values(["合致パターン数", "Ticker"], ascending=[False, True]).reset_index(drop=True)


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
# 7. メイン処理
# ==========================================
def main():
    print(f"=== スキャン開始: {datetime.datetime.now()} ===")

    tickers_all, jpx_diag = get_jpx_tickers()
    print(f"JPX取得診断: {jpx_diag}")
    print(f"対象銘柄数: {len(tickers_all)}")

    batches = [tickers_all[i:i+BATCH_SIZE] for i in range(0, len(tickers_all), BATCH_SIZE)]
    total = len(batches)
    print(f"バッチ数: {total}")

    all_a, all_b1, all_b2 = [], [], []
    ctr = {}
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(process_batch, b, ctr, MIN_AVG_TURNOVER, MIN_MARKET_CAP): i
                   for i, b in enumerate(batches)}
        completed = 0
        for future in as_completed(futures):
            try:
                ra, rb1, rb2 = future.result()
                all_a.extend(ra); all_b1.extend(rb1); all_b2.extend(rb2)
            except Exception as e:
                print(f"バッチエラー: {e}")
            completed += 1
            if completed % 10 == 0 or completed == total:
                elapsed = time.time() - t0
                print(f"  進捗 {completed}/{total} バッチ完了 ({elapsed/60:.1f}分経過)")
            time.sleep(SLEEP_SEC)

    elapsed = time.time() - t0
    print(f"=== スキャン完了 ({elapsed/60:.1f}分) ===")
    print(f"週足A: {len(all_a)}件 / 日足B1: {len(all_b1)}件 / 日足B2: {len(all_b2)}件")

    dfa  = format_a(all_a)
    dfb1 = format_b1(all_b1)
    dfb2 = format_b2(all_b2)

    print("ファンダメンタルズ取得中...")
    dfa  = enrich_with_fundamentals(dfa)
    dfb1 = enrich_with_fundamentals(dfb1)
    dfb2 = enrich_with_fundamentals(dfb2)

    dfm = build_multi_hit(dfa, dfb1, dfb2)

    # ── スプレッドシートへ書き込み ──
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("環境変数 SPREADSHEET_ID が設定されていません")

    print("Googleスプレッドシートへ書き込み中...")
    gc = get_gspread_client()
    write_df_to_sheet(gc, spreadsheet_id, "複数パターン合致", dfm)
    write_df_to_sheet(gc, spreadsheet_id, "週足パターンA",     dfa)
    write_df_to_sheet(gc, spreadsheet_id, "日足B1押し目待ち",   dfb1)
    write_df_to_sheet(gc, spreadsheet_id, "日足B2反発エントリー", dfb2)

    # ── メタ情報シート（最終実行日時など）も書いておく ──
    meta_df = pd.DataFrame([{
        "最終実行日時": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "対象銘柄数": len(tickers_all),
        "週足A件数": len(dfa),
        "日足B1件数": len(dfb1),
        "日足B2件数": len(dfb2),
        "複数合致件数": len(dfm),
        "JPX取得診断": jpx_diag,
    }])
    write_df_to_sheet(gc, spreadsheet_id, "実行ログ", meta_df)

    print("=== 完了 ===")


if __name__ == "__main__":
    main()
