"""Live monitor for a running baseline_runner. Read-only; stdlib only.

    python crawlers/monitor.py [--port 8765]   ->  http://127.0.0.1:8765
"""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB = os.path.join(ROOT, "data/outputs/baseline_gate_v2/electric_bus/crawl.db")
VN = timezone(timedelta(hours=7))
RANGES = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24)}


def _ts(text):
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=VN)).astimezone(timezone.utc)


def _kv(c, key):
    row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(row[0]) if row else None
    except ValueError:
        return row[0]


def snapshot(rng):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    try:
        now = datetime.now(timezone.utc)
        run = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        rows = [(r[0], r[1], _ts(r[2])) for r in c.execute(
            "SELECT platform, comment_type, crawled_at FROM comments WHERE crawled_at IS NOT NULL")]
        if rng == "run" and run:
            start = _ts(run["started_at"])
        elif rng in RANGES:
            start = now - RANGES[rng]
        else:
            start = min((t for _, _, t in rows), default=now - timedelta(hours=1))
        n = 60
        start = min(start, now - timedelta(minutes=2))
        step = (now - start) / n
        times = [start + step * (i + 1) for i in range(n)]
        series = {p: [sum(1 for q, _, t in rows if q == p and t < start)] * n
                  for p in ("facebook", "youtube")}
        base0 = {p: series[p][0] for p in series}
        for p in series:
            mine = sorted(t for q, _, t in rows if q == p)
            base, j, out = series[p][0], 0, []
            for edge in times:
                while j < len(mine) and mine[j] < start:
                    j += 1
                while j < len(mine) and mine[j] <= edge:
                    j += 1
                    base += 1
                out.append(base)
            series[p] = out
        totals = {}
        for p, kind, _ in rows:
            totals[f"{p}_{kind}"] = totals.get(f"{p}_{kind}", 0) + 1
        recent = {p: sum(1 for q, _, t in rows if q == p and t >= now - timedelta(minutes=15)) * 4
                  for p in ("facebook", "youtube")}
        reasons = {}
        for r in c.execute("SELECT platform, verdict, reason, crawled_at FROM contexts"):
            if r["crawled_at"] and _ts(r["crawled_at"]) < start:
                continue
            key = "accepted" if r["verdict"] == "accept" else (r["reason"] or "?").split(":")[0]
            d = reasons.setdefault(key, {"facebook": 0, "youtube": 0})
            d[r["platform"]] = d.get(r["platform"], 0) + 1
        queue = {}
        for r in c.execute("SELECT platform, status, COUNT(*) n FROM discovery_queue GROUP BY 1,2"):
            queue.setdefault(r["platform"], {})[r["status"]] = r["n"]
        events = [dict(r) for r in c.execute(
            "SELECT ts, kind, platform, substr(detail,1,300) detail FROM events "
            "WHERE ts >= ? ORDER BY id DESC LIMIT 60", (start.isoformat(),))]
        return {
            "now": now.isoformat(), "start": start.isoformat(),
            "run": dict(run) if run else None,
            "status": {p: _kv(c, f"{p}_status") for p in ("facebook", "youtube")},
            "totals": totals, "total": len(rows), "recent_per_hour": recent,
            "times": [t.isoformat() for t in times], "series": series, "base": base0,
            "reasons": reasons, "queue": queue, "events": events,
        }
    finally:
        c.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif url.path == "/api":
            try:
                rng = parse_qs(url.query).get("range", ["run"])[0]
                self._send(200, json.dumps(snapshot(rng)), "application/json")
            except Exception as exc:  # DB briefly locked/missing: let the page keep its last render
                self._send(503, json.dumps({"error": repr(exc)}), "application/json")
        else:
            self._send(404, "not found", "text/plain")


PAGE = r"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crawl Monitor</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#6f6d67;--grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);
--fb:#eb6834;--yt:#2a78d6;--good:#006300;--warn:#8a5a00;--bad:#d03b3b;--hover:rgba(11,11,11,.05)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#a09e96;--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--fb:#d95926;--yt:#3987e5;--good:#0ca30c;--warn:#fab219;--bad:#ec6a6a;--hover:rgba(255,255,255,.07)}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#a09e96;--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--fb:#d95926;--yt:#3987e5;--good:#0ca30c;--warn:#fab219;--bad:#ec6a6a;--hover:rgba(255,255,255,.07)}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 48px}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between}
h1{font-size:18px;margin:0}h2{font-size:14px;margin:0 0 8px}
.sub{color:var(--ink2);font-size:12px}
.live{display:inline-flex;align-items:center;gap:6px}.dot{width:8px;height:8px;border-radius:50%;background:var(--good)}.stale .dot{background:var(--bad)}
.row{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}
.seg button,.btn{font:inherit;color:var(--ink2);background:var(--surface);border:1px solid var(--ring);padding:5px 12px;cursor:pointer}
.seg button:first-child{border-radius:8px 0 0 8px}.seg button:last-child{border-radius:0 8px 8px 0}.seg button+button{border-left:0}
.btn{border-radius:8px}.seg button:hover,.btn:hover{background:var(--hover)}
.seg button[aria-pressed=true]{color:var(--ink);font-weight:600;background:var(--hover)}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:14px 16px;min-width:0}
.big{font-size:30px;font-weight:600;line-height:1.1}.lab{color:var(--ink2);font-size:12px}
.bar{height:8px;background:var(--grid);border-radius:4px;margin:8px 0 4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--yt);border-radius:4px}
.key{display:inline-block;width:14px;height:0;border-top:2px solid;vertical-align:middle;margin-right:6px}
.legend{display:flex;gap:16px;color:var(--ink2);font-size:12px;margin-bottom:6px}
.stat{display:inline-flex;gap:6px;align-items:center;font-weight:600}.ok{color:var(--good)}.wait{color:var(--warn)}.err{color:var(--bad)}
.stack{display:grid;gap:12px;margin-top:12px}.cols{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.chart{position:relative}svg{display:block;width:100%;height:auto;touch-action:none}
svg text{font:11px system-ui,sans-serif;fill:var(--muted)}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:6px 10px;box-shadow:0 4px 14px rgba(0,0,0,.18);font-size:12px;white-space:nowrap;z-index:5;display:none}
.tip b{font-size:14px}.tip div{display:flex;gap:6px;align-items:center}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--grid);vertical-align:top}td:first-child{white-space:nowrap}
th{color:var(--ink2);font-weight:500}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll{max-height:320px;overflow:auto}.det{color:var(--ink2);word-break:break-all}
.rbar{display:grid;grid-template-columns:150px 1fr 54px;gap:8px;align-items:center;margin:6px 0;font-size:12px}
.rbar .t{display:grid;gap:2px}.rbar .t i{display:block;height:8px;border-radius:0 4px 4px 0;min-width:2px}.rbar .t i:hover{filter:brightness(1.15)}
.rbar .l{color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rbar .v{text-align:right;font-variant-numeric:tabular-nums}
select{font:inherit;background:var(--surface);color:var(--ink);border:1px solid var(--ring);border-radius:8px;padding:4px 8px}
.loading{opacity:.6}
</style></head><body><main>
<header><div><h1>Crawl Monitor</h1><div class="sub" id="runline">…</div></div>
<div class="sub live" id="live"><span class="dot"></span><span id="upd">đang tải</span></div></header>
<div class="row"><div class="seg" id="range" role="group" aria-label="Khoảng thời gian">
<button data-r="run" aria-pressed="true">Run này</button><button data-r="1h" aria-pressed="false">1 giờ</button>
<button data-r="6h" aria-pressed="false">6 giờ</button><button data-r="24h" aria-pressed="false">24 giờ</button>
<button data-r="all" aria-pressed="false">Tất cả</button></div></div>
<div class="grid" id="tiles"></div>
<div class="stack">
<div class="card"><h2>Comment mới (từ đầu khoảng)</h2>
<div class="legend"><span><span class="key" style="border-color:var(--fb)"></span>Facebook</span><span><span class="key" style="border-color:var(--yt)"></span>YouTube</span>
<button class="btn" id="tbl" style="margin-left:auto;padding:2px 10px">Xem bảng</button></div>
<div class="chart" id="chart"></div><div id="tblwrap" class="scroll" hidden></div></div>
<div class="cols">
<div class="card"><h2>Bài đã xét theo kết quả gate</h2>
<div class="legend"><span><span class="key" style="border-color:var(--fb)"></span>Facebook</span><span><span class="key" style="border-color:var(--yt)"></span>YouTube</span></div>
<div id="reasons"></div></div>
<div class="card"><h2>Hàng đợi</h2><div id="queue"></div></div></div>
<div class="card"><h2 style="display:flex;gap:8px;align-items:center">Sự kiện <select id="kind" aria-label="Lọc sự kiện"><option value="">tất cả</option></select></h2>
<div class="scroll"><table><thead><tr><th>Giờ (VN)</th><th>Loại</th><th>Nền tảng</th><th>Chi tiết</th></tr></thead><tbody id="ev"></tbody></table></div></div>
</div><div class="tip" id="tip"></div></main>
<script>
const $=s=>document.querySelector(s),NS="http://www.w3.org/2000/svg",TARGET_FALLBACK=5000;
const fmt=n=>Number(n||0).toLocaleString("vi-VN"),css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const hm=(iso,full)=>new Date(iso).toLocaleString("vi-VN",{timeZone:"Asia/Ho_Chi_Minh",hour:"2-digit",minute:"2-digit",...(full?{day:"2-digit",month:"2-digit"}:{})});
let range="run",data=null,kind="",lastOk=0;
function el(tag,attrs={},text){const e=document.createElement(tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(text!=null)e.textContent=text;return e}
function sv(tag,attrs={},text){const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(text!=null)e.textContent=text;return e}
const tip=$("#tip");
function showTip(host,x,y,rows,head){tip.replaceChildren();if(head)tip.append(el("div",{class:"sub"},head));
 for(const r of rows){const d=el("div");d.append(el("span",{class:"key",style:"border-color:"+r.color}),el("b",{},r.value),el("span",{class:"sub"},r.label));tip.append(d)}
 tip.style.display="block";const hb=host.getBoundingClientRect(),pb=tip.offsetParent.getBoundingClientRect();
 let left=x-pb.left+14;if(left+tip.offsetWidth>pb.width)left=x-pb.left-tip.offsetWidth-14;tip.style.left=Math.max(0,left)+"px";tip.style.top=(y-pb.top+14)+"px"}
const hideTip=()=>tip.style.display="none";
function statusView(s){const m={running:["▶","ok","đang chạy"],queue_empty:["✓","ok","hết hàng đợi"],quota_wait:["⏸","wait","chờ quota"],auth_required:["⚠","err","cần đăng nhập"],quarantined:["✕","err","bị cách ly"],idle:["•","wait","nghỉ"]};
 return m[s]||["?","wait",String(s||"không rõ")]}
function tiles(){const d=data,t=d.totals,tar=(d.run&&d.run.target_minimum)||TARGET_FALLBACK,pct=Math.min(100,d.total/tar*100);
 const rate=d.recent_per_hour.facebook+d.recent_per_hour.youtube,left=Math.max(0,tar-d.total);
 const eta=left===0?"đã đạt":rate>0?"~"+(left/rate).toFixed(1)+" giờ nữa":"chưa có tốc độ";
 const box=$("#tiles");box.replaceChildren();
 const c1=el("div",{class:"card"});c1.append(el("div",{class:"lab"},"Tổng comment / mục tiêu"),el("div",{class:"big"},fmt(d.total)+" / "+fmt(tar)));
 const b=el("div",{class:"bar"});b.append(el("i",{style:"width:"+pct+"%"}));c1.append(b,el("div",{class:"lab"},pct.toFixed(0)+"% · "+eta));
 const mk=(label,p,color)=>{const c=el("div",{class:"card"}),s=statusView(d.status[p]);
  c.append(el("div",{class:"lab"},label),el("div",{class:"big",style:"color:var(--ink)"},fmt((d.totals[p+"_top_level"]||0)+(d.totals[p+"_reply"]||0))));
  c.append(el("div",{class:"lab"},fmt(d.totals[p+"_top_level"])+" gốc · "+fmt(d.totals[p+"_reply"])+" trả lời · "+fmt(d.recent_per_hour[p])+"/giờ"));
  const st=el("div",{class:"stat "+s[1]});st.append(el("span",{},s[0]),el("span",{},s[2]));c.append(st);return c};
 box.append(c1,mk("Facebook","facebook"),mk("YouTube","youtube"))}
function chart(){const host=$("#chart");host.querySelector("svg")?.remove();
 const d=data,W=Math.max(320,host.clientWidth||600),H=260,m={l:46,r:12,t:10,b:24},iw=W-m.l-m.r,ih=H-m.t-m.b;
 const xs=d.times.map(t=>+new Date(t)),ser=[{k:"facebook",n:"Facebook",c:css("--fb")},{k:"youtube",n:"YouTube",c:css("--yt")}];
 const dl=k=>d.series[k].map(v=>v-d.base[k]),all=ser.flatMap(s=>dl(s.k));let lo=0,hi=Math.max(...all,1);hi=Math.ceil(hi*1.08);
 const X=t=>m.l+(t-xs[0])/(xs[xs.length-1]-xs[0]||1)*iw,Y=v=>m.t+ih-(v-lo)/(hi-lo)*ih;
 const svg=sv("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":"Comment mới theo thời gian cho Facebook và YouTube"});
 for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,y=Y(v);svg.append(sv("line",{x1:m.l,x2:W-m.r,y1:y,y2:y,stroke:i?css("--grid"):css("--axis"),"stroke-width":1}),sv("text",{x:m.l-6,y:y+4,"text-anchor":"end"},fmt(Math.round(v))))}
 for(let i=0;i<=4;i++){const t=xs[0]+(xs[xs.length-1]-xs[0])*i/4;svg.append(sv("text",{x:X(t),y:H-6,"text-anchor":i==0?"start":i==4?"end":"middle"},hm(new Date(t).toISOString(),(xs[xs.length-1]-xs[0])>864e5)))}
 for(const s of ser){svg.append(sv("polyline",{points:xs.map((t,i)=>X(t)+","+Y(dl(s.k)[i])).join(" "),fill:"none",stroke:s.c,"stroke-width":2,"stroke-linejoin":"round","stroke-linecap":"round"}))}
 const cross=sv("line",{y1:m.t,y2:m.t+ih,stroke:css("--axis"),"stroke-width":1,visibility:"hidden"}),dots=ser.map(s=>sv("circle",{r:4,fill:s.c,stroke:css("--surface"),"stroke-width":2,visibility:"hidden"}));
 svg.append(cross,...dots);
 const hit=sv("rect",{x:m.l,y:m.t,width:iw,height:ih,fill:"transparent",tabindex:0});svg.append(hit);
 const at=(cx,cy)=>{const r=svg.getBoundingClientRect(),px=(cx-r.left)/r.width*W,t=xs[0]+(px-m.l)/iw*(xs[xs.length-1]-xs[0]);
  let bi=0;xs.forEach((x,i)=>{if(Math.abs(x-t)<Math.abs(xs[bi]-t))bi=i});
  cross.setAttribute("x1",X(xs[bi]));cross.setAttribute("x2",X(xs[bi]));cross.setAttribute("visibility","visible");
  ser.forEach((s,i)=>{dots[i].setAttribute("cx",X(xs[bi]));dots[i].setAttribute("cy",Y(dl(s.k)[bi]));dots[i].setAttribute("visibility","visible")});
  showTip(host,cx,cy,ser.map(s=>({color:s.c,value:"+"+fmt(dl(s.k)[bi])+" (tổng "+fmt(d.series[s.k][bi])+")",label:s.n})),hm(d.times[bi],true))};
 hit.addEventListener("pointermove",e=>at(e.clientX,e.clientY));
 hit.addEventListener("pointerleave",()=>{hideTip();cross.setAttribute("visibility","hidden");dots.forEach(x=>x.setAttribute("visibility","hidden"))});
 host.append(svg);
 const tb=el("table"),th=el("thead"),hr=el("tr");th.append(hr);tb.append(th);
 ["Giờ (VN)","Facebook","YouTube"].forEach((h,i)=>hr.append(el("th",i?{class:"n"}:{},h)));
 const body=el("tbody");d.times.forEach((t,i)=>{const r=el("tr");r.append(el("td",{},hm(t,true)),el("td",{class:"n"},fmt(d.series.facebook[i])),el("td",{class:"n"},fmt(d.series.youtube[i])));body.append(r)});
 tb.append(body);$("#tblwrap").replaceChildren(tb)}
function reasons(){const box=$("#reasons");box.replaceChildren();const rs=Object.entries(data.reasons).sort((a,b)=>(b[1].facebook+b[1].youtube)-(a[1].facebook+a[1].youtube));
 if(!rs.length){box.append(el("div",{class:"sub"},"Chưa có bài nào trong khoảng này."));return}
 const mx=Math.max(...rs.flatMap(([,v])=>[v.facebook,v.youtube]),1);
 const names={accepted:"✓ chấp nhận",date_unresolved:"không đọc được ngày",out_of_window:"ngoài cửa sổ ngày",missing_group:"thiếu từ khoá xe buýt điện",not_vietnam:"không phải VN",metadata_unresolved:"không đọc được nội dung"};
 for(const [k,v] of rs){const r=el("div",{class:"rbar"}),t=el("div",{class:"t"});
  r.append(el("div",{class:"l",title:k},names[k]||k),t,el("div",{class:"v"},fmt(v.facebook)+" · "+fmt(v.youtube)));
  for(const [p,c,n] of [["facebook","--fb","Facebook"],["youtube","--yt","YouTube"]]){const b=el("i",{style:"width:"+Math.max(v[p]?1:0,v[p]/mx*100)+"%;background:var("+c+")"});
   b.addEventListener("pointermove",e=>showTip(b,e.clientX,e.clientY,[{color:css(c),value:fmt(v[p]),label:n}],names[k]||k));b.addEventListener("pointerleave",hideTip);t.append(b)}
  box.append(r)}}
function queue(){const box=$("#queue");box.replaceChildren();const tb=el("table"),th=el("thead"),h=el("tr");th.append(h);tb.append(th);["Nền tảng","Trạng thái","Số task"].forEach((x,i)=>h.append(el("th",i==2?{class:"n"}:{},x)));
 const body=el("tbody");for(const p of Object.keys(data.queue))for(const [s,n] of Object.entries(data.queue[p])){const r=el("tr");r.append(el("td",{},p),el("td",{},s),el("td",{class:"n"},fmt(n)));body.append(r)}
 tb.append(body);box.append(tb)}
function events(){const sel=$("#kind"),kinds=[...new Set(data.events.map(e=>e.kind))].sort();
 if(kinds.join()!==[...sel.options].slice(1).map(o=>o.value).join()){sel.replaceChildren(new Option("tất cả",""),...kinds.map(k=>new Option(k,k)));sel.value=kinds.includes(kind)?kind:""}
 const body=$("#ev");body.replaceChildren();for(const e of data.events.filter(e=>!kind||e.kind===kind)){const r=el("tr");r.append(el("td",{},hm(e.ts,true)),el("td",{},e.kind),el("td",{},e.platform||"—"),el("td",{class:"det"},e.detail||""));body.append(r)}}
function render(){if(!data||tip.style.display==="block")return;const r=data.run;$("#runline").textContent=r?`${r.run_id} · bắt đầu ${hm(r.started_at,true)} · hạn ${hm(r.deadline_at,true)} (giờ VN)`:"chưa có run";
 tiles();chart();reasons();queue();events()}
async function poll(){try{const res=await fetch("/api?range="+range,{cache:"no-store"});if(!res.ok)throw 0;data=await res.json();lastOk=Date.now();document.body.classList.remove("loading");render()}catch(e){}
 const age=(Date.now()-lastOk)/1000,live=$("#live");live.classList.toggle("stale",age>15);$("#upd").textContent=lastOk?(age>15?"mất kết nối "+Math.round(age)+"s":"cập nhật "+hm(new Date(lastOk).toISOString())):"đang tải"}
$("#range").addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;range=b.dataset.r;document.querySelectorAll("#range button").forEach(x=>x.setAttribute("aria-pressed",x===b));document.body.classList.add("loading");poll()});
$("#kind").addEventListener("change",e=>{kind=e.target.value;if(data)events()});
$("#tbl").addEventListener("click",()=>{const w=$("#tblwrap"),show=w.hidden;w.hidden=!show;$("#chart").hidden=show;$("#tbl").textContent=show?"Xem biểu đồ":"Xem bảng"});
new ResizeObserver(()=>{if(data&&!$("#chart").hidden)chart()}).observe($("#chart"));
poll();setInterval(poll,5000);
</script></body></html>
"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    print(f"monitor http://127.0.0.1:{args.port}  (db: {DB})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
