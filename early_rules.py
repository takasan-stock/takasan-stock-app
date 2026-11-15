from __future__ import annotations

import numpy as np
import pandas as pd

WATCHLIST_COLUMNS=["証券コード","Ticker","銘柄名","登録日","メモ"]


def _numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out=df.loc[:,~df.columns.duplicated(keep="first")].copy()
    for c in dict.fromkeys(cols):
        if c in out.columns:
            out[c]=pd.to_numeric(out[c],errors="coerce")
    return out


def normalize_watchlist(df: pd.DataFrame) -> pd.DataFrame:
    out=df.copy() if df is not None else pd.DataFrame()
    for c in WATCHLIST_COLUMNS:
        if c not in out.columns:
            out[c]=""
    return out[WATCHLIST_COLUMNS].fillna("").astype(str)


def early_top20_audit(
    df: pd.DataFrame,
    min_score: float=70.0,
    min_priority: float=70.0,
) -> pd.DataFrame:
    """EARLY最優先TOP20の共通監査ロジック。"""
    if df is None or df.empty:
        return pd.DataFrame()

    out=_numeric(df,[
        "週足転換スコア","初動優先スコア","EARLYスコア","完成4週騰落率%",
        "20週MA再奪回から週数","40週MA再奪回から週数","40週MA乖離%",
        "RR目安","実戦想定損切り距離%","長期Stage4点","長期押し目点",
    ])

    if "週足転換タイプ" in out.columns:
        out=out[out["週足転換タイプ"].astype(str)=="EARLY_TURN"].copy()
    if out.empty:
        return out

    verdicts=[]
    reason_text=[]
    for _,row in out.iterrows():
        reasons=[]

        score=row.get("週足転換スコア",np.nan)
        if "週足転換スコア" in out.columns:
            if pd.isna(score):
                reasons.append("週足転換スコア欠損")
            elif float(score)<min_score:
                reasons.append(f"週足転換スコア<{min_score:g}")

        priority=row.get("初動優先スコア",np.nan)
        if "初動優先スコア" in out.columns:
            if pd.isna(priority):
                reasons.append("初動優先スコア欠損")
            elif float(priority)<min_priority:
                reasons.append(f"初動優先スコア<{min_priority:g}")

        if "急落判定" in out.columns and str(row.get("急落判定",""))=="⚠️急落ダメージ":
            reasons.append("急落ダメージ")
        if "反転確認" in out.columns and str(row.get("反転確認",""))!="✅反転確認":
            reasons.append("反転未確認")
        if "押し目再始動判定" in out.columns and str(row.get("押し目再始動判定",""))=="↗️押し目再始動":
            reasons.append("押し目再始動")
        if "下降継続判定" in out.columns and str(row.get("下降継続判定",""))=="⬇️下降継続":
            reasons.append("下降継続")
        if "反転定着" in out.columns and str(row.get("反転定着",""))!="✅定着確認":
            reasons.append("反転未定着")
        if "EARLY形状判定" in out.columns and str(row.get("EARLY形状判定",""))!="✅EARLY適合":
            reasons.append("EARLY形状外")

        if "長期ハード除外" in out.columns:
            block_raw=row.get("長期ハード除外",np.nan)
            if pd.isna(block_raw) or str(block_raw).strip()=="":
                reasons.append("長期ハード判定欠損")
            elif str(block_raw)!="✅なし":
                reasons.append(str(block_raw).replace("× ","長期:"))

        if "完成4週騰落率%" in out.columns:
            r4=row.get("完成4週騰落率%",np.nan)
            if pd.isna(r4):
                reasons.append("4週騰落率欠損")
            elif float(r4)<-8.0:
                reasons.append("4週下落<-8%")
            elif float(r4)>12.0:
                reasons.append("4週上昇>12%")

        for col,label in [
            ("20週MA再奪回から週数","20週MA再奪回"),
            ("40週MA再奪回から週数","40週MA再奪回"),
        ]:
            if col in out.columns:
                weeks=row.get(col,np.nan)
                if pd.isna(weeks):
                    reasons.append(f"{label}欠損")
                elif float(weeks)<0 or float(weeks)>8 or float(weeks)>=999:
                    reasons.append(f"{label}>8週/未奪回")

        rr=row.get("RR目安",np.nan)
        if "RR目安" in out.columns:
            if pd.isna(rr):
                reasons.append("RR欠損")
            elif float(rr)<2.0:
                reasons.append("RR<2.0")

        stop=row.get("実戦想定損切り距離%",np.nan)
        if "実戦想定損切り距離%" in out.columns:
            if pd.isna(stop):
                reasons.append("損切り距離欠損")
            elif float(stop)>7.5:
                reasons.append("損切り>7.5%")

        stage=str(row.get("長期ステージ",""))
        stage4=row.get("長期Stage4点",np.nan)
        if (
            stage=="TRANSITION"
            and pd.notna(stage4)
            and float(stage4)>=3
            and (
                (pd.notna(rr) and float(rr)<2.5)
                or (pd.notna(stop) and float(stop)>=7.0)
            )
        ):
            reasons.append("TRANSITION実戦効率不足")

        verdicts.append("✅通過" if not reasons else "×除外")
        reason_text.append("—" if not reasons else " / ".join(reasons))

    out["TOP20判定"]=verdicts
    out["TOP20除外理由"]=reason_text

    sort_cols=[]
    asc=[]
    if "初動優先スコア" in out.columns:
        sort_cols.append("初動優先スコア"); asc.append(False)
    if "EARLYスコア" in out.columns:
        sort_cols.append("EARLYスコア"); asc.append(False)
    if sort_cols:
        out=out.sort_values(sort_cols,ascending=asc)
    return out.reset_index(drop=True)


def early_top20_pool(
    df: pd.DataFrame,
    min_score: float=70.0,
    min_priority: float=70.0,
) -> pd.DataFrame:
    audit=early_top20_audit(df,min_score,min_priority)
    if audit.empty or "TOP20判定" not in audit.columns:
        return pd.DataFrame()
    return audit[audit["TOP20判定"]=="✅通過"].reset_index(drop=True)


def early_ideal_mask(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(False,index=getattr(df,"index",pd.Index([])))

    rank=df.get("初動ランク",pd.Series("",index=df.index)).astype(str)
    stage=df.get("長期ステージ",pd.Series("",index=df.index)).astype(str)
    rr=pd.to_numeric(df.get("RR目安",pd.Series(np.nan,index=df.index)),errors="coerce")
    stop=pd.to_numeric(df.get("実戦想定損切り距離%",pd.Series(np.nan,index=df.index)),errors="coerce")
    reversal=df.get("反転確認",pd.Series("",index=df.index)).astype(str)
    stable=df.get("反転定着",pd.Series("",index=df.index)).astype(str)
    shape=df.get("EARLY形状判定",pd.Series("",index=df.index)).astype(str)

    return (
        rank.eq("S")
        & stage.eq("EARLY_STAGE_OK")
        & rr.ge(3.0)
        & stop.between(3.0,5.0,inclusive="both")
        & reversal.eq("✅反転確認")
        & stable.eq("✅定着確認")
        & shape.eq("✅EARLY適合")
    )


def build_watchlist_promotion_board(
    watchlist: pd.DataFrame,
    weekly: pd.DataFrame,
) -> pd.DataFrame:
    base=normalize_watchlist(watchlist)
    if base.empty:
        return base

    source=(
        weekly.loc[:,~weekly.columns.duplicated(keep="first")].copy()
        if weekly is not None and not weekly.empty
        else pd.DataFrame()
    )
    enrich_cols=[
        "Ticker","初動優先スコア","初動ランク","長期ステージ","RR目安",
        "実戦想定損切り距離%","反転確認","反転定着","EARLY形状判定",
        "急落判定","追撃余地","初動鮮度週","20週MA再奪回から週数",
        "40週MA再奪回から週数",
    ]
    available=[c for c in enrich_cols if c in source.columns]

    if "Ticker" in source.columns:
        source["Ticker"]=source["Ticker"].astype(str).str.strip()
        if "初動優先スコア" in source.columns:
            source["初動優先スコア"]=pd.to_numeric(source["初動優先スコア"],errors="coerce")
            source=source.sort_values("初動優先スコア",ascending=False,na_position="last")
        source=source[available].drop_duplicates("Ticker",keep="first")
        board=base.merge(source,on="Ticker",how="left")
    else:
        board=base.copy()

    numeric_cols=[
        "初動優先スコア","RR目安","実戦想定損切り距離%",
        "初動鮮度週","20週MA再奪回から週数","40週MA再奪回から週数",
    ]
    for c in enrich_cols:
        if c=="Ticker":
            continue
        if c not in board.columns:
            board[c]=np.nan if c in numeric_cols else ""
    for c in numeric_cols:
        board[c]=pd.to_numeric(board[c],errors="coerce")

    ideal=early_ideal_mask(board)
    rank=board["初動ランク"].fillna("").astype(str)
    stage=board["長期ステージ"].fillna("").astype(str)
    rr=board["RR目安"]
    stop=board["実戦想定損切り距離%"]
    reversal=board["反転確認"].fillna("").astype(str)
    stable=board["反転定着"].fillna("").astype(str)
    shape=board["EARLY形状判定"].fillna("").astype(str)
    data_present=rank.ne("") | stage.ne("") | rr.notna() | board["初動優先スコア"].notna()

    entry_check=(
        ~ideal
        & rank.isin(["S","A"])
        & stage.isin(["EARLY_STAGE_OK","TRANSITION"])
        & rr.ge(2.5)
        & stop.le(6.0)
        & reversal.eq("✅反転確認")
        & stable.eq("✅定着確認")
        & shape.eq("✅EARLY適合")
    )
    watch=(
        ~ideal & ~entry_check & data_present
        & (
            rank.isin(["S","A","B"])
            | stage.isin(["EARLY_STAGE_OK","TRANSITION","PULLBACK_BORDER"])
        )
    )
    board["監視ステータス"]=np.select(
        [ideal,entry_check,watch,data_present],
        ["⭐ 理想本命","🔥 ENTRY確認","👀 WATCH","⏸ WAIT"],
        default="— データなし",
    )
    board["次アクション"]=np.select(
        [ideal,entry_check,watch,data_present],
        [
            "Entry Hunter / Pre-Tradeへ",
            "Entry Hunterでタイミング確認",
            "週足・反転条件を継続監視",
            "条件改善待ち",
        ],
        default="週足転換Hデータなし",
    )
    priority={"⭐ 理想本命":0,"🔥 ENTRY確認":1,"👀 WATCH":2,"⏸ WAIT":3,"— データなし":4}
    board["_priority"]=board["監視ステータス"].map(priority).fillna(9)
    return board.sort_values(
        ["_priority","初動優先スコア","Ticker"],
        ascending=[True,False,True],
        na_position="last",
    ).drop(columns=["_priority"]).reset_index(drop=True)
