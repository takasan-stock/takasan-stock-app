import json, os, sys
from datetime import date
from pathlib import Path

import gspread
import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.oauth2.service_account import Credentials

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from early_rules import (
    early_top20_pool,
    early_ideal_mask,
    build_watchlist_promotion_board,
)
from jquants_prices import JQuantsPriceClient

app = FastAPI(title="Takasan Stock Hunter API", version="0.1.0")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in origins else origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SHEET_ID = os.getenv("SPREADSHEET_ID","")
WATCHLIST = "ウォッチリスト"
WATCH_COLS = ["証券コード","Ticker","銘柄名","登録日","メモ"]

class WatchIn(BaseModel):
    code: str = ""
    ticker: str
    name: str = ""

class MemoIn(BaseModel):
    memo: str = ""

def _client():
    raw = os.getenv("GCP_SERVICE_ACCOUNT_JSON","")
    if not SHEET_ID or not raw:
        raise HTTPException(503, "Google Sheets credentials are not configured")
    info = json.loads(raw)
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def _sheet(name: str) -> pd.DataFrame:
    try:
        ws = _client().open_by_key(SHEET_ID).worksheet(name)
        vals = ws.get_all_values()
    except gspread.WorksheetNotFound:
        return pd.DataFrame()
    if len(vals) < 2:
        return pd.DataFrame(columns=vals[0] if vals else [])
    df = pd.DataFrame(vals[1:], columns=vals[0])
    return df.loc[:,~df.columns.duplicated(keep="first")]

def _watch_df():
    df = _sheet(WATCHLIST)
    for c in WATCH_COLS:
        if c not in df.columns: df[c] = ""
    return df[WATCH_COLS].fillna("").astype(str)

def _records(df: pd.DataFrame):
    """DataFrameをJSON安全なrecordsへ変換する。NaN/NaT/infはnullへ。"""
    if df is None or df.empty:
        return []
    safe = df.copy()
    numeric_cols = safe.select_dtypes(include=["number"]).columns
    if len(numeric_cols):
        safe[numeric_cols] = safe[numeric_cols].replace([float("inf"), float("-inf")], pd.NA)
    return json.loads(safe.to_json(orient="records", force_ascii=False, date_format="iso"))

def _save_watch(df: pd.DataFrame):
    sh = _client().open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(WATCHLIST)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WATCHLIST, rows=max(500, len(df)+50), cols=10)
    out = df.copy()
    for c in WATCH_COLS:
        if c not in out.columns: out[c] = ""
    out = out[WATCH_COLS].fillna("").astype(str)
    ws.update([WATCH_COLS] + out.values.tolist())

@app.get("/api/health")
def health():
    return {"ok": bool(SHEET_ID and os.getenv("GCP_SERVICE_ACCOUNT_JSON")), "service":"takasan-stock-hunter-api"}

@app.get("/api/summary")
def summary():
    return {"rows": _records(_sheet("4系統サマリー"))}

def _find_stock_row(df: pd.DataFrame, ticker: str) -> dict:
    if df is None or df.empty:
        return {}
    work = df.loc[:,~df.columns.duplicated(keep="first")].copy()
    raw = str(ticker or "").strip()
    code = raw.split(".")[0]
    mask = pd.Series(False, index=work.index)
    if "Ticker" in work.columns:
        mask |= work["Ticker"].astype(str).str.strip().eq(raw)
    if "証券コード" in work.columns:
        mask |= work["証券コード"].astype(str).str.strip().eq(code)
    hit = work[mask].copy()
    if hit.empty:
        return {}
    if "初動優先スコア" in hit.columns:
        hit["_sort_score"] = pd.to_numeric(hit["初動優先スコア"], errors="coerce")
        hit = hit.sort_values("_sort_score", ascending=False, na_position="last").drop(columns=["_sort_score"])
    records = _records(hit.head(1))
    return records[0] if records else {}

@app.get("/api/stock-context/{ticker}")
def stock_context(ticker: str):
    summary_row = _find_stock_row(_sheet("4系統サマリー"), ticker)
    weekly_row = _find_stock_row(_sheet("週足転換H"), ticker)
    merged = {**summary_row, **weekly_row}
    return {
        "row": merged,
        "sources": {
            "summary": bool(summary_row),
            "weekly": bool(weekly_row),
        },
    }

@app.get("/api/early")
def early():
    df = early_top20_pool(_sheet("週足転換H"),70,70).head(20).copy()
    if not df.empty:
        df["理想本命"] = early_ideal_mask(df).map({True:"⭐ 理想本命",False:""})
    return {"rows": _records(df)}

@app.get("/api/watchlist")
def watchlist():
    return {"rows": _records(_watch_df())}

@app.get("/api/watch-board")
def watch_board():
    board = build_watchlist_promotion_board(
        _watch_df(),
        _sheet("週足転換H"),
    )
    return {"rows": _records(board)}

@app.post("/api/watchlist")
def add_watch(item: WatchIn):
    ticker = item.ticker.strip()
    if not ticker: raise HTTPException(400,"ticker is required")
    df = _watch_df()
    if ticker in set(df["Ticker"].str.strip()):
        return {"added":0,"duplicate":1}
    new = pd.DataFrame([{
        "証券コード":item.code.strip(),"Ticker":ticker,"銘柄名":item.name.strip(),
        "登録日":date.today().isoformat(),"メモ":""
    }])
    _save_watch(pd.concat([df,new],ignore_index=True))
    return {"added":1,"duplicate":0}

@app.put("/api/watchlist/{ticker}/memo")
def update_memo(ticker: str, body: MemoIn):
    df = _watch_df()
    mask = df["Ticker"].str.strip().eq(ticker.strip())
    if not mask.any(): raise HTTPException(404,"ticker not found")
    df.loc[mask,"メモ"] = body.memo
    _save_watch(df)
    return {"updated":int(mask.sum())}

@app.delete("/api/watchlist/{ticker}", status_code=204)
def delete_watch(ticker: str):
    df = _watch_df()
    out = df[~df["Ticker"].str.strip().eq(ticker.strip())].reset_index(drop=True)
    _save_watch(out)
    return None

def _chart_period_start(period: str):
    p = str(period or "6mo").strip().lower()
    offsets = {
        "1mo": pd.DateOffset(months=1),
        "3mo": pd.DateOffset(months=3),
        "6mo": pd.DateOffset(months=6),
        "1y": pd.DateOffset(years=1),
        "2y": pd.DateOffset(years=2),
        "5y": pd.DateOffset(years=5),
    }
    return (pd.Timestamp.today().normalize() - offsets.get(p, offsets["6mo"])).date()

def _chart_rows_from_df(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        work.columns = work.columns.get_level_values(0)
    if isinstance(work.index, pd.DatetimeIndex):
        work = work.reset_index()
    date_col = "Date" if "Date" in work.columns else work.columns[0]
    if "Close" not in work.columns:
        return []
    rows = []
    for _, r in work.tail(260).iterrows():
        def pick(name):
            value = pd.to_numeric(pd.Series([r.get(name)]), errors="coerce").iloc[0]
            return None if pd.isna(value) else float(value)
        close = pick("Close")
        if close is None:
            continue
        open_ = pick("Open")
        high = pick("High")
        low = pick("Low")
        volume = pick("Volume")
        rows.append({
            "Date": str(r[date_col])[:10],
            "Open": open_ if open_ is not None else close,
            "High": high if high is not None else close,
            "Low": low if low is not None else close,
            "Close": close,
            "Volume": volume,
        })
    return rows

@app.get("/api/chart/{ticker}")
def chart(ticker: str, period: str="6mo"):
    errors = []

    # Primary source for Japanese equities: J-Quants, when configured.
    try:
        jq = JQuantsPriceClient.from_env()
        if jq is not None:
            df = jq.daily_bars(ticker, _chart_period_start(period), date.today())
            rows = _chart_rows_from_df(df)
            if rows:
                return {"rows": rows, "source": "J-Quants"}
            errors.append("J-Quants: empty")
        else:
            errors.append("J-Quants: not configured")
    except Exception as exc:
        errors.append(f"J-Quants: {type(exc).__name__}")
        print(f"[chart] J-Quants failed {ticker}: {type(exc).__name__}: {exc}", flush=True)

    # Secondary fallback: yfinance.
    try:
        df = yf.download(ticker, period=period, interval="1d", auto_adjust=True, progress=False)
        rows = _chart_rows_from_df(df)
        if rows:
            return {"rows": rows, "source": "Yahoo Finance"}
        errors.append("Yahoo Finance: empty")
    except Exception as exc:
        errors.append(f"Yahoo Finance: {type(exc).__name__}")
        print(f"[chart] Yahoo failed {ticker}: {type(exc).__name__}: {exc}", flush=True)

    # Keep the endpoint successful so the UI can show a useful data-source message.
    return {"rows": [], "source": None, "unavailable_reason": " / ".join(errors)}
