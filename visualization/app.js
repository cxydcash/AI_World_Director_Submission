/* AI 开放世界 —— Player View（玩家视角）
 * 只消费 /api/open-world/player-view 的观察层模型。
 * AI 编导 / Timeline / 原始状态只在开发者调试区显示。
 */

const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");

const LOCATION_LABELS = { home: "家", forest: "森林", town: "村庄", stone_road: "石板路" };

const state = {
  view: null,
  selectedNpcId: null,
  selectedClueKey: null,
  pollTimer: null,
  devMode: false,
  devEngine: "open-world",
  page: "field",
  wasRunningBeforeLog: null,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let payload = {};
  if (text) { try { payload = JSON.parse(text); } catch { payload = { error: text }; } }
  if (!response.ok) throw new Error(payload.error || payload.message || "请求失败");
  return payload;
}

function post(path, payload) {
  return api(path, {
    method: "POST",
    headers: payload === undefined ? {} : { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

/* ------------------------------------------------------------------ 地图 */

function scale(point) {
  const [x, y] = point || [0, 0];
  const padding = 34;
  return [padding + (x / 100) * (canvas.width - padding * 2),
          canvas.height - padding - (y / 100) * (canvas.height - padding * 2)];
}

function drawMap(view) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#243247"; ctx.lineWidth = 1;
  for (let i = 0; i <= 10; i++) {
    const p = 34 + (i / 10) * (canvas.width - 68);
    ctx.beginPath(); ctx.moveTo(p, 34); ctx.lineTo(p, canvas.height - 34); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(34, p); ctx.lineTo(canvas.width - 34, p); ctx.stroke();
  }
  for (const loc of (view.map || {}).locations || []) {
    const [x, y] = scale(loc.position);
    ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.fillStyle = "#2f4a67"; ctx.fill();
    ctx.fillStyle = "#9fb4d0"; ctx.font = "14px 'Microsoft YaHei', sans-serif";
    const label = LOCATION_LABELS[loc.id] || loc.name;
    ctx.fillText(label, x - label.length * 7, y - 16);
  }
  const track = state.selectedNpcId && view.tracks ? view.tracks[state.selectedNpcId] : null;
  if (track && track.points && track.points.length > 1) {
    ctx.beginPath(); ctx.strokeStyle = "rgba(247,185,85,0.75)"; ctx.lineWidth = 2;
    track.points.forEach((point, index) => {
      const [px, py] = scale(point.position);
      if (index === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }
  for (const clue of (view.map || {}).clues || []) {
    const [x, y] = scale(clue.position);
    ctx.beginPath(); ctx.rect(x - 6, y - 6, 12, 12);
    ctx.fillStyle = clue.discovered ? "#6ee7a8" : "#8b9bb4"; ctx.fill();
    ctx.fillStyle = "#d8e4f3"; ctx.font = "12px 'Microsoft YaHei', sans-serif";
    ctx.fillText(clue.name, x + 10, y + 4);
  }
  const player = (view.map || {}).player || {};
  const [px, py] = scale(player.position);
  ctx.beginPath(); ctx.arc(px, py, 10, 0, Math.PI * 2); ctx.fillStyle = "#58c4dd"; ctx.fill();
  ctx.fillStyle = "#e8eef8"; ctx.font = "13px 'Microsoft YaHei', sans-serif";
  ctx.fillText("玩家", px + 13, py - 10);
  for (const npc of (view.map || {}).npcs || []) {
    const [x, y] = scale(npc.position);
    const selected = npc.id === state.selectedNpcId;
    ctx.beginPath(); ctx.arc(x, y, selected ? 12 : 9, 0, Math.PI * 2);
    ctx.fillStyle = selected ? "#ffd479" : "#f7b955"; ctx.fill();
    if (selected) { ctx.beginPath(); ctx.arc(x, y, 17, 0, Math.PI * 2); ctx.strokeStyle = "#ffd479"; ctx.lineWidth = 2; ctx.stroke(); }
    ctx.fillStyle = "#f3f7fd"; ctx.font = "13px 'Microsoft YaHei', sans-serif";
    ctx.fillText(npc.name, x + 13, y - 10);
  }
}

canvas.addEventListener("click", event => {
  const view = state.view; if (!view) return;
  const rect = canvas.getBoundingClientRect();
  const px = (event.clientX - rect.left) * (canvas.width / rect.width);
  const py = (event.clientY - rect.top) * (canvas.height / rect.height);
  let best = null;
  for (const npc of (view.map || {}).npcs || []) {
    const [x, y] = scale(npc.position);
    const d = Math.hypot(px - x, py - y);
    if (d <= 18 && (!best || d < best.d)) best = { kind: "npc", id: npc.id, d };
  }
  for (const clue of (view.map || {}).clues || []) {
    const [x, y] = scale(clue.position);
    const d = Math.hypot(px - x, py - y);
    if (d <= 16 && (!best || d < best.d)) best = { kind: "clue", id: clue.key, d };
  }
  if (!best) return;
  if (best.kind === "npc") { state.selectedNpcId = best.id; showNpcDetail(best.id); }
  else { state.selectedClueKey = best.id; showClueDetail(best.id); }
});

/* ---------------------------------------------------------------- 渲染 */

function renderOverview(view) {
  const o = view.world_overview || {};
  document.getElementById("overview").innerHTML =
    `<div class="kv"><span class="label">当前时间</span><span>${escapeHtml(o.time_label || "")}</span></div>` +
    `<div class="kv"><span class="label">天气</span><span>${escapeHtml(o.weather || "")}</span></div>` +
    `<div class="kv"><span class="label">我的位置</span><span>${escapeHtml(o.place || "")}</span></div>` +
    `<div class="kv"><span class="label">人物</span><span>共 ${o.people_total || 0} 人，主要人物 ${o.people_main || 0} 人</span></div>` +
    `<div class="kv"><span class="label">线索</span><span>共 ${o.clue_total || 0} 条，已发现 ${o.clue_discovered || 0} 条</span></div>`;
  const llm = (view.debug || {}).llm || {};
  if (!llm.dialogue_enabled) {
    html += `<div class="warn-box">⚠️ 当前 NPC 对话运行在“规则兜底”模式（没有检测到可用的 LLM API Key），` +
      `所以回答会显得很死板。请检查服务启动环境里的 LLM_API_KEY 是否被设成了空值。</div>`;
  }
}

function renderStories(view) {
  const box = document.getElementById("stories");
  const stories = view.stories || [];
  if (!stories.length) {
    box.innerHTML = `<p class="muted">今天结束后，会自动把这一天整理成一幕一幕的故事。</p>`;
    return;
  }
  box.innerHTML = stories.map(story => {
    if (!story.ready) {
      return `<div class="story"><div class="story-title">${escapeHtml(story.label)}</div>` +
        `<p class="muted">这一天的故事正在整理中…</p></div>`;
    }
    const scenes = (story.scenes || []).map(scene => {
      const dialogue = (scene.dialogue || []).filter(d => d && d.line).map(d =>
        `<div class="story-line"><span class="story-speaker">${escapeHtml(d.speaker || "某人")}：</span>${escapeHtml(d.line)}</div>`
      ).join("");
      return `<div class="story-scene">
        <div class="story-scene-title">${escapeHtml(scene.title || "一幕")}</div>
        ${scene.text ? `<p class="story-text">${escapeHtml(scene.text)}</p>` : ""}
        ${dialogue ? `<div class="story-dialogue">${dialogue}</div>` : ""}
      </div>`;
    }).join("");
    const tag = story.source === "ai" ? "编导整理" : "事实记录";
    return `<div class="story">
      <div class="story-title">${escapeHtml(story.title || story.label)}<span class="tag">${escapeHtml(tag)}</span></div>
      ${scenes || `<p class="muted">这一天没有足够的事件可以成幕。</p>`}
    </div>`;
  }).join("");
}
function personCard(p) {
  const reasons = (p.reasons || []).map(r => `<span class="chip">${escapeHtml(r)}</span>`).join("");
  return `<div class="person-card" data-npc="${escapeHtml(p.id)}">
    <div class="person-head"><strong>${escapeHtml(p.name)}</strong><span class="hint">${escapeHtml(p.title)}</span><span class="tag">${escapeHtml(p.place)}</span></div>
    <div class="person-line">${escapeHtml(p.action)}　｜　${escapeHtml(p.goal)}</div>
    <div class="person-reasons">${reasons}</div>
  </div>`;
}

function renderFocus(view) {
  const box = document.getElementById("focus-list");
  const candidates = view.focus_candidates || [];
  const focused = new Set((view.map || {}).focus_ids || []);
  if (!candidates.length) {
    box.innerHTML = `<span class="muted">还没有可以关注的人。</span>`;
    return;
  }
  box.innerHTML = candidates.map(p =>
    `<button class="focus-chip${focused.has(p.id) ? " on" : ""}" data-focus="${escapeHtml(p.id)}">` +
    `${escapeHtml(p.name)}${p.met ? "" : "（未接触）"}</button>`
  ).join("");
  for (const chip of box.querySelectorAll("[data-focus]")) {
    chip.onclick = async () => {
      const next = new Set(focused);
      const id = chip.dataset.focus;
      if (next.has(id)) next.delete(id); else next.add(id);
      try {
        const data = await post("/api/open-world/map-focus", { npc_ids: Array.from(next) });
        renderPlayer(data);
      } catch (error) {
        document.getElementById("player-feedback").textContent = error.message;
      }
    };
  }
}

function renderLocalInfo(view) {
  const box = document.getElementById("local-info");
  const local = view.local_info || {};
  const npcs = local.npcs || [];
  const clues = local.clues || [];
  let html = `<div class="kv"><span class="label">你在这里</span><span>${escapeHtml(local.place || "空地")}</span></div>`;
  if (!npcs.length && !clues.length) {
    html += `<p class="muted">这里暂时没有你关注的人，也没有你已经发现的线索。</p>`;
    box.innerHTML = html;
    return;
  }
  if (npcs.length) {
    html += `<div class="chron-section"><h4>你关注的、正在这里的人</h4><ul class="dir-list">` +
      npcs.map(n => `<li><strong>${escapeHtml(n.name)}</strong>（${escapeHtml(n.title)}）：` +
        `${escapeHtml(n.action)}　｜　${escapeHtml(n.goal)}</li>`).join("") + `</ul></div>`;
  }
  if (clues.length) {
    html += `<div class="chron-section"><h4>你在这里发现的线索</h4><ul class="dir-list">` +
      clues.map(c => `<li><strong>${escapeHtml(c.name)}</strong>：${escapeHtml(c.status_label)}　` +
        `${escapeHtml(c.current)}　发现者：${escapeHtml((c.discoverers || []).join("、") || "暂无")}</li>`).join("") + `</ul></div>`;
  }
  box.innerHTML = html;
}

function renderPeople(view) {
  const people = view.people || [];
  const main = people.filter(p => p.tier === "main");
  const side = people.filter(p => p.tier === "side");
  const bg = people.filter(p => p.tier === "background");
  const empty = `<p class="muted">目前还没有与你有关系的人。</p>`;
  document.getElementById("people-main").innerHTML = main.length ? main.map(personCard).join("") : empty;
  document.getElementById("people-side").innerHTML = side.length ? side.map(personCard).join("") : `<p class="muted">暂时没有次要人物。</p>`;
  document.getElementById("people-bg").innerHTML = bg.length ? bg.map(personCard).join("") : `<p class="muted">没有背景人物。</p>`;
  for (const node of document.querySelectorAll("[data-npc]")) {
    node.onclick = () => { state.selectedNpcId = node.dataset.npc; showNpcDetail(node.dataset.npc); };
  }
}

function clueHighlight(clue) {
  const attrs = clue.attributes || [];
  const appearance = attrs.find(a => a.label === "外观 / 内容");
  const typeAttr = attrs.find(a => a.label === "线索类型");
  const parts = [];
  if (typeAttr && typeAttr.value) parts.push(typeAttr.value);
  if (appearance && appearance.value) parts.push(appearance.value);
  return parts.join(" · ");
}

function renderClues(view) {
  const box = document.getElementById("clues");
  const clues = view.clues || [];
  if (!clues.length) { box.innerHTML = `<p class="muted">目前还没有发现任何线索。</p>`; return; }
  box.innerHTML = clues.map(c => {
    const highlight = clueHighlight(c);
    return `<div class="clue-card" data-clue="${escapeHtml(c.key)}">
      <div class="clue-title">${escapeHtml(c.name)}<span class="tag ${c.discovered ? "good" : "warn"}">${escapeHtml(c.status_label)}</span><span class="tag">${escapeHtml(c.detail_level || "")}</span></div>
      ${highlight ? `<div class="clue-highlight">${escapeHtml(highlight)}</div>` : ""}
      <div class="clue-meta">当前位置：${escapeHtml(c.current)}　来源：${escapeHtml(c.origin)}　出现：${escapeHtml(c.created_label)}</div>
      <div class="clue-meta">发现者：${c.discoverers.length ? escapeHtml(c.discoverers.join("、")) : "暂无"}</div>
      <div class="clue-meta hint">点击查看这条线索的区别特征与背景记录</div>
    </div>`;
  }).join("");
  for (const node of document.querySelectorAll("[data-clue]")) {
    node.onclick = () => { state.selectedClueKey = node.dataset.clue; showClueDetail(node.dataset.clue); };
  }
}

function renderHeard(view) {
  const box = document.getElementById("heard");
  const heard = view.heard || [];
  if (!heard.length) {
    box.innerHTML = `<p class="muted">你还没有听到任何人说话。走近村民再听，或者直接和TA说话。</p>`;
    return;
  }
  const toMe = heard.filter(h => h.kind === "to_player");
  const over = heard.filter(h => h.kind === "overheard");
  const rows = list => list.slice().reverse().map(h =>
    `<div class="feed-row"><span class="feed-time">${escapeHtml(h.time_label)}</span>` +
    `<span class="tag">${escapeHtml(h.kind_label)}</span>` +
    `<span class="feed-text"><strong>${escapeHtml(h.speaker)}</strong>` +
    (h.listener && h.listener !== "玩家" ? ` → ${escapeHtml(h.listener)}` : "") +
    `：${escapeHtml(h.text)}</span></div>`).join("");
  let html = "";
  html += `<div class="chron-section"><h4>别人对我说</h4>${toMe.length ? rows(toMe) : `<p class="muted">暂时没有人对你说话。</p>`}</div>`;
  html += `<div class="chron-section"><h4>我路过听到</h4>${over.length ? rows(over) : `<p class="muted">暂时没有听到别人互相说话。</p>`}</div>`;
  box.innerHTML = html;
}

function renderCommitments(view) {
  const box = document.getElementById("commitments");
  const items = view.commitments || [];
  const notices = view.notices || [];
  let html = "";
  if (notices.length) {
    html += `<div class="chron-section"><h4>刚刚有人告诉你</h4><ul class="dir-list">` +
      notices.map(n => `<li>${escapeHtml(n.text)}</li>`).join("") + `</ul></div>`;
  }
  if (!items.length) {
    html += `<p class="muted">目前没有和别人说定什么事。</p>`;
  } else {
    html += `<div class="chron-section"><h4>最近的约定</h4><ul class="dir-list">` +
      items.slice().reverse().map(c =>
        `<li><strong>${escapeHtml(c.npc)}</strong>：${escapeHtml(c.action)}` +
        `<span class="tag">${escapeHtml(c.status_label || "")}</span></li>`).join("") + `</ul></div>`;
  }
  box.innerHTML = html;
}
function renderMyStory(view) {
  const mine = view.my_story || {};
  let html = `<div class="kv"><span class="label">我现在在</span><span>${escapeHtml(mine.place || "")}</span></div>`;
  if (mine.current_stay) {
    html += `<div class="chron-note">正在 ${escapeHtml(mine.current_stay.place)} 停留（已 ${mine.current_stay.ticks} 个时间刻）</div>`;
  }
  const actions = mine.actions || [];
  html += `<div class="chron-section"><h4>我做过的动作</h4>` +
    (actions.length
      ? `<ul class="dir-list">${actions.slice(0, 12).map(a => `<li><span class="feed-time">${escapeHtml(a.time_label)}</span>${escapeHtml(a.text)}<span class="tag">${escapeHtml(a.result)}</span></li>`).join("")}</ul>`
      : `<p class="muted">我还没有做过会影响世界的动作。</p>`) + `</div>`;
  const thoughts = mine.thoughts || [];
  if (thoughts.length) {
    html += `<div class="chron-section"><h4>我想过</h4><ul class="dir-list">${thoughts.slice(0, 6).map(t => `<li><span class="feed-time">${escapeHtml(t.time_label)}</span>${escapeHtml(t.text)}</li>`).join("")}</ul></div>`;
  }
  const stays = mine.stays || [];
  html += `<div class="chron-section"><h4>我停留过的地方（停留 5 个时间刻以上才记录）</h4>` +
    (stays.length
      ? `<ul class="dir-list">${stays.slice().reverse().map(s => `<li><span class="feed-time">${escapeHtml(s.time_label)}</span>在 ${escapeHtml(s.place)} 停留了约 ${s.ticks} 个时间刻</li>`).join("")}</ul>`
      : `<p class="muted">还没有停留超过 5 个时间刻的位置。</p>`) + `</div>`;
  const places = mine.places || [];
  html += `<div class="chron-section"><h4>我到过的地点</h4>` +
    (places.length ? places.map(p => `<span class="chip">${escapeHtml(p.place)}</span>`).join("") : `<span class="muted">还没有到过命名地点。</span>`) + `</div>`;
  const daily = mine.daily || [];
  html += `<div class="chron-section"><h4>我的一天（按天）</h4>` +
    (daily.length ? daily.slice().reverse().map(d => `<p class="chron-para">${escapeHtml(d.text)}</p>`).join("") : `<p class="muted">还没有记录。</p>`) + `</div>`;
  document.getElementById("me-story").innerHTML = html;
}

function renderTalkOptions(view) {
  const select = document.getElementById("talk-npc");
  const current = select.value;
  select.innerHTML = (view.people || []).map(p =>
    `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}（${escapeHtml(p.title)}）</option>`).join("");
  if (current) select.value = current;
  if (state.selectedNpcId) select.value = state.selectedNpcId;
}

function renderPlayer(view) {
  state.view = view;
  document.getElementById("world-time").textContent = view.time_label;
  document.getElementById("world-weather").textContent = `天气：${view.weather.label}`;
  document.getElementById("world-status").textContent = view.paused ? "世界已暂停" : "世界自动运行中";
  document.getElementById("toggle-world").textContent = view.paused ? "继续世界" : "暂停世界";
  renderOverview(view); renderStories(view); renderPeople(view); renderClues(view);
  renderHeard(view); renderCommitments(view); renderFocus(view); renderLocalInfo(view);
  renderMyStory(view); renderTalkOptions(view); drawMap(view);
}

/* ------------------------------------------------------------ 详情面板 */

function showNpcDetail(npcId) {
  const view = state.view; if (!view) return;
  const p = (view.npcs_detail || {})[npcId];
  const box = document.getElementById("npc-detail");
  if (!p) { box.textContent = "没有找到这个人。"; return; }
  const track = (view.tracks || {})[npcId];
  let html = `<div class="kv"><span class="label">身份</span><span>${escapeHtml(p.title)}</span></div>`;
  html += `<div class="kv"><span class="label">当前地点</span><span>${escapeHtml(p.place)}</span></div>`;
  html += `<div class="kv"><span class="label">当前行动</span><span>${escapeHtml(p.action)}</span></div>`;
  html += `<div class="kv"><span class="label">当前打算</span><span>${escapeHtml(p.goal)}</span></div>`;
  html += `<div class="kv"><span class="label">关注度</span><span>${escapeHtml(p.tier_label)}（${p.score}）${p.reasons.length ? "：" + escapeHtml(p.reasons.join("、")) : ""}</span></div>`;
  html += `<div class="chron-section"><h4>最近轨迹</h4>` +
    (track && track.points && track.points.length
      ? `<p class="muted small">${escapeHtml(p.name)} 走过的路线：` + track.points.map(t => escapeHtml(t.place)).join(" → ") + `</p>`
      : `<p class="muted">暂无历史轨迹。</p>`) + `</div>`;
  document.getElementById("npc-detail-title").textContent = `${p.name}（${p.title}）`;
  box.innerHTML = html;
  document.getElementById("sec-npc-detail").classList.remove("hidden");
}

function showClueDetail(key) {
  const view = state.view; if (!view) return;
  const clue = (view.clues || []).find(c => c.key === key);
  const box = document.getElementById("clue-detail");
  if (!clue) { box.textContent = "没有找到这条线索。"; return; }
  const life = (clue.lifecycle || []).map(e =>
    `<li><span class="feed-time">${escapeHtml(e.time_label)}</span><span class="tag">${escapeHtml(e.stage || "")}</span>${escapeHtml(e.text)}</li>`).join("");
  const attrs = (clue.attributes || []).map(a =>
    `<div class="kv"><span class="label">${escapeHtml(a.label)}</span><span>${escapeHtml(a.value || "")}</span></div>`).join("");
  const records = (clue.related_records || []).map(r =>
    `<li><span class="feed-time">${escapeHtml(r.time_label)}</span>${escapeHtml(r.who || "")}：${escapeHtml(r.text || "")}</li>`).join("");
  let html = `<div class="kv"><span class="label">状态</span><span>${escapeHtml(clue.status_label)}　<span class="tag">${escapeHtml(clue.detail_level || "")}</span></span></div>`;
  html += `<div class="chron-section"><h4>区别特征（这条线索与别的东西有什么不同）</h4>${attrs}</div>`;
  html += `<div class="chron-section"><h4>背景记录（探索越多，这里越完整）</h4>` +
    (records ? `<ul class="dir-list">${records}</ul>` : `<p class="muted">还没有与这条线索相关的记忆或对话。</p>`) + `</div>`;
  html += `<div class="chron-section"><h4>流转过程</h4>${life ? `<ul class="dir-list">${life}</ul>` : `<p class="muted">还没有流转记录。</p>`}</div>`;
  document.getElementById("clue-detail-title").textContent = clue.name;
  box.innerHTML = html;
  document.getElementById("sec-clue-detail").classList.remove("hidden");
}

document.getElementById("npc-detail-close").onclick = () => document.getElementById("sec-npc-detail").classList.add("hidden");
document.getElementById("clue-detail-close").onclick = () => document.getElementById("sec-clue-detail").classList.add("hidden");

/* ------------------------------------------------------------ 玩家操作 */

async function switchPage(page) {
  state.page = page;
  const toLog = page === "log";
  document.getElementById("player-app").classList.toggle("hidden", toLog);
  document.getElementById("log-app").classList.toggle("hidden", !toLog);
  document.getElementById("page-field").classList.toggle("active", !toLog);
  document.getElementById("page-log").classList.toggle("active", toLog);

  if (toLog) {
    // 去看记录时暂停世界，避免文字一直在变
    const running = state.view ? !state.view.paused : false;
    state.wasRunningBeforeLog = running;
    if (running) {
      try { await post("/api/open-world/pause"); } catch (error) { /* 忽略 */ }
    }
  } else if (state.wasRunningBeforeLog) {
    // 回到地图页时，如果之前是运行的就恢复运行
    state.wasRunningBeforeLog = null;
    try { await post("/api/open-world/start"); } catch (error) { /* 忽略 */ }
  }
  await refreshPlayer();
}

async function refreshPlayer() {
  try { renderPlayer(await api("/api/open-world/player-view")); }
  catch (error) { document.getElementById("world-status").textContent = error.message; }
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(refreshPlayer, 1000);
}

async function move(direction) {
  try { await post("/api/open-world/move", { direction }); await refreshPlayer(); }
  catch (error) { document.getElementById("player-feedback").textContent = error.message; }
}

document.getElementById("focus-auto").onclick = async () => {
  try { renderPlayer(await post("/api/open-world/map-focus", { npc_ids: null })); }
  catch (error) { document.getElementById("player-feedback").textContent = error.message; }
};
document.getElementById("page-field").onclick = () => switchPage("field");
document.getElementById("page-log").onclick = () => switchPage("log");

document.getElementById("toggle-world").onclick = async () => {
  const paused = state.view ? state.view.paused : true;
  try { await post(paused ? "/api/open-world/start" : "/api/open-world/pause"); await refreshPlayer(); }
  catch (error) { document.getElementById("world-status").textContent = error.message; }
};
for (const button of document.querySelectorAll("[data-advance]")) {
  button.onclick = async () => {
    button.disabled = true;
    try { await post("/api/open-world/advance", { hours: Number(button.dataset.advance) }); await refreshPlayer(); }
    catch (error) { document.getElementById("world-status").textContent = error.message; }
    finally { button.disabled = false; }
  };
}
document.getElementById("reset-world").onclick = async () => {
  try {
    await post("/api/open-world/reset");
    state.selectedNpcId = null; state.selectedClueKey = null;
    document.getElementById("sec-npc-detail").classList.add("hidden");
    document.getElementById("sec-clue-detail").classList.add("hidden");
    await post("/api/open-world/pause");
    await refreshPlayer();
  } catch (error) { document.getElementById("world-status").textContent = error.message; }
};
for (const button of document.querySelectorAll("[data-move]")) button.onclick = () => move(button.dataset.move);
document.addEventListener("keydown", event => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  // 忽略操作系统的按键重复：按住 WASD/方向键不会反复推进世界时间。
  if (event.repeat) return;
  const map = { w: "w", a: "a", s: "s", d: "d", arrowup: "w", arrowleft: "a", arrowdown: "s", arrowright: "d" };
  if (map[event.key.toLowerCase()]) { event.preventDefault(); move(map[event.key.toLowerCase()]); }
});
document.getElementById("talk-button").onclick = async () => {
  const feedback = document.getElementById("player-feedback");
  const message = document.getElementById("talk-input").value.trim();
  if (!message) { feedback.textContent = "先说点什么。"; return; }
  feedback.textContent = "正在说话…";
  try {
    const result = await post("/api/open-world/talk", { npc_id: document.getElementById("talk-npc").value, message });
    feedback.textContent = result.reply || result.message || "说完了。";
    document.getElementById("talk-input").value = "";
    await refreshPlayer();
  } catch (error) { feedback.textContent = error.message; }
};
document.getElementById("thought-button").onclick = async () => {
  const box = document.getElementById("player-feedback");
  const input = document.getElementById("thought-input").value.trim();
  if (!input) { box.textContent = "先写下你的想法。"; return; }
  box.textContent = "正在理解你的想法…";
  try {
    const result = await post("/api/open-world/analyze", { input });
    const candidates = result.candidates || [];
    box.innerHTML = candidates.length
      ? "你可以这样做：" + candidates.slice(0, 3).map(c => `<span class="chip">${escapeHtml(c.label || c.action || "")}</span>`).join("")
      : escapeHtml(result.message || "现在这里没有可以做这件事的条件。");
    await refreshPlayer();
  } catch (error) { box.textContent = error.message; }
};

/* -------------------------------------------------------- 开发者调试 */

async function refreshDebug() {
  if (!state.devMode || !state.view) return;
  const debug = state.view.debug || {};
  const dir = document.getElementById("debug-director");
  const events = debug.events || [];
  dir.innerHTML = events.length
    ? events.map(ev => `<div class="dir-card">
        <div class="dir-title">${escapeHtml(ev.time_label)}　<span class="tag ${ev.is_ai ? "good" : ""}">${escapeHtml(ev.author)}</span><span class="tag">${escapeHtml(ev.status)}</span><span class="hint">${escapeHtml(ev.event_id || "")}</span></div>
        <ul class="dir-list">${(ev.directives || []).map(t => `<li>${escapeHtml(t)}</li>`).join("")}</ul>
        ${ev.reason ? `<div class="muted small">reason: ${escapeHtml(ev.reason)}</div>` : ""}
        <details><summary>raw effects</summary><pre>${escapeHtml(JSON.stringify(ev.raw_effects || [], null, 1))}</pre></details>
      </div>`).join("")
    : `<p class="muted">没有编导事件。</p>`;

  const rawRecent = debug.recent_raw || [];
  document.getElementById("debug-recent").innerHTML = rawRecent.length
    ? `<ul class="dir-list">${rawRecent.map(r => `<li><span class="feed-time">${escapeHtml(r.time_label)}</span>[${escapeHtml(r.category_label)}] ${escapeHtml(r.text)}</li>`).join("")}</ul>`
    : `<p class="muted">没有原始事件流。</p>`;
  const llm = debug.llm || {};
  const llmBox = document.getElementById("debug-llm");
  if (llmBox) {
    llmBox.innerHTML =
      `<div class="kv"><span class="label">对话 LLM</span><span>${llm.dialogue_enabled ? "已启用" : "未启用（规则兜底）"}</span></div>` +
      `<div class="kv"><span class="label">编导 LLM</span><span>${llm.director_enabled ? "已启用" : "未启用"}</span></div>` +
      `<div class="kv"><span class="label">NPC 目标决策</span><span>${escapeHtml(llm.npc_goal_provider || "未接入（不产生新目标）")}</span></div>`;
  }
  const log = debug.player_action_log || [];
  document.getElementById("debug-player-actions").innerHTML = log.length
    ? `<ul class="dir-list">${log.slice().reverse().map(a => `<li>${escapeHtml(String(a.time))}｜${escapeHtml(a.action)}｜success=${a.success}｜${escapeHtml(a.raw_text)}</li>`).join("")}</ul>`
    : `<p class="muted">玩家 Action 日志为空（没有玩家操作）。</p>`;

  document.getElementById("debug-timeline").innerHTML =
    `<ul class="dir-list">${(debug.timeline || []).map(t =>
      `<li>${escapeHtml(t.event_id)}｜${escapeHtml(t.status)}｜trigger_at=${t.trigger_at}｜source=${escapeHtml(t.source || "")}</li>`).join("")}</ul>`;

  const suspect = debug.suspect_actor_defaults || [];
  const stateBox = document.getElementById("debug-state");
  try {
    let path = "/api/state";
    if (state.devEngine === "simulation") path = "/api/simulation/state";
    if (state.devEngine === "interactive") path = "/api/interactive/state";
    if (state.devEngine === "open-world") path = "/api/open-world/state";
    const payload = await api(path);
    stateBox.innerHTML =
      `<p class="muted small">actor 默认值可疑条目（actor=player 但不是玩家行为）：${suspect.length} 条</p>` +
      `<pre>${escapeHtml(JSON.stringify(payload.snapshot || payload, null, 1).slice(0, 4000))}</pre>`;
  } catch (error) { stateBox.textContent = error.message; }
}

document.getElementById("dev-toggle").onclick = () => {
  state.devMode = !state.devMode;
  document.getElementById("dev-app").classList.toggle("hidden", !state.devMode);
  refreshDebug();
};
for (const button of document.querySelectorAll(".mode-button")) {
  button.onclick = () => {
    state.devEngine = button.dataset.mode;
    for (const other of document.querySelectorAll(".mode-button")) other.classList.toggle("active", other === button);
    refreshDebug();
  };
}

refreshPlayer().then(startPolling);
setInterval(() => { if (state.devMode) refreshDebug(); }, 2000);



