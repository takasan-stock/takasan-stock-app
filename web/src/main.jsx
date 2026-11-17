import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, BarChart3, Home, Radar, RefreshCw, Star, Trash2 } from "lucide-react";
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
      .then((r) => setChart(r.rows || []))
      .catch(() => setChart([]));
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
        {page==="chart" && <ChartPage summary={summary} ticker={selectedTicker} setTicker={setSelectedTicker} rows={chart} watchBoard={watchBoard} addWatch={addWatch}/>}
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
  const activeCount=rows.filter(r=>["⭐ 理想本命","🔥 ENTRY確認","👀 WATCH"].includes(r["監視ステータス"])).length;
  return <>
    <section className="pageSummary">
      <Kpi label="⭐ 理想本命" value={idealCount} sub="即確認"/>
      <Kpi label="🔥 ENTRY確認" value={entryCount} sub="タイミング確認"/>
      <Kpi label="👀 継続監視" value={activeCount} sub="アクティブ"/>
    </section>
    <Panel title="🚦 ウォッチ昇格ボード"><div className="tableWrap"><table><thead><tr>
      <th>状態</th><th>銘柄</th><th>ランク</th><th>長期</th><th>RR</th><th>損切り</th><th>次アクション</th><th>メモ</th><th></th>
    </tr></thead><tbody>{rows.map((r,i)=><tr key={r.Ticker||i} className={r["監視ステータス"].includes("理想")?"ideal":""}>
      <td><StatusBadge text={r["監視ステータス"]}/></td>
      <td><b>{r["証券コード"]}</b><br/><small>{r["銘柄名"]}</small></td>
      <td>{r["初動ランク"]||"—"}</td><td>{r["長期ステージ"]||"—"}</td><td>{r["RR目安"]||"—"}</td>
      <td>{r["実戦想定損切り距離%"] ? `${r["実戦想定損切り距離%"]}%` : "—"}</td>
      <td><span className="nextAction">{r["次アクション"]||"—"}</span></td>
      <td><div className="memoRow"><input value={drafts[r.Ticker] ?? r["メモ"] ?? ""} onChange={e=>setDrafts({...drafts,[r.Ticker]:e.target.value})}/><button className="mini" onClick={()=>saveMemo(r.Ticker,drafts[r.Ticker] ?? r["メモ"] ?? "")}>保存</button></div></td>
      <td><button className="danger" title="WATCHLISTから削除" onClick={()=>removeWatch(r.Ticker)}><Trash2 size={16}/></button></td>
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

function ChartPage({summary,ticker,setTicker,rows,watchBoard,addWatch}){
  const target=summary.find(r=>r.Ticker===ticker) || {};
  const watched=isWatched(target, watchBoard);
  const pts=rows.map((r,i)=>({x:i,y:num(r.Close)})).filter(p=>p.y!==null);
  const ys=pts.map(p=>p.y); const min=ys.length?Math.min(...ys):null, max=ys.length?Math.max(...ys):null;
  const last=ys.length?ys[ys.length-1]:null;
  const poly=pts.map(p=>`${(p.x/Math.max(pts.length-1,1))*100},${95-((p.y-min)/Math.max((max-min),1))*85}`).join(" ");
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
        <span>6か月終値</span>
        <strong>{last!==null ? last.toLocaleString(undefined,{maximumFractionDigits:2}) : "—"}</strong>
        <small>{min!==null && max!==null ? `Range ${min.toLocaleString(undefined,{maximumFractionDigits:2})} - ${max.toLocaleString(undefined,{maximumFractionDigits:2})}` : "データなし"}</small>
      </div>
    </section>
    <Panel title="チャート">
      <div className="chartControls"><select value={ticker} onChange={e=>setTicker(e.target.value)}>
        {summary.slice(0,200).map(r=><option key={r.Ticker} value={r.Ticker}>{r["証券コード"]} {r["銘柄名"]}</option>)}
      </select><button className={`mini watchTextButton ${watched ? "watched" : ""}`} onClick={()=>!watched && addWatch(target)} disabled={watched}>
        <Star size={16} fill={watched ? "currentColor" : "none"}/>{watched ? "WATCHLIST登録済み" : "ウォッチ追加"}
      </button></div>
      <div className="chartBox">{pts.length?<svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points={poly} fill="none" stroke="currentColor" strokeWidth="1.5"/></svg>:<span>チャートデータなし</span>}</div>
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
