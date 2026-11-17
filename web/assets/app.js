(function () {
  const $ = id => document.getElementById(id);
  const { el, esc } = R;
  const thread = $("thread"), board = $("board"), q = $("q"), send = $("send");
  const sessionId = "s" + Date.now().toString(36);
  const TOOL = {
    resolve_region: "동네 확인", resolve_industry: "업종 확인", list_datasets: "쓸 데이터 찾기",
    profile_dataset: "데이터 살펴보기", query_table: "표 조회", diagnose_area: "상권 진단",
    compare_areas: "동네 비교", trend: "추이 계산", make_chart: "차트 만들기",
    export_check: "반출 사전 점검", build_report: "보고서 정리"
  };
  const EXAMPLES = ["노형동에서 카페 창업하면 어때?", "애월읍과 한림읍 한식당 비교해 줘", "대정읍 편의점은 언제 손님이 많아?"];
  let industries = [], busy = false, pick = { region: null, industry: null };

  async function api(path, opts) {
    const r = await fetch(path, opts);
    const ct = r.headers.get("content-type") || "";
    const body = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((body && body.message) || ("요청 실패 " + r.status));
    return body;
  }

  let facts = null;
  function drawFacts() {
    const ul = $("facts");
    if (!ul || !facts) return;
    ul.innerHTML = facts.map(f => "<li><span>" + esc(f[0]) + "</span><b>" + esc(f[1]) + "</b></li>").join("");
  }

  // 상단 상태
  async function loadStatus() {
    try {
      const s = await api("/api/status");
      for (const d of s.datasets || []) R.datasets[d.id] = d;
      const up = await api("/api/uploads").catch(() => ({ uploads: [] }));
      for (const u of up.uploads || []) R.datasets[u.dataset_id] = { name: u.name, period: u.period, source_kind: "center" };
      const hubN = (s.datasets || []).filter(d => d.source_kind === "hub" && d.available).length;
      const llm = s.llm || {};
      const centerN = (up.uploads || []).length;
      $("status").innerHTML =
        "<span><span class=\"dot" + (llm.configured ? " on" : "") + "\"></span>" +
        (llm.configured ? "AI 모델 <b>" + esc(llm.model || "연결됨") + "</b>" : "<b>규칙 모드</b> (AI 키 없음)") + "</span>" +
        "<span>공개 데이터 <b>" + hubN + "종</b></span>" +
        "<span>센터 데이터 <b>" + centerN + "건</b></span>";
      facts = [["제주데이터허브 공개 데이터", hubN + "종"], ["제주빅데이터센터 데이터(올린 것)", centerN + "건"], ["읍면동", "43곳"]];
      drawFacts();
    } catch (e) {
      $("status").textContent = "서버에 연결하지 못했습니다";
    }
  }

  // 왼쪽 첫 안내
  function intro() {
    const box = el("div", { class: "intro" });
    const exBox = el("div");
    exBox.appendChild(el("h2", { class: "side-title" }, "이렇게 물어보세요"));
    const ex = el("ul", { class: "examples" });
    for (const t of EXAMPLES) {
      const li = el("li");
      li.appendChild(el("button", { type: "button", class: "example", onclick: () => ask(t) }, '<span class="qa-mark" aria-hidden="true">Q</span>' + esc(t)));
      ex.appendChild(li);
    }
    exBox.appendChild(ex);
    const factBox = el("div");
    factBox.appendChild(el("h2", { class: "side-title" }, "쓸 수 있는 데이터"));
    factBox.appendChild(el("ul", { class: "facts", id: "facts" }));
    box.append(exBox, factBox);
    thread.appendChild(box);
    drawFacts();
  }

  // 첫 결과 판: 지도에서 고르기
  function pickBoard() {
    board.innerHTML = "";
    const inner = el("div", { class: "board-inner pick fade" });
    inner.appendChild(el("h2", {}, "어느 동네, 어떤 가게인가요?"));
    const mapPanel = el("div", { class: "panel" });
    mapPanel.appendChild(el("div", { class: "pick-step" }, '<span class="qa-mark" aria-hidden="true">1</span>동네를 누르세요'));
    const mapSlot = el("div");
    mapPanel.appendChild(mapSlot);
    const choose = n => { pick.region = n; drawMap(); drawSel(); selBox.value = n; };
    const drawMap = () => { mapSlot.innerHTML = ""; mapSlot.appendChild(R.map({ highlight: pick.region ? [pick.region] : [], onPick: choose })); };
    const selBox = el("select", { class: "region-select", "aria-label": "동네" });
    selBox.innerHTML = '<option value="">동네 고르기</option>' + Object.keys(window.JEJU_TILES.tiles).sort((a, b) => a.localeCompare(b, "ko")).map(n => '<option>' + esc(n) + "</option>").join("");
    selBox.addEventListener("change", () => selBox.value && choose(selBox.value));
    mapPanel.appendChild(selBox);
    inner.appendChild(mapPanel);
    const indPanel = el("div", { class: "panel" });
    indPanel.appendChild(el("div", { class: "pick-step" }, '<span class="qa-mark" aria-hidden="true">2</span>업종을 고르세요'));
    const chips = el("div", { class: "choices" });
    for (const ind of industries) {
      const c = el("button", { type: "button", class: "chip", "aria-pressed": "false" }, esc(ind.name));
      c.addEventListener("click", () => {
        pick.industry = ind.name;
        chips.querySelectorAll(".chip").forEach(x => x.setAttribute("aria-pressed", String(x === c)));
        drawSel();
      });
      chips.appendChild(c);
    }
    indPanel.appendChild(chips);
    inner.appendChild(indPanel);
    const go = el("div", { class: "pick-go" });
    const sel = el("div", { class: "pick-sel" });
    const btn = el("button", { type: "button", class: "btn btn-primary" }, "진단하기");
    btn.addEventListener("click", () => ask(pick.region + "에서 " + pick.industry + " 창업하면 어때?"));
    go.append(btn, sel);
    inner.appendChild(go);
    const drawSel = () => {
      btn.disabled = !(pick.region && pick.industry);
      sel.innerHTML = (pick.region ? '<span class="here">' + esc(pick.region) + "</span>" : '<span class="none">동네를 누르세요</span>') +
        " · " + (pick.industry ? esc(pick.industry) : '<span class="none">업종을 고르세요</span>');
    };
    drawMap(); drawSel();
    inner.appendChild(foot());
    board.appendChild(inner);
  }

  function foot() {
    return el("div", { class: "foot" }, "자료: 제주데이터허브(jejudatahub.net) 공개 데이터, 제주빅데이터센터 제공 데이터(올린 경우). 계산은 이 PC에서 하고, 센터 데이터는 AI 모델로 보내지 않습니다.");
  }

  // 질문 보내기
  async function ask(text) {
    text = String(text || "").trim();
    if (!text || busy) return;
    busy = true; send.disabled = true; q.value = "";
    const intro = thread.querySelector(".intro"); if (intro) intro.remove();
    thread.appendChild(el("div", { class: "msg-user" }, '<span class="qa-mark" aria-hidden="true">Q</span><span>' + esc(text) + "</span>"));
    const agent = el("div", { class: "msg-agent" });
    const stepsTitle = el("div", { class: "steps-title" }, "Agent가 한 일");
    const steps = el("ol", { class: "steps" });
    const running = el("li", { class: "step running" }, '<div class="step-name">질문 읽는 중</div>');
    steps.appendChild(running);
    agent.append(stepsTitle, steps);
    thread.appendChild(agent);
    scrollThread();
    let shown = 0;
    try {
      const job = await api("/api/chat", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ session_id: sessionId, message: text }) });
      let st;
      for (;;) {
        st = await api("/api/jobs/" + encodeURIComponent(job.job_id));
        const list = st.steps_so_far || [];
        for (; shown < list.length; shown++) {
          steps.insertBefore(stepItem(list[shown]), running);
          running.querySelector(".step-name").textContent = nextLabel(list[shown].tool);
        }
        scrollThread();
        if (st.status !== "running") break;
        await new Promise(r => setTimeout(r, 350));
      }
      running.remove();
      if (!steps.children.length) { stepsTitle.remove(); steps.remove(); }
      agent.dataset.anchor = "1";
      if (st.status === "error") throw new Error("분석 중 오류가 났습니다. 다시 물어봐 주세요.");
      await showResult(agent, st.result || {}, text);
    } catch (e) {
      running.remove();
      agent.appendChild(el("div", { class: "notice" }, esc(e.message || "요청을 처리하지 못했습니다.")));
    } finally {
      busy = false; send.disabled = false;
      const top = agent.previousElementSibling || agent;
      thread.scrollTop = top.offsetTop - thread.offsetTop - 8;
      q.focus({ preventScroll: true });
    }
  }

  function nextLabel(tool) {
    return tool === "build_report" ? "답변 정리 중" : "다음 단계 고르는 중";
  }

  function stepItem(s) {
    const li = el("li", { class: "step done" });
    const sum = String(s.summary || "").split("\n")[0];
    li.innerHTML = '<div class="step-name">' + esc(TOOL[s.tool] || s.tool) + (s.ms ? '<span class="step-ms">' + (s.ms / 1000).toFixed(1) + "초</span>" : "") + "</div>" +
      (sum && s.tool !== "build_report" ? '<div class="step-sum">' + R.chipify(esc(sum)) + "</div>" : "");
    return li;
  }

  function scrollThread() { thread.scrollTop = thread.scrollHeight; }

  function cleanAnswer(md) {
    return String(md || "").split("\n").filter(l => !/^보고서 `[^`]+`가 만들어졌습니다/.test(l.trim())).join("\n").trim();
  }

  function showResult(agent, res, question) {
    if (res.notice) agent.appendChild(el("div", { class: "notice" }, esc(res.notice)));
    const row = el("div", { class: "answer-row" }, '<span class="qa-mark a" aria-hidden="true">A</span>');
    row.appendChild(el("div", { class: "answer" }, R.md(cleanAnswer(res.answer_markdown))));
    agent.appendChild(row);
    const clar = res.clarification;
    if (clar && (clar.candidates || []).length) {
      const box = el("div", { class: "choices" });
      for (const c of clar.candidates) {
        const isRegion = !!window.JEJU_TILES.tiles[c];
        box.appendChild(el("button", { type: "button", class: "chip", onclick: () => ask(isRegion ? c + " " + question : question + " " + c) }, esc(c)));
      }
      agent.appendChild(box);
      return;
    }
    if ((res.artifacts || []).length) return renderBoard(res, question);
  }

  // 결과 판
  async function renderBoard(res, question) {
    const arts = {};
    for (const a of res.artifacts || []) if (a && a.id) arts[a.id] = a;
    const report = res.report_id ? await api("/api/report/" + encodeURIComponent(res.report_id)).catch(() => null) : null;
    board.innerHTML = "";
    const inner = el("div", { class: "board-inner fade" });

    const std = arts.standing, map = arts.map_floating, cmp = arts.compare_table;
    const here = map && map.highlight ? map.highlight : [];
    let title = report && report.title ? report.title : "분석 결과";
    const m = title.match(/^(.+?)\s+(\S+)\s+상권 진단(?: 보고서)?$/);
    const head = el("div", { class: "head" });
    const printHead = el("div", { class: "print-only" }, '<p class="meta"><b>제주 동네 상권 진단</b> · ' + esc(question) + " · " + esc(new Date().toLocaleString("ko-KR")) + "</p>");
    const cmpM = arts.compare_metrics;
    head.appendChild(el("h2", {}, m && !cmpM ? '<span class="here">' + esc(m[1]) + "</span> " + esc(m[2])
      : cmpM ? cmpM.regions.map(r => '<span class="here">' + esc(r) + "</span>").join(" · ") + " " + esc(cmpM.title.replace(/ 지역 비교$/, ""))
      : esc(cmp ? cmp.title : title)));
    if (std) head.appendChild(el("div", { class: "meta" }, "제주 읍면동 안에서의 위치"));
    const actions = el("div", { class: "actions" });
    actions.appendChild(el("button", { type: "button", class: "btn", onclick: () => window.print() }, "보고서 인쇄"));
    for (const id of ["evidence", "compare_table"]) {
      if (arts[id] && res.report_id) actions.appendChild(el("a", { class: "btn", href: "/api/report/" + encodeURIComponent(res.report_id) + "/csv/" + id, download: "" }, "표 CSV"));
    }
    head.appendChild(actions);
    inner.append(printHead, head);

    if (cmpM) {
      const p = el("div", { class: "panel gap-below" });
      p.appendChild(R.compare(cmpM));
      inner.appendChild(p);
    }
    if (std || map) {
      const top = el("div", { class: "top" + (std ? "" : " solo") });
      if (std) {
        const p = el("div", { class: "panel" });
        p.appendChild(R.ranks(std.items || []));
        p.appendChild(el("div", { class: "panel-foot" }, "1위에 가까울수록 값이 큽니다. " + R.chipify("hub:583") + " " + R.chipify("hub:793") + " " + R.chipify("hub:748") + " " + R.chipify("hub:613")));
        top.appendChild(p);
      }
      if (map) {
        const p = el("div", { class: "panel" });
        p.appendChild(el("div", { class: "panel-title" }, "유동인구 지수"));
        const vals = {};
        for (const pt of ((map.series || [])[0] || {}).points || []) vals[pt.name] = pt.value;
        p.appendChild(R.map({ values: vals, highlight: here, legend: ["낮음", "높음"], label: "읍면동별 유동인구 지수" }));
        p.appendChild(el("div", { class: "panel-foot" }, "허브 집계값이라 사람 수가 아니며, 동네끼리 비교할 때만 씁니다. " + R.chipify("hub:583")));
        top.appendChild(p);
      }
      inner.appendChild(top);
    }

    const chartIds = ["demand_floating", "competition_bar", "time_bar", "price_donut"].concat(cmpM ? [] : ["compare_chart"]).filter(id => arts[id]);
    if (chartIds.length) {
      const sec = el("div", { class: "section" });
      const grid = el("div", { class: "charts" });
      for (const id of chartIds) {
        const a = arts[id];
        const p = el("div", { class: "panel" });
        p.appendChild(el("div", { class: "panel-title" }, esc(chartTitle(a))));
        p.appendChild(R.chart(a, here.concat(cmp ? [] : [])));
        p.appendChild(el("div", { class: "panel-foot" }, "자료 " + String(a.source || "").split(/,\s*/).filter(Boolean).map(R.srcChip).join(" ") + periodOf(a.source)));
        grid.appendChild(p);
      }
      sec.appendChild(grid);
      inner.appendChild(sec);
    }

    const tbl = arts.evidence || cmp;
    if (tbl) {
      const sec = el("div", { class: "section" });
      sec.appendChild(el("h3", {}, cmp ? "비교 표" : "근거 숫자"));
      const p = el("div", { class: "panel" });
      p.appendChild(R.table(cmp ? dropCols(cmp, /출처·기간/) : tbl, here));
      sec.appendChild(p);
      inner.appendChild(sec);
    }

    if (report && (report.limitations || []).length) {
      const sec = el("div", { class: "section" });
      sec.appendChild(el("h3", {}, "이 결과로 알 수 없는 것"));
      sec.appendChild(el("ul", { class: "limits" }, report.limitations.map(l => "<li>" + esc(l) + "</li>").join("")));
      inner.appendChild(sec);
    }

    const ec = report && report.export_check;
    if (ec) inner.appendChild(exportPanel(ec));

    if (report && (report.sources || []).length) {
      const sec = el("div", { class: "section" });
      sec.appendChild(el("h3", {}, "쓴 데이터"));
      const rows = report.sources.map(s => [s.provider || (s.source_kind === "hub" ? "제주데이터허브" : "제주빅데이터센터"), s.dataset, s.period || "", s.source_kind === "hub" ? "공개" : s.source_kind === "center_dummy" ? "센터(가상 예시)" : "센터"]);
      const p = el("div", { class: "panel" });
      p.appendChild(R.table({ columns: ["제공", "데이터", "기간", "구분"], rows: rows }));
      sec.appendChild(p);
      inner.appendChild(sec);
    }
    inner.appendChild(foot());
    board.appendChild(inner);
    board.scrollTop = 0;
    document.body.dataset.ready = String(Date.now());
  }

  function dropCols(t, re) {
    const keep = t.columns.map((c, i) => re.test(c) ? -1 : i).filter(i => i >= 0);
    return Object.assign({}, t, { columns: keep.map(i => t.columns[i]), rows: t.rows.map(r => keep.map(i => r[i])) });
  }
  function chartTitle(a) {
    return String(a.title || "").replace(/\(허브 집계값\)/, "");
  }
  function periodOf(src) {
    const d = R.datasets[String(src || "").split(/,\s*/)[0]];
    return d && d.period ? " · " + esc(d.period) : "";
  }

  function exportPanel(ec) {
    const sec = el("div", { class: "section" });
    sec.appendChild(el("h3", {}, "반출 사전 점검"));
    const p = el("div", { class: "panel export" });
    const n = (ec.suppressed || []).length;
    p.appendChild(el("div", { class: "export-grid" }, n
      ? '<span class="export-big">' + n + "칸 가림</span><span>10 미만이거나, 합계에서 거꾸로 계산되지 않게 함께 가린 칸" + (ec.total_hidden ? " · 합계 비공개" : "") + "</span>"
      : '<span class="export-big">가린 칸 없음</span><span>표의 모든 값이 10 이상입니다' + (ec.total_hidden ? " · 합계 비공개" : "") + "</span>"));
    if (ec.notice) p.appendChild(el("p", { class: "panel-foot" }, esc(ec.notice)));
    if (ec.application_text) {
      p.appendChild(el("label", { class: "panel-title", for: "appText", style: "display:block;margin:16px 0 0" }, "반출 신청서 설명 초안"));
      const ta = el("textarea", { id: "appText", readonly: "" });
      ta.value = ec.application_text;
      p.appendChild(ta);
      const copy = el("button", { type: "button", class: "btn no-print", style: "margin-top:8px" }, "복사");
      copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(ta.value); } catch (e) { ta.select(); document.execCommand("copy"); } copy.textContent = "복사됨"; });
      p.appendChild(copy);
    }
    sec.appendChild(p);
    return sec;
  }

  // 센터 데이터 시트
  const sheet = $("dataSheet");
  $("openData").addEventListener("click", () => { listUploads(); sheet.showModal(); });
  async function listUploads(errors) {
    const ul = $("uploads");
    const data = await api("/api/uploads").catch(() => ({ uploads: [] }));
    ul.innerHTML = "";
    for (const e of errors || []) ul.appendChild(el("li", {}, '<span class="u-name u-err">' + esc(e.file) + "</span><span></span><span class=\"u-meta\">" + esc(e.message) + "</span>"));
    for (const u of data.uploads || []) {
      R.datasets[u.dataset_id] = { name: u.name, period: u.period, source_kind: "center" };
      ul.appendChild(el("li", {}, '<span class="u-name">' + esc(u.name) + "</span>" + (u.demo ? '<span class="u-tag">가상 예시</span>' : "<span></span>") +
        '<span class="u-meta">' + esc(u.filename) + " · " + R.num(u.rows) + "행 · " + esc(u.period || "") + "</span>"));
    }
    loadStatus();
  }
  async function upload(files) {
    const errors = [];
    for (const f of files) {
      try {
        const res = await api("/api/upload", { method: "POST", headers: { "x-filename": encodeURIComponent(f.name), "content-type": "text/csv" }, body: f });
        if (res && res.ok === false) errors.push({ file: f.name, message: res.message || "알아볼 수 없는 파일입니다." });
      } catch (e) { errors.push({ file: f.name, message: e.message }); }
    }
    listUploads(errors);
  }
  $("file").addEventListener("change", e => upload(e.target.files));
  const drop = $("drop");
  drop.addEventListener("dragover", e => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); upload(e.dataTransfer.files); });
  $("loadDemo").addEventListener("click", async () => { await api("/api/demo/load-center", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }); listUploads(); });
  $("clearUploads").addEventListener("click", async () => { await api("/api/uploads", { method: "DELETE" }); listUploads(); });

  // 입력
  $("ask").addEventListener("submit", e => { e.preventDefault(); ask(q.value); });
  q.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(q.value); } });

  (async function start() {
    intro();
    await loadStatus();
    industries = await api("/api/industries").catch(() => []);
    if (!Array.isArray(industries)) industries = industries.industries || [];
    pickBoard();
    const auto = new URLSearchParams(location.search).get("q");
    if (auto) ask(auto);
  })();
})();
