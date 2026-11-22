/* PIA 2021 Legislative Research Assistant – dependency-free front end (works fully offline). */
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ORD = { first: 1, second: 2, third: 3, fourth: 4, fifth: 5, sixth: 6, seventh: 7, eighth: 8 };
const state = { history: [], busy: false, health: null };

/* ------------------------------------------------------------------ markdown (small, safe) */
function inline(t) {
  t = esc(t);
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>").replace(/`([^`]+)`/g, "<code>$1</code>");
  // citations: [s.104(2)]  [s.318 “petroleum”]  [Seventh Schedule, para. 10(2)]
  t = t.replace(/\[(s\.\s*\d{1,3}(?:\s*\([^)\]]{1,6}\))*(?:\s*(?:“|&quot;)[^”\]]{1,80}(?:”|&quot;))?|(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth) Schedule[^\]]{0,60})\]/g,
    (m, c) => `<span class="cite" data-cite="${esc(c)}" title="Open provision">${esc(c)}</span>`);
  return t;
}
function md(src) {
  const lines = src.replace(/\r/g, "").split("\n"); const out = []; let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*$/.test(l)) { i++; continue; }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)$/))) { out.push(`<h3>${inline(m[2])}</h3>`); i++; continue; }
    if (/^\s*>/.test(l)) { const b = []; while (i < lines.length && /^\s*>/.test(lines[i])) b.push(lines[i++].replace(/^\s*>\s?/, "")); out.push(`<blockquote>${b.map(inline).join("<br>")}</blockquote>`); continue; }
    if (/^\s*\|/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const row = s => s.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      const head = row(l); i += 2; const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(row(lines[i++]));
      out.push(`<table><thead><tr>${head.map(h => `<th>${inline(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`); continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d+[.)]/.test(l); const items = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      out.push(`<${ordered ? "ol" : "ul"}>${items.map(x => `<li>${inline(x)}</li>`).join("")}</${ordered ? "ol" : "ul"}>`); continue;
    }
    const p = []; while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,4}\s|\s*>|\s*\||\s*([-*•]|\d+[.)])\s)/.test(lines[i])) p.push(lines[i++]);
    out.push(`<p>${p.map(inline).join("<br>")}</p>`);
  }
  return out.join("\n");
}

/* ------------------------------------------------------------------ API helpers */
async function jget(u) { const r = await fetch(u); if (!r.ok) throw new Error(await r.text()); return r.json(); }

async function init() {
  try {
    const h = await jget("/api/health"); state.health = h;
    $("#pill-llm").textContent = h.llm ? `LLM: ${h.llm_model}` : "Retrieval-only mode (no LLM key)";
    if (!h.llm) $("#pill-llm").classList.add("off");
    $("#pill-pipe").textContent = `${h.pipeline.dense} · ${h.pipeline.rerank}`;
    $("#facts").innerHTML = [[h.sections, "Sections"], [h.definitions, "Defined terms"], [h.chunks, "Indexed passages"], [9, "Schedules"]].map(([n, l]) => `<div class="fact"><b>${n}</b><span>${l}</span></div>`).join("");
  } catch (e) { $("#pill-llm").textContent = "Backend offline"; $("#pill-llm").classList.add("off"); }
  const s = await jget("/api/samples");
  $("#samples").innerHTML = s.map(g => `<div class="grp">${esc(g.group)}</div>` + g.items.map(q => `<button class="sample">${esc(q)}</button>`).join("")).join("");
  $("#samples").addEventListener("click", e => { const b = e.target.closest(".sample"); if (b) ask(b.textContent); });
  buildTree();
}

async function buildTree() {
  const b = await jget("/api/browse");
  let html = "";
  for (const ch of b.chapters) {
    html += `<details class="ch"><summary>Chapter ${ch.number} – ${esc(ch.title.charAt(0) + ch.title.slice(1).toLowerCase())}</summary>`;
    for (const p of ch.parts) {
      const secs = p.sections.map(s => `<a class="sec" data-n="${s.n}" data-t="${esc((s.n + " " + s.title).toLowerCase())}"><b>${s.n}</b>${esc(s.title)}</a>`).join("");
      html += p.part ? `<details class="pt"><summary>Part ${p.part} – ${esc(p.title.charAt(0) + p.title.slice(1).toLowerCase())}</summary>${secs}</details>` : secs;
    }
    html += "</details>";
  }
  html += `<details class="ch"><summary>Schedules</summary>` + b.schedules.map(sc =>
    `<details class="pt"><summary>${esc(sc.name.replace(" (Explanatory Memorandum)", ""))}${sc.title ? " – " + esc(sc.title.slice(0, 40)) : ""}</summary>` +
    sc.paragraphs.map(p => `<a class="sec" data-sch="${sc.ordinal}" data-para="${p.n ?? ""}" data-t="${esc((sc.name + " " + (p.heading || "") + " " + (p.n ?? "")).toLowerCase())}"><b>${p.n ?? "•"}</b>${esc((p.heading || "").slice(0, 60))}</a>`).join("") + "</details>").join("") + "</details>";
  $("#tree").innerHTML = html;
}
$("#tree").addEventListener("click", e => {
  const a = e.target.closest(".sec"); if (!a) return;
  if (a.dataset.n) openSection(+a.dataset.n); else openSchedule(+a.dataset.sch, a.dataset.para ? +a.dataset.para : null);
});
$("#browse-filter").addEventListener("input", e => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll("#tree .sec").forEach(a => a.style.display = !q || a.dataset.t.includes(q) ? "" : "none");
  document.querySelectorAll("#tree details").forEach(d => { if (q) d.open = true; });
});
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
  $("#tab-ask").classList.toggle("hidden", t.dataset.tab !== "ask"); $("#tab-browse").classList.toggle("hidden", t.dataset.tab !== "browse");
}));

/* ------------------------------------------------------------------ provision viewer */
function showDrawer(title, sub, html) {
  $("#drawer").classList.remove("hidden"); $(".layout").classList.add("with-drawer");
  $("#drawer-title").innerHTML = `${esc(title)}<small>${esc(sub || "")}</small>`; $("#drawer-body").innerHTML = html; $("#drawer").scrollTop = 0;
}
$("#drawer-close").onclick = () => { $("#drawer").classList.add("hidden"); $(".layout").classList.remove("with-drawer"); };
function paraHtml(sub) {
  let h = `<div class="sub" data-label="${esc(sub.label)}"><span class="lab">${esc(sub.label)}</span>${esc(sub.text)}</div>`;
  for (const p of sub.paras || []) {
    h += `<div class="para"><span class="lab">${esc(p.label)}</span>${esc(p.text)}</div>`;
    for (const sp of p.subparas || []) h += `<div class="para l2"><span class="lab">${esc(sp.label)}</span>${esc(sp.text)}</div>`;
  }
  return h;
}
async function openSection(n, hlLabel, term) {
  try {
    const s = await jget(`/api/provision/section/${n}`);
    let h = "";
    if (n === 318) {
      h += `<div class="notice">Section 318 defines ${s.definitions.length} terms. Select a term below or use the filter in your question.</div>`;
      h += s.definitions.map(d => `<div class="def" data-term="${esc(d.term.toLowerCase())}">${esc(d.text)}</div>`).join("");
    } else h += s.subsections.map(paraHtml).join("");
    const chips = (arr, f) => arr.map(f).join("");
    if (s.refers_to.length || s.refers_to_schedules.length) h += `<div class="refs"><h4>This section refers to</h4>${chips(s.refers_to, r => `<span class="chip" data-sec="${r.section}">s.${r.section} ${esc(r.title.replace(/\.$/, "").slice(0, 38))}</span>`)}${chips(s.refers_to_schedules, r => `<span class="chip" data-sch="${r}">Schedule ${r}</span>`)}</div>`;
    if (s.cited_by.length) h += `<div class="refs"><h4>Cited by</h4>${chips(s.cited_by.slice(0, 24), r => `<span class="chip" data-sec="${r.section}">s.${r.section} ${esc(r.title.replace(/\.$/, "").slice(0, 30))}</span>`)}</div>`;
    h += `<div class="nav"><a class="chip" ${s.prev ? `data-sec="${s.prev}"` : 'style="visibility:hidden"'}>← s.${s.prev}</a><a class="chip" ${s.next ? `data-sec="${s.next}"` : 'style="visibility:hidden"'}>s.${s.next} →</a></div>`;
    showDrawer(`Section ${s.number} — ${s.title.replace(/\.$/, "")}`, `Chapter ${s.chapter}${s.part ? " · Part " + s.part : ""} · Gazette p. A${s.page_start}${s.page_end !== s.page_start ? "–A" + s.page_end : ""}`, h);
    if (hlLabel) { const el = [...document.querySelectorAll("#drawer-body .sub")].find(x => x.dataset.label === hlLabel); if (el) { el.classList.add("hl"); el.scrollIntoView({ block: "center" }); } }
    if (term) { const el = [...document.querySelectorAll("#drawer-body .def")].find(x => x.dataset.term === term.toLowerCase()); if (el) { el.classList.add("hl"); el.scrollIntoView({ block: "center" }); } }
  } catch (e) { showDrawer("Not found", "", `<p>${esc(e.message)}</p>`); }
}
async function openSchedule(o, para, hlLabel) {
  try {
    const s = await jget(`/api/provision/schedule/${o}` + (para ? `?para=${para}` : ""));
    let h = s.operative ? "" : `<div class="nonop">Explanatory memorandum – not an operative provision of the Act.</div>`;
    h += s.reference ? `<div class="notice">Applies to: ${esc(s.reference)}</div>` : "";
    for (const p of s.paragraphs) {
      if (p.part) h += `<div class="grp">Part ${esc(p.part)} – ${esc(p.part_title)}</div>`;
      if (p.heading && p.number) h += `<div class="grp">${esc(p.heading)}</div>`;
      h += `<div class="sub" data-pn="${p.number ?? ""}"><span class="lab">${p.number ?? ""}.</span></div>` + p.subsections.map(paraHtml).join("");
    }
    h += `<div class="refs"><h4>Other paragraphs</h4>${s.all_paragraphs.filter(x => x !== null).map(x => `<span class="chip" data-sch="${o}" data-para="${x}">para ${x}</span>`).join("")}${para ? `<span class="chip" data-sch="${o}">whole schedule</span>` : ""}</div>`;
    showDrawer(`${s.name}${para ? ", paragraph " + para : ""}`, `${s.title || ""} · Gazette p. A${s.page_start}`, h);
    if (hlLabel) { const el = [...document.querySelectorAll("#drawer-body .sub")].find(x => x.dataset.label === hlLabel); if (el) { el.classList.add("hl"); el.scrollIntoView({ block: "center" }); } }
  } catch (e) { showDrawer("Not found", "", `<p>${esc(e.message)}</p>`); }
}
$("#drawer").addEventListener("click", e => {
  const c = e.target.closest(".chip"); if (!c) return;
  if (c.dataset.sec) openSection(+c.dataset.sec); else if (c.dataset.sch) openSchedule(+c.dataset.sch, c.dataset.para ? +c.dataset.para : null);
});
function openCitation(text) {
  let m;
  if ((m = text.match(/^s\.\s*(\d+)\s*((?:\([^)]{1,6}\)\s*)*)(?:[“"]([^”"]+)[”"])?/i))) {
    const subs = (m[2] || "").match(/\([^)]{1,6}\)/g); return openSection(+m[1], subs ? subs[0] : null, m[3]);
  }
  if ((m = text.match(/^(First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth) Schedule(?:[^\d]*?(\d+))?\s*((?:\([^)]{1,6}\)\s*)*)/i))) {
    const subs = (m[3] || "").match(/\([^)]{1,6}\)/g); return openSchedule(ORD[m[1].toLowerCase()], m[2] ? +m[2] : null, subs ? subs[0] : null);
  }
}
document.addEventListener("click", e => { const c = e.target.closest(".cite"); if (c) openCitation(c.dataset.cite); const s = e.target.closest(".srcitem"); if (s) { if (s.dataset.sec) openSection(+s.dataset.sec); else if (s.dataset.sch) openSchedule(+s.dataset.sch, s.dataset.para ? +s.dataset.para : null); } });

/* ------------------------------------------------------------------ conversation */
function addUser(q) {
  $("#welcome")?.remove();
  const d = document.createElement("div"); d.className = "msg user"; d.innerHTML = `<div class="bubble">${esc(q)}</div>`; $("#chat").appendChild(d);
}
function addAnswer() {
  const d = document.createElement("div"); d.className = "msg";
  d.innerHTML = `<div class="answer"><div class="body caret"><p style="color:var(--muted)">Searching the Act…</p></div><div class="vslot"></div><div class="meta"></div><div class="sslot"></div></div>`;
  $("#chat").appendChild(d); $("#chat").scrollTop = $("#chat").scrollHeight; return d;
}
function sourcesHtml(r) {
  const items = r.sources.map(s => {
    const tag = s.pinned ? `<span class="tag pin">cited by you</span>` : `<span class="tag">${esc(s.role)}</span>`;
    const sc = s.chunks.length ? `<div class="scores">score ${s.score} · ` + s.chunks.map(c => `${esc(c.label.slice(0, 22))}: bm25#${c.bm25_rank} dense#${c.dense_rank}${c.rerank != null ? " rerank " + c.rerank : ""}`).join(" | ") + `</div>` : "";
    const attr = s.kind === "schedule" ? `data-sch="${s.schedule}" data-para="${s.para ?? ""}"` : `data-sec="${s.section}"`;
    return `<div class="srcitem" ${attr}><div class="h">${esc(s.header)}${tag}</div><div class="sn">${esc(s.snippet)}</div>${sc}</div>`;
  }).join("");
  return `<details class="src"><summary>Provisions consulted (${r.sources.length}) — click any to read in full</summary>${items}</details>`;
}
function verifyHtml(v, chat) {
  if (v.uncited_answer) return `<div class="verify warn"><b class="bad">⚠ No citations</b> — treat this answer with caution.</div>`;
  const bad = v.problems.length; const cls = bad ? "warn" : "ok";
  const c = `<b class="${v.citations_ok === v.citations_total ? "ok" : "bad"}">${v.citations_ok === v.citations_total ? "✓" : "✗"} ${v.citations_ok}/${v.citations_total} citations resolve to supplied provisions</b>`;
  const q = v.quotes_total ? `<b class="${v.quotes_ok === v.quotes_total ? "ok" : "bad"}">${v.quotes_ok === v.quotes_total ? "✓" : "✗"} ${v.quotes_ok}/${v.quotes_total} quotations verbatim</b>` : `<span>no direct quotations</span>`;
  return `<div class="verify ${cls}">${c}${q}<span class="x" style="margin-left:auto"><button class="btn ghost small copy">Copy answer</button></span>${bad ? `<ul>${v.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}</div>`;
}
async function ask(q) {
  q = q.trim(); if (!q || state.busy) return;
  state.busy = true; $("#send").disabled = true; $("#q").value = ""; $("#q").style.height = "auto";
  addUser(q); const node = addAnswer(); const body = $(".body", node); let text = ""; let raf = 0; let res = null;
  const render = () => { raf = 0; body.innerHTML = md(text); $("#chat").scrollTop = $("#chat").scrollHeight; };
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, history: state.history.slice(-6), rewrite: true }) });
    if (!r.ok || !r.body) throw new Error(await r.text());
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true }); let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = (raw.match(/^event: (.*)$/m) || [])[1]; const data = JSON.parse((raw.match(/^data: (.*)$/m) || [, "{}"])[1]);
        if (ev === "retrieval") {
          res = data; $(".sslot", node).innerHTML = sourcesHtml(data);
          const rw = [...(data.rewrites || []), ...(data.sub_questions || [])];
          $(".meta", node).innerHTML = `<div class="rewrites">Pipeline: ${esc(data.pipeline.sparse)} + ${esc(data.pipeline.dense)} → ${esc(data.pipeline.rerank)} · retrieval ${data.timings.retrieve_ms} ms${data.timings.rerank_ms ? " · rerank " + data.timings.rerank_ms + " ms" : ""}${rw.length ? "<br>Query rewrites: " + rw.map(esc).join(" · ") : ""}</div>`;
          if (data.standalone_question && data.standalone_question !== q) $(".meta", node).insertAdjacentHTML("afterbegin", `<div>Interpreted as: <em>${esc(data.standalone_question)}</em></div>`);
        } else if (ev === "token") { text += data.text; if (!raf) raf = requestAnimationFrame(render); }
        else if (ev === "notice") { $(".meta", node).insertAdjacentHTML("afterbegin", `<div class="notice">${esc(data.message)}</div>`); }
        else if (ev === "verification") {
          body.classList.remove("caret"); render();
          $(".vslot", node).innerHTML = verifyHtml(data);
          const bad = new Set(data.citations.filter(c => !c.ok).map(c => c.text));
          body.querySelectorAll(".cite").forEach(el => { if (bad.has(el.dataset.cite)) { el.classList.add("bad"); el.title = "Could not be verified against the supplied provisions"; } });
          $(".copy", node)?.addEventListener("click", () => navigator.clipboard.writeText(body.innerText + "\n\n— Petroleum Industry Act, 2021 Research Assistant"));
        } else if (ev === "error") throw new Error(data.message);
      }
    }
    body.classList.remove("caret"); render();
    state.history.push({ role: "user", content: q }, { role: "assistant", content: text.slice(0, 1500) });
    // open the top provision automatically for direct citations
    if (res && res.sources.length && Object.keys(res.pinned || {}).length) { const s = res.sources.find(x => x.pinned) || res.sources[0]; s.kind === "schedule" ? openSchedule(s.schedule, s.para) : openSection(s.section); }
  } catch (e) {
    body.classList.remove("caret"); body.innerHTML = `<p style="color:var(--bad)"><b>Something went wrong:</b> ${esc(e.message)}</p>`;
  } finally { state.busy = false; $("#send").disabled = false; $("#q").focus(); }
}
$("#composer").addEventListener("submit", e => { e.preventDefault(); ask($("#q").value); });
$("#q").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask($("#q").value); } });
$("#q").addEventListener("input", e => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px"; });
$("#inspector").addEventListener("change", e => document.body.classList.toggle("insp", e.target.checked));
$("#btn-new").onclick = () => { state.history = []; $("#chat").innerHTML = ""; location.reload(); };
$("#btn-print").onclick = () => window.print();
init();
