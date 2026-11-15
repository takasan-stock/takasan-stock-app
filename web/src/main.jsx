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
        {page==="home" && <HomePage early={earlyTop} watchBoard={watchBoard} summary={summary} addWatch={addWatch}/>}
        {page==="early" && <EarlyPage rows={earlyTop} addWatch={addWatch}/>}
        {page==="watch" && <WatchPage rows={watchBoard} saveMemo={saveMemo} removeWatch={removeWatch}/>}
        {page==="ranking" && <RankingPage rows={summary} addWatch={addWatch}/>}
        {page==="chart" && <ChartPage summary={summary} ticker={selectedTicker} setTicker={setSelectedTicker} rows={chart} addWatch={addWatch}/>}
      </>}
    </main>
  </div>;
}

function pageTitle(page){return ({home:"TODAY COMMAND CENTER",early:"EARLY HUNTER",watch:"WATCHLIST",ranking:"RANKING",chart:"CHART"})[page]}

function Kpi({label,value,sub}){return <div className="kpi"><span>{label}</span><strong>{value}</strong><small>{sub}</small></div>}

function HomePage({early,watchBoard,summary,addWatch}){
  const idealCount = early.filter(ideal).length;
  const entryCount = watchBoard.filter(r=>r["監視ステータス"]==="🔥 ENTRY確認").length;
  return <>
    <section className="kpis">
      <Kpi label="⭐ 理想本命" value={idealCount} sub="EARLY最優先"/>
      <Kpi label="🔥 ENTRY確認" value={entryCount} sub="ウォッチ昇格"/>
      <Kpi label="🌱 EARLY TOP20" value={early.length} sub="初動候補"/>
      <Kpi label="🏆 総合候補" value={summary.length} sub="全ランキング"/>
    </section>
    <Panel title="今日見るべき EARLY">
      <EarlyTable rows={early.slice(0,10)} addWatch={addWatch}/>
    </Panel>
  </>
}

function EarlyPage({rows,addWatch}){return <Panel title="EARLY最優先 TOP20"><EarlyTable rows={rows} addWatch={addWatch}/></Panel>}

function EarlyTable({rows,addWatch}) {
  return <div className="tableWrap"><table><thead><tr>
    <th>銘柄</th><th>初動</th><th>ランク</th><th>長期ステージ</th><th>RR</th><th>損切り</th><th>反転</th><th>定着</th><th>形状</th><th></th>
  </tr></thead><tbody>{rows.map((r,i)=><tr key={r.Ticker||i} className={ideal(r)?"ideal":""}>
    <td><b>{r["証券コード"]}</b><br/><small>{r["銘柄名"]}</small></td>
    <td>{r["初動優先スコア"]}</td><td><Badge text={r["初動ランク"]}/></td>
    <td>{r["長期ステージ"]}</td><td>{r["RR目安"]}</td><td>{r["実戦想定損切り距離%"]}%</td>
    <td>{r["反転確認"]}</td><td>{r["反転定着"]}</td><td>{r["EARLY形状判定"]}</td>
    <td><button className="mini" onClick={()=>addWatch(r)}>⭐</button></td>
  </tr>)}</tbody></table></div>
}

function WatchPage({rows,saveMemo,removeWatch}) {
  const [drafts,setDrafts]=useState({});
  return <Panel title="🚦 ウォッチ昇格ボード"><div className="tableWrap"><table><thead><tr>
    <th>状態</th><th>銘柄</th><th>ランク</th><th>長期</th><th>RR</th><th>損切り</th><th>メモ</th><th></th>
  </tr></thead><tbody>{rows.map((r,i)=><tr key={r.Ticker||i} className={r["監視ステータス"].includes("理想")?"ideal":""}>
    <td><Badge text={r["監視ステータス"]}/></td>
    <td><b>{r["証券コード"]}</b><br/><small>{r["銘柄名"]}</small></td>
    <td>{r["初動ランク"]||"—"}</td><td>{r["長期ステージ"]||"—"}</td><td>{r["RR目安"]||"—"}</td>
    <td>{r["実戦想定損切り距離%"] ? `${r["実戦想定損切り距離%"]}%` : "—"}</td>
    <td><div className="memoRow"><input value={drafts[r.Ticker] ?? r["メモ"] ?? ""} onChange={e=>setDrafts({...drafts,[r.Ticker]:e.target.value})}/><button className="mini" onClick={()=>saveMemo(r.Ticker,drafts[r.Ticker] ?? r["メモ"] ?? "")}>保存</button></div></td>
    <td><button className="danger" onClick={()=>removeWatch(r.Ticker)}><Trash2 size={16}/></button></td>
  </tr>)}</tbody></table></div></Panel>
}

function RankingPage({rows,addWatch}) {
  const sorted=[...rows].sort((a,b)=>(num(b["総合スコア"])||0)-(num(a["総合スコア"])||0)).slice(0,50);
  return <Panel title="総合ランキング"><div className="tableWrap"><table><thead><tr>
    <th>銘柄</th><th>総合</th><th>ランク</th><th>実戦</th><th>RS</th><th>出来高M</th><th>主力</th><th></th>
  </tr></thead><tbody>{sorted.map((r,i)=><tr key={r.Ticker||i}>
    <td><b>{r["証券コード"]}</b><br/><small>{r["銘柄名"]}</small></td>
    <td>{r["総合スコア"]}</td><td><Badge text={r["総合ランク"]}/></td><td>{r["実戦スコア"]}</td>
    <td>{r["RS Rating"]}</td><td>{r["出来高モメンタム"]}</td><td>{r["主力シグナル"]}</td>
    <td><button className="mini" onClick={()=>addWatch(r)}>⭐</button></td>
  </tr>)}</tbody></table></div></Panel>
}

function ChartPage({summary,ticker,setTicker,rows,addWatch}){
  const target=summary.find(r=>r.Ticker===ticker) || {};
  const pts=rows.map((r,i)=>({x:i,y:num(r.Close)})).filter(p=>p.y!==null);
  const ys=pts.map(p=>p.y); const min=Math.min(...ys,0), max=Math.max(...ys,1);
  const poly=pts.map(p=>`${(p.x/Math.max(pts.length-1,1))*100},${95-((p.y-min)/Math.max(max-min,1))*85}`).join(" ");
  return <><Panel title="チャート">
    <div className="chartControls"><select value={ticker} onChange={e=>setTicker(e.target.value)}>
      {summary.slice(0,200).map(r=><option key={r.Ticker} value={r.Ticker}>{r["証券コード"]} {r["銘柄名"]}</option>)}
    </select><button className="mini" onClick={()=>addWatch(target)}>⭐ ウォッチ追加</button></div>
    <div className="chartBox">{pts.length?<svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points={poly} fill="none" stroke="currentColor" strokeWidth="1.5"/></svg>:<span>チャートデータなし</span>}</div>
  </Panel></>
}

function Panel({title,children}){return <section className="panel"><h2>{title}</h2>{children}</section>}
function Badge({text}){return <span className="badge">{text || "—"}</span>}

createRoot(document.getElementById("root")).render(<App />);
