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
    return {"rows": _sheet("4系統サマリー").to_dict("records")}

@app.get("/api/early")
def early():
    df = early_top20_pool(_sheet("週足転換H"),70,70).head(20).copy()
    if not df.empty:
        df["理想本命"] = early_ideal_mask(df).map({True:"⭐ 理想本命",False:""})
    return {"rows": df.to_dict("records")}

@app.get("/api/watchlist")
def watchlist():
    return {"rows": _watch_df().to_dict("records")}

@app.get("/api/watch-board")
def watch_board():
    board = build_watchlist_promotion_board(
        _watch_df(),
        _sheet("週足転換H"),
    )
    return {"rows": board.to_dict("records")}

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

@app.get("/api/chart/{ticker}")
def chart(ticker: str, period: str="6mo"):
    try:
        df = yf.download(ticker, period=period, interval="1d", auto_adjust=True, progress=False)
        if df is None or df.empty: return {"rows":[]}
        if isinstance(df.columns,pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        date_col = df.columns[0]
        rows=[]
        for _,r in df.tail(260).iterrows():
            rows.append({"Date":str(r[date_col])[:10],"Close":float(r["Close"])})
        return {"rows":rows}
    except Exception as exc:
        raise HTTPException(502, f"chart fetch failed: {type(exc).__name__}")
