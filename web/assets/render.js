// 화면 그리기 도우미: 짧은 마크다운, 출처 표시, 순위 눈금, 타일 지도, 차트, 표.
(function () {
  const R = {};
  const SVGNS = "http://www.w3.org/2000/svg";
  const STEPS = ["#E6EDF7", "#BCCDE8", "#87A5D6", "#4E76BE", "#224DA0"];
  const HERE = "#E0640E";

  R.datasets = {};          // id -> {name, period, source_kind}
  const SHORT = {
    "hub:583": "유동인구", "hub:793": "상가 정보", "hub:748": "상권분석 매쉬업", "hub:502": "시간대 카드 이용",
    "hub:504": "요일 카드 이용", "hub:495": "메뉴 매출", "hub:613": "정류소 승차", "hub:612": "정류소 정보",
    "hub:512": "시간대 버스 승차"
  };

  R.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  };
  R.el = function (tag, attrs, html) {
    const e = document.createElement(tag);
    for (const k in attrs || {}) {
      if (k === "class") e.className = attrs[k];
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    if (html != null) e.innerHTML = html;
    return e;
  };
  function svg(tag, attrs) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    return e;
  }

  R.isCenter = id => /^center:/.test(String(id || ""));
  R.srcName = function (id) {
    if (SHORT[id]) return SHORT[id];
    const d = R.datasets[id];
    if (d && d.name) return (R.isCenter(id) ? "센터 " : "") + d.name.replace(/_v\d+$/, "").replace(/^\d-\d+\s*/, "");
    if (R.isCenter(id)) return "센터 " + id.slice(7);
    return id;
  };
  R.srcFull = function (id) {
    const d = R.datasets[id];
    const who = R.isCenter(id) ? "제주빅데이터센터" : "제주데이터허브";
    return d ? who + " · " + d.name + (d.period ? " · " + d.period : "") : who + " · " + id;
  };
  R.srcChip = function (id) {
    return '<span class="src' + (R.isCenter(id) ? " center" : "") + '" title="' + R.esc(R.srcFull(id)) + '">' + R.esc(R.srcName(id)) + "</span>";
  };
  R.chipify = function (html) {
    return html.replace(/\b(hub:\d+|center:\d-\d+)\b/g, m => R.srcChip(m));
  };

  // 짧은 마크다운(답변 표시용)
  R.md = function (text) {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    const out = [];
    let list = null, table = null, para = [];
    const inline = s => R.chipify(R.esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>"));
    const flushPara = () => { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } };
    const flushList = () => { if (list) { out.push("<" + list.tag + ">" + list.items.map(i => "<li>" + inline(i) + "</li>").join("") + "</" + list.tag + ">"); list = null; } };
    const flushTable = () => {
      if (table) {
        const rows = table.filter(r => !/^\s*\|?\s*:?-{2,}/.test(r));
        const cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
        const head = cells(rows[0]);
        const body = rows.slice(1).map(cells);
        out.push('<table><thead><tr>' + head.map(h => "<th>" + inline(h) + "</th>").join("") + "</tr></thead><tbody>" +
          body.map(r => "<tr>" + r.map(c => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") + "</tbody></table>");
        table = null;
      }
    };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (/^\s*\|.*\|\s*$/.test(line)) { flushPara(); flushList(); (table = table || []).push(line); continue; }
      flushTable();
      let m;
      if (!line.trim()) { flushPara(); flushList(); continue; }
      if ((m = line.match(/^#{1,6}\s+(.*)/))) { flushPara(); flushList(); out.push("<h4>" + inline(m[1]) + "</h4>"); continue; }
      if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { flushPara(); if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; } list.items.push(m[1]); continue; }
      if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flushPara(); if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; } list.items.push(m[1]); continue; }
      if ((m = line.match(/^\*\*([^*]+)\*\*\s*$/)) && para.length === 0) { flushList(); out.push("<h4>" + inline(m[1]) + "</h4>"); continue; }
      flushList();
      para.push(line);
    }
    flushPara(); flushList(); flushTable();
    return out.join("");
  };

  R.num = function (v, digits) {
    if (v == null || v === "" || v === "-") return "-";
    const n = typeof v === "number" ? v : Number(String(v).replace(/,/g, ""));
    if (!isFinite(n)) return R.esc(v);
    return n.toLocaleString("ko-KR", { maximumFractionDigits: digits == null ? (Math.abs(n) >= 100 ? 0 : 1) : digits });
  };
  R.compact = function (n) {
    const a = Math.abs(n);
    if (a >= 1e8) return R.num(n / 1e8, 1) + "억";
    if (a >= 1e4) return R.num(n / 1e4, a >= 1e6 ? 0 : 1) + "만";
    return R.num(n);
  };

  // 43칸 순위 눈금
  R.ranks = function (items) {
    const box = R.el("div", { class: "ranks" });
    for (const it of items) {
      const count = it.count || 43, rank = it.rank || Math.round((it.percentile || 0) / 100 * count) || 1;
      const warn = /^주의/.test(it.tone || "");
      const row = R.el("div", { class: "rank-row" });
      row.innerHTML =
        '<div class="rank-label">' + R.esc(it.metric) + ' <span class="rank-tone' + (warn ? " warn" : "") + '">' + R.esc(it.tone || "") + "</span></div>" +
        '<div class="rank-pos"><b>' + rank + "<small>위</small></b> / " + count + "곳</div>";
      const track = R.el("div", { class: "track", role: "img", "aria-label": it.metric + " " + count + "곳 중 " + rank + "위" });
      const cells = R.el("div", { class: "track-cells" });
      for (let i = 1; i <= count; i++) cells.appendChild(R.el("i", i <= Math.ceil(count / 3) ? { class: "top3" } : {}));
      track.appendChild(cells);
      const mark = R.el("span", { class: "track-mark" });
      mark.style.left = ((rank - 0.5) / count * 100) + "%";
      track.appendChild(mark);
      row.appendChild(track);
      const ends = R.el("div", { class: "track-ends" }, "<span>" + R.esc(it.key === "stores" ? "가게 많음" : "높음") + "</span><span>" + R.esc(it.key === "stores" ? "가게 적음" : "낮음") + "</span>");
      row.appendChild(ends);
      box.appendChild(row);
    }
    return box;
  };

  // 제주 타일 지도
  R.map = function (opts) {
    const T = window.JEJU_TILES, cell = 46, gap = 4;
    const values = opts.values || {};
    const vals = Object.values(values).filter(v => typeof v === "number").sort((a, b) => a - b);
    const q = k => vals.length ? vals[Math.min(vals.length - 1, Math.floor(vals.length * k))] : 0;
    const cuts = [q(.2), q(.4), q(.6), q(.8)];
    const stepOf = v => (v == null ? -1 : cuts.filter(c => v >= c).length);
    const W = T.cols * cell, H = T.rows * cell;
    const s = svg("svg", { viewBox: (-gap) + " " + (-gap) + " " + (W + gap * 2) + " " + (H + gap * 2), role: "img", "aria-label": opts.label || "제주 읍면동 지도" });
    const here = new Set(opts.highlight || []);
    s.appendChild(svg("path", { class: "island", d: "M " + (2 * cell - 14) + " " + (2.5 * cell) + " C " + (2 * cell) + " " + (0.4 * cell) + ", " + (8 * cell) + " " + (-0.5 * cell) + ", " + (13 * cell) + " " + (0.4 * cell) +
      " C " + (16.5 * cell) + " " + (1 * cell) + ", " + (17.4 * cell) + " " + (2.6 * cell) + ", " + (15.6 * cell) + " " + (4.2 * cell) +
      " C " + (13 * cell) + " " + (5.6 * cell) + ", " + (5 * cell) + " " + (5.5 * cell) + ", " + (3 * cell) + " " + (4.2 * cell) + " C " + (2.2 * cell) + " " + (3.7 * cell) + ", " + (1.9 * cell) + " " + (3.1 * cell) + ", " + (2 * cell - 14) + " " + (2.5 * cell) + " Z" }));
    for (const [name, pos] of Object.entries(T.tiles)) {
      const g = svg("g", { class: "tile" + (here.has(name) ? " here" : ""), tabindex: opts.onPick ? "0" : "-1", "data-name": name });
      const st = stepOf(values[name]);
      const fill = st < 0 ? STEPS[0] : STEPS[st];
      const r = svg("rect", { x: pos[0] * cell, y: pos[1] * cell, width: cell - gap, height: cell - gap, rx: 2, fill: fill });
      const t = svg("text", { x: pos[0] * cell + (cell - gap) / 2, y: pos[1] * cell + (cell - gap) / 2 + 4, "text-anchor": "middle", fill: st >= 3 ? "#fff" : "#222222" });
      t.textContent = name.replace(/(동|읍|면)$/, "");
      const title = svg("title");
      title.textContent = name + (values[name] != null ? " · " + R.num(values[name]) : "");
      g.append(title, r, t);
      if (opts.onPick) {
        g.addEventListener("click", () => opts.onPick(name));
        g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); opts.onPick(name); } });
      }
      s.appendChild(g);
    }
    const wrap = R.el("div", { class: "map" });
    wrap.appendChild(s);
    if (opts.legend) {
      const lg = R.el("div", { class: "legend" });
      lg.innerHTML = "<span>" + R.esc(opts.legend[0]) + "</span>" + STEPS.map(c => '<i style="background:' + c + '"></i>').join("") +
        "<span>" + R.esc(opts.legend[1]) + '</span><i class="here-key"></i><span>' + R.esc(opts.hereLabel || "선택한 동네") + "</span>";
      wrap.appendChild(lg);
    }
    return wrap;
  };

  // 지역 비교: 지표마다 막대 한 쌍
  R.compare = function (art) {
    const box = el2("div", "cmp");
    for (const m of art.metrics || []) {
      const vals = m.values.map(v => (typeof v === "number" ? v : null));
      const max = Math.max(...vals.filter(v => v != null), 1);
      const best = Math.max(...vals.filter(v => v != null));
      const row = el2("div", "cmp-row");
      row.appendChild(R.el("div", { class: "cmp-label" }, R.esc(m.label) + " " + R.srcChip(m.source)));
      art.regions.forEach((name, i) => {
        const v = vals[i];
        const line = el2("div", "cmp-line" + (v != null && v === best && vals.length > 1 ? " top" : ""));
        line.innerHTML = '<span class="cmp-name">' + R.esc(name) + '</span><span class="cmp-bar"><i style="transform:scaleX(' + (v == null ? 0 : v / max) + ')"></i></span>' +
          '<span class="cmp-val">' + (v == null ? "자료 없음" : (m.unit === "허브 집계값" ? R.compact(v) : R.num(v) + m.unit)) + "</span>";
        row.appendChild(line);
      });
      box.appendChild(row);
    }
    return box;
  };
  function el2(tag, cls) { return R.el(tag, { class: cls }); }

  // 차트
  R.chart = function (art, hereNames) {
    const pts = ((art.series || [])[0] || {}).points || [];
    const here = new Set(hereNames || []);
    if (art.kind === "line") return lineChart(pts, art.unit);
    if (art.kind === "donut" || art.kind === "stacked") return stackChart(pts);
    return barChart(pts, art.unit, here);
  };

  function lineChart(pts, unit) {
    const W = 560, H = 220, L = 52, B = 28, T = 16, Rr = 16;
    const vs = pts.map(p => p.value);
    let lo = Math.min(...vs), hi = Math.max(...vs);
    const pad = (hi - lo) * 0.15 || hi * 0.05 || 1; lo -= pad; hi += pad;
    const x = i => L + (W - L - Rr) * (pts.length === 1 ? 0.5 : i / (pts.length - 1));
    const y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
    const s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img" });
    for (let k = 0; k <= 3; k++) {
      const v = lo + (hi - lo) * k / 3, yy = y(v);
      s.appendChild(svg("line", { x1: L, x2: W - Rr, y1: yy, y2: yy, class: "grid" }));
      const t = svg("text", { x: L - 8, y: yy + 4, "text-anchor": "end", class: "axis" }); t.textContent = R.compact(v); s.appendChild(t);
    }
    pts.forEach((p, i) => {
      if (i % 2 && pts.length > 8 && i !== pts.length - 1) return;
      const t = svg("text", { x: x(i), y: H - 6, "text-anchor": "middle", class: "axis" });
      t.textContent = String(p.name).replace(/^20(\d\d)-(\d\d)$/, (m, a, b) => (i === 0 || b === "01" ? a + "." : "") + Number(b) + "월");
      s.appendChild(t);
    });
    s.appendChild(svg("polyline", { points: pts.map((p, i) => x(i) + "," + y(p.value)).join(" "), fill: "none", stroke: "#224DA0", "stroke-width": 2.5, "stroke-linejoin": "round" }));
    pts.forEach((p, i) => {
      const last = i === pts.length - 1;
      s.appendChild(svg("circle", { cx: x(i), cy: y(p.value), r: last ? 5 : 3, fill: last ? HERE : "#224DA0" }));
    });
    const lp = pts[pts.length - 1];
    if (lp) { const t = svg("text", { x: x(pts.length - 1), y: y(lp.value) - 10, "text-anchor": "end", class: "val" }); t.textContent = R.compact(lp.value); s.appendChild(t); }
    const wrap = R.el("div", { class: "chart" }); wrap.appendChild(s); return wrap;
  }

  function barChart(pts, unit, here) {
    const rowH = 30, W = 560, L = 76, Rr = 64;
    const H = pts.length * rowH + 4;
    const max = Math.max(...pts.map(p => p.value || 0), 1);
    const s = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img" });
    pts.forEach((p, i) => {
      const y = i * rowH + 4, w = (W - L - Rr) * (p.value || 0) / max;
      const isHere = here.has(p.name);
      const lab = svg("text", { x: L - 10, y: y + 15, "text-anchor": "end", class: "axis" }); lab.textContent = p.name;
      if (isHere) { lab.setAttribute("fill", "#A9480A"); lab.setAttribute("font-weight", "700"); }
      s.appendChild(lab);
      s.appendChild(svg("rect", { x: L, y: y, width: Math.max(2, w), height: rowH - 10, rx: 0, fill: isHere ? HERE : "#87A5D6" }));
      const v = svg("text", { x: L + w + 8, y: y + 15, class: "val" }); v.textContent = p.value == null ? "가림" : R.num(p.value) + (unit === "%" ? "%" : unit === "개" ? "개" : ""); s.appendChild(v);
    });
    const wrap = R.el("div", { class: "chart" }); wrap.appendChild(s); return wrap;
  }

  function stackChart(pts) {
    const total = pts.reduce((a, p) => a + (p.value || 0), 0) || 1;
    const shades = ["#224DA0", "#4E76BE", "#87A5D6", "#BCCDE8", "#D4DFEF", "#E6EDF7"];
    const wrap = R.el("div", { class: "chart" });
    const bar = R.el("div", { class: "stack", role: "img" });
    const keys = R.el("div", { class: "stack-keys" });
    pts.forEach((p, i) => {
      const share = (p.value || 0) / total * 100, c = shades[Math.min(i, shades.length - 1)];
      const seg = R.el("span", {}, share >= 9 ? R.num(share, 0) + "%" : "");
      seg.style.flex = String(Math.max(share, 0.4)); seg.style.background = c; if (i >= 2) seg.style.color = "#222222";
      bar.appendChild(seg);
      keys.appendChild(R.el("span", {}, '<i style="background:' + c + '"></i>' + R.esc(p.name) + " " + R.num(share, 1) + "%"));
    });
    wrap.append(bar, keys); return wrap;
  }

  // 표
  R.table = function (art, hereNames) {
    const here = new Set(hereNames || []);
    const cols = art.columns || [];
    const srcCol = cols.findIndex(c => /출처/.test(c));
    const textCols = new Set(cols.map((c, i) => /기간|연도|단위|지역|지표|데이터|제공|구분/.test(c) ? i : -1).filter(i => i >= 0));
    const wrap = R.el("div", { class: "tbl-wrap" });
    let h = '<table class="tbl"><thead><tr>' + cols.map(c => "<th>" + R.esc(c) + "</th>").join("") + "</tr></thead><tbody>";
    for (const row of art.rows || []) {
      h += '<tr class="' + (here.has(row[0]) ? "here" : "") + '">';
      row.forEach((v, i) => {
        if (i === srcCol) {
          const ids = String(v).match(/\b(hub:\d+|center:\d-\d+)\b/g);
          h += "<td>" + (ids ? ids.map(R.srcChip).join(" ") : R.esc(v)) + "</td>"; return;
        }
        if (textCols.has(i) && v !== null) { h += "<td>" + R.esc(v) + "</td>"; return; }
        const isNum = typeof v === "number" || /^-?[\d,]+(\.\d+)?$/.test(String(v));
        if (v === null || v === "*" || v === "가림") { h += '<td class="num hidden-cell" title="반출 기준으로 가림">가림</td>'; return; }
        h += isNum ? '<td class="num">' + R.num(v) + "</td>" : "<td>" + R.chipify(R.esc(v)) + "</td>";
      });
      h += "</tr>";
    }
    wrap.innerHTML = h + "</tbody></table>";
    return wrap;
  };

  window.R = R;
})();
