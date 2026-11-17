import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, BarChart3, Home, Radar, RefreshCw, Star, Trash2 } from "lucide-react";
import { CandlestickSeries, ColorType, HistogramSeries, LineSeries, createChart } from "lightweight-charts";
import "./styles.css";

const API = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

async function api(path, options) {
  const res = await fetch(`${API}${path}`, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.status === 204 ? null : res.json();
}

const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const ideal = (row) => row["理想本命"] === "⭐ 理想本命"
  || row["監視ステータス"] === "⭐ 理想本命";

const isWatched = (row, watchRows = []) => {
  const ticker = String(row?.Ticker || "").trim();
  const code = String(row?.["証券コード"] || "").trim();
  return watchRows.some((w) => {
    const wt = String(w?.Ticker || "").trim();
    const wc = String(w?.["証券コード"] || "").trim();
    return (ticker && wt === ticker) || (code && wc === code);
  });
};

function App() {
  const [page, setPage] = useState("home");
  const [summary, setSummary] = useState([]);
  const [early, setEarly] = useState([]);
  const [watch, setWatch] = useState([]);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedTicker, setSelectedTicker] = useState("");
  const [chart, setChart] = useState([]);
  const [chartInfo, setChartInfo] = useState({source:null, reason:null});

  const reload = async () => {
    setLoading(true);
    try {
      const [h,s,e,w] = await Promise.all([
        api("/api/health"),
        api("/api/summary"),
        api("/api/early"),
        api("/api/watch-board"),
      ]);
      setHealth(h); setSummary(s.rows || []); setEarly(e.rows || []); setWatch(w.rows || []);
      if (!selectedTicker && (s.rows || []).length) setSelectedTicker(s.rows[0].Ticker || "");
    } finally { setLoading(false); }
  };

  useEffect(() => { reload(); }, []);

  useEffect(() => {
    if (!selectedTicker) return;
    api(`/api/chart/${encodeURIComponent(selectedTicker)}?period=6mo`)
      .then((r) => {
        setChart(r.rows || []);
        setChartInfo({source:r.source || null, reason:r.unavailable_reason || null});
      })
      .catch((e) => {
        setChart([]);
        setChartInfo({source:null, reason:String(e?.message || "chart fetch failed")});
      });
  }, [selectedTicker]);

  const earlyTop = useMemo(() => early.slice(0,20), [early]);
  const watchBoard = useMemo(() => watch, [watch]);

  const addWatch = async (row) => {
    await api("/api/watchlist", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({
        code: String(row["証券コード"] || ""),
        ticker: String(row.Ticker || ""),
        name: String(row["銘柄名"] || ""),
      })
    });
    await reload();
  };

  const removeWatch = async (ticker) => {
    await api(`/api/watchlist/${encodeURIComponent(ticker)}`, {method:"DELETE"});
    await reload();
  };

  const saveMemo = async (ticker, memo) => {
    await api(`/api/watchlist/${encodeURIComponent(ticker)}/memo`, {
      method:"PUT",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({memo})
    });
    await reload();
  };

  const openChart = (row) => {
    if (row?.Ticker) setSelectedTicker(row.Ticker);
    setPage("chart");
  };

  return <div className="app">
    <aside className="sidebar">
      <div className="brand"><Radar size={24}/><span>Takasan Stock Hunter</span></div>
      {[
        ["home","TODAY",Home],["early","EARLY HUNTER",Activity],["watch","WATCHLIST",Star],
        ["ranking","RANKING",BarChart3],["chart","CHART",BarChart3]
      ].map(([id,label,Icon]) => <button key={id} className={page===id?"nav active":"nav"} onClick={()=>setPage(id)}>
        <Icon size={18}/><span>{label}</span>
      </button>)}
      <div className="sidebarFooter">
        <span className={health?.ok ? "dot ok":"dot"}></span>
        <span>{health?.ok ? "DATA ONLINE":"CHECK DATA"}</span>
      </div>
    </aside>

    <main>
      <header className="topbar">
        <div><h1>{pageTitle(page)}</h1><p>今日、何を見るか。次に、何をするか。</p></div>
        <button className="refresh" onClick={reload}><RefreshCw size={17}/>再取得</button>
      </header>

      {loading ? <div className="loading">データを読み込み中...</div> : <>
        {page==="home" && <HomePage early={earlyTop} watchBoard={watchBoard} summary={summary} addWatch={addWatch} openWatch={()=>setPage("watch")} openChart={openChart}/>} 
        {page==="early" && <EarlyPage rows={earlyTop} watchBoard={watchBoard} addWatch={addWatch}/>}
        {page==="watch" && <WatchPage rows={watchBoard} saveMemo={saveMemo} removeWatch={removeWatch}/>}
        {page==="ranking" && <RankingPage rows={summary} watchBoard={watchBoard} addWatch={addWatch}/>}
        {page==="chart" && <ChartPage summary={summary} earlyRows={early} ticker={selectedTicker} setTicker={setSelectedTicker} rows={chart} chartInfo={chartInfo} watchBoard={watchBoard} addWatch={addWatch}/>} 
      </>}
    </main>
  </div>;
}

function pageTitle(page){return ({home:"TODAY COMMAND CENTER",early:"EARLY HUNTER",watch:"WATCHLIST",ranking:"RANKING",chart:"CHART"})[page]}

function Kpi({label,value,sub}){return <div className="kpi"><span>{label}</span><strong>{value}</strong><small>{sub}</small></div>}

function HomePage({early,watchBoard,summary,addWatch,openWatch,openChart}){
  const idealCount = early.filter(ideal).length;
  const entryCount = watchBoard.filter(r=>r["監視ステータス"]==="🔥 ENTRY確認").length;

  const activeWatch = watchBoard
    .filter(r=>["⭐ 理想本命","🔥 ENTRY確認","👀 WATCH"].includes(r["監視ステータス"]))
    .map(r=>({...r,_source:"watch"}));

  const watchKeys = new Set(activeWatch.map(r=>String(r.Ticker||r["証券コード"]||"").trim()));
  const earlyCandidates = early
    .filter(r=>ideal(r) || ["S","A"].includes(String(r["初動ランク"]||"")))
    .filter(r=>!watchKeys.has(String(r.Ticker||r["証券コード"]||"").trim()))
    .map(r=>({...r,_source:"early"}));

  const priority = {"⭐ 理想本命":0,"🔥 ENTRY確認":1,"👀 WATCH":2};
  const actionRows = [...activeWatch,...earlyCandidates]
    .sort((a,b)=>{
      const ap = a._source==="watch" ? (priority[a["監視ステータス"]] ?? 3) : (ideal(a) ? 1 : 3);
      const bp = b._source==="watch" ? (priority[b["監視ステータス"]] ?? 3) : (ideal(b) ? 1 : 3);
      if (ap !== bp) return ap-bp;
      return (num(b["初動優先スコア"])||0) - (num(a["初動優先スコア"])||0);
    })
    .slice(0,6);

  return <>
    <section className="kpis">
      <Kpi label="⭐ 理想本命" value={idealCount} sub="EARLY最優先"/>
      <Kpi label="🔥 ENTRY確認" value={entryCount} sub="ウォッチ昇格"/>
      <Kpi label="🌱 EARLY TOP20" value={early.length} sub="初動候補"/>
      <Kpi label="🏆 総合候補" value={summary.length} sub="全ランキング"/>
    </section>

    <section className="actionBoard">
      <div className="actionBoardHead">
        <div>
          <span className="eyebrow">TODAY ACTION QUEUE</span>
          <h2>今日やること</h2>
          <p>上から順に確認。判断済みの銘柄はWATCHLISTとCHARTへ。</p>
        </div>
        <button className="mini actionBoardLink" onClick={openWatch}>WATCHLISTを開く</button>
      </div>

      {actionRows.length ? <div className="actionQueue">
        {actionRows.map((r,i)=>{
          const isWatch = r._source==="watch";
          const status = isWatch ? r["監視ステータス"] : (ideal(r) ? "⭐ 理想本命" : "🌱 EARLY候補");
          const action = isWatch ? (r["次アクション"]||"継続確認") : "WATCHLIST登録を検討";
          const watched = isWatched(r,watchBoard);
          return <article className={`actionItem action-${i+1}`} key={r.Ticker||r["証券コード"]||i}>
            <div className="actionPriority">{i+1}</div>
            <div className="actionIdentity">
              <StatusBadge text={status}/>
              <strong>{r["証券コード"]||"—"} <small>{r["銘柄名"]||""}</small></strong>
            </div>
            <div className="actionMetrics">
              <span>初動 <b>{r["初動優先スコア"]||"—"}</b></span>
              <span>Rank <b>{r["初動ランク"]||"—"}</b></span>
              <span>RR <b>{r["RR目安"]||"—"}</b></span>
            </div>
            <div className="actionNext">{action}</div>
            <div className="actionButtons">
              {!watched && <button className="mini addAction" onClick={()=>addWatch(r)}><Star size={15}/> WATCHLIST</button>}
              {watched && <button className="mini watchedAction" disabled><Star size={15} fill="currentColor"/> 登録済み</button>}
              <button className="mini chartAction" onClick={()=>openChart(r)}>CHART</button>
            </div>
          </article>
        })}
      </div> : <div className="actionEmpty">現在、優先アクションはありません。EARLY TOP20を確認してください。</div>}
    </section>

    <Panel title="今日見るべき EARLY">
      <EarlyTable rows={early.slice(0,10)} watchRows={watchBoard} addWatch={addWatch}/>
    </Panel>
  </>
}

function EarlyPage({rows,watchBoard,addWatch}){
  const idealCount=rows.filter(ideal).length;
  const watchedCount=rows.filter(r=>isWatched(r,watchBoard)).length;
  const sCount=rows.filter(r=>String(r["初動ランク"]||"")==="S").length;
  return <>
    <section className="pageSummary">
      <Kpi label="⭐ 理想本命" value={idealCount} sub="最優先候補"/>
      <Kpi label="★ WATCHLIST" value={watchedCount} sub="登録済み"/>
      <Kpi label="Sランク" value={sCount} sub="初動ランク"/>
    </section>
    <Panel title="EARLY最優先 TOP20"><EarlyTable rows={rows} watchRows={watchBoard} addWatch={addWatch}/></Panel>
  </>
}

function EarlyTable({rows,watchRows,addWatch}) {
  return <div className="tableWrap"><table><thead><tr>
    <th>銘柄</th><th>初動</th><th>ランク</th><th>長期ステージ</th><th>RR</th><th>損切り</th><th>反転</th><th>定着</th><th>形状</th><th></th>
  </tr></thead><tbody>{rows.map((r,i)=>{
    const watched = isWatched(r, watchRows);
    return <tr key={r.Ticker||i} className={ideal(r)?"ideal":""}>
      <td><b>{r["証券コード"]}</b><br/><small>{r["銘柄名"]}</small></td>
      <td>{r["初動優先スコア"]}</td><td><Badge text={r["初動ランク"]}/></td>
      <td>{r["長期ステージ"]}</td><td>{r["RR目安"]}</td><td>{r["実戦想定損切り距離%"]}%</td>
      <td>{r["反転確認"]}</td><td>{r["反転定着"]}</td><td>{r["EARLY形状判定"]}</td>
      <td>
        <button
          className={`mini watchToggle ${watched ? "watched" : ""}`}
          onClick={()=>!watched && addWatch(r)}
          disabled={watched}
          title={watched ? "WATCHLIST登録済み" : "WATCHLISTに追加"}
          aria-label={watched ? "WATCHLIST登録済み" : "WATCHLISTに追加"}
        >
          <Star size={17} fill={watched ? "currentColor" : "none"}/>
        </button>
      </td>
    </tr>
  })}</tbody></table></div>
}

function WatchPage({rows,saveMemo,removeWatch}) {
  const [drafts,setDrafts]=useState({});
  const idealCount=rows.filter(r=>String(r["監視ステータス"]||"").includes("理想")).length;
  const entryCount=rows.filter(r=>r["監視ステータス"]==="🔥 ENTRY確認").length;
  const watchCount=rows.filter(r=>r["監視ステータス"]==="👀 WATCH").length;
  return <>
    <section className="pageSummary watchSummary">
      <Kpi label="⭐ 理想本命" value={idealCount} sub="即確認"/>
      <Kpi label="🔥 ENTRY確認" value={entryCount} sub="タイミング確認"/>
      <Kpi label="👀 継続監視" value={watchCount} sub="WATCH"/>
      <Kpi label="📌 WATCHLIST総数" value={rows.length} sub="登録銘柄"/>
    </section>
    <Panel title="🚦 ウォッチ昇格ボード"><div className="tableWrap"><table><thead><tr>
      <th>状態</th><th>銘柄</th><th>ランク</th><th>長期</th><th>RR</th><th>損切り</th><th>次アクション</th><th>メモ</th><th></th>
    </tr></thead><tbody>{rows.map((r,i)=><tr key={r.Ticker||i} className={r["監視ステータス"].includes("理想")?"ideal":""}>
      <td><StatusBadge text={r["監視ステータス"]}/></td>
      <td><b>{r["証券コード"]}</b><br/><small>{r["銘柄名"]}</small></td>
      <td>{r["初動ランク"]||"—"}</td><td>{r["長期ステージ"]||"—"}</td><td>{r["RR目安"]||"—"}</td>
      <td>{r["実戦想定損切り距離%"] ? `${r["実戦想定損切り距離%"]}%` : "—"}</td>
      <td><span className="nextAction">{r["次アクション"]||"—"}</span></td>
      <td><div className="memoRow"><input placeholder="確認ポイント・材料・次回チェックなど" value={drafts[r.Ticker] ?? r["メモ"] ?? ""} onChange={e=>setDrafts({...drafts,[r.Ticker]:e.target.value})}/><button className="mini" onClick={()=>saveMemo(r.Ticker,drafts[r.Ticker] ?? r["メモ"] ?? "")}>保存</button></div></td>
      <td><button className="danger" title="WATCHLISTから削除" onClick={()=>{
        if (window.confirm(`${r["証券コード"]||""} ${r["銘柄名"]||""} をWATCHLISTから削除しますか？`)) removeWatch(r.Ticker);
      }}><Trash2 size={16}/></button></td>
    </tr>)}</tbody></table></div></Panel>
  </>
}

function RankingPage({rows,watchBoard,addWatch}) {
  const sorted=[...rows].sort((a,b)=>(num(b["総合スコア"])||0)-(num(a["総合スコア"])||0)).slice(0,50);
  const watchedCount=sorted.filter(r=>isWatched(r,watchBoard)).length;
  const topScore=num(sorted[0]?.["総合スコア"]) ?? "—";
  return <>
    <section className="pageSummary">
      <Kpi label="🏆 TOP SCORE" value={topScore} sub="総合スコア"/>
      <Kpi label="★ WATCHLIST" value={watchedCount} sub="TOP50内"/>
      <Kpi label="📊 表示件数" value={sorted.length} sub="総合ランキング"/>
    </section>
    <Panel title="総合ランキング"><div className="tableWrap"><table><thead><tr>
      <th>#</th><th>銘柄</th><th>総合</th><th>ランク</th><th>実戦</th><th>RS</th><th>出来高M</th><th>主力</th><th></th>
    </tr></thead><tbody>{sorted.map((r,i)=>{
      const watched = isWatched(r, watchBoard);
      return <tr key={r.Ticker||i} className={i<3?"topRank":""}>
      <td><span className="rankNo">{i+1}</span></td>
      <td><b>{r["証券コード"]}</b><br/><small>{r["銘柄名"]}</small></td>
      <td>{r["総合スコア"]}</td><td><Badge text={r["総合ランク"]}/></td><td>{r["実戦スコア"]}</td>
      <td>{r["RS Rating"]}</td><td>{r["出来高モメンタム"]}</td><td>{r["主力シグナル"]}</td>
      <td>
        <button
          className={`mini watchToggle ${watched ? "watched" : ""}`}
          onClick={()=>!watched && addWatch(r)}
          disabled={watched}
          title={watched ? "WATCHLIST登録済み" : "WATCHLISTに追加"}
        >
          <Star size={17} fill={watched ? "currentColor" : "none"}/>
        </button>
      </td>
    </tr>
  })}</tbody></table></div></Panel>
  </>
}


function smaData(rows, period){
  const values=[];
  let sum=0;
  return rows.map((r)=>{
    const v=num(r.Close);
    values.push(v);
    if(v!==null) sum+=v;
    if(values.length>period){
      const old=values.shift();
      if(old!==null) sum-=old;
    }
    if(values.length<period || values.some(x=>x===null)) return null;
    return {time:r.Date,value:sum/period};
  }).filter(Boolean);
}

function FinancialChart({rows}){
  const hostRef=useRef(null);
  useEffect(()=>{
    const host=hostRef.current;
    if(!host || !rows.length) return;

    const chart=createChart(host,{
      width:host.clientWidth,
      height:host.clientHeight || 500,
      layout:{
        background:{type:ColorType.Solid,color:"#fbfdff"},
        textColor:"#5f778c",
        attributionLogo:true,
      },
      grid:{
        vertLines:{color:"#edf2f6"},
        horzLines:{color:"#edf2f6"},
      },
      rightPriceScale:{
        borderColor:"#dbe6ee",
        scaleMargins:{top:0.08,bottom:0.22},
      },
      timeScale:{
        borderColor:"#dbe6ee",
        timeVisible:false,
        secondsVisible:false,
        rightOffset:3,
        barSpacing:8,
        minBarSpacing:3,
      },
      crosshair:{
        vertLine:{color:"#8fb0c8",width:1,style:2},
        horzLine:{color:"#8fb0c8",width:1,style:2},
      },
      handleScroll:true,
      handleScale:true,
    });

    const candles=chart.addSeries(CandlestickSeries,{
      upColor:"#28b779",
      downColor:"#e35d6a",
      borderVisible:false,
      wickUpColor:"#28b779",
      wickDownColor:"#e35d6a",
      priceLineVisible:true,
      lastValueVisible:true,
    });
    candles.setData(rows.map(r=>({
      time:r.Date,
      open:num(r.Open) ?? num(r.Close),
      high:num(r.High) ?? num(r.Close),
      low:num(r.Low) ?? num(r.Close),
      close:num(r.Close),
    })).filter(d=>d.close!==null));

    const ma20=chart.addSeries(LineSeries,{
      color:"#2d80c3",lineWidth:2,priceLineVisible:false,lastValueVisible:false,title:"MA20"
    });
    ma20.setData(smaData(rows,20));

    const ma60=chart.addSeries(LineSeries,{
      color:"#e7a429",lineWidth:2,priceLineVisible:false,lastValueVisible:false,title:"MA60"
    });
    ma60.setData(smaData(rows,60));

    const volume=chart.addSeries(HistogramSeries,{
      priceFormat:{type:"volume"},
      priceScaleId:"volume",
      priceLineVisible:false,
      lastValueVisible:false,
    });
    volume.priceScale().applyOptions({scaleMargins:{top:0.82,bottom:0}});
    volume.setData(rows.map(r=>{
      const o=num(r.Open), c=num(r.Close), v=num(r.Volume);
      return {time:r.Date,value:v || 0,color:(c!==null && o!==null && c>=o)?"rgba(40,183,121,.42)":"rgba(227,93,106,.42)"};
    }));

    chart.timeScale().fitContent();

    const ro=new ResizeObserver(()=>{
      if(host.clientWidth && host.clientHeight){
        chart.applyOptions({width:host.clientWidth,height:host.clientHeight});
      }
    });
    ro.observe(host);
    return ()=>{ro.disconnect();chart.remove();};
  },[rows]);

  return <div ref={hostRef} className="financialChart"/>;
}


function lastSma(rows, period){
  const vals=rows.slice(-period).map(r=>num(r.Close)).filter(v=>v!==null);
  if(vals.length<period) return null;
  return vals.reduce((a,b)=>a+b,0)/period;
}

function entryAssessment(row, chartRows){
  const rank=String(row?.["初動ランク"]||"");
  const stage=String(row?.["長期ステージ"]||"");
  const rr=num(row?.["RR目安"]);
  const stop=num(row?.["実戦想定損切り距離%"]);
  const reversal=String(row?.["反転確認"]||"");
  const settle=String(row?.["反転定着"]||"");
  const shape=String(row?.["EARLY形状判定"]||"");
  const close=num(chartRows?.[chartRows.length-1]?.Close);
  const ma20=lastSma(chartRows||[],20);
  const ma60=lastSma(chartRows||[],60);

  const checks=[
    {label:"初動ランク",pass:["S","A"].includes(rank),value:rank||"—",hint:"S / A"},
    {label:"長期ステージ",pass:stage==="EARLY_STAGE_OK",value:stage||"—",hint:"EARLY_STAGE_OK"},
    {label:"RR",pass:rr!==null && rr>=3,value:rr!==null?rr.toFixed(2):"—",hint:"3.0以上"},
    {label:"損切り距離",pass:stop!==null && stop<=5,value:stop!==null?`${stop.toFixed(1)}%`:"—",hint:"5%以内"},
    {label:"反転",pass:reversal.includes("確認"),value:reversal||"—",hint:"反転確認"},
    {label:"定着",pass:settle.includes("確認"),value:settle||"—",hint:"定着確認"},
    {label:"形状",pass:shape.includes("適合"),value:shape||"—",hint:"EARLY適合"},
    {label:"価格 / MA20",pass:close!==null && ma20!==null && close>=ma20,value:(close!==null&&ma20!==null)?`${((close/ma20-1)*100).toFixed(1)}%`:"—",hint:"終値がMA20以上"},
    {label:"MA20 / MA60",pass:ma20!==null && ma60!==null && ma20>=ma60,value:(ma20!==null&&ma60!==null)?(ma20>=ma60?"上向き":"下向き"):"—",hint:"MA20≧MA60"},
  ];

  const passed=checks.filter(c=>c.pass).length;
  const corePassed=checks.slice(0,7).filter(c=>c.pass).length;
  const status=(corePassed===7 && passed>=8)?"READY":(corePassed>=5 && passed>=6)?"PREP":"WAIT";
  return {status,passed,total:checks.length,checks,ma20,ma60,close};
}

function ChartPage({summary,earlyRows,ticker,setTicker,rows,chartInfo,watchBoard,addWatch}){
  const baseTarget=summary.find(r=>r.Ticker===ticker) || {};
  const earlyTarget=earlyRows.find(r=>r.Ticker===ticker || String(r["証券コード"]||"")===String(baseTarget["証券コード"]||"")) || {};
  const watchTarget=watchBoard.find(r=>r.Ticker===ticker || String(r["証券コード"]||"")===String(baseTarget["証券コード"]||"")) || {};
  const target={...baseTarget,...earlyTarget,...watchTarget};
  const watched=isWatched(target, watchBoard);
  const ys=rows.map(r=>num(r.Close)).filter(v=>v!==null);
  const min=ys.length?Math.min(...ys):null, max=ys.length?Math.max(...ys):null;
  const last=ys.length?ys[ys.length-1]:null;
  const latest=rows.length?rows[rows.length-1]:null;
  const prev=rows.length>1?num(rows[rows.length-2]?.Close):null;
  const change=(last!==null && prev!==null && prev!==0)?((last-prev)/prev)*100:null;
  const assessment=entryAssessment(target,rows);
  return <>
    <section className="chartHero">
      <div>
        <span className="eyebrow">SELECTED</span>
        <h2>{target["証券コード"]||"—"} <small>{target["銘柄名"]||""}</small></h2>
        <div className="chartMeta">
          <span>総合 <b>{target["総合スコア"]||"—"}</b></span>
          <span>ランク <b>{target["総合ランク"]||"—"}</b></span>
          <span>RS <b>{target["RS Rating"]||"—"}</b></span>
        </div>
      </div>
      <div className="chartPrice">
        <span>終値 {latest?.Date ? <em>{latest.Date}</em> : null}</span>
        <strong>{last!==null ? last.toLocaleString(undefined,{maximumFractionDigits:2}) : "—"}</strong>
        <small className={change!==null ? (change>=0?"priceUp":"priceDown") : ""}>
          {change!==null ? `${change>=0?"+":""}${change.toFixed(2)}%` : "—"} 
          {min!==null && max!==null ? ` / 6M ${min.toLocaleString()} - ${max.toLocaleString()}` : ""}
        </small>
      </div>
    </section>

    <section className={`entryCheckPanel entry-${assessment.status.toLowerCase()}`}>
      <div className="entryCheckHead">
        <div>
          <span className="eyebrow">ENTRY CHECK / 補助判定</span>
          <h2>購入前チェック</h2>
          <p>既存のEARLY条件とチャート位置をまとめた最終確認。Entry Hunter本体の判定は変更しません。</p>
        </div>
        <div className="entryStatus">
          <span className={`entryStatusBadge ${assessment.status.toLowerCase()}`}>{assessment.status}</span>
          <strong>{assessment.passed}<small> / {assessment.total}</small></strong>
        </div>
      </div>
      <div className="entryCheckGrid">
        {assessment.checks.map((c,i)=><div className={`entryCheckItem ${c.pass?"pass":"fail"}`} key={i}>
          <span className="entryCheckIcon">{c.pass?"✓":"!"}</span>
          <div>
            <b>{c.label}</b>
            <strong>{c.value}</strong>
            <small>{c.hint}</small>
          </div>
        </div>)}
      </div>
      <div className="entryCheckFoot">
        <span>MA20 {assessment.ma20!==null?assessment.ma20.toLocaleString(undefined,{maximumFractionDigits:1}):"—"}</span>
        <span>MA60 {assessment.ma60!==null?assessment.ma60.toLocaleString(undefined,{maximumFractionDigits:1}):"—"}</span>
        <span>終値 {assessment.close!==null?assessment.close.toLocaleString(undefined,{maximumFractionDigits:1}):"—"}</span>
      </div>
    </section>

    <Panel title="チャート">
      <div className="chartControls"><select value={ticker} onChange={e=>setTicker(e.target.value)}>
        {summary.slice(0,200).map(r=><option key={r.Ticker} value={r.Ticker}>{r["証券コード"]} {r["銘柄名"]}</option>)}
      </select><button className={`mini watchTextButton ${watched ? "watched" : ""}`} onClick={()=>!watched && addWatch(target)} disabled={watched}>
        <Star size={16} fill={watched ? "currentColor" : "none"}/>{watched ? "WATCHLIST登録済み" : "ウォッチ追加"}
      </button></div>
      <div className="chartLegend">
        <span><i className="legendCandle"></i>ローソク足</span>
        <span><i className="legendMa20"></i>MA20</span>
        <span><i className="legendMa60"></i>MA60</span>
        <span className="chartSource">DATA: {chartInfo?.source || "—"}</span>
      </div>
      <div className="chartBox proChart">{rows.length?<FinancialChart rows={rows}/>:<div className="chartEmpty"><b>チャートデータなし</b><small>{chartInfo?.reason || "株価データを取得できませんでした"}</small></div>}</div>
    </Panel>
  </>
}

function Panel({title,children}){return <section className="panel"><h2>{title}</h2>{children}</section>}
function Badge({text}){return <span className="badge">{text || "—"}</span>}
function StatusBadge({text}){
  const v=String(text||"");
  const tone=v.includes("理想")?"gold":v.includes("ENTRY")?"fire":v.includes("WATCH")?"watch":v.includes("WAIT")?"wait":"muted";
  return <span className={`statusBadge ${tone}`}>{v||"—"}</span>
}

createRoot(document.getElementById("root")).render(<App />);
