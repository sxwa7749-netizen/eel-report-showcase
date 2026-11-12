"use strict";
if(window.DemoPageSession&&!window.DemoPageSession.allowed)throw new Error('请重新登录');

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const CATS = ["老", "新", "暂无"];
const SHIFTS = ["早", "晚"];
// 老板端漏喂提示：今天几点以后这一班还没填才算漏喂（北京时间，24 小时制）。
// 早上 4:30、下午 15:00 喂料，留约 1.5~2 小时给员工填写
const SHIFT_DUE = { "早": 6, "晚": 17 };
const WEEK = "日一二三四五六";

const store = {
  get(k, d) { try { const storage=['page','bossTab','bossFarm'].includes(k)?sessionStorage:localStorage;const v = storage.getItem("demo." + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { const storage=['page','bossTab','bossFarm'].includes(k)?sessionStorage:localStorage;storage.setItem("demo." + k, JSON.stringify(v)); } catch { /* 忽略 */ } },
};

const pad2 = (n) => String(n).padStart(2, "0");
const dateStr = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const todayStr = () => dateStr(new Date());
const lastDay = (y, m) => new Date(y, m, 0).getDate();

function halfOf(date) {
  const [y, m, d] = date.split("-").map(Number);
  return { year: y, month: m, half: d <= 15 ? "上" : "下" };
}

const state = {
  role: "admin",
  loginRequired: false,
  csrfToken: "",
  page: store.get('page','home'),
  sub: null,
  half: halfOf(todayStr()),
  halfInfo: null,
  pid: null,
  report: null,
  pondId: store.get("pondId", null),
  reportFilter: "全部",
  reportMode: store.get("reportMode", "h"),
  today: { date: todayStr(), shift: new Date().getHours() < 12 ? "早" : "晚" },
  monthYM: null,
  bossMonth: todayStr().slice(0, 7),
  bossRange: "全月",
  bossTab: store.get("bossTab", "overview"),
  bossFarm: store.get("bossFarm", "all"),   // 老板端：all＝全部厂，或某个厂的 id
  farm: { id: "demo", name: "示例", mark: "一", title: "示例鳗场" },  // 当前登录的厂（/api/me 返回）
  farms: [],
  needSetup: false,
  payrollSection: store.get("paySection", "monthly"),
  payrollEmployee: "",
  paySheetFor: null,
  payView: store.get("payView", "list"),
};

document.addEventListener("click", (event) => {
  const action = event.target.closest?.('[data-ui-action]')?.dataset.uiAction;
  if (action === "reload") location.reload();
  if (action === "print") window.print();
});

// ---------- 工具 ----------

// 老板端看某个厂时，请求带上 X-Farm（录入账号固定在自己的厂，服务器不认这个头）
function farmOf(opts = {}) {
  if (opts.farm) return opts.farm;
  return state.role === "boss" && state.bossFarm && state.bossFarm !== "all" ? state.bossFarm : "";
}

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  const farm = farmOf(opts);
  if (farm) init.headers["X-Farm"] = farm;
  delete init.farm;
  if (!["GET", "HEAD", "OPTIONS"].includes((init.method || "GET").toUpperCase())) init.headers["X-CSRF-Token"] = state.csrfToken;
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch("api/" + path, init);
  if (res.status === 401) { location.href = "login"; throw new Error("请先登录"); }
  if(path!=='me')checkFarmResponse(res);
  if (!res.ok) {
    let msg = res.statusText;
    let details;
    try { details=await res.json();msg=details.detail||msg; } catch { /* 忽略 */ }
    const error=new Error(msg);error.importFormat=details?.import_format;throw error;
  }
  const result = await res.json();
  if (path === "me") state.csrfToken = result.csrf_token || "";
  return result;
}

function checkFarmResponse(response){
  const farm=response.headers.get('X-Demo-Farm');
  if(state.loginRequired&&state.role==='admin'&&farm&&farm!==state.farm.id){
    apiCache.clear();location.replace('login');throw new Error('账号已切换，请重新登录');
  }
}

async function exportReport(pid){
  try{
    const response=await fetch(`api/periods/${pid}/export`);
    if(response.status===401){location.href='login';return;}
    checkFarmResponse(response);
    if(!response.ok)throw new Error('导出失败，请稍后重试');
    const disposition=response.headers.get('Content-Disposition')||'';
    const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i),plain=disposition.match(/filename="([^"]+)"/i);
    const link=document.createElement('a'),url=URL.createObjectURL(await response.blob());
    link.href=url;link.download=encoded?decodeURIComponent(encoded[1]):plain?plain[1]:'鳗场报表.xlsx';
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  }catch(error){setStatus(error.message||'导出失败');}
}

// 读取类请求的短期缓存：切页面、切班次、切池号时直接用已有数据，不再等网络。
// 写入后按前缀清掉相关缓存；数据对象本身就是页面上正在改的那份，所以不会不同步。
const apiCache = new Map();
// 缓存按「厂 + 地址」分开，老板切厂时不会拿到别的厂的数据
function cached(path, ttl = 20000, farm = farmOf()) {
  const key = `${farm}|${path}`;
  const hit = apiCache.get(key);
  if (hit && Date.now() - hit.t < ttl) return hit.p;
  const p = api(path, { farm });
  apiCache.set(key, { t: Date.now(), p });
  p.catch(() => apiCache.delete(key));
  return p;
}
function invalidate(...prefixes) {
  if (prefixes.includes("dashboard")) prefixes.push("feed-alert"); // 首页数据变了，漏喂提示也跟着重算
  if (prefixes.includes("payroll") || prefixes.some((x) => x.startsWith("boss"))) prefixes.push("boss/all");
  for (const k of [...apiCache.keys()]) {
    const path = k.slice(k.indexOf("|") + 1);
    if (prefixes.some((x) => path.startsWith(x))) apiCache.delete(k);
  }
}
const prefetch = (path, ttl) => cached(path, ttl).catch(() => {});

function fmt(v, digits = 1) {
  if (v === null || v === undefined || v === "") return "";
  return Number(v).toLocaleString("zh-CN", { maximumFractionDigits: digits });
}
const dash = (s) => (s === "" ? "—" : s);
const pct = (v, d = 2) => (v == null ? "" : fmt(v * 100, d) + "%");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function parseNum(str) {
  const s = String(str).trim().replace(/,/g, "");
  if (s === "") return { ok: true, v: null };
  const v = Number(s);
  return Number.isFinite(v) ? { ok: true, v } : { ok: false };
}

let toastTimer;
function setStatus(text, isErr = false) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.toggle("err", isErr);
  el.classList.add("show");
  clearTimeout(toastTimer);
  if (!isErr) toastTimer = setTimeout(() => el.classList.remove("show"), text === "已保存" ? 1200 : 5000);
  const live = $("#saveLive");
  if (live) { live.textContent = isErr ? "保存失败" : text; live.className = isErr ? "err" : ""; }
}

function debounce(fn, ms) {
  let t;
  const wrapped = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  wrapped.flush = () => { clearTimeout(t); fn(); };
  return wrapped;
}

const halfLabel = (h = state.half) => `${h.year}年${h.month}月${h.half === "上" ? "上半月" : "下半月"}`;
const halfRange = (h = state.half) => (h.half === "上" ? "1-15" : `16-${lastDay(h.year, h.month)}`);
const isDone = (r) => r.feed_kg != null || r.daily_feed_sum != null || r.daily_feed != null || r.injured != null || r.daily_injury_sum != null;

const ICON = {
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 8v5M12 16.5v.5"/><circle cx="12" cy="12" r="9"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  table: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/></svg>',
  report: '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M9 12h7M9 16h7"/></svg>',
  feed: '<svg viewBox="0 0 24 24"><path d="M4 11h16a8 8 0 0 1-16 0z"/><path d="M9 4c0 2 2 2 2 4M14 4c0 2 2 2 2 4"/></svg>',
  hurt: '<svg viewBox="0 0 24 24"><path d="M3 12c3-5 9-6 14-3l4-2-1 5 1 5-4-2c-5 3-11 2-14-3z"/><circle cx="8" cy="11" r=".8"/></svg>',
  pond: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M5 20h14"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 4v12M7 11l5 5 5-5M5 20h14"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
  logout: '<svg viewBox="0 0 24 24"><path d="M14 4h5v16h-5M10 8l-4 4 4 4M6 12h10"/></svg>',
  left: '<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>',
  right: '<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
  print: '<svg viewBox="0 0 24 24"><path d="M7 9V3h10v6M7 17H4v-7h16v7h-3M7 14h10v7H7z"/></svg>',
  close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  lock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
  money: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9.5v5M18 9.5v5"/></svg>',
};

// ---------- 弹窗 ----------

// 底部弹窗：统一加右上角关闭按钮；内容由各页面传入
function openSheet(html, bind) {
  const body = $("#sheetBody");
  body.innerHTML = `<button type="button" class="sheet-close" aria-label="关闭">${ICON.close}</button>${html}`;
  $(".sheet-close", body).onclick = closeSheet;
  const dlg = $("#sheet");
  if (!dlg.open) dlg.showModal();
  dlg.scrollTop = 0;
  bind?.(dlg);
}

// 确认弹窗（代替浏览器自带的 confirm，手机上不再显示网址和小按钮）。
// 传一句话时，问号前是标题、问号后是说明；也可以传 {title, message, html, ok, danger}。
function askConfirm(msg, extra = {}) {
  let o = extra;
  if (typeof msg === "string") {
    const i = msg.search(/[？?]/);
    o = { title: i >= 0 ? msg.slice(0, i + 1) : msg, message: i >= 0 ? msg.slice(i + 1).trim() : "", ...extra };
  } else o = msg;
  const danger = o.danger ?? /删除|移除/.test(o.title);
  const caution = !danger && /撤销|离职/.test(o.title);
  const ok = o.ok || (/删除/.test(o.title) ? "删除" : /移除/.test(o.title) ? "移除" : /撤销/.test(o.title) ? "撤销结算"
    : /离职/.test(o.title) ? "设为离职" : /^解锁/.test(o.title) ? "解锁" : /^锁定/.test(o.title) ? "锁定" : /发放/.test(o.title) ? "确认发放" : "确定");
  const icon = danger ? ICON.trash : /锁定|解锁/.test(o.title) ? ICON.lock : /发放/.test(o.title) ? ICON.money : ICON.warn;
  return new Promise((resolve) => {
    const dlg = $("#confirm");
    $("#confirmBody").innerHTML = `
      <div class="big-icon ${danger ? "bad" : caution ? "warn" : "brand"}">${icon}</div>
      <h2>${esc(o.title)}</h2>
      ${o.message ? `<p>${esc(o.message).replace(/\n/g, "<br>")}</p>` : ""}
      ${o.html || ""}
      <div class="sheet-actions"><button type="button" class="btn lg" data-no>${o.cancel || "取消"}</button>
        <button type="button" class="btn lg ${danger ? "danger-solid" : "cta"}" data-yes>${ok}</button></div>`;
    const done = (v) => { if (dlg.open) dlg.close(); resolve(v); };
    $("[data-no]", dlg).onclick = () => done(false);
    $("[data-yes]", dlg).onclick = () => done(true);
    dlg.oncancel = (e) => { e.preventDefault(); done(false); };
    dlg.onclick = (e) => { if (e.target === dlg) done(false); };
    dlg.showModal();
    $("[data-yes]", dlg).focus();
  });
}
const closeSheet = () => $("#sheet").open && $("#sheet").close();
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });

// ---------- 保存：每期数据 ----------

const dirtyRows = new Set();
const dirtyFields = new Map();
let activeEntrySaves = 0;
let activeDailySaves = 0;
function markEntry(id, ...fields) {
  dirtyRows.add(id);
  const selected = dirtyFields.get(id) || new Set();
  fields.forEach(field => selected.add(field));
  dirtyFields.set(id, selected);
}
let savingEntries = Promise.resolve();
let refreshCalc = () => {};

const saveEntries = debounce(() => {
  if (!dirtyRows.size || !state.report) return;
  const pid = state.report.period.id;
  const period = Object.fromEntries(["year", "month", "day_range"].map(k => [k,state.report.period[k]]));
  const rows = [...dirtyRows].map((id) => {
    const row = state.report.rows.find(r => r.pond_id === id);
    const fields = [...(dirtyFields.get(id) || [])];
    return { pond_id:id, fields, ...Object.fromEntries(fields.map(f => [f,row[f] ?? null])) };
  });
  dirtyRows.clear();
  rows.forEach(r => dirtyFields.delete(r.pond_id));
  activeEntrySaves++;
  setStatus("保存中…");
  savingEntries = savingEntries.then(async () => {
    try {
      const rep = await api(`periods/${pid}/entries`, { method: "PUT", json: { rows, patch: true, period } });
      if (state.report?.period.id !== pid) return;
      for (const r of rep.rows) {
        const local = state.report.rows.find((x) => x.pond_id === r.pond_id);
        if (local) local.calc = r.calc;
      }
      state.report.totals = rep.totals;
      invalidate("half/report", "dashboard", "boss");
      refreshCalc();
      setStatus("已保存");
    } catch (err) {
      rows.forEach((r) => markEntry(r.pond_id, ...r.fields));
      setStatus("保存失败：" + err.message, true);
    } finally { activeEntrySaves--; }
  });
}, 500);

// ---------- 保存：每日早晚记录 ----------

const pendingDaily = new Map();
let savingDaily = Promise.resolve();
const dailyKey = (c) => `${c.year}|${c.month}|${c.kind}|${c.day}|${c.shift}|${c.pond_id}`;

const saveDaily = debounce(() => {
  if (!pendingDaily.size) return;
  const cells = [...pendingDaily.values()];
  pendingDaily.clear();
  activeDailySaves++;
  const groups = {};
  for (const c of cells) (groups[`${c.year}|${c.month}|${c.kind}`] ||= []).push(c);
  setStatus("保存中…");
  savingDaily = savingDaily.then(async () => {
    try {
      for (const list of Object.values(groups)) {
        const { year, month, kind } = list[0];
        await api("daily", { method: "PUT", json: { year, month, kind, cells: list } });
      }
      invalidate("dashboard", "half/report", "boss");
      setStatus("已保存");
    } catch (err) {
      cells.forEach((c) => { if (!pendingDaily.has(dailyKey(c))) pendingDaily.set(dailyKey(c), c); });
      setStatus("保存失败，请检查网络", true);
    } finally { activeDailySaves--; }
  });
}, 500);

function queueDaily(cell) {
  pendingDaily.set(dailyKey(cell), cell);
  setStatus("保存中…");
  saveDaily();
}

async function flushPending() {
  if (dirtyRows.size) saveEntries.flush();
  if (pendingDaily.size) saveDaily.flush();
  await savingEntries;
  await savingDaily;
  return !dirtyRows.size && !pendingDaily.size;
}

window.addEventListener("beforeunload", (e) => {
  if (dirtyRows.size || pendingDaily.size || activeEntrySaves || activeDailySaves) { e.preventDefault(); e.returnValue = ""; }
});

// ---------- 半月切换（代替原来的「新建期间」） ----------

const halfKey = (h) => `half/report?year=${h.year}&month=${h.month}&half=${encodeURIComponent(h.half)}`;

// 半月信息和期报一次取回（原来要先后发两次请求）
async function loadHalf() {
  const h = state.half;
  const r = await cached(halfKey(h));
  state.halfInfo = r.info;
  state.pid = r.info.id;
  state.report = r.report;
  // 顺手预取前后两个半月，左右切换时不用等
  const next = h.half === "上" ? { ...h, half: "下" } : { year: h.month === 12 ? h.year + 1 : h.year, month: h.month === 12 ? 1 : h.month + 1, half: "上" };
  const prev = h.half === "下" ? { ...h, half: "上" } : { year: h.month === 1 ? h.year - 1 : h.year, month: h.month === 1 ? 12 : h.month - 1, half: "下" };
  setTimeout(() => { prefetch(halfKey(prev)); prefetch(halfKey(next)); }, 300);
}

function halfNavHTML() {
  return `<div class="stepper">
    <button class="icon-btn" data-step="-1" aria-label="上一个半月">${ICON.left}</button>
    <div class="stepper-label"><b>${state.half.year}年${state.half.month}月</b>
      <small>${state.half.half === "上" ? "上半月" : "下半月"} · ${halfRange()}日</small></div>
    <button class="icon-btn" data-step="1" aria-label="下一个半月">${ICON.right}</button>
  </div>`;
}

function bindHalfNav() {
  $$(".stepper [data-step]").forEach((b) => b.addEventListener("click", async () => {
    if (!(await flushPending())) return;
    let { year, month, half } = state.half;
    if (b.dataset.step === "1") {
      if (half === "上") half = "下"; else { half = "上"; month += 1; if (month > 12) { month = 1; year += 1; } }
    } else if (half === "下") half = "上";
    else { half = "下"; month -= 1; if (month < 1) { month = 12; year -= 1; } }
    state.half = { year, month, half };
    render();
  }));
}

function startPeriodHTML() {
  const prev = state.halfInfo?.prev_label;
  return `<div class="card empty">
    <h4>${esc(halfLabel())}还没有期报</h4>
    <p>${prev ? `开始后自动沿用「${esc(prev)}」：上期的转化率、现有重量、现尾数就是本期的转化率、原重量、原尾数，上期改了本期跟着变。<br>每月 1 号、16 号会自动开始，一般不用点。` : "第一期要手填原重量、原尾数、转化率；以后每半月自动开始并沿用上期。"}</p>
    <button class="btn cta lg" id="startHalf">${ICON.plus}开始录入本期</button>
  </div>`;
}

function bindStart() {
  $("#startHalf")?.addEventListener("click", async () => {
    const r = await api("half", { method: "POST", json: state.half });
    invalidate("half/report", "dashboard", "boss");
    state.pid = r.id;
    if (state.page !== "pond") return go("pond");
    render();
  });
}

// 页面上用的厂名：老板端是正在看的那个厂，录入端是自己的厂
function farmTitle() {
  if (state.role === "boss" && state.bossFarm !== "all") return state.farms.find((f) => f.id === state.bossFarm)?.title || state.farm.title;
  return state.farm.title;
}

// ---------- 导航 ----------

const PAGE_TITLE = { home: "鳗场", today: "每日记录", pond: "期报录入", report: "报表", more: "更多" };
const SUB_TITLE = {
  "grid-entry": "表格录入", "grid-report": "报表表格", "month-feed": "每日投喂月表",
  "month-injury": "每日伤苗月表", ponds: "池号管理", import: "导入 Excel", boss: "老板后台", "report-import": "报表导入", "deleted-periods": "已删除期报",
};

$("#bottomnav").addEventListener("click", (e) => {
  const page = e.target.closest("button")?.dataset.page;
  if (page) go(page);
});
$("#backBtn").addEventListener("click", () => go(state.page));

async function go(page, sub = null) {
  if (!(await flushPending())) return;
  state.page = page;
  state.sub = sub;
  store.set("page", page);
  window.scrollTo(0, 0);
  $("#view").innerHTML = `<div class="skeleton"><i></i><i></i><i></i><i></i></div>`;
  render();
}

async function render() {
  const activeFarm=state.role==='boss'&&state.bossFarm!=='all'?state.farms.find(f=>f.id===state.bossFarm)||state.farm:state.farm;
  const allFarms=state.role==='boss'&&state.bossFarm==='all'&&state.farms.length>1;
  document.title=allFarms?'鳗场报表':activeFarm.title;
  $('#brandMark').textContent=allFarms?'鳗':activeFarm.mark;
  if (state.role === "boss") {
    document.body.classList.remove("staff-mode");
    $("#bottomnav").hidden = true;
    $("#backBtn").hidden = true;
    $("#brandMark").hidden = false;
    $("#title").textContent = allFarms ? "鳗场报表" : activeFarm.title;
    $("#subtitle").textContent = "经营概况 · 工资结算";
    $("#appbarRight").innerHTML = `<a class="icon-btn" href="logout" aria-label="退出登录">${ICON.logout}</a>`;
    const view = $("#view"); view.className = ""; view.onclick = null;
    try { await renderBoss(view); }
    catch (err) { view.innerHTML = `<div class="card empty"><h4>加载失败</h4><p>${esc(err.message)}</p><button class="btn" data-ui-action="reload">重新加载</button></div>`; }
    return;
  }
  const staff = state.role === "staff";
  if (staff) { state.page = "today"; state.sub = null; }
  document.body.classList.toggle("staff-mode", staff);
  $$("#bottomnav button").forEach((b) => b.classList.toggle("active", b.dataset.page === state.page));
  $("#bottomnav").hidden = staff;
  $("#backBtn").hidden = !state.sub;
  $("#brandMark").hidden = !!state.sub;
  $("#title").textContent = state.sub ? SUB_TITLE[state.sub] : staff ? `${state.farm.title} · 每日记录` : state.page === "home" ? state.farm.title : PAGE_TITLE[state.page];
  $("#subtitle").textContent = "";
  $("#appbarRight").innerHTML = state.loginRequired
    ? `<a class="icon-btn" href="logout" aria-label="退出登录">${ICON.logout}</a>` : "";
  refreshCalc = () => {};
  const view = $("#view");
  view.className = (state.sub && !["ponds", "import", "report-import", "deleted-periods"].includes(state.sub) ? "wide " : "") + (staff ? "staff " : "")
    + (state.page === "today" && !state.sub ? "has-submit" : "");
  view.onclick = null;
  if (state.needSetup) {  // 新厂还没有池号：先填池数
    $("#bottomnav").hidden = true; $("#backBtn").hidden = true;
    $("#title").textContent = state.farm.title; $("#subtitle").textContent = "第一次使用";
    renderSetup(view);
    return;
  }
  try {
    const fn = {
      home: renderHome, today: renderToday, pond: renderPond, report: renderReport, more: renderMore,
      "grid-entry": renderGridEntry, "grid-report": renderGridReport,
      "month-feed": (v) => renderMonth(v, "feed"), "month-injury": (v) => renderMonth(v, "injury"),
      ponds: renderPonds, import: renderImport, "report-import": renderReportImport, "deleted-periods": renderDeletedPeriods,
    }[state.sub || state.page] || renderHome;
    await fn(view);
  } catch (err) {
    view.innerHTML = `<div class="card empty"><h4>加载失败</h4><p>${esc(err.message)}</p><button class="btn" data-ui-action="reload">重新加载</button></div>`;
  }
}

// =====================================================================
// 首页（管理员）
// =====================================================================

// 新厂第一次进入：填有几口池，自动生成 1#～N#，之后在「更多 → 池号管理」里改名、分新老鳗、停用
function renderSetup(view) {
  view.innerHTML = `
    <div class="hello"><h2>欢迎使用 ${esc(state.farm.title)}</h2><p>第一次使用，先告诉系统有几口池。</p></div>
    <form class="card" id="setupForm" novalidate>
      <label class="field"><span>池子数量 <span class="unit">口</span></span>
        <input name="count" type="number" inputmode="numeric" min="1" max="300" step="1" required placeholder="比如 30" autofocus></label>
      <div class="field" style="margin-top:14px"><span>先全部设为</span>
        <div class="seg small" id="setupCat">${["新", "老"].map((c, i) => `<button type="button" data-cat="${c}" class="${i ? "" : "active"}">${c}鳗</button>`).join("")}</div></div>
      <p class="hint" style="margin-top:12px" id="setupPreview">会生成 1#、2#、3#… 这样的池号。</p>
      <p class="err" id="setupErr" role="alert"></p>
      <button class="btn cta block lg" type="submit">生成池号</button>
    </form>
    <p class="hint">之后可以在「更多 → 池号管理」里改池号名字、分新鳗老鳗、加池或停用。</p>`;
  const f = $("#setupForm", view);
  let cat = "新";
  $("#setupCat", view).onclick = (e) => {
    const b = e.target.closest("[data-cat]"); if (!b) return;
    cat = b.dataset.cat;
    $$("[data-cat]", view).forEach((x) => x.classList.toggle("active", x === b));
  };
  f.elements.count.oninput = () => {
    const n = Math.floor(Number(f.elements.count.value));
    $("#setupPreview", view).textContent = n >= 1 && n <= 300 ? `会生成 1# 到 ${n}#，共 ${n} 口池。` : "会生成 1#、2#、3#… 这样的池号。";
  };
  f.onsubmit = async (e) => {
    e.preventDefault();
    const n = Math.floor(Number(f.elements.count.value));
    if (!(n >= 1 && n <= 300)) { $("#setupErr", view).textContent = "请填 1～300 之间的数字"; return; }
    if (!(await askConfirm({ title: `生成 ${n} 口池？`, message: `池号 1# 到 ${n}#，全部先设为${cat}鳗。生成后可以在池号管理里修改。`, ok: "生成" }))) return;
    const btn = $("button[type=submit]", f); btn.disabled = true;
    try {
      await api("ponds/setup", { method: "POST", json: { count: n, category: cat } });
      state.needSetup = false; apiCache.clear();
      setStatus(`已生成 ${n} 口池`);
      go("more", "ponds");
    } catch (err) { $("#setupErr", view).textContent = err.message; btn.disabled = false; }
  };
}

// 管理端首页的漏喂提示（和老板端同一套判断，按北京时间的今天）
function renderFeedAlert(a) {
  const host = $("#feedAlert");
  if (!host || !a?.today) return;
  const t = a.today, [, month] = t.date.split("-").map(Number);
  const fc = feedCheck({ live: t, month, day: t.day, shiftsOf: (r) => t.shifts[r.pond_id] || {}, needs: (r) => r.needs_feed });
  const shift = SHIFTS.find((s) => a.rows.some((r) => fc.st(r, s) === "bad")) || "早";
  host.innerHTML = fc.card(a.rows, { action: `<button class="btn small" data-shift="${shift}" style="margin-top:10px">去补填${shift}班 ›</button>` });
}

async function renderHome(view) {
  const date = todayStr();
  const pAlert = cached("feed-alert", 30000);
  pAlert.catch(() => {});
  const d = await cached(`dashboard?date=${date}`, 10000);
  // 预取最常去的两页：今日填写、本期报表
  setTimeout(() => {
    const [py, pm] = date.split("-").map(Number);
    prefetch(`daily?year=${py}&month=${pm}&kind=feed`);
    prefetch(`daily?year=${py}&month=${pm}&kind=injury`);
    prefetch(halfKey(halfOf(date)));
  }, 200);
  const [, m, day] = date.split("-").map(Number);
  const wd = WEEK[new Date().getDay()];
  const hour = new Date().getHours();
  const greet = hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好";

  const tile = (s) => {
    const x = d.shifts[s];
    const done = x.total && x.filled >= x.total;
    const chip = done ? `<span class="chip ok">${ICON.check}已完成</span>`
      : x.filled ? `<span class="chip warn">进行中</span>` : `<span class="chip idle">未开始</span>`;
    return `<button class="shift-tile" data-shift="${s}">
      <div class="top"><span class="name">${s}班</span>${chip}</div>
      <div class="count">${x.filled}<small> / ${x.total} 池</small></div>
      <div class="bar ${done ? "" : "warn"}"><i style="width:${x.total ? (x.filled / x.total) * 100 : 0}%"></i></div>
      <div class="meta"><span>投喂 <b>${fmt(x.feed)}</b> kg</span><span>伤苗 <b>${fmt(x.injury, 0)}</b></span></div>
    </button>`;
  };
  const cur = hour < 12 ? "早" : "晚";
  const miss = d.shifts[cur].missing;
  const missHTML = d.shifts[cur].filled && miss.length
    ? `<div class="missing">${cur}班还没填：<b>${miss.slice(0, 12).map(esc).join("、")}${miss.length > 12 ? ` 等 ${miss.length} 个池` : ""}</b></div>` : "";

  const maxFeed = Math.max(1, ...d.week.map((w) => w.feed));
  const weekHTML = d.week.map((w, i) => `
    <div class="col ${i === 6 ? "today" : ""}">
      <span class="v">${w.feed ? fmt(w.feed, 0) : ""}</span>
      <span class="b" style="height:${Math.max(3, (w.feed / maxFeed) * 100)}px"></span>
      <span class="d">${i === 6 ? "今" : w.day}</span>
    </div>`).join("");

  let periodHTML;
  if (d.period) {
    const t = d.period.totals, all = t["总计"], o = t["老"], n = t["新"];
    periodHTML = `<div class="kpi-grid">
      <div class="kpi hero"><div class="k">现有库存</div><div class="v">${fmt(all.cur_weight, 0)}<small>kg</small></div>
        <div class="s">老 ${fmt(o.cur_weight, 0)} · 新 ${fmt(n.cur_weight, 0)}</div></div>
      <div class="kpi"><div class="k">合计尾数</div><div class="v">${fmt(all.cur_count, 0)}</div>
        <div class="s">平均 P 数 ${dash(fmt(all.avg_p))}</div></div>
      <div class="kpi"><div class="k">本期累计投喂</div><div class="v">${fmt(d.half.feed_sum, 0)}<small>kg</small></div>
        <div class="s">伤苗 ${fmt(d.half.injury_sum, 0)} 尾</div></div>
    </div>`;
  } else {
    periodHTML = `<div class="kpi-grid">
      <div class="kpi"><div class="k">本期累计投喂</div><div class="v">${fmt(d.half.feed_sum, 0)}<small>kg</small></div></div>
      <div class="kpi"><div class="k">本期累计伤苗</div><div class="v">${fmt(d.half.injury_sum, 0)}<small>尾</small></div></div>
    </div>
    <div class="card" style="margin-top:10px;display:flex;align-items:center;gap:12px">
      <div style="flex:1;font-size:14px;color:var(--muted)">本期还没开始期报录入，库存和 P 数暂时算不出来</div>
      <button class="btn cta small" id="homeStart">开始</button></div>`;
  }

  view.innerHTML = `
    <div class="hello"><h2>${m}月${day}日 周${wd}</h2><p>${greet}，看看今天的情况</p></div>
    <div class="section-head"><h3>今日填写</h3><button data-go="today">去填写 ›</button></div>
    <div class="shift-grid">${tile("早")}${tile("晚")}</div>
    ${missHTML}
    <div id="feedAlert" style="margin-top:12px"></div>
    <div class="section-head"><h3>${esc(halfLabel(halfOf(date)))} · ${esc(d.half.day_range)}日</h3><button data-go="report">看报表 ›</button></div>
    ${periodHTML}
    <div class="section-head"><h3>近 7 天投喂（kg）</h3></div>
    <div class="card"><div class="week">${weekHTML}</div></div>
    <div class="quick">
      <button class="btn" data-go="pond">${ICON.pond}期报录入</button>
      <button class="btn" id="homeExport" ${d.half.id ? "" : "disabled"}>${ICON.download}导出 Excel</button>
    </div>`;

  pAlert.then(renderFeedAlert, () => {});

  view.onclick = async (e) => {
    if (state.page !== "home" || state.sub) return;
    const t = e.target.closest("[data-shift],[data-go],#homeExport,#homeStart");
    if (!t) return;
    if (t.dataset.shift) { state.today = { date, shift: t.dataset.shift }; return go("today"); }
    if (t.dataset.go) { state.half = halfOf(date); return go(t.dataset.go); }
    if (t.id === "homeExport" && d.half.id) exportReport(d.half.id);
    if (t.id === "homeStart") {
      state.half = halfOf(date);
      await api("half", { method: "POST", json: state.half });
      invalidate("half/report", "dashboard", "boss");
      go("pond");
    }
  };
}

// =====================================================================
// 每日记录（员工每天早晚填：投喂量、伤苗）
// =====================================================================

async function renderToday(view) {
  const [y, m, d] = state.today.date.split("-").map(Number);
  // 同一个月的数据缓存着：切早晚班、切日期都是本地切换，不用重新请求
  const [feed, injury] = await Promise.all([
    cached(`daily?year=${y}&month=${m}&kind=feed`, 60000),
    cached(`daily?year=${y}&month=${m}&kind=injury`, 60000),
  ]);
  const shift = state.today.shift;
  const other = shift === "早" ? "晚" : "早";
  const grids = { feed, injury };
  const val = (kind, s, pid) => grids[kind].values[`${d}|${s}|${pid}`];
  const isToday = state.today.date === todayStr();
  const expected = feed.ponds.filter((p) => p.category !== "暂无");
  const empties = feed.ponds.filter((p) => p.category === "暂无");
  const filled = (pid) => val("feed", shift, pid) != null || val("injury", shift, pid) != null;

  const row = (p) => {
    const of = val("feed", other, p.id), oi = val("injury", other, p.id);
    const hint = of != null || oi != null ? `${other}班 ${fmt(of) || "—"}kg · 伤${fmt(oi, 0) || "—"}` : "";
    return `<div class="pond-row ${filled(p.id) ? "filled" : ""}" data-p="${p.id}">
      <div class="who"><span class="dot">${ICON.check}</span>
        <span class="nm"><b>${esc(p.name)}</b><small>${esc(hint)}</small></span></div>
      <input class="num-input" data-kind="feed" inputmode="decimal" enterkeyhint="next" autocomplete="off" placeholder="kg" value="${val("feed", shift, p.id) ?? ""}" aria-label="${esc(p.name)} 投喂">
      <input class="num-input inj" data-kind="injury" inputmode="numeric" enterkeyhint="next" autocomplete="off" placeholder="尾" value="${val("injury", shift, p.id) ?? ""}" aria-label="${esc(p.name)} 伤苗">
    </div>`;
  };
  const head = `<div class="pond-row head"><span style="flex:1">池号</span><span class="c1">投喂 kg</span><span class="c2">伤苗 尾</span></div>`;

  view.innerHTML = `
    <div class="stepper">
      <button class="icon-btn" id="prevDay" aria-label="前一天">${ICON.left}</button>
      <div class="stepper-label daily-date-inline">
        <input type="date" id="datePick" value="${state.today.date}" min="2000-01-01" max="2100-12-31" aria-label="选择查看或填写的日期">
        <small>周${WEEK[new Date(y, m - 1, d).getDay()]}${isToday ? " · 今天" : ""}</small>
        <button class="btn small" id="backToToday" ${isToday ? "hidden" : ""}>今天</button>
      </div>
      <button class="icon-btn" id="nextDay" aria-label="后一天">${ICON.right}</button>
    </div>
    <div class="seg" id="shiftSeg" style="margin-bottom:14px">
      ${SHIFTS.map((s) => `<button data-s="${s}" class="${s === shift ? "active" : ""}">${s}班</button>`).join("")}
    </div>
    <div class="progress-line"><span>已填 <b id="pgN">0</b> / ${expected.length} 池</span><div class="bar warn" id="pgBar"><i></i></div></div>
    <div class="pond-list" id="todayList">${head}${expected.map(row).join("")}</div>
    ${empties.length ? `<details class="empty-ponds"><summary>空池 ${empties.length} 个（${empties.map((p) => esc(p.name)).join("、")}），一般不用填</summary>
      <div class="pond-list" id="emptyList">${head}${empties.map(row).join("")}</div></details>` : ""}
    <div class="submit-bar"><div class="inner">
      <div class="sum"><span>投喂<b id="sumFeed">0</b></span><span>伤苗<b id="sumInj">0</b></span><span>状态<b id="saveLive" style="font-size:14px">—</b></span></div>
      <button class="btn cta" id="submitShift">提交${shift}班</button>
    </div></div>`;

  const update = () => {
    let n = 0, fs = 0, is = 0;
    for (const p of expected) if (filled(p.id)) n++;
    for (const p of feed.ponds) { fs += val("feed", shift, p.id) || 0; is += val("injury", shift, p.id) || 0; }
    $("#pgN").textContent = n;
    const bar = $("#pgBar");
    bar.firstElementChild.style.width = `${expected.length ? (n / expected.length) * 100 : 0}%`;
    bar.classList.toggle("warn", n < expected.length);
    $("#sumFeed").textContent = fmt(fs) || "0";
    $("#sumInj").textContent = fmt(is, 0) || "0";
  };
  update();

  const onInput = (e) => {
    const el = e.target;
    if (!el.dataset.kind) return;
    const p = parseNum(el.value);
    el.classList.toggle("bad", !p.ok);
    if (!p.ok) { setStatus("请填数字", true); return; }
    const line = el.closest(".pond-row");
    const pid = Number(line.dataset.p);
    const key = `${d}|${shift}|${pid}`;
    if (p.v == null) delete grids[el.dataset.kind].values[key]; else grids[el.dataset.kind].values[key] = p.v;
    line.classList.toggle("filled", filled(pid));
    queueDaily({ year: y, month: m, kind: el.dataset.kind, day: d, shift, pond_id: pid, value: p.v });
    update();
  };
  const onKey = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const inputs = $$("#todayList input");
    const next = inputs[inputs.indexOf(e.target) + 1];
    if (next) { next.focus(); next.select(); } else e.target.blur();
  };
  for (const list of $$(".pond-list", view)) {
    list.addEventListener("input", onInput);
    list.addEventListener("keydown", onKey);
    list.addEventListener("focusin", (e) => { if (e.target.matches("input")) { e.target.select(); e.target.scrollIntoView({ block: "center", behavior: "smooth" }); } });
  }

  const moveDate = async (dt) => {
    if (!await flushPending()) return;
    state.today.date = dateStr(dt);
    render();
  };
  $("#backToToday").addEventListener("click", () => moveDate(new Date()));
  $("#prevDay").addEventListener("click", () => moveDate(new Date(y, m - 1, d - 1)));
  $("#nextDay").addEventListener("click", () => moveDate(new Date(y, m - 1, d + 1)));
  $("#datePick").addEventListener("change", async (e) => {
    const selected = e.target.value;
    if (!selected || !e.target.validity.valid) return;
    if (!await flushPending()) { e.target.value = state.today.date; return; }
    state.today.date = selected;
    render();
  });
  $("#shiftSeg").addEventListener("click", async (e) => {
    const s = e.target.dataset.s;
    if (s && s !== shift) { state.today.shift = s; if (!(await flushPending())) return; render(); }
  });

  $("#submitShift").addEventListener("click", async () => {
    const btn = $("#submitShift");
    btn.disabled = true; btn.textContent = "保存中…";
    const ok = await flushPending();
    btn.disabled = false; btn.textContent = `提交${shift}班`;
    if (!ok) {
      return openSheet(`<div class="big-icon warn">${ICON.warn}</div><h2>还没保存成功</h2>
        <p>可能是网络不好。数据还在手机上，请稍后再点一次「提交」。</p>
        <div class="sheet-actions one"><button class="btn brand lg" data-close>知道了</button></div>`,
      (dlg) => $("[data-close]", dlg).onclick = closeSheet);
    }
    const missing = expected.filter((p) => !filled(p.id));
    const stats = `<div class="stats"><div><b>${expected.length - missing.length}/${expected.length}</b><span>已填池</span></div>
      <div><b>${$("#sumFeed").textContent}</b><span>投喂 kg</span></div><div><b>${$("#sumInj").textContent}</b><span>伤苗 尾</span></div></div>`;
    const dayLabel = `${m}月${d}日${shift}班`;
    if (missing.length) {
      openSheet(`<div class="big-icon warn">${ICON.warn}</div><h2>还有 ${missing.length} 个池没填</h2>${stats}
        <div class="names">${missing.map((p) => esc(p.name)).join("、")}</div>
        <p>已填的都保存好了。没填的池如果今天没投喂、没伤苗，可以直接提交。</p>
        <div class="sheet-actions"><button class="btn" data-back>继续填写</button><button class="btn cta" data-done>仍然提交</button></div>`,
      (dlg) => {
        $("[data-back]", dlg).onclick = () => {
          closeSheet();
          const first = $(`.pond-row[data-p="${missing[0].id}"] input`);
          first?.focus();
        };
        $("[data-done]", dlg).onclick = () => done(dayLabel, stats);
      });
    } else done(dayLabel, stats);
  });

  function done(label, stats) {
    openSheet(`<div class="big-icon ok">${ICON.check}</div><h2>${label}已提交</h2>${stats}
      <p>数据已经保存，管理员那边马上能看到。</p>
      <div class="sheet-actions one"><button class="btn brand lg" data-close>好的</button></div>`,
    (dlg) => $("[data-close]", dlg).onclick = closeSheet);
  }
}

// =====================================================================
// 期报录入（一次一个池）
// =====================================================================

const POND_FIELDS = {
  feed_kg: { label: "投料", unit: "kg" },
  daily_feed: { label: "当日投喂", unit: "kg" },
  injured: { label: "伤苗", unit: "尾" },
  conv_rate: { label: "转化率", unit: "%", pct: true },
  orig_weight: { label: "原重量", unit: "kg" },
  orig_count: { label: "原尾数", unit: "尾" },
  cur_count_override: { label: "现尾数", unit: "尾" },
};

// 转化率、原重量、原尾数空着时实时沿用上一期：输入框里显示灰色的「沿用上期 …」
function inheritPh(r, f) {
  const v = r.calc?.inherit?.[f];
  if (v == null) return "";
  return `沿用上期 ${f === "conv_rate" ? fmt(v * 100, 2) : fmt(v, f === "orig_count" ? 0 : 1)}`;
}

function inputValue(r, f) {
  const v = r[f];
  if (v == null) return "";
  return POND_FIELDS[f].pct ? String(+(v * 100).toFixed(4)) : String(v);
}

async function renderPond(view) {
  await loadHalf();
  if (!state.pid) { view.innerHTML = halfNavHTML() + startPeriodHTML(); bindHalfNav(); bindStart(); return; }
  const rows = state.report.rows;
  if (!rows.length) { view.innerHTML = halfNavHTML() + `<div class="card empty"><h4>没有启用的池号</h4><p>到「更多 → 池号管理」添加</p></div>`; bindHalfNav(); return; }
  let idx = rows.findIndex((r) => r.pond_id === state.pondId);
  if (idx < 0) idx = 0;
  const r = rows[idx];
  state.pondId = r.pond_id;
  store.set("pondId", r.pond_id);
  const next = rows[idx + 1];
  const done = rows.filter(isDone).length;

  const field = (f, extra = "") => {
    const c = POND_FIELDS[f];
    return `<label class="field"><span>${c.label} <span class="unit">${c.unit}</span></span>
      <input data-f="${f}" inputmode="decimal" enterkeyhint="next" autocomplete="off" value="${esc(inputValue(r, f))}" ${inheritPh(r, f) ? `placeholder="${esc(inheritPh(r, f))}"` : ""} ${extra}></label>`;
  };

  view.innerHTML = `
    ${halfNavHTML()}
    <div class="progress-line"><span>已填 <b>${done}</b> / ${rows.length} 池</span><div class="bar ${done < rows.length ? "warn" : ""}"><i style="width:${(done / rows.length) * 100}%"></i></div></div>
    <div class="chip-grid" id="chips">
      ${rows.map((x, i) => `<button class="pchip ${isDone(x) ? "done" : ""} ${i === idx ? "cur" : ""}" data-i="${i}">${esc(x.name)}</button>`).join("")}
    </div>
    <div class="card" id="pondCard">
      <div class="pond-head">
        <span class="big">${esc(r.name)}</span>
        <div class="seg small" id="catSeg">${CATS.map((c) => `<button data-c="${c}" class="${c === r.category ? "active" : ""}">${c}</button>`).join("")}</div>
      </div>
      <div class="fields-2">
        <div>${field("feed_kg")}<div class="field-hint" id="feedHint"></div></div>
        ${field("daily_feed")}
        <div>${field("injured", 'id="injuredInput"')}<div class="field-hint" id="injHint"></div></div>
        ${field("conv_rate")}
      </div>
      <div class="calc-box" id="calcBox"></div>
      <details class="more-fields" ${r.calc.orig_weight == null ? "open" : ""}>
        <summary><span>期初：原重量 <b>${dash(fmt(r.calc.orig_weight))}</b> kg · 原尾数 <b>${dash(fmt(r.calc.orig_count, 0))}</b>${Object.keys(r.calc.inherit || {}).length ? " · 沿用上期" : ""}</span></summary>
        ${Object.keys(r.calc.inherit || {}).length ? `<p class="hint" style="margin:8px 0">转化率、原重量、原尾数空着时，自动用上一期的转化率、现有重量、现尾数，上一期改了这里跟着变；填了数就以填的为准，清空恢复自动。</p>` : ""}
        <div class="fields-2">
          ${field("orig_weight")}
          ${field("orig_count")}
          ${field("cur_count_override", 'id="ccInput"')}
          <label class="field">备注<textarea data-f="note">${esc(r.note)}</textarea></label>
        </div>
      </details>
    </div>
    <div class="pager">
      <button class="btn lg" id="prevPond" ${idx === 0 ? "disabled" : ""}>上一池</button>
      <button class="btn cta lg" id="nextPond">${next ? `下一池 ${esc(next.name)}` : "完成，看报表"}</button>
    </div>`;

  bindHalfNav();
  refreshCalc = () => drawPondCalc(r);
  drawPondCalc(r);

  const card = $("#pondCard");
  card.addEventListener("input", (e) => {
    const el = e.target, f = el.dataset.f;
    if (!f) return;
    if (f === "note") r.note = el.value;
    else {
      const p = parseNum(el.value);
      el.classList.toggle("bad", !p.ok);
      if (!p.ok) { setStatus("请填数字", true); return; }
      r[f] = p.v == null ? null : POND_FIELDS[f].pct ? p.v / 100 : p.v;
    }
    markEntry(r.pond_id, f);
    setStatus("保存中…");
    saveEntries();
    $(`#chips [data-i="${idx}"]`).classList.toggle("done", isDone(r));
  });
  card.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.target.tagName !== "INPUT") return;
    e.preventDefault();
    const inputs = $$("input", card).filter((x) => x.offsetParent);
    const n = inputs[inputs.indexOf(e.target) + 1];
    if (n) { n.focus(); n.select(); } else e.target.blur();
  });
  card.addEventListener("focusin", (e) => { if (e.target.matches("input")) e.target.select(); });
  card.addEventListener("click", (e) => {
    if (e.target.id === "useFeedSum") {
      e.preventDefault();
      const inp = $('input[data-f="feed_kg"]', card);
      inp.value = String(r.daily_feed_sum);
      inp.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
  $("#catSeg").addEventListener("click", (e) => {
    const c = e.target.dataset.c;
    if (!c) return;
    r.category = c;
    $$("#catSeg button").forEach((b) => b.classList.toggle("active", b.dataset.c === c));
    markEntry(r.pond_id, "category");
    saveEntries();
  });

  const goIdx = async (i) => {
    if (!(await flushPending())) return;
    if (i >= rows.length) return go("report");
    state.pondId = rows[i].pond_id;
    window.scrollTo(0, 0);
    render();
  };
  $("#chips").addEventListener("click", (e) => { if (e.target.dataset.i) goIdx(Number(e.target.dataset.i)); });
  $("#prevPond").addEventListener("click", () => goIdx(idx - 1));
  $("#nextPond").addEventListener("click", () => goIdx(idx + 1));
}

function drawPondCalc(r) {
  const c = r.calc, p = state.report.period;
  $("#calcBox").innerHTML = `
    <div><span>增重 kg</span><b>${dash(fmt(c.gain))}</b></div>
    <div><span>现有重量 kg</span><b>${dash(fmt(c.cur_weight))}</b></div>
    <div><span>现尾数${c.count_is_manual ? "·手填" : ""}</span><b>${dash(fmt(c.cur_count, 0))}</b></div>
    <div><span>P 数</span><b>${dash(fmt(c.p))}</b></div>
    <div><span>点数${c.points_auto ? ` · ${p.month}/${c.points_day}自动` : ""}</span><b>${dash(pct(c.points))}</b></div>
    <div><span>伤苗${c.injured_auto ? "·每日合计" : ""}</span><b>${dash(fmt(c.injured, 0))}</b></div>`;
  $("#feedHint").innerHTML = r.daily_feed_sum != null
    ? `${p.day_from}-${p.day_to} 号每日投喂共 ${fmt(r.daily_feed_sum)} kg${r.feed_kg == null ? "，已自动使用；手填可覆盖" : "；清空投料可恢复自动合计"}` : "投料留空时自动使用每日投喂合计";
  $("#injHint").textContent = r.daily_injury_sum != null
    ? `每日记录共 ${fmt(r.daily_injury_sum, 0)} 尾${r.injured == null ? "，已自动使用" : "；清空则用合计"}`
    : "员工每天填的伤苗会自动加到这里";
  $("#injuredInput").placeholder = r.daily_injury_sum != null ? fmt(r.daily_injury_sum, 0) : "";
  $("#ccInput").placeholder = "自动 " + fmt(c.cur_count, 0);
}

// =====================================================================
// 报表
// =====================================================================

async function renderReport(view) {
  await loadHalf();
  if (!state.pid) { view.innerHTML = halfNavHTML() + startPeriodHTML(); bindHalfNav(); bindStart(); return; }
  const rep = state.report;
  const t = rep.totals, all = t["总计"], o = t["老"], n = t["新"];
  const filter = state.reportFilter;
  const rows = rep.rows.filter((r) => filter === "全部" || r.category === filter);

  view.innerHTML = `
    ${halfNavHTML()}
    <div class="kpi-grid">
      <div class="kpi hero"><div class="k">现有库存</div><div class="v">${fmt(all.cur_weight, 0)}<small>kg</small></div>
        <div class="s">老 ${fmt(o.cur_weight, 0)} · 新 ${fmt(n.cur_weight, 0)} · 原重 ${fmt(all.orig_weight, 0)}</div></div>
      <div class="kpi"><div class="k">合计尾数</div><div class="v">${fmt(all.cur_count, 0)}</div>
        <div class="s">老 ${fmt(o.cur_count, 0)}<br>新 ${fmt(n.cur_count, 0)}</div></div>
      <div class="kpi"><div class="k">平均 P 数</div><div class="v">${dash(fmt(all.avg_p))}</div>
        <div class="s">老 ${dash(fmt(o.avg_p))}<br>新 ${dash(fmt(n.avg_p))}</div></div>
      <div class="kpi"><div class="k">总投料</div><div class="v">${fmt(all.feed_kg, 0)}<small>kg</small></div>
        <div class="s">增重 ${fmt(all.gain, 0)} kg</div></div>
      <div class="kpi"><div class="k">伤苗合计</div><div class="v">${fmt(all.injured, 0)}<small>尾</small></div>
        <div class="s">老 ${fmt(o.injured, 0)} · 新 ${fmt(n.injured, 0)}</div></div>
    </div>
    <div class="section-head"><h3>各池明细</h3></div>
    <div class="seg small" id="filterSeg" style="margin-bottom:10px">
      ${["全部", "老", "新"].map((f) => `<button data-f="${f}" class="${f === filter ? "active" : ""}">${f}</button>`).join("")}
    </div>
    <div class="report-list" id="reportList">
      <div class="report-head"><span>池号</span><span style="text-align:right">现有重量 kg</span><span style="text-align:right">P 数</span><span></span></div>
      ${rows.map((r) => `
        <div class="report-row"><span class="n">${esc(r.name)} <span class="tag ${r.category}">${r.category}</span></span>
          <span class="r">${dash(fmt(r.calc.cur_weight))}</span><span class="r">${dash(fmt(r.calc.p))}</span>${ICON.right}</div>
        <div class="report-detail">
          <div><span>投料 kg${r.calc.feed_auto ? " · 每日合计" : ""}</span><b>${dash(fmt(r.calc.feed_kg))}</b></div>
          <div><span>转化率${r.calc.inherit?.conv_rate != null ? " · 沿用上期" : ""}</span><b>${dash(pct(r.calc.conv_rate, 1))}</b></div>
          <div><span>增重 kg</span><b>${dash(fmt(r.calc.gain))}</b></div>
          <div><span>原重量 kg${r.calc.inherit?.orig_weight != null ? " · 沿用上期" : ""}</span><b>${dash(fmt(r.calc.orig_weight))}</b></div>
          <div><span>伤苗 尾</span><b>${dash(fmt(r.calc.injured, 0))}</b></div>
          <div><span>现尾数</span><b>${dash(fmt(r.calc.cur_count, 0))}</b></div>
          <div><span>当日投喂 kg</span><b>${dash(fmt(r.daily_feed))}</b></div>
          <div><span>点数${r.calc.points_auto ? ` · ${rep.period.month}/${r.calc.points_day}自动` : ""}</span><b>${dash(pct(r.calc.points))}</b></div>
          <div><span>每日投喂合计</span><b>${dash(fmt(r.daily_feed_sum))}</b></div>
          ${r.note ? `<div class="note"><span>备注</span><b style="font-weight:500">${esc(r.note)}</b></div>` : ""}
        </div>`).join("")}
    </div>
    <div class="quick" style="margin-top:14px">
      <button class="btn brand" id="exportBtn">${ICON.download}导出 Excel</button>
      <button class="btn" id="tableBtn">${ICON.table}表格视图</button>
    </div>`;

  bindHalfNav();
  $("#filterSeg").addEventListener("click", (e) => { if (e.target.dataset.f) { state.reportFilter = e.target.dataset.f; render(); } });
  $("#reportList").addEventListener("click", (e) => e.target.closest(".report-row")?.classList.toggle("open"));
  $("#exportBtn").addEventListener("click", () => exportReport(state.pid));
  $("#tableBtn").addEventListener("click", () => go("report", "grid-report"));
}

// =====================================================================
// 更多
// =====================================================================

function menuItem(id, icon, title, sub, cls = "") {
  return `<button class="menu-item ${cls}" data-go="${id}"><span class="ico">${icon}</span>
    <span class="txt">${title}${sub ? `<small>${sub}</small>` : ""}</span>${cls ? "" : ICON.right}</button>`;
}

async function renderMore(view) {
  await loadHalf();
  const label = halfLabel();
  const removed = await api("deleted-periods");
  view.innerHTML = `
    <div class="section-head"><h3>${esc(label)}</h3></div>
    <div class="menu">
      ${menuItem("export", ICON.download, "导出 Excel", state.pid ? "报表 + 竖版 + 投喂、伤苗月表，按 A4 排版" : "本期还没有期报")}
      ${menuItem("grid-entry", ICON.table, "表格录入", "所有池号一张表，适合电脑")}
      ${menuItem("grid-report", ICON.report, "报表表格", "横版 / 竖版，可打印")}
    </div>
    <div class="section-head"><h3>每日记录</h3></div>
    <div class="menu">
      ${menuItem("month-feed", ICON.feed, "每日投喂月表", "整月早晚投喂，可直接修改")}
      ${menuItem("month-injury", ICON.hurt, "每日伤苗月表", "整月早晚伤苗，可直接修改")}
    </div>
    <div class="section-head"><h3>设置</h3></div>
    <div class="menu">
      ${menuItem("ponds", ICON.pond, "池号管理", "增减池号、调整顺序、老 / 新 / 空池")}
      ${menuItem("import", ICON.upload, "导入原 Excel 报表", "")}
      ${menuItem("report-import", ICON.upload, "报表导入", "导回网页导出的报表，支持旧版文件")}
      ${removed.length ? menuItem("deleted-periods", ICON.trash, "已删除期报", "恢复误删的期报，保留当前已填数据") : ""}
      ${state.pid ? menuItem("del-period", ICON.trash, `删除「${esc(label)}」期报`, "", "danger") : ""}
      ${state.loginRequired ? `<a class="menu-item" href="logout"><span class="ico">${ICON.logout}</span><span class="txt">退出登录</span></a>` : ""}
    </div>`;
  view.onclick = async (e) => {
    const id = e.target.closest("[data-go]")?.dataset.go;
    if (!id || state.page !== "more" || state.sub) return;
    if (id === "export") { if (state.pid) exportReport(state.pid); else setStatus("本期还没有期报"); }
    else if (id === "del-period") {
      if (!(await askConfirm(`确定删除「${label}」的期报数据？删除后可在「已删除期报」恢复。\n（每日投喂、伤苗记录不受影响）`))) return;
      await api(`periods/${state.pid}`, { method: "DELETE", json: {period:Object.fromEntries(["year","month","day_range"].map(k=>[k,state.report.period[k]]))} });
      invalidate("half/report", "dashboard", "boss");
      render();
    } else go("more", id);
  };
}

// ---------- 表格录入（电脑） ----------

const ENTRY_COLS = [
  { f: "feed_kg", label: "投料", unit: "kg" },
  { f: "conv_rate", label: "转化率", unit: "%", pct: true },
  { f: "orig_weight", label: "原重量", unit: "kg" },
  { f: "injured", label: "伤苗", unit: "留空用每日合计" },
  { f: "orig_count", label: "原尾数", unit: "尾" },
  { f: "daily_feed", label: "当日投喂", unit: "kg" },
  { f: "cur_count_override", label: "现尾数", unit: "留空自动算" },
];
const CALC_COLS = [
  { k: "gain", label: "增重", unit: "kg", show: (v) => fmt(v, 1) },
  { k: "cur_weight", label: "现有重量", unit: "kg", show: (v) => fmt(v, 1) },
  { k: "p", label: "P数", unit: "", show: (v) => fmt(v, 1) },
  { k: "points", label: "点数", unit: "%", show: (v) => pct(v) },
];

function gridNav(e) {
  const t = e.target;
  if (!(t instanceof HTMLInputElement)) return;
  let dir = 0;
  if (e.key === "Enter" || e.key === "ArrowDown") dir = 1;
  else if (e.key === "ArrowUp") dir = -1;
  if (!dir) return;
  e.preventDefault();
  const inputs = $$(`input[data-col="${t.dataset.col}"]`, e.currentTarget);
  const next = inputs[inputs.indexOf(t) + dir];
  if (next) { next.focus(); next.select(); }
}

async function renderGridEntry(view) {
  await loadHalf();
  if (!state.pid) { view.innerHTML = halfNavHTML() + startPeriodHTML(); bindHalfNav(); bindStart(); return; }
  const { rows } = state.report;
  const iv = (r, c) => (r[c.f] == null ? "" : c.pct ? String(+(r[c.f] * 100).toFixed(4)) : String(r[c.f]));
  view.innerHTML = `
    <div style="max-width:520px">${halfNavHTML()}</div>
    <p class="hint">白色格子填写，灰色自动算，改完自动保存。按 Enter 跳到下一个池号。</p>
    <div class="table-wrap">
      <table class="grid" id="entryGrid">
        <thead><tr><th class="sticky">池号</th><th>分类</th>
          ${ENTRY_COLS.map((c) => `<th>${c.label}<small>${c.unit}</small></th>`).join("")}
          ${CALC_COLS.map((c) => `<th>${c.label}<small>${c.unit}</small></th>`).join("")}<th>备注</th></tr></thead>
        <tbody>${rows.map((r) => `
          <tr data-pond="${r.pond_id}"><td class="sticky">${esc(r.name)}</td>
            <td><select data-f="category">${CATS.map((c) => `<option ${c === r.category ? "selected" : ""}>${c}</option>`).join("")}</select></td>
            ${ENTRY_COLS.map((c) => `<td class="${c.f === "cur_count_override" && r.calc.count_is_manual ? "manual" : ""}">
              <input size="7" data-f="${c.f}" data-col="${c.f}" inputmode="decimal" autocomplete="off" value="${esc(iv(r, c))}"
              ${c.f === "cur_count_override" ? `placeholder="${esc(fmt(r.calc.cur_count, 0))}"` : ""}
              ${inheritPh(r, c.f) ? `placeholder="${esc(inheritPh(r, c.f).replace("沿用上期 ", "上期"))}"` : ""}
              ${c.f === "injured" && r.daily_injury_sum != null ? `placeholder="${esc(fmt(r.daily_injury_sum, 0))}"` : ""}></td>`).join("")}
            ${CALC_COLS.map((c) => `<td class="calc" data-k="${c.k}">${c.show(r.calc[c.k])}</td>`).join("")}
            <td><input size="14" class="note" data-f="note" data-col="note" value="${esc(r.note)}"></td></tr>`).join("")}
        </tbody></table></div>`;
  bindHalfNav();
  const grid = $("#entryGrid");
  grid.addEventListener("keydown", gridNav);
  grid.addEventListener("focusin", (e) => { if (e.target.matches("input")) e.target.select(); });
  const onInput = (e) => {
    const el = e.target, f = el.dataset.f;
    if (!f) return;
    const row = state.report.rows.find((r) => r.pond_id === Number(el.closest("tr").dataset.pond));
    if (f === "category" || f === "note") row[f] = el.value;
    else {
      const p = parseNum(el.value);
      el.classList.toggle("bad", !p.ok);
      if (!p.ok) { setStatus("请填数字", true); return; }
      const col = ENTRY_COLS.find((c) => c.f === f);
      row[f] = p.v == null ? null : col.pct ? p.v / 100 : p.v;
    }
    markEntry(row.pond_id, f);
    setStatus("保存中…");
    saveEntries();
  };
  grid.addEventListener("input", onInput);
  grid.addEventListener("change", onInput);
  refreshCalc = () => {
    for (const r of state.report.rows) {
      const tr = $(`#entryGrid tr[data-pond="${r.pond_id}"]`);
      if (!tr) continue;
      for (const c of CALC_COLS) $(`td[data-k="${c.k}"]`, tr).textContent = c.show(r.calc[c.k]);
      $('input[data-f="cur_count_override"]', tr).placeholder = fmt(r.calc.cur_count, 0);
    }
  };
}

// ---------- 报表表格（横版 / 竖版） ----------

const METRICS = [
  ["投料", "kg", (r) => fmt(r.calc.feed_kg)],
  ["转化率", "%", (r) => pct(r.calc.conv_rate, 1)],
  ["增重", "kg", (r) => fmt(r.calc.gain)],
  ["原重量", "kg", (r) => fmt(r.calc.orig_weight)],
  ["现有重量", "kg", (r) => fmt(r.calc.cur_weight)],
  ["伤苗", "尾", (r) => fmt(r.calc.injured, 0)],
  ["现尾数", "尾", (r) => fmt(r.calc.cur_count, 0)],
  ["现有P数", "P", (r) => fmt(r.calc.p)],
  ["当日投喂", "kg", (r) => fmt(r.daily_feed)],
  ["点数", "%", (r) => pct(r.calc.points)],
];

async function renderGridReport(view) {
  await loadHalf();
  if (!state.pid) { view.innerHTML = halfNavHTML() + startPeriodHTML(); bindHalfNav(); bindStart(); return; }
  const { period, rows, totals } = state.report;
  const h = state.reportMode === "h";
  const body = h
    ? `<table class="grid"><thead><tr><th class="sticky">项目</th><th>单位</th>
        ${rows.map((r) => `<th>${esc(r.name)}<small><span class="tag ${r.category}">${r.category}</span></small></th>`).join("")}</tr></thead>
        <tbody>${METRICS.map(([n, u, g]) => `<tr><td class="sticky">${n}</td><td class="calc" style="text-align:center">${u}</td>
          ${rows.map((r) => `<td class="calc">${g(r)}</td>`).join("")}</tr>`).join("")}</tbody></table>`
    : `<table class="grid"><thead><tr><th class="sticky">池号</th><th>分类</th>${METRICS.map(([n, u]) => `<th>${n}<small>${u}</small></th>`).join("")}<th>备注</th></tr></thead>
        <tbody>${rows.map((r) => `<tr><td class="sticky">${esc(r.name)}</td><td class="calc" style="text-align:center"><span class="tag ${r.category}">${r.category}</span></td>
          ${METRICS.map(([, , g]) => `<td class="calc">${g(r)}</td>`).join("")}<td class="text">${esc(r.note)}</td></tr>`).join("")}</tbody></table>`;
  const sum = `<table class="grid"><thead><tr><th class="sticky">分类</th><th>池数</th><th>投料 kg</th><th>增重 kg</th><th>原重 kg</th>
      <th>现有重量 kg</th><th>伤苗</th><th>现尾数</th><th>平均 P 数</th></tr></thead>
    <tbody>${["总计", ...CATS].map((k) => { const s = totals[k]; return `<tr class="${k === "总计" ? "total" : ""}">
      <td class="sticky">${k}</td><td class="calc">${s.ponds}</td><td class="calc">${fmt(s.feed_kg)}</td><td class="calc">${fmt(s.gain)}</td>
      <td class="calc">${fmt(s.orig_weight)}</td><td class="calc">${fmt(s.cur_weight)}</td><td class="calc">${fmt(s.injured, 0)}</td>
      <td class="calc">${fmt(s.cur_count, 0)}</td><td class="calc">${fmt(s.avg_p)}</td></tr>`; }).join("")}</tbody></table>`;
  view.innerHTML = `
    <div class="no-print" style="max-width:520px">${halfNavHTML()}</div>
    <div class="toolbar">
      <div class="seg small" id="modeSeg" style="width:180px">
        <button data-m="h" class="${h ? "active" : ""}">横版</button><button data-m="v" class="${h ? "" : "active"}">竖版</button></div>
      <button class="btn small" data-ui-action="print">${ICON.print}打印</button>
    </div>
    <h3 class="report-title">${esc(farmTitle())} ${period.year}年${period.month}月（${esc(period.day_range)}）号报表</h3>
    <div class="table-wrap">${body}</div>
    <div class="table-wrap" style="margin-top:14px">${sum}</div>`;
  bindHalfNav();
  $("#modeSeg").addEventListener("click", (e) => {
    const m = e.target.dataset.m;
    if (m) { state.reportMode = m; store.set("reportMode", m); render(); }
  });
}

// ---------- 每日月表（投喂 / 伤苗） ----------

function weekRanges(days) {
  const out = [];
  for (let s = 1; s <= days; s += 7) out.push([s, Math.min(s + 6, days)]);
  return out;
}

async function renderMonth(view, kind) {
  if (!state.monthYM) state.monthYM = { year: state.half.year, month: state.half.month };
  const { year, month } = state.monthYM;
  const g = await cached(`daily?year=${year}&month=${month}&kind=${kind}`, 60000);
  const digits = kind === "feed" ? 1 : 0;
  let rowsHTML = "";
  for (const [a, b] of weekRanges(g.days)) {
    for (let d = a; d <= b; d++) {
      SHIFTS.forEach((s, i) => {
        rowsHTML += `<tr class="${i === 0 ? "day-start" : ""}"><td class="sticky">${i === 0 ? `${d}日 ` : ""}<span style="font-weight:400;color:var(--muted)">${s}</span></td>
          ${g.ponds.map((p) => `<td><input size="4" data-col="p${p.id}" data-d="${d}" data-s="${s}" data-p="${p.id}" inputmode="decimal" autocomplete="off" value="${g.values[`${d}|${s}|${p.id}`] ?? ""}"></td>`).join("")}
          <td class="calc" data-rowsum="${d}|${s}"></td></tr>`;
      });
    }
    rowsHTML += `<tr class="sub"><td class="sticky">${a}-${b}日小计</td>
      ${g.ponds.map((p) => `<td class="calc" data-wk="${a}-${b}|${p.id}"></td>`).join("")}<td class="calc" data-wk="${a}-${b}|all"></td></tr>`;
  }
  rowsHTML += `<tr class="total"><td class="sticky">本月合计</td>
    ${g.ponds.map((p) => `<td class="calc" data-mt="${p.id}"></td>`).join("")}<td class="calc" data-mt="all"></td></tr>`;

  view.innerHTML = `
    <div class="toolbar">
      <label>年 <input type="number" class="ym-input" id="mY" value="${year}"></label>
      <label>月 <input type="number" class="ym-input" id="mM" value="${month}" min="1" max="12" style="width:64px"></label>
      <button class="btn small" data-ui-action="print">${ICON.print}打印</button>
    </div>
    <p class="hint">员工在「每日记录」填的都在这里，也可以直接改。按 Enter 跳到下一行。</p>
    <div class="table-wrap"><table class="grid" id="monthGrid">
      <thead><tr><th class="sticky">日期</th>${g.ponds.map((p) => `<th>${esc(p.name)}</th>`).join("")}<th>合计</th></tr></thead>
      <tbody>${rowsHTML}</tbody></table></div>`;

  const changeYM = async () => {
    const y = Number($("#mY").value), m = Number($("#mM").value);
    if (y >= 2000 && m >= 1 && m <= 12) { if (!(await flushPending())) return; state.monthYM = { year: y, month: m }; render(); }
  };
  $("#mY").addEventListener("change", changeYM);
  $("#mM").addEventListener("change", changeYM);

  const totals = () => {
    const get = (d, s, pid) => g.values[`${d}|${s}|${pid}`] || 0;
    const mt = {};
    let all = 0;
    for (const [a, b] of weekRanges(g.days)) {
      let wkAll = 0;
      for (const p of g.ponds) {
        let w = 0;
        for (let d = a; d <= b; d++) w += get(d, "早", p.id) + get(d, "晚", p.id);
        $(`[data-wk="${a}-${b}|${p.id}"]`).textContent = w ? fmt(w, digits) : "";
        mt[p.id] = (mt[p.id] || 0) + w;
        wkAll += w;
      }
      $(`[data-wk="${a}-${b}|all"]`).textContent = wkAll ? fmt(wkAll, digits) : "";
      all += wkAll;
      for (let d = a; d <= b; d++) for (const s of SHIFTS) {
        const sum = g.ponds.reduce((acc, p) => acc + get(d, s, p.id), 0);
        $(`[data-rowsum="${d}|${s}"]`).textContent = sum ? fmt(sum, digits) : "";
      }
    }
    for (const p of g.ponds) $(`[data-mt="${p.id}"]`).textContent = mt[p.id] ? fmt(mt[p.id], digits) : "";
    $('[data-mt="all"]').textContent = all ? fmt(all, digits) : "";
  };
  totals();

  const grid = $("#monthGrid");
  grid.addEventListener("keydown", gridNav);
  grid.addEventListener("focusin", (e) => { if (e.target.matches("input")) e.target.select(); });
  grid.addEventListener("input", (e) => {
    const el = e.target;
    const p = parseNum(el.value);
    el.classList.toggle("bad", !p.ok);
    if (!p.ok) { setStatus("请填数字", true); return; }
    const key = `${el.dataset.d}|${el.dataset.s}|${el.dataset.p}`;
    if (p.v == null) delete g.values[key]; else g.values[key] = p.v;
    queueDaily({ year, month, kind, day: +el.dataset.d, shift: el.dataset.s, pond_id: +el.dataset.p, value: p.v });
    totals();
  });
}

// ---------- 池号管理 ----------

let pondDraft = [];

async function renderPonds(view) {
  pondDraft = await api("ponds");
  view.innerHTML = `
    <div class="card">
      <p class="hint">「暂无」表示空池：员工填写时会收起来不用填。停用的池号不会再出现，历史数据保留。每一期的分类也可以在「期报」里单独改。</p>
      <div id="pondEdit"></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px">
        <button class="btn" id="addPond">${ICON.plus}添加池号</button>
        <button class="btn cta" id="savePonds">保存</button>
      </div>
      <p id="pondMsg" class="hint" style="margin-top:8px"></p>
    </div>`;
  drawPonds();
  $("#addPond").addEventListener("click", () => {
    const nums = pondDraft.map((p) => parseInt(p.name, 10)).filter(Number.isFinite);
    pondDraft.push({ id: null, name: `${(nums.length ? Math.max(...nums) : 0) + 1}#`, category: "新", active: 1 });
    drawPonds();
  });
  $("#savePonds").addEventListener("click", async () => {
    const msg = $("#pondMsg");
    try {
      pondDraft = await api("ponds", { method: "PUT", json: { ponds: pondDraft } });
      apiCache.clear();
      drawPonds();
      msg.className = "ok"; msg.textContent = "已保存";
    } catch (err) { msg.className = "err"; msg.textContent = err.message; }
  });
}

function drawPonds() {
  const box = $("#pondEdit");
  box.innerHTML = pondDraft.map((p, i) => `
    <div class="pond-edit ${p.active ? "" : "inactive"}" data-i="${i}">
      <input type="text" data-k="name" value="${esc(p.name)}" aria-label="池号">
      <div class="seg small">${CATS.map((c) => `<button data-cat="${c}" class="${c === p.category ? "active" : ""}">${c}</button>`).join("")}</div>
      <div class="ops">
        <button class="btn" data-op="up" ${i === 0 ? "disabled" : ""} aria-label="上移">↑</button>
        <button class="btn" data-op="toggle" title="${p.active ? "停用" : "启用"}">${p.active ? "停" : "启"}</button>
        <button class="btn danger" data-op="del" aria-label="移除">✕</button>
      </div>
    </div>`).join("");
  box.oninput = (e) => {
    if (e.target.dataset.k) pondDraft[Number(e.target.closest(".pond-edit").dataset.i)].name = e.target.value;
  };
  box.onclick = async (e) => {
    const item = e.target.closest(".pond-edit");
    if (!item) return;
    const i = Number(item.dataset.i);
    if (e.target.dataset.cat) { pondDraft[i].category = e.target.dataset.cat; return drawPonds(); }
    const op = e.target.dataset.op;
    if (op === "up") [pondDraft[i - 1], pondDraft[i]] = [pondDraft[i], pondDraft[i - 1]];
    if (op === "toggle") pondDraft[i].active = pondDraft[i].active ? 0 : 1;
    if (op === "del") {
      if (pondDraft[i].id && !(await askConfirm(`移除 ${pondDraft[i].name}？有历史数据的池号会改为停用，数据保留。`))) return;
      pondDraft.splice(i, 1);
    }
    if (op) drawPonds();
  };
}

// ---------- 导入 ----------

let pendingFeedFile=null;
async function useFeedImport(file){
  pendingFeedFile=file;
  await go('more','import');
  setStatus('已识别为每日饲料表，请选择数据所属月份后预览。');
}

function importNotice(element,text,error=false){
  element.className=error?'err':'ok';element.setAttribute('role',error?'alert':'status');
  element.textContent=text;setStatus(text,error);
}

async function renderDeletedPeriods(view) {
  const rows = await api("deleted-periods");
  view.innerHTML = rows.length ? rows.map(r => `<div class="card"><h3>${esc(r.label)}</h3><p class="hint">${r.ponds} 个池号 · 删除时间 ${esc(r.deleted_at)}（服务器时间）</p><p class="hint">恢复只补回缺失字段，不覆盖当前非空数据。</p><button class="btn cta block" data-restore-period="${r.id}">恢复期报</button></div>`).join("") : `<div class="card empty"><h4>没有待恢复的期报</h4></div>`;
  $$('[data-restore-period]', view).forEach(b => b.onclick = async () => {
    if (!(await askConfirm('恢复这期报表？已有非空数据会保留。'))) return;
    b.disabled = true;
    try { await api(`deleted-periods/${b.dataset.restorePeriod}/restore`, {method:'POST'}); apiCache.clear(); render(); setStatus('已恢复'); }
    catch(err) { setStatus(err.message, true); b.disabled = false; }
  });
}

async function renderReportImport(view) {
  view.innerHTML=`<div class="card">
    <p class="hint">选择本厂网页导出的.xlsx报表。支持以前的四张表格式，也支持新版带数据备份的报表；先预览、再确认导入。</p>
    <label class="btn block" style="position:relative">${ICON.upload}<span id="reportFileName">选择报表文件</span><input type="file" id="reportFile" accept=".xlsx" style="position:absolute;inset:0;opacity:0"></label>
    <label style="display:block;margin-top:12px">导入方式<select id="reportMode" class="payroll-select"><option value="false">只补空白，保留已有非空数据</option><option value="true">用文件非空数据更新对应记录</option></select></label>
    <button class="btn block" id="reportPreview" style="margin-top:12px">预览导入内容</button>
    <div id="reportImportMessage" class="hint" style="margin-top:12px"></div>
    <button class="btn cta block" id="reportApply" style="margin-top:12px" hidden>确认导入</button></div>`;
  let prepared=null;
  const file=$('#reportFile'),mode=$('#reportMode'),preview=$('#reportPreview'),apply=$('#reportApply'),message=$('#reportImportMessage');
  const reset=()=>{prepared=null;apply.hidden=true;message.className='hint';message.textContent='';};
  file.onchange=()=>{reset();$('#reportFileName').textContent=file.files[0]?.name||'选择报表文件';};
  mode.onchange=reset;
  const form=(selected,previewOnly)=>{const fd=new FormData();fd.append('file',selected.file);fd.append('overwrite',selected.overwrite);fd.append('preview_only',String(previewOnly));return fd;};
  const busy=value=>{file.disabled=mode.disabled=preview.disabled=apply.disabled=value;};
  preview.onclick=async()=>{
    reset();if(!file.files[0]){importNotice(message,'请先选择报表文件',true);return;}
    const selected={file:file.files[0],overwrite:mode.value};busy(true);preview.textContent='正在读取…';message.textContent='正在读取报表…';setStatus('正在读取报表…');
    try{const r=await api('import/report',{method:'POST',body:form(selected,true)});prepared=selected;
      message.innerHTML=`<b>${esc(r.farm)} · ${esc(r.label)}</b><p>${esc(r.source)}：${r.ponds} 个池号，${r.daily_cells} 格每日记录。</p><p>当前这期已有 ${r.existing_ponds} 个池号填写了数据；本次方式：${esc(r.mode)}。</p>${r.warnings.map(w=>`<p>${esc(w)}</p>`).join('')}<p>只处理当前厂和文件对应日期，不删除其他池号、期间或工资数据。</p>`;
      apply.hidden=false;
      setStatus('预览完成，请核对数据后点击确认导入。');
    }catch(err){if(err.importFormat==='feed')await useFeedImport(selected.file);else importNotice(message,'读取失败：'+err.message,true);}finally{busy(false);preview.textContent='预览导入内容';}
  };
  apply.onclick=async()=>{
    if(!prepared)return;busy(true);apply.textContent='正在导入…';setStatus('正在导入，请稍候…');
    try{const r=await api('import/report',{method:'POST',body:form(prepared,false)});apiCache.clear();
      importNotice(message,`导入成功！${r.farm}「${r.label}」，${r.ponds} 个池号、${r.daily_cells} 格每日记录。`);
      prepared=null;apply.hidden=true;
      try{const report=await api(`periods/${r.period_id}/report`);state.half={year:report.period.year,month:report.period.month,half:report.period.day_from<=1?'上':'下'};}catch{setStatus('导入成功，数据已保存。请刷新页面查看。');}
    }catch(err){importNotice(message,'导入失败：'+err.message,true);}finally{busy(false);apply.textContent='确认导入';}
  };
}

async function renderImport(view) {
  let forwardedFile=pendingFeedFile;pendingFeedFile=null;
  view.innerHTML = `
    <div class="card">
      <h3>导入每日饲料表</h3>
      <p class="hint">支持「每日饲料转换率计算.xlsx」。导入后在「每日」继续填写，期报投料留空时自动使用每日合计。手填投料优先。</p>
      <label>数据所属月份<input id="feedMonth" type="month" required value=""></label>
      <label>选择表格<input id="feedFile" type="file" accept=".xlsx"></label>
      <p id="feedSelectedFile" class="hint" ${forwardedFile?'':'hidden'}>${forwardedFile?'已识别文件：'+esc(forwardedFile.name)+'，请选择数据所属月份。':''}</p>
      <button class="btn block" id="feedPreview">预览导入内容</button>
      <div id="feedSummary" class="hint" style="margin-top:12px"></div>
      <button class="btn cta block" id="feedConfirm" hidden>确认导入</button>
      <button class="btn block" id="feedContinue" hidden>去网页填写每日数据</button>
    </div>
    <div class="card">
      <h3>导入原期报格式</h3>
      <p class="hint">选择原「鳗场报表」格式的 .xlsx，会按文件里的年份、月份、日期范围建立（或覆盖）对应期报，并导入投食计划表和伤苗表。</p>
      <label class="btn block" style="position:relative">${ICON.upload}<span id="fileName">选择文件</span>
        <input type="file" id="importFile" accept=".xlsx" style="position:absolute;inset:0;opacity:0"></label>
      <button class="btn cta block" id="importBtn" style="margin-top:10px">导入</button>
      <p id="importMsg" class="hint" style="margin-top:10px"></p>
    </div>`;
  let prepared = null;
  const previewButton = $("#feedPreview"), confirmButton = $("#feedConfirm");
  const summary = $("#feedSummary");
  const resetPreview = () => { prepared = null; confirmButton.hidden = true; summary.className='hint';summary.textContent = ""; $("#feedContinue").hidden = true; };
  $("#feedMonth").onchange = resetPreview;
  if(forwardedFile)try{const transfer=new DataTransfer();transfer.items.add(forwardedFile);$('#feedFile').files=transfer.files;}catch{/* 仍可通过已识别的文件预览 */}
  $("#feedFile").onchange = ()=>{forwardedFile=null;$('#feedSelectedFile').hidden=true;resetPreview();};
  previewButton.onclick = async () => {
    resetPreview();
    const file = $("#feedFile").files[0]||forwardedFile, ym = $("#feedMonth").value;
    if (!file || !/^\d{4}-\d{2}$/.test(ym)) { importNotice(summary,'请选择文件和数据所属月份',true);return; }
    const [year, month] = ym.split("-").map(Number);
    const fd = new FormData();
    fd.append("file", file); fd.append("year", year); fd.append("month", month);
    fd.append("preview_only", "true");
    previewButton.disabled = true;
    previewButton.textContent='正在读取…';summary.textContent = "正在读取…";setStatus('正在读取饲料表…');
    try {
      const r = await api("import/feed", { method: "POST", body: fd });
      prepared = { file, year, month };
      summary.innerHTML = `<b>${r.year}年${r.month}月</b>` + r.sheets.map(s => `<p>${esc(s.sheet)}：${s.ponds} 个池号，${s.daily_cells} 格投喂，共 ${fmt(s.total_feed)} kg；其中 ${s.existing_cells} 格已有记录，将更新。</p><p>${s.sample.map(v => `${v.day}日${esc(v.shift)} ${esc(v.name)} ${fmt(v.value)}kg`).join("；")}</p>`).join("") + r.warnings.map(w => `<p>${esc(w)}</p>`).join("") + `<p>初始重量和转化率按表中非空数字更新；已有手填投料保留并优先使用。请核对月份后确认。</p>`;
      confirmButton.hidden = false;
      setStatus('预览完成，请核对月份和数据后点击确认导入。');
    } catch (err) { importNotice(summary,'读取失败：'+err.message,true); }
    finally { previewButton.disabled = false;previewButton.textContent='预览导入内容'; }
  };
  confirmButton.onclick = async () => {
    if (!prepared) return;
    const selected = prepared;
    const fd = new FormData();
    fd.append("file", selected.file); fd.append("year", selected.year); fd.append("month", selected.month); fd.append("preview_only", "false");
    confirmButton.disabled = previewButton.disabled = true;
    confirmButton.textContent='正在导入…';setStatus('正在导入，请稍候…');
    $("#feedMonth").disabled = $("#feedFile").disabled = true;
    try {
      const r = await api("import/feed", { method: "POST", body: fd });
      apiCache.clear();
      importNotice(summary,`导入成功！${r.year}年${r.month}月，共 ${r.daily_cells} 格投喂。可在「每日」继续查看和填写。`);
      confirmButton.hidden = true;
      prepared = null;
      $("#feedContinue").hidden = false;
      $("#feedContinue").onclick = () => { state.today.date = `${selected.year}-${pad2(selected.month)}-01`; state.page = "today"; state.sub = null; render(); };
    } catch (err) { importNotice(summary,'导入失败：'+err.message,true); }
    finally { confirmButton.disabled = previewButton.disabled = false;confirmButton.textContent='确认导入'; $("#feedMonth").disabled = $("#feedFile").disabled = false; }
  };
  $("#importFile").addEventListener("change", (e) => { $("#fileName").textContent = e.target.files[0]?.name || "选择文件"; });
  $("#importBtn").addEventListener("click", async () => {
    const input=$("#importFile"),file=input.files[0];
    const msg = $("#importMsg");
    if (!file) { importNotice(msg,'请先选择文件',true);return; }
    const fd = new FormData();
    fd.append("file", file);
    const button=$('#importBtn');button.disabled=input.disabled=true;button.textContent='正在导入…';
    msg.className = "hint"; msg.textContent = "导入中…";setStatus('正在导入，请稍候…');
    try {
      const r = await api("import", { method: "POST", body: fd });
      apiCache.clear();
      importNotice(msg,`导入成功！「${r.label}」：${r.ponds} 个池号，每日记录 ${r.daily_cells} 格。`);
      try{const p = await api(`periods/${r.period_id}/report`);state.half = { year: p.period.year, month: p.period.month, half: p.period.day_from <= 1 ? "上" : "下" };}catch{setStatus('导入成功，数据已保存。请刷新页面查看。');}
    } catch (err) { if(err.importFormat==='feed')await useFeedImport(file);else importNotice(msg,'导入失败：'+err.message,true); }
    finally{button.disabled=input.disabled=false;button.textContent='导入';}
  });
}

// ---------- 老板端（手机优先）：经营概况 + 工资台账 ----------

const yuan = (c) => ((c || 0) / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const KIND = { leave: "请假", bonus: "奖金", advance: "借支", adjust: "加减" };
const signed = (c) => (c < 0 ? "−¥" : "+¥") + yuan(Math.abs(c));

function monthShift(ym, delta) {
  let [y, m] = ym.split("-").map(Number);
  m += delta;
  if (m < 1) { m = 12; y -= 1; }
  if (m > 12) { m = 1; y += 1; }
  return `${y}-${pad2(m)}`;
}

// 漏喂判断（老板端经营页、管理端首页共用）。
// live 有值＝按今天实时判断：还没到点的班次没填只算「待填」；喂 0 随时都算问题。
// live 为空＝看历史那天，没填或 0 都算问题。needs(r)＝这个池需要喂（有鱼）。
function feedCheck({ live, month, day, shiftsOf, needs }) {
  const due = (s) => !live || live.hour >= SHIFT_DUE[s];
  const st = (r, s) => {
    if (!needs(r)) return "ok";
    const v = shiftsOf(r)[s];
    if (v != null) return Number(v) === 0 ? "bad" : "ok";
    return due(s) ? "bad" : "wait";
  };
  const flagged = (r) => SHIFTS.some((s) => st(r, s) === "bad");
  const tag = (r, s) => {
    const v = shiftsOf(r)[s], x = st(r, s);
    return `<span class="sf ${x === "ok" ? "" : x}">${s} ${v != null ? fmt(v) : x === "wait" ? "待填" : "未填"}</span>`;
  };
  const why = (r) => SHIFTS.filter((s) => st(r, s) === "bad").map((s) => `${s}${shiftsOf(r)[s] == null ? "未填" : "0"}`).join("、");
  const card = (rows, { action = "" } = {}) => {
    const bad = rows.filter(flagged);
    const waiting = live ? SHIFTS.filter((s) => !due(s) && rows.some((r) => st(r, s) === "wait")) : [];
    const label = live ? `今天（${month}月${day}日）` : `${month}月${day}日`;
    const notes = (waiting.length ? `<small class="ac-note">${waiting.map((s) => `${s}班 ${SHIFT_DUE[s]} 点后还没填才提示`).join("，")}</small>` : "")
      + (live ? `<small class="ac-note">更新于 ${pad2(live.hour)}:${pad2(live.minute)}，页面开着会每分钟自动刷新</small>` : "");
    return bad.length ? `<div class="alert-card">
        <div class="ac-title">${ICON.warn}${label} 有 ${bad.length} 个池早班或晚班投喂为 0 / 没填</div>
        <div class="ac-list">${bad.map((r) => `<span><b>${esc(r.name)}</b> ${why(r)}</span>`).join("")}</div>${notes}${action}
      </div>`
      : `<div class="alert-card ok"><div>${ICON.check}${label} ${waiting.length ? "目前" : "早晚投喂都已填写，"}没有漏喂或喂 0 的池</div>${notes}</div>`;
  };
  return { flagged, tag, card, st };
}

async function renderBoss(view) {
  const [year, month] = state.bossMonth.split("-").map(Number);
  const wage = state.bossTab === "payroll", sale = state.bossTab === "sale";
  $("#subtitle").textContent = wage ? "工资结算" : sale ? "卖鱼预估" : "经营概况";
  const multi = state.farms.length > 1, all = multi && state.bossFarm === "all";
  view.classList.toggle("has-action", wage && !all);
  // 两个标签的数据同时取，切换标签不用再等
  const pBoss = all ? null : cached(`boss?year=${year}&month=${month}`, 30000);
  const pPay = all ? null : cached(`payroll/smart?year=${year}&month=${month}`, 30000);
  const pAll = all ? cached(`boss/all?year=${year}&month=${month}`, 30000, "") : null;
  for (const p of [pBoss, pPay, pAll]) p?.catch(() => {});
  const isNow = state.bossMonth === todayStr().slice(0, 7);
  const farmSeg = multi ? `<div class="seg farm-seg" id="bossFarms">${[{ id: "all", name: "全部" }, ...state.farms]
    .map((f) => `<button data-farm="${f.id}" class="${f.id === state.bossFarm ? "active" : ""}">${esc(f.name)}</button>`).join("")}</div>` : "";
  view.innerHTML = `
    ${farmSeg}
    <div class="stepper">
      <button class="icon-btn" data-m="-1" aria-label="上个月">${ICON.left}</button>
      <div class="stepper-label"><b>${year}年${month}月</b><small>${isNow ? "本月" : "点此选月份"}</small>
        <input type="month" id="bossMonth" value="${state.bossMonth}" min="2000-01" max="2100-12" aria-label="选择月份"></div>
      <button class="icon-btn" data-m="1" aria-label="下个月">${ICON.right}</button>
    </div>
    <div class="seg" id="bossTabs" style="margin-bottom:14px">
      <button data-tab="overview" class="${!wage && !sale ? "active" : ""}">经营</button>
      <button data-tab="sale" class="${sale ? "active" : ""}">卖鱼</button>
      <button data-tab="payroll" class="${wage ? "active" : ""}">工资</button>
    </div>
    <div id="bossContent"><div class="skeleton"><i></i><i></i><i></i></div></div>`;
  $$("[data-m]", view).forEach((b) => b.onclick = () => { state.bossMonth = monthShift(state.bossMonth, Number(b.dataset.m)); render(); });
  $("#bossMonth").onchange = (e) => { if (e.target.value && e.target.validity.valid) { state.bossMonth = e.target.value; render(); } };
  $("#bossTabs").onclick = (e) => {
    const tab = e.target.closest("[data-tab]")?.dataset.tab;
    if (tab && tab !== state.bossTab) { state.bossTab = tab; store.set("bossTab", tab); render(); }
  };
  if (multi) $("#bossFarms").onclick = (e) => {
    const fid = e.target.closest("[data-farm]")?.dataset.farm;
    if (fid && fid !== state.bossFarm) { state.bossFarm = fid; store.set("bossFarm", fid); state.bossOpen = {}; render(); }
  };
  const host = $("#bossContent");
  if (sale) {
    // 全部：几个厂的池子一起选；单个厂：只看这个厂
    const ids = all ? state.farms.map((f) => f.id) : [farmOf()];
    const sources = await Promise.all(ids.map((fid) => saleSource(fid, year, month)));
    renderBossSale(host, sources.filter((s) => s.report));
  } else if (all) {
    const data = await pAll;
    if (wage) renderBossAllPayroll(host, data); else renderBossAll(host, month, data);
  } else if (wage) renderBossPayroll(host, year, month, await pPay);
  else renderBossOverview(host, year, month, await pBoss);
}

// 卖鱼用的期报：这个月最新一期；月初还没建新期报时用上个月最新的
async function saleSource(fid, year, month) {
  const farm = state.farms.find((f) => f.id === fid) || state.farm;
  let b = await cached(`boss?year=${year}&month=${month}`, 30000, fid);
  if (!b.latest) {
    const [py, pm] = monthShift(`${year}-${pad2(month)}`, -1).split("-").map(Number);
    try { b = await cached(`boss?year=${py}&month=${pm}`, 30000, fid); } catch { /* 用本月的空结果 */ }
  }
  return { farm, report: b.latest };
}

// ---------- 老板端「全部」：几个厂合在一起看 ----------
function allFeedBad(a) {
  if (!a?.today) return [];
  const [, m] = a.today.date.split("-").map(Number);
  const fc = feedCheck({ live: a.today, month: m, day: a.today.day, shiftsOf: (r) => a.today.shifts[r.pond_id] || {}, needs: (r) => r.needs_feed });
  return a.rows.filter(fc.flagged);
}

function renderBossAll(view, month, data) {
  if (!view.isConnected) return;
  const fs = data.farms;
  const sum = (get) => fs.reduce((a, f) => a + (Number(get(f)) || 0), 0);
  const w = sum((f) => f.totals?.["总计"].cur_weight), n = sum((f) => f.totals?.["总计"].cur_count);
  const bad = fs.map((f) => allFeedBad(f.alert));
  const badN = bad.reduce((a, b) => a + b.length, 0);
  const goFarm = (fid) => { state.bossFarm = fid; store.set("bossFarm", fid); render(); };
  view.innerHTML = `
    <div class="kpi-grid">
      <div class="kpi hero"><div class="k">${fs.length} 个厂现有库存</div>
        <div class="v">${fmt(w, 0)}<small>kg</small></div>
        <div class="s">${fs.map((f) => `${esc(f.farm.name)} ${fmt(f.totals?.["总计"].cur_weight || 0, 0)}`).join(" · ")}</div></div>
      <div class="kpi"><div class="k">合计尾数</div><div class="v">${fmt(n, 0)}</div></div>
      <div class="kpi"><div class="k">整体 P 数</div><div class="v">${w > 0 && n > 0 ? fmt(Math.floor(n / w * 10) / 10) : "—"}</div></div>
      <div class="kpi"><div class="k">${month}月投喂</div><div class="v">${fmt(sum((f) => f.monthly_feed), 0)}<small>kg</small></div></div>
      <div class="kpi ${badN ? "bad" : ""}"><div class="k">今天漏喂 / 喂 0</div><div class="v">${badN}<small>池</small></div></div>
    </div>
    <div class="section-head"><h3>各厂</h3><span class="hint" style="margin:0">点开看这个厂</span></div>
    <div class="list-card">${fs.map((f, i) => {
      const t = f.totals?.["总计"];
      return `<button type="button" class="list-row farm-row" data-go-farm="${f.farm.id}">
        <span class="avatar">${esc(f.farm.mark)}</span>
        <div class="lr-main"><b>${esc(f.farm.title)}</b>
          <small>${f.ponds ? `${f.ponds} 口池` : "还没设置池号"}${f.period ? ` · 期报到 ${f.period.month}月${f.period.day_to}日` : f.ponds ? " · 还没有期报" : ""}</small></div>
        <div class="lr-end"><b>${t ? `${fmt(t.cur_weight, 0)}<small> kg</small>` : "—"}</b>
          ${bad[i].length ? `<span class="chip bad">${ICON.warn}${bad[i].length} 池漏喂</span>` : t ? `<small>P ${dash(fmt(t.avg_p))}</small>` : ""}</div>
      </button>`;
    }).join("")}</div>
    ${badN ? `<div class="alert-card" style="margin-top:12px"><div class="ac-title">${ICON.warn}今天有 ${badN} 个池早班或晚班投喂为 0 / 没填</div>
      <div class="ac-list">${fs.map((f, i) => bad[i].map((r) => `<span><b>${esc(f.farm.name)} ${esc(r.name)}</b></span>`).join("")).join("")}</div></div>` : ""}
    <p class="hint" style="margin-top:12px">每个厂取当月最新一期期报（还没有就用上个月的）。整体 P 数＝几个厂总尾数 ÷ 总重量。</p>`;
  view.onclick = (e) => { const b = e.target.closest("[data-go-farm]"); if (b) goFarm(b.dataset.goFarm); };
}

function renderBossAllPayroll(view, data) {
  if (!view.isConnected) return;
  const fs = data.farms;
  const total = fs.reduce((a, f) => a + f.pay_total_cents, 0);
  view.innerHTML = `
    <div class="kpi-grid"><div class="kpi hero"><div class="k">${fs.length} 个厂应发工资合计</div>
      <div class="v">${fmt(total / 100, 2)}<small>元</small></div>
      <div class="s">${fs.reduce((a, f) => a + f.pay_people, 0)} 人</div></div></div>
    <div class="section-head"><h3>各厂</h3><span class="hint" style="margin:0">点开看明细、改工资</span></div>
    <div class="list-card">${fs.map((f) => `
      <button type="button" class="list-row farm-row" data-go-farm="${f.farm.id}">
        <span class="avatar">${esc(f.farm.mark)}</span>
        <div class="lr-main"><b>${esc(f.farm.title)}</b><small>${f.pay_people ? `${f.pay_people} 人` : "还没有员工"}</small></div>
        <div class="lr-end"><b>${fmt(f.pay_total_cents / 100, 2)}<small> 元</small></b></div>
      </button>`).join("")}</div>
    <p class="hint" style="margin-top:12px">工资按厂分开算、分开锁定。点某个厂进去，和原来一样记一笔、手动改数、加员工。</p>`;
  view.onclick = (e) => {
    const b = e.target.closest("[data-go-farm]");
    if (b) { state.bossFarm = b.dataset.goFarm; store.set("bossFarm", state.bossFarm); render(); }
  };
}

function renderBossOverview(view, year, month, boss) {
  if (!view.isConnected) return;
  const range = state.bossRange;
  const report = range === "全月" ? boss.latest : boss.halves[range === "上半月" ? "上" : "下"];
  const seg = `<div class="seg small" id="bossRange" style="margin-bottom:12px">${["全月", "上半月", "下半月"].map((r) => `<button data-range="${r}" class="${r === range ? "active" : ""}">${r}</button>`).join("")}</div>`;
  if (!report) {
    view.innerHTML = `${seg}<div class="card empty"><h4>这个${range === "全月" ? "月" : "半月"}还没有期报</h4><p>养殖端录入期报后，这里会显示库存、尾数和 P 数。</p></div>`;
  } else {
    const t = report.totals, all = t["总计"], n = t["新"], o = t["老"];
    const feed = range === "全月" ? boss.monthly_feed : all.feed_kg;
    // 每池点数 + 早晚投喂：有重量的池，早班或晚班投喂为 0 或没填的，重点提示。
    // 看本月时按「今天」实时判断；看以前的月份按那一期最后一次投喂日。
    const live = boss.today;
    const feedDay = live ? live.day : report.period.feed_day;
    const fc = feedCheck({
      live, month, day: feedDay,
      shiftsOf: (r) => (live ? live.shifts[r.pond_id] || {} : r.calc.feed_shifts || {}),
      needs: (r) => r.category !== "暂无" && (r.calc.cur_weight || 0) > 0,
    });
    const flagged = (r) => !!feedDay && fc.flagged(r);
    const shiftTag = fc.tag;
    const pondRow = (r) => `<div class="pond-pt ${flagged(r) ? "warn" : ""}">
      <div class="pp-name"><b>${esc(r.name)}</b><small>${dash(fmt(r.calc.cur_weight, 0))} kg · P ${dash(fmt(r.calc.p))}</small></div>
      <div class="pp-feed">${feedDay ? SHIFTS.map((s) => shiftTag(r, s)).join("") : ""}</div>
      <div class="pp-pt"><b>${dash(pct(r.calc.points))}</b><small>点数</small></div>
    </div>`;
    state.bossOpen ||= {};
    const group = (name, g) => {
      const ponds = report.rows.filter((r) => r.category === name);
      const bad = ponds.filter(flagged);
      const open = state.bossOpen[name] ?? bad.length > 0;
      return `<details class="stat-row grp" data-grp="${name}" ${open ? "open" : ""}>
        <summary>
          <div class="stat-name"><span class="tag ${name}">${name}鳗</span><small>${g.ponds} 个池</small>
            ${bad.length ? `<span class="chip bad">${ICON.warn}${bad.length} 池漏喂</span>` : ""}<span class="grp-more">${open ? "收起" : "看各池"}</span></div>
          <div class="stat-cells">
            <div><span>重量 kg</span><b>${fmt(g.cur_weight, 0)}</b></div>
            <div><span>尾数</span><b>${fmt(g.cur_count, 0)}</b></div>
            <div><span>P 数</span><b>${dash(fmt(g.avg_p))}</b></div>
            <div><span>点数</span><b>${dash(pct(g.points))}</b></div>
          </div>
        </summary>
        <div class="pond-pts">${ponds.length ? ponds.map(pondRow).join("") : `<p class="hint">没有${name}鳗池</p>`}</div>
      </details>`;
    };
    const alertHTML = !feedDay ? `<div class="alert-card info">这一期还没有每日投喂记录，点数暂时按期报手填的「当日投喂」计算。</div>`
      : fc.card(report.rows);
    view.innerHTML = `${seg}
      <div class="kpi-grid">
        <div class="kpi hero"><div class="k">现有库存 · 截至 ${month}月${report.period.day_to}日</div>
          <div class="v">${fmt(all.cur_weight, 0)}<small>kg</small></div>
          <div class="s">新鳗 ${fmt(n.cur_weight, 0)} · 老鳗 ${fmt(o.cur_weight, 0)}</div></div>
        <div class="kpi"><div class="k">合计尾数</div><div class="v">${fmt(all.cur_count, 0)}</div></div>
        <div class="kpi"><div class="k">平均 P 数</div><div class="v">${dash(fmt(all.avg_p))}</div></div>
        <div class="kpi"><div class="k">${range === "全月" ? "本月投喂" : "本期投料"}</div><div class="v">${fmt(feed, 0)}<small>kg</small></div></div>
        <div class="kpi"><div class="k">投喂点数${all.points_day ? ` · ${month}/${all.points_day}` : ""}</div><div class="v">${dash(pct(all.points))}</div></div>
      </div>
      <div class="section-head"><h3>新鳗 / 老鳗 · 各池点数</h3>${feedDay ? `<span class="hint" style="margin:0">${live ? "今天" : `${month}月${feedDay}日`}早晚投喂</span>` : ""}</div>
      ${alertHTML}
      <div class="list-card">${group("新", n)}${group("老", o)}</div>
      <p class="hint" style="margin-top:12px">${range === "全月" ? "全月库存取当月最新一期期报。" : ""}平均 P 数＝总尾数 ÷ 总重量；点数按各池现有重量加权${all.points_expected > all.points_ponds ? `（${all.points_ponds}/${all.points_expected} 个池有投喂记录）` : ""}。${t["暂无"].ponds ? `总计含 ${t["暂无"].ponds} 个空池。` : ""}</p>`;
  }
  $("#bossRange", view).onclick = (e) => { if (e.target.dataset.range) { state.bossRange = e.target.dataset.range; renderBossOverview(view, year, month, boss); } };
  $$("details.grp", view).forEach((d) => d.addEventListener("toggle", () => {
    state.bossOpen[d.dataset.grp] = d.open;
    $(".grp-more", d).textContent = d.open ? "收起" : "看各池";
  }));
}

// ---------- 老板端：卖鱼预估（临时计算，不保存到服务器；上次填的内容记在这台手机上） ----------
// sources：[{farm, report}]，一个厂或几个厂一起（老板端「全部」时可跨厂凑一个订单）
function renderBossSale(view, sources) {
  if (!view.isConnected) return;
  if (!sources.length) {
    view.innerHTML = `<div class="card empty"><h4>这个月和上个月都没有期报</h4><p>卖鱼预估要用期报里各池的现有重量和尾数，养殖端录入期报后就能算。</p></div>`;
    return;
  }
  const multi = sources.length > 1;
  const saved = store.get("saleCalc", {});
  // 池子用「厂id:池号」区分；多厂以前存的只有池号，算示例的
  const fixKey = (k) => (k.includes(":") ? k : `demo:${k}`);
  // splits：每池各 P 数段的吨数（以前存的 samples 是条数，不再使用）
  const S = {
    picked: (saved.picked || []).map(fixKey), rules: { ...SALE_DEFAULT_RULES, ...(saved.rules || {}) },
    splits: Object.fromEntries(Object.entries(saved.splits || {}).map(([k, v]) => [fixKey(k), v])),
  };
  delete S.rules.tolSmall; delete S.rules.tolBig;
  const save = () => store.set("saleCalc", S);
  const wan = (v, d = 2) => (v == null ? "—" : fmt(v / 10000, d));
  // 可选的池：有鱼（有重量）的池，按厂、按期报顺序
  const units = sources.flatMap((src) => src.report.rows
    .filter((r) => r.category !== "暂无" && (r.calc.cur_weight || 0) > 0)
    .map((r) => ({ key: `${src.farm.id}:${r.name}`, label: multi ? `${src.farm.name} ${r.name}` : r.name, farm: src.farm, row: r })));
  const byKey = Object.fromEntries(units.map((u) => [u.key, u]));
  const fishRows = units;  // 「全选」用
  const pick = () => ({ found: units.filter((u) => S.picked.includes(u.key)), lost: [] });
  const chip = (u) => {
    const r = u.row, on = S.picked.includes(u.key);
    const p = saleP(r.calc.cur_count > 0 ? r.calc.cur_weight * 1000 / r.calc.cur_count : null);
    return `<button type="button" class="pond-chip ${on ? "on" : ""}" data-pick="${esc(u.key)}" aria-pressed="${on}">
      <b>${esc(r.name)}</b><small>${p != null ? `P ${saleFmtP(p)}` : "缺尾数"} · ${fmt(r.calc.cur_weight / 1000, 1)}吨</small></button>`;
  };
  const chipsHTML = multi
    ? sources.map((src) => {
      const us = units.filter((u) => u.farm.id === src.farm.id);
      return us.length ? `<div class="chips-farm">${esc(src.farm.title)}</div><div class="pond-chips">${us.map(chip).join("")}</div>` : "";
    }).join("")
    : `<div class="pond-chips">${units.map(chip).join("")}</div>`;
  const pickHead = () => {
    const f = pick().found;
    return `已选 <b>${f.length}</b> 口${f.length ? ` · ${fmt(f.reduce((a, u) => a + u.row.calc.cur_weight, 0) / 1000, 1)} 吨` : ""}`;
  };
  // saleEstimate 用 name 作为分段吨数的键，这里传「厂id:池号」，显示时再换成池名
  const pondInput = (u) => ({ name: u.key, weight: u.row.calc.cur_weight, count: u.row.calc.cur_count });
  const periodNote = sources.map((s) => `${multi ? s.farm.name + " " : ""}${s.report.period.month}月${s.report.period.day_to}日`).join("、");

  // 价格规则：价格用「万元/吨」填，和合同写法一样
  // 单价放在上面随时改（行情会变）；分档克数不常改，收在「P 数分档」里
  const PRICE_FIELDS = [["p1", 2], ["p2", 3], ["pEx", null]];
  const priceLabel = (band) => (band == null ? "超出部分" : saleBandPLabels(S.rules)[band]);
  const RULE_FIELDS = [
    ["cutoff", "扣重：这个克数及以下", "g（合同按克写）"], ["smallMax", "小规格到", "g"], ["t1Max", "规格1 到", "g"], ["t2Max", "规格2 到", "g"],  ];
  const ruleVal = (k, money) => (money ? S.rules[k] / 10000 : S.rules[k]);
  // 规则摘要按 P 数写（克重只在改规则时用）
  const sum = (r) => {
    const L = saleBandPLabels(r);
    return `${L[2]}：${fmt(r.p1 / 10000, 2)}万 · ${L[3]}：${fmt(r.p2 / 10000, 2)}万 · ${L[1]} 和 ${L[4]}：${fmt(r.pEx / 10000, 2)}万 · ${L[0]}：扣重`;
  };
  const pTxt = (p) => `P ${saleFmtP(p)}`;

  view.innerHTML = `
    <div class="card sale-form">
      <div class="fields-2">
        <label class="field"><span>订单吨数</span><input id="sOrder" type="number" inputmode="decimal" min="0" step="0.1" value="${S.rules.orderTons}"></label>
        <label class="field"><span>停料掉重 <span class="unit">%，可不填</span></span><input id="sLoss" type="number" inputmode="decimal" min="0" max="50" step="0.1" value="${S.rules.loss || ""}" placeholder="0"></label>
      </div>
      <div class="sale-prices">
        <div class="field-title">单价 <span class="unit">万元/吨，行情变了直接改</span></div>
        <div class="fields-3">${PRICE_FIELDS.map(([k, band]) => `<label class="field"><span data-plabel="${band ?? ""}">${priceLabel(band)}</span>
          <input data-rule="${k}" data-money="1" type="number" inputmode="decimal" min="0" step="0.01" value="${ruleVal(k, true)}"></label>`).join("")}</div>
      </div>
      <div class="sale-pick-head"><span class="field-title">选池子 <small id="sPickHead">${pickHead()}</small></span>
        <span><button type="button" class="link-btn" id="sAll">全选</button><button type="button" class="link-btn" id="sClear">清空</button></span></div>
      ${fishRows.length ? `<div id="sChips">${chipsHTML}</div>` : `<p class="hint">期报里没有有重量的池。</p>`}
      <details class="sale-rules">
        <summary><b>P 数分档</b><small id="sRuleSum">${esc(sum(S.rules))}</small></summary>
        <div class="fields-2" style="margin-top:12px">${RULE_FIELDS.map(([k, l, u, m]) => `<label class="field"><span>${l} <span class="unit">${u}</span></span>
          <input data-rule="${k}" data-money="${m ? 1 : ""}" type="number" inputmode="decimal" min="0" step="${m ? "0.01" : "1"}" value="${ruleVal(k, m)}"></label>`).join("")}</div>
        <button type="button" class="btn small" id="sReset" style="margin-top:12px">恢复默认（这份闽清订单）</button>
      </details>
    </div>
    <div id="saleOut"></div>
    <p class="hint" style="margin-top:12px">重量、尾数取 ${periodNote} 期报的现有重量和尾数，P 数＝尾数 ÷ 重量（每公斤几条，和经营页一样）。合同按克重定价，这里换算成 P 数显示。没分段的池按期报重量和 P 数整池归档，大小不匀时会有偏差；点池子按 P 数段分别填吨数，按各段价格算，更接近实际。小规格、大规格「照顾几条」按吨算影响很小，没计入。只是预估，以实际过磅和抽检为准。</p>`;

  const out = $("#saleOut", view);
  const update = () => {
    const { found, lost } = pick();
    if (!found.length) {
      out.innerHTML = `<div class="card empty"><h4>还没选池子</h4><p>在上面点要卖的池子，可以多选。</p>${lost.length ? `<p class="err">期报里找不到：${lost.map((n) => `${n}#`).join("、")}</p>` : ""}</div>`;
      return;
    }
    const e = saleEstimate(found.map(pondInput), S.rules, S.splits);
    const short = e.order > 0 && e.gap < 0;
    out.innerHTML = `
      <div class="kpi-grid">
        <div class="kpi hero"><div class="k">预估货款${e.order ? ` · 按 ${fmt(e.sellTons, 1)} 吨` : ""}</div>
          <div class="v">${wan(e.amountOrder)}<small>万元</small></div>
          <div class="s">平均单价 ${wan(e.avgPrice)} 万/吨${e.order && e.gap > 0 ? ` · 全部卖掉约 ${wan(e.amountAll)} 万` : ""}</div></div>
        <div class="kpi"><div class="k">${found.length} 口池可供</div><div class="v">${fmt(e.tons, 1)}<small>吨</small></div>
          <div class="s">整体 ${pTxt(e.avgP)}</div></div>
        <div class="kpi ${short ? "bad" : ""}"><div class="k">比订单 ${fmt(e.order, 1)} 吨</div>
          <div class="v">${e.order ? `${short ? "差" : "多"} ${fmt(Math.abs(e.gap), 1)}<small>吨</small>` : "—"}</div></div>
      </div>
      ${short ? `<div class="alert-card"><div class="ac-title">${ICON.warn}选中的池不够订单量，还差 ${fmt(-e.gap, 1)} 吨</div></div>` : ""}
      ${lost.length || e.missing.length ? `<div class="alert-card info">${lost.length ? `期报里找不到：${lost.map((n) => `${n}#`).join("、")}。` : ""}${e.missing.length ? `${e.missing.map((k) => esc(byKey[k]?.label || k)).join("、")} 没有重量或尾数，没算钱。` : ""}</div>` : ""}
      <div class="section-head"><h3>各池预估</h3><span class="hint" style="margin:0">点池子按 P 数分吨</span></div>
      <div class="list-card">${e.rows.map((x) => `
        <button type="button" class="list-row sale-row" data-pond="${esc(x.name)}">
          <div class="sr-name"><b>${esc(byKey[x.name]?.label || x.name)} <span class="sr-p">${x.p != null ? pTxt(x.p) : "缺尾数"}</span></b>
            <small>${fmt(x.tons, 2)} 吨${x.source === "分段" ? ` · 期报 ${fmt(x.reportTons, 2)} 吨` : ""}</small></div>
          <div class="sr-src"><span class="chip ${x.source === "分段" ? "ok" : x.source === "缺数据" ? "bad" : "idle"}">${x.source === "分段" ? "已分段" : x.source}</span>${x.deductPct >= 0.5 ? `<small>扣重约 ${fmt(x.deductPct, 0)}%</small>` : ""}</div>
          <div class="sr-money"><b>${wan(x.amount)}<small>万</small></b><small>${x.price == null ? "—" : `${wan(x.price)} 万/吨`}</small></div>
        </button>`).join("")}</div>`;
  };
  update();

  const rerunRules = () => {
    $("#sRuleSum", view).textContent = sum(S.rules);
    $$("[data-plabel]", view).forEach((el) => { el.textContent = priceLabel(el.dataset.plabel === "" ? null : Number(el.dataset.plabel)); });
    save(); update();
  };
  $("#sOrder", view).oninput = (ev) => { S.rules.orderTons = Math.max(0, Number(ev.target.value) || 0); save(); update(); };
  $("#sLoss", view).oninput = (ev) => { S.rules.loss = Math.min(50, Math.max(0, Number(ev.target.value) || 0)); save(); update(); };
  const syncPick = () => {
    $$("[data-pick]", view).forEach((b) => { const on = S.picked.includes(b.dataset.pick); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    $("#sPickHead", view).innerHTML = pickHead();
    save(); update();
  };
  $("#sChips", view)?.addEventListener("click", (ev) => {
    const name = ev.target.closest("[data-pick]")?.dataset.pick;
    if (!name) return;
    S.picked = S.picked.includes(name) ? S.picked.filter((n) => n !== name) : [...S.picked, name];
    syncPick();
  });
  $("#sAll", view).onclick = () => { S.picked = fishRows.map((u) => u.key); syncPick(); };
  $("#sClear", view).onclick = () => { S.picked = []; syncPick(); };
  $$("[data-rule]", view).forEach((inp) => inp.oninput = () => {
    const v = Number(inp.value);
    if (inp.value === "" || !Number.isFinite(v) || v < 0) return;
    S.rules[inp.dataset.rule] = inp.dataset.money ? Math.round(v * 10000) : Math.round(v);
    rerunRules();
  });
  $("#sReset", view).onclick = () => {
    const keep = { orderTons: S.rules.orderTons, loss: S.rules.loss };
    S.rules = { ...SALE_DEFAULT_RULES, ...keep };
    $$("[data-rule]", view).forEach((inp) => { inp.value = ruleVal(inp.dataset.rule, !!inp.dataset.money); });
    rerunRules();
  };

  // 点池子：按 P 数段分别填吨数，实时看这口池的单价和金额
  out.onclick = (ev) => {
    const name = ev.target.closest("[data-pond]")?.dataset.pond;
    if (!name) return;
    const row = byKey[name];
    if (!row) return;
    const base = saleEstimatePond(pondInput(row), S.rules);
    const labels = saleBandPLabels(S.rules), prices = saleBandPrices(S.rules), cur = S.splits[name] || [];
    const priceTxt = (v) => (v ? `${fmt(v / 10000, 2)}万/吨` : "扣重");
    openSheet(`<h2>${esc(row.label)} · 按 P 数分吨</h2>
      <p class="hint">期报：${fmt(base.reportTons, 2)} 吨${base.p != null ? ` · ${pTxt(base.p)}` : ""}。估计这口池每个 P 数段大概有几吨就填几，没有的空着。填了就按填的吨数和各段价格算；不填按期报重量和 P 数整池算。</p>
      <div class="sample-grid">${labels.map((l, i) => `<label class="field"><span><b class="sp-p">${l}</b> <span class="unit">${priceTxt(prices[i])}</span></span>
        <span class="num-unit"><input data-band="${i}" type="number" inputmode="decimal" min="0" step="0.01" value="${cur[i] || ""}" placeholder="0"><i>吨</i></span></label>`).join("")}</div>
      <div class="sample-preview" id="spPrev"></div>
      <div class="sheet-actions"><button type="button" class="btn danger" id="spClear">清除分段</button><button type="button" class="btn cta" id="spOk">好了</button></div>`, (dlg) => {
      const read = () => $$("[data-band]", dlg).map((i) => Math.max(0, Number(i.value) || 0));
      const prev = () => {
        const p = saleEstimatePond(pondInput(row), S.rules, read());
        const filled = read().reduce((a, b) => a + b, 0);
        $("#spPrev", dlg).innerHTML = p.source === "分段"
          ? `合计 <b>${fmt(filled, 2)} 吨</b>（期报 ${fmt(base.reportTons, 2)} 吨${Math.abs(filled - base.reportTons) >= 0.01 ? `，${filled > base.reportTons ? "多" : "少"} ${fmt(Math.abs(filled - base.reportTons), 2)} 吨` : ""}）<br>平均单价 <b>${wan(p.price)} 万/吨</b>${p.deductPct >= 0.5 ? ` · 扣重约 ${fmt(p.deductPct, 0)}%` : ""} · 这口池约 <b>${wan(p.amount)} 万元</b>`
          : `还没填，按期报 ${fmt(base.reportTons, 2)} 吨、${p.p != null ? pTxt(p.p) : "—"} 算：${p.price == null ? "—" : wan(p.price) + " 万/吨"}`;
      };
      $$("[data-band]", dlg).forEach((i) => i.oninput = prev);
      prev();
      $("#spOk", dlg).onclick = () => {
        const n = read();
        if (n.some((x) => x > 0)) S.splits[name] = n; else delete S.splits[name];
        save(); closeSheet(); update();
      };
      $("#spClear", dlg).onclick = () => { delete S.splits[name]; save(); closeSheet(); update(); };
    });
  };
}

function renderBossPayroll(view, year, month, data) {
  if (!view.isConnected) return;
  const section = state.payrollSection;
  const staff = data.employees;
  const lockedEmp = (eid) => data.rows.some((r) => r.employee_id === eid && r.paid);
  const recordDate = state.bossMonth === todayStr().slice(0, 7) ? todayStr() : `${state.bossMonth}-01`;
  const reload = (eid = null) => { invalidate("payroll"); state.paySheetFor = eid; closeSheet(); render(); };

  // 「锁定」= 发完工资后防止误改（后端的结算）；不锁定不影响计算
  const status = (r) => r.paid ? `<span class="chip idle">${ICON.lock}已锁定</span>`
    : r.net_cents < 0 ? `<span class="chip bad">需核对</span>` : "";
  const short = (c) => yuan(c).replace(/\.00$/, "");
  const formula = (r) => [
    `月薪 ${short(r.salary_cents)}`,
    r.deduction_cents ? `− 请假${Number(r.leave_days) ? esc(r.leave_days) + "天" : ""} ${short(r.deduction_cents)}` : "",
    r.bonus_cents ? `+ 奖金 ${short(r.bonus_cents)}` : "",
    r.advance_cents ? `− 借支 ${short(r.advance_cents)}` : "",
    r.adjust_cents ? `${r.adjust_cents < 0 ? "−" : "+"} 加减 ${short(Math.abs(r.adjust_cents))}` : "",
  ].filter(Boolean).join(" ");
  const pl = (label, value, cls = "") => `<div class="pay-line ${cls}"><span><em class="pl-label">${label}</em></span><b>${value}</b></div>`;
  const manualTag = (r) => (r.salary_manual || r.deduction_manual ? `<span class="tag manual">手动</span>` : "");
  const sub = (r) => [
    `月薪 ${yuan(r.salary_cents)}`,
    Number(r.leave_days) ? `请假 ${esc(r.leave_days)} 天` : "",
    r.bonus_cents ? `奖金 ${yuan(r.bonus_cents)}` : "",
    r.advance_cents ? `借支 ${yuan(r.advance_cents)}` : "",
    r.adjust_cents ? `加减 ${signed(r.adjust_cents)}` : "",
  ].filter(Boolean).join(" · ");
  const evRow = (e, withName) => `<button class="list-row" data-ev="${e.id}">
    <span class="chip k-${e.kind}">${KIND[e.kind] || e.kind}</span>
    <span class="lr-main"><b>${withName ? esc(e.name) + " · " : ""}${esc(e.date.slice(5).replace("-", "月"))}日</b>${e.note ? `<small>${esc(e.note)}</small>` : ""}</span>
    <span class="lr-end"><b>${e.kind === "leave" ? `${esc(e.leave_days)} 天` : e.kind === "bonus" ? `+¥${yuan(e.amount_cents)}` : e.kind === "advance" ? `−¥${yuan(e.amount_cents)}` : signed(e.amount_cents)}</b></span>
  </button>`;

  let body = "";
  if (section === "monthly") {
    // 重点是「这个月一共要发多少、每人多少、怎么算出来的」
    const legacy = data.legacy.rows.map((l) => ({ ...l, advance_cents: 0, adjust_cents: 0, legacy: true }));
    const all = [...data.rows, ...legacy];
    const sum = (k) => all.reduce((s, r) => s + (r[k] || 0), 0);
    const unlocked = data.rows.filter((r) => !r.paid && r.net_cents >= 0);
    const tableView = state.payView === "table";
    const nameCell = (r) => `${esc(r.name)}${r.legacy ? ' <span class="tag 暂无">旧</span>' : ""}${r.salary_manual || r.deduction_manual ? ' <span class="tag manual">手</span>' : ""}${r.paid ? ` <span class="lock-ico">${ICON.lock}</span>` : ""}`;
    body = all.length ? `
      <div class="card pay-summary">
        <div class="k">${month}月应发工资 · ${all.length} 人</div>
        <div class="v">¥${yuan(data.total_cents)}</div>
        <div class="pay-lines">
          ${pl("月薪合计", `¥${yuan(sum("salary_cents"))}`)}
          ${sum("deduction_cents") ? pl("请假扣款", `−¥${yuan(sum("deduction_cents"))}`) : ""}
          ${sum("bonus_cents") ? pl("奖金", `+¥${yuan(sum("bonus_cents"))}`) : ""}
          ${sum("advance_cents") ? pl("借支", `−¥${yuan(sum("advance_cents"))}`) : ""}
          ${sum("adjust_cents") ? pl("其他加减", signed(sum("adjust_cents"))) : ""}
          ${pl("应发合计", `¥${yuan(data.total_cents)}`, "total")}
        </div>
      </div>
      <div class="seg small no-print" id="payView" style="margin-bottom:12px">
        <button data-v="list" class="${tableView ? "" : "active"}">按人看</button>
        <button data-v="table" class="${tableView ? "active" : ""}">工资表</button>
      </div>
      <h3 class="report-title print-only">${esc(farmTitle())} ${year}年${month}月工资表</h3>
      ${tableView ? `
        <div class="table-wrap pay-table"><table class="grid">
          <thead><tr><th class="sticky">姓名</th><th>月薪</th><th>请假<small>天</small></th><th>扣款</th><th>奖金</th><th>借支</th><th>加减</th><th>应发</th></tr></thead>
          <tbody>${all.map((r) => `<tr ${r.legacy ? "" : `data-pay="${r.employee_id}"`}>
            <td class="sticky text">${nameCell(r)}</td>
            <td class="calc">${yuan(r.salary_cents)}</td>
            <td class="calc">${Number(r.leave_days) ? esc(r.leave_days) : ""}</td>
            <td class="calc">${r.deduction_cents ? yuan(r.deduction_cents) : ""}</td>
            <td class="calc">${r.bonus_cents ? yuan(r.bonus_cents) : ""}</td>
            <td class="calc">${r.advance_cents ? yuan(r.advance_cents) : ""}</td>
            <td class="calc">${r.adjust_cents ? (r.adjust_cents < 0 ? "−" : "+") + yuan(Math.abs(r.adjust_cents)) : ""}</td>
            <td class="calc net ${r.net_cents < 0 ? "err" : ""}">${yuan(r.net_cents)}</td></tr>`).join("")}
            <tr class="total"><td class="sticky">合计</td><td class="calc">${yuan(sum("salary_cents"))}</td><td class="calc"></td>
              <td class="calc">${yuan(sum("deduction_cents"))}</td><td class="calc">${yuan(sum("bonus_cents"))}</td><td class="calc">${yuan(sum("advance_cents"))}</td>
              <td class="calc">${sum("adjust_cents") ? signed(sum("adjust_cents")).replace("¥", "") : ""}</td><td class="calc net">${yuan(data.total_cents)}</td></tr>
          </tbody></table></div>
        <div class="quick no-print" style="margin-top:12px"><button class="btn" data-ui-action="print">${ICON.print}打印 / 存成 PDF</button><button class="btn" id="toList">按人看明细</button></div>`
      : `<div class="list-card">${all.map((r) => `
        <div class="list-row" ${r.legacy ? "" : `data-pay="${r.employee_id}" role="button" tabindex="0"`}>
          <span class="avatar">${esc(r.name.slice(0, 1))}</span>
          <span class="lr-main"><b>${esc(r.name)} ${manualTag(r)}${r.legacy ? ' <span class="tag 暂无">旧记录</span>' : ""}</b><small class="formula">${formula(r)}</small></span>
          <span class="lr-end"><b class="${r.net_cents < 0 ? "err" : ""}">¥${yuan(r.net_cents)}</b>${status(r)}</span>
        </div>`).join("")}</div>
        <p class="hint" style="margin-top:10px">点员工看明细、手动改数或记一笔。请假扣款＝月薪 ÷ ${data.month_days} 天 × 请假天数。</p>`}
      ${data.rows.length ? `<div class="lock-zone no-print">${unlocked.length
        ? `<button type="button" class="link-btn" id="settleAll">${ICON.lock}发完工资后，锁定本月（${unlocked.length} 人）</button>`
        : `<span class="hint">${ICON.lock} 本月工资已锁定</span>`}
        <p class="hint">可选。锁定后本月工资和记录不能再改，防止事后误改；需要改时在员工明细里解锁。不锁定不影响计算。</p></div>` : ""}`
      : `<div class="card empty"><h4>本月还没有工资</h4><p>先添加员工档案、设置月薪，以后每个月会自动生成。</p></div>`;
    body += `
      ${data.legacy.rows.some((r) => r.can_upgrade) ? `<div class="card"><h3 class="card-title">这些旧记录可以改用自动计算</h3><p class="hint">未发放、没有请假扣款和奖金。切换后按同样月薪自动算，原记录保留，不会重复计薪。</p>
        ${data.legacy.rows.filter((r) => r.can_upgrade).map((r) => `<button class="btn block" data-upgrade="${r.id}" style="margin-top:8px">${esc(r.name)}：改用自动计算</button>`).join("")}</div>` : ""}
      ${data.legacy.rows.length ? `<details class="card fold"><summary>历史手工工资（${data.legacy.rows.length} 条，保留原值）</summary><div id="legacyPayroll"></div></details>` : ""}`;
  } else if (section === "records") {
    const shown = data.events.filter((r) => !state.payrollEmployee || String(r.employee_id) === state.payrollEmployee);
    const groups = {};
    for (const e of shown) (groups[e.date] ||= []).push(e);
    body = `
      <div class="filter-chips" id="empFilter">
        <button data-emp="" class="${!state.payrollEmployee ? "active" : ""}">全部</button>
        ${staff.map((e) => `<button data-emp="${e.id}" class="${state.payrollEmployee === String(e.id) ? "active" : ""}">${esc(e.name)}</button>`).join("")}
      </div>
      ${Object.keys(groups).sort().reverse().map((d) => {
        const dt = new Date(d);
        return `<div class="date-group">${dt.getMonth() + 1}月${dt.getDate()}日 · 周${WEEK[dt.getDay()]}</div>
          <div class="list-card">${groups[d].map((e) => evRow(e, true)).join("")}</div>`;
      }).join("") || `<div class="card empty"><h4>本月没有记录</h4><p>请假、奖金、借支和其他加减记下来后，会自动计入工资。</p></div>`}`;
  } else {
    // 在职的排前面；已离职的收起来，需要时再展开
    const empRow = (e) => `
      <button class="list-row" data-emp-edit="${e.id}">
        <span class="avatar ${e.active ? "" : "off"}">${esc(e.name.slice(0, 1))}</span>
        <span class="lr-main"><b>${esc(e.name)}</b><small>月薪 ${e.salary_cents == null ? yuan(e.default_salary_cents) : yuan(e.salary_cents)} · ${esc(e.start_month)} 起${e.end_month ? ` · 计薪到 ${esc(e.end_month)}` : ""}</small></span>
        <span class="lr-end">${e.left ? `<span class="chip idle">已离职</span>` : e.active ? `<span class="chip ok">计薪中</span>` : `<span class="chip idle">未开始</span>`}</span>
      </button>`;
    const current = staff.filter((e) => !e.left), left = staff.filter((e) => e.left);
    body = staff.length ? `
      ${current.length ? `<div class="list-card">${current.map(empRow).join("")}</div>` : `<div class="card empty"><h4>没有在职员工</h4><p>点下方「添加员工」。</p></div>`}
      ${left.length ? `<details class="card fold" style="margin-top:12px"><summary>已离职 ${left.length} 人</summary><div class="list-card">${left.map(empRow).join("")}</div></details>` : ""}
      <p class="hint" style="margin-top:10px">点员工可以改月薪、设为离职；还没有任何工资记录的（比如加错了）可以直接删除。</p>`
      : `<div class="card empty"><h4>还没有员工档案</h4><p>档案只需录入一次，月薪会自动用于后面每个月。</p></div>`;
  }

  view.innerHTML = `
    <div class="seg small" id="paySections" style="margin-bottom:12px">
      ${[["monthly", "本月结算"], ["records", "记录"], ["employees", "员工"]].map(([id, label]) => `<button data-section="${id}" class="${section === id ? "active" : ""}">${label}</button>`).join("")}
    </div>
    ${body}
    <div class="action-bar"><div class="inner">
      <button class="btn" id="addEmp">${ICON.plus}添加员工</button>
      <button class="btn cta" id="addEv">${ICON.plus}记一笔</button>
    </div></div>`;

  $("#paySections", view).onclick = (e) => { const s = e.target.dataset.section; if (s) { state.payrollSection = s; store.set("paySection", s); renderBossPayroll(view, year, month, data); } };
  $("#addEmp", view).onclick = () => employeeForm();
  $("#addEv", view).onclick = () => eventForm(null, state.payrollEmployee ? Number(state.payrollEmployee) : null);
  $("#empFilter", view)?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-emp]");
    if (b) { state.payrollEmployee = b.dataset.emp; renderBossPayroll(view, year, month, data); }
  });
  view.onkeydown = (e) => { if (e.key === "Enter" && e.target.matches("[data-pay]")) e.target.click(); };
  $("#payView", view)?.addEventListener("click", (e) => {
    const v = e.target.dataset.v;
    if (v) { state.payView = v; store.set("payView", v); renderBossPayroll(view, year, month, data); }
  });
  $("#toList", view)?.addEventListener("click", () => { state.payView = "list"; store.set("payView", "list"); renderBossPayroll(view, year, month, data); });
  view.onclick = (e) => {
    if (e.target.closest("#settleAll")) return settleAll(e.target.closest("#settleAll"));
    const pay = e.target.closest("[data-pay]");
    if (pay) return paySheet(data.rows.find((r) => r.employee_id === Number(pay.dataset.pay)));
    const ev = e.target.closest("[data-ev]");
    if (ev && !e.target.closest("#sheet")) return eventForm(data.events.find((x) => x.id === Number(ev.dataset.ev)));
    const emp = e.target.closest("[data-emp-edit]");
    if (emp) return employeeForm(staff.find((x) => x.id === Number(emp.dataset.empEdit)));
    const up = e.target.closest("[data-upgrade]");
    if (up) return upgrade(data.legacy.rows.find((r) => r.id === Number(up.dataset.upgrade)), up);
  };
  if (section === "monthly" && data.legacy.rows.length) {
    const host = $("#legacyPayroll", view);
    renderLegacyPayroll(host, year, month).then(() => {
      if (host.isConnected) { $("#addWage", host)?.setAttribute("hidden", ""); $("#carryWages", host)?.setAttribute("hidden", ""); }
    });
  }
  if (state.paySheetFor) {
    const r = data.rows.find((x) => x.employee_id === state.paySheetFor);
    state.paySheetFor = null;
    if (r) paySheet(r);
  }

  // ----- 员工工资明细（底部弹出） -----
  function paySheet(r) {
    const line = (label, value, note = "", cls = "") => `<div class="pay-line ${cls}"><span><em class="pl-label">${label}</em>${note ? `<small>${note}</small>` : ""}</span><b>${value}</b></div>`;
    const own = data.events.filter((e) => e.employee_id === r.employee_id);
    const auto = (v) => `<small class="was">自动 ${v}</small>`;
    openSheet(`
      <div class="sheet-head"><span class="avatar">${esc(r.name.slice(0, 1))}</span>
        <div><h2>${esc(r.name)}</h2><p>${year}年${month}月工资${r.paid ? " · 已锁定" : ""}</p></div>${r.net_cents < 0 ? status(r) : ""}</div>
      <div class="pay-lines">
        ${line(`固定月薪${r.salary_manual ? ' <span class="tag manual">手动</span>' : ""}`, `¥${yuan(r.salary_cents)}`, r.salary_manual ? auto(`¥${yuan(r.salary_auto_cents)}`) : "")}
        ${line(`请假 ${esc(r.leave_days)} 天扣款${r.deduction_manual ? ' <span class="tag manual">手动</span>' : ""}`, `−¥${yuan(r.deduction_cents)}`,
          r.deduction_manual ? auto(`−¥${yuan(r.deduction_auto_cents)}`) : Number(r.leave_days) ? `${yuan(r.salary_cents)} ÷ ${r.month_days} 天 × ${esc(r.leave_days)} 天` : "")}
        ${line("奖金", `+¥${yuan(r.bonus_cents)}`)}
        ${line("借支", `−¥${yuan(r.advance_cents)}`)}
        ${r.adjust_cents ? line("其他加减", signed(r.adjust_cents)) : ""}
        ${line("本月应发", `¥${yuan(r.net_cents)}`, "", `total ${r.net_cents < 0 ? "neg" : ""}`)}
      </div>
      ${r.override_note ? `<p class="note-box">手动改数原因：${esc(r.override_note)}</p>` : ""}
      ${r.net_cents < 0 ? `<p class="err">借支超过可发工资，请先核对记录。</p>` : ""}
      ${r.paid ? `<p class="note-box">${ICON.lock} 已于 ${esc(r.settled_at || "")} 锁定，要修改请先解锁。</p>` : ""}
      <div class="section-head"><h3>本月记录（${own.length}）</h3></div>
      ${own.length ? `<div class="list-card">${own.map((e) => evRow(e, false)).join("")}</div>` : `<p class="hint">本月没有请假、奖金、借支或加减。</p>`}
      ${r.paid ? "" : r.net_cents >= 0 ? `<button type="button" class="link-btn" id="psSettle">${ICON.lock}发完了，锁定${esc(r.name)}本月工资（可选）</button>` : ""}
      <div class="sheet-actions" style="margin-top:4px">
        ${r.paid ? `<button class="btn" data-close>关闭</button><button class="btn" id="psReopen">${ICON.lock}解锁</button>`
          : `<button class="btn" id="psOverride">手动改数</button><button class="btn cta" id="psAddEv">${ICON.plus}记一笔</button>`}
      </div>`, (dlg) => {
      $("[data-close]", dlg)?.addEventListener("click", closeSheet);
      $("#psAddEv", dlg)?.addEventListener("click", () => eventForm(null, r.employee_id, true));
      $("#psOverride", dlg)?.addEventListener("click", () => overrideForm(r));
      $$("[data-ev]", dlg).forEach((b) => b.onclick = () => { if (!r.paid) eventForm(own.find((e) => e.id === Number(b.dataset.ev)), null, true); });
      $("#psSettle", dlg)?.addEventListener("click", async (e) => {
        if (!(await askConfirm(`锁定${r.name}的${month}月工资？应发 ¥${yuan(r.net_cents)}。锁定后本月工资和记录不能再改，防止事后误改；需要时可以解锁。`))) return;
        e.target.disabled = true;
        try { await api("payroll/settle", { method: "POST", json: { year, month, employee_id: r.employee_id, expected_net_cents: r.net_cents } }); setStatus("已锁定"); reload(r.employee_id); }
        catch (err) { setStatus(err.message, true); e.target.disabled = false; }
      });
      $("#psReopen", dlg)?.addEventListener("click", async (e) => {
        if (!(await askConfirm(`解锁${r.name}的${month}月工资？解锁后可以修改，原来锁定时的金额会留存备查。`))) return;
        e.target.disabled = true;
        try { await api("payroll/reopen", { method: "POST", json: { year, month, employee_id: r.employee_id } }); setStatus("已解锁"); reload(r.employee_id); }
        catch (err) { setStatus(err.message, true); e.target.disabled = false; }
      });
    });
  }

  // ----- 表单（统一样式：底部弹出、大输入框） -----
  function form(title, inner, onSubmit, extra) {
    openSheet(`<h2>${title}</h2><form id="sheetForm" class="sheet-form" novalidate>${inner}
      <p class="err" id="formErr" role="alert"></p>
      <div class="sheet-actions">${extra?.left || `<button type="button" class="btn" data-close>取消</button>`}<button class="btn cta" type="submit">保存</button></div></form>`, (dlg) => {
      const f = $("#sheetForm", dlg);
      $("[data-close]", dlg)?.addEventListener("click", closeSheet);
      extra?.ready?.(f, dlg);
      f.onsubmit = async (e) => {
        e.preventDefault();
        if (!f.reportValidity()) return;
        const btn = $("button[type=submit]", f); btn.disabled = true;
        try { await onSubmit(Object.fromEntries(new FormData(f))); }
        catch (err) { $("#formErr", f).textContent = err.message; btn.disabled = false; }
      };
    });
  }
  const field = (label, input, unit = "") => `<label class="field"><span>${label}${unit ? ` <span class="unit">${unit}</span>` : ""}</span>${input}</label>`;

  function eventForm(r = null, presetEid = null, back = false) {
    if (!staff.length) { setStatus("请先添加员工档案", true); return; }
    const editable = staff.filter((e) => e.active || e.id === r?.employee_id);
    const kind = r?.kind || "leave";
    const sel = r?.employee_id ?? presetEid;
    form(r ? "编辑记录" : "记一笔", `
      ${field("员工", `<select name="employee_id" required>${editable.map((e) => `<option value="${e.id}" ${e.id === sel ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select>`)}
      <input type="hidden" name="kind" value="${kind}">
      <div class="seg small" id="kindSeg">${Object.entries(KIND).map(([k, l]) => `<button type="button" data-k="${k}" class="${k === kind ? "active" : ""}">${l}</button>`).join("")}</div>
      <div class="fields-2">
        ${field("日期", `<input name="date" type="date" required min="2000-01-01" max="2100-12-31" value="${r?.date || recordDate}">`)}
        ${r ? "" : `<div data-only="leave">${field("请假到", `<input name="end_date" type="date" min="2000-01-01" max="2100-12-31" value="${recordDate}">`, "连续请假")}</div>`}
      </div>
      <div data-only="leave">${field("每天请假", `<input name="leave_days" type="number" inputmode="decimal" min="0.1" max="1" step="0.1" value="${r?.kind === "leave" ? esc(r.leave_days) : "1"}">`, "天，0.1～1")}</div>
      <div data-only="adjust"><input type="hidden" name="sign" value="${r && r.amount_cents < 0 ? "-" : "+"}">
        <div class="seg small" id="signSeg"><button type="button" data-s="+" class="${!(r && r.amount_cents < 0) ? "active" : ""}">加钱</button><button type="button" data-s="-" class="${r && r.amount_cents < 0 ? "active" : ""}">扣钱</button></div></div>
      <div data-only="money">${field("金额", `<input name="amount" type="number" inputmode="decimal" min="0.01" step="0.01" value="${r && r.kind !== "leave" ? (Math.abs(r.amount_cents) / 100).toFixed(2) : ""}">`, "元")}</div>
      ${field("说明", `<input name="note" maxlength="1000" value="${esc(r?.note || "")}" placeholder="可不填">`, `<span id="noteReq"></span>`)}`,
    async (b) => {
      const body = { ...b, employee_id: Number(b.employee_id), ...(r ? { id: r.id } : {}) };
      await api("payroll/events", { method: "POST", json: body });
      setStatus("已保存，工资已重新计算");
      reload(back ? body.employee_id : null);
    }, {
      left: r ? `<button type="button" class="btn danger" id="evDel">删除</button>` : null,
      ready(f, dlg) {
        const sync = () => {
          const k = f.elements.kind.value;
          $$("[data-only]", f).forEach((el) => {
            const only = el.dataset.only;
            const show = only === "money" ? k !== "leave" : only === k;
            el.hidden = !show;
            $$("input", el).forEach((i) => { if (i.type !== "hidden") i.disabled = !show; });
          });
          f.elements.amount.required = k !== "leave";
          f.elements.leave_days.required = k === "leave";
          f.elements.note.required = k === "adjust";
          f.elements.note.placeholder = k === "adjust" ? "必填，例如：全勤奖 / 损坏工具" : "可不填";
          $("#noteReq", f).textContent = k === "adjust" ? "必填" : "";
        };
        $("#kindSeg", f).onclick = (e) => { const k = e.target.dataset.k; if (!k) return; f.elements.kind.value = k; $$("#kindSeg button", f).forEach((x) => x.classList.toggle("active", x === e.target)); sync(); };
        $("#signSeg", f).onclick = (e) => { const s = e.target.dataset.s; if (!s) return; f.elements.sign.value = s; $$("#signSeg button", f).forEach((x) => x.classList.toggle("active", x === e.target)); };
        if (!r) {
          let prev = f.elements.date.value;
          f.elements.date.onchange = () => { const end = f.elements.end_date; if (end.value === prev || end.value < f.elements.date.value) end.value = f.elements.date.value; prev = f.elements.date.value; };
        }
        sync();
        $("#evDel", dlg)?.addEventListener("click", async (e) => {
          if (!(await askConfirm("删除这条记录？未结算的工资会随之重新计算。"))) return;
          e.target.disabled = true;
          try { await api(`payroll/events/${r.id}`, { method: "DELETE" }); setStatus("已删除"); reload(back ? r.employee_id : null); }
          catch (err) { $("#formErr", f).textContent = err.message; e.target.disabled = false; }
        });
      },
    });
  }

  function overrideForm(r) {
    const manual = r.salary_manual || r.deduction_manual;
    const v = (on, cents) => (on ? (cents / 100).toFixed(2) : "");
    form(`手动改数 · ${esc(r.name)}`, `
      <p class="hint">只改 ${month} 月，其他月份不受影响。留空就用自动计算的数。</p>
      ${field("本月月薪", `<input name="salary" type="number" inputmode="decimal" min="0" step="0.01" value="${v(r.salary_manual, r.salary_cents)}" placeholder="自动 ${yuan(r.salary_auto_cents)}">`, `元 · 自动 ${yuan(r.salary_auto_cents)}`)}
      ${field("请假扣款", `<input name="deduction" type="number" inputmode="decimal" min="0" step="0.01" value="${v(r.deduction_manual, r.deduction_cents)}" placeholder="自动 ${yuan(r.deduction_auto_cents)}">`, `元 · 自动 ${yuan(r.deduction_auto_cents)}`)}
      ${field("原因", `<input name="note" maxlength="1000" value="${esc(r.override_note || "")}" placeholder="例如：16号入职，按半月发">`, "必填")}`,
    async (b) => {
      await api("payroll/override", { method: "POST", json: { ...b, year, month, employee_id: r.employee_id } });
      setStatus(b.salary || b.deduction ? "已保存手动改数" : "已恢复自动计算");
      reload(r.employee_id);
    }, {
      left: manual ? `<button type="button" class="btn" id="ovClear">恢复自动</button>` : null,
      ready(f, dlg) {
        $("#ovClear", dlg)?.addEventListener("click", async (e) => {
          e.target.disabled = true;
          try { await api("payroll/override", { method: "POST", json: { year, month, employee_id: r.employee_id, salary: "", deduction: "" } }); setStatus("已恢复自动计算"); reload(r.employee_id); }
          catch (err) { $("#formErr", f).textContent = err.message; e.target.disabled = false; }
        });
      },
    });
  }

  function employeeForm(e = null) {
    form(e ? "编辑员工" : "添加员工", `
      ${field("姓名", `<input name="name" required maxlength="60" value="${esc(e?.name || "")}">`)}
      <div class="fields-2">
        ${field("固定月薪", `<input name="salary" type="number" inputmode="decimal" min="0" step="0.01" required value="${e ? ((e.salary_cents ?? e.default_salary_cents) / 100).toFixed(2) : ""}">`, "元")}
        ${field("从哪个月起", `<input name="effective_month" type="month" required min="${e?.start_month || "2000-01"}" max="2100-12" value="${e && e.start_month > state.bossMonth ? e.start_month : state.bossMonth}">`)}
      </div>
      <p class="hint">${e ? "改月薪从所选月份起生效，已结算的月份不变。只想改某一个月，请在员工明细里用「手动改数」。" : "从这个月起，每个月自动生成工资草稿。"}</p>
      ${field("最后计薪月份", `<input name="end_month" type="month" min="2000-01" max="2100-12" value="${esc(e?.end_month || "")}">`, "在职留空")}
      ${field("备注", `<input name="note" maxlength="1000" value="${esc(e?.note || "")}">`)}
      ${!e ? "" : e.deletable ? `<div class="danger-zone">
          <button type="button" class="btn danger block" id="empDel">删除这名员工</button>
          <p class="hint">这名员工还没有任何工资记录，可以直接删除（比如添加错了）。</p></div>`
        : e.end_month ? `<div class="danger-zone">
          <button type="button" class="btn block" id="empBack">恢复在职</button>
          <p class="hint">已设置计薪到 ${esc(e.end_month)}。恢复后每个月继续自动生成工资。有工资记录的员工不能删除，以免以前的账对不上。</p></div>`
        : `<div class="danger-zone">
          <button type="button" class="btn danger block" id="empLeave">设为离职</button>
          <p class="hint">${month} 月工资照常，从下个月起不再生成。以前的工资和记录都保留。有工资记录的员工不能删除，以免以前的账对不上。</p></div>`}`,
    async (b) => {
      await api("payroll/employees", { method: "POST", json: { ...b, ...(e ? { id: e.id } : {}) } });
      setStatus(b.end_month && !e?.end_month ? `已设为离职，${b.end_month} 之后不再生成工资` : "已保存，工资已重新计算");
      reload();
    }, {
      ready(f, dlg) {
        $("#empLeave", dlg)?.addEventListener("click", async () => {
          if (!(await askConfirm(`把${e.name}设为离职？${month} 月工资照常，从下个月起不再自动生成。`))) return;
          f.elements.end_month.value = state.bossMonth;
          f.requestSubmit();
        });
        $("#empBack", dlg)?.addEventListener("click", () => {
          f.elements.end_month.value = "";
          f.requestSubmit();
        });
        $("#empDel", dlg)?.addEventListener("click", async (ev) => {
          if (!(await askConfirm(`删除员工「${e.name}」？删除后不能恢复。`))) return;
          ev.target.disabled = true;
          try { await api(`payroll/employees/${e.id}`, { method: "DELETE" }); setStatus(`已删除 ${e.name}`); reload(); }
          catch (err) { $("#formErr", f).textContent = err.message; ev.target.disabled = false; }
        });
      },
    });
  }

  // ----- 锁定（可选）：发完工资后锁定本月，防止事后误改 -----
  async function settleAll(btn) {
    const list = data.rows.filter((r) => !r.paid && r.net_cents >= 0);
    const neg = data.rows.filter((r) => !r.paid && r.net_cents < 0);
    const sum = list.reduce((s, r) => s + r.net_cents, 0);
    const ok = await askConfirm({
      title: `锁定 ${list.length} 人的${month}月工资？`,
      ok: "锁定",
      html: `<div class="confirm-list">${list.map((r) => `<div><span>${esc(r.name)}</span><b>¥${yuan(r.net_cents)}</b></div>`).join("")}
          <div class="total"><span>合计</span><b>¥${yuan(sum)}</b></div></div>
        ${neg.length ? `<p class="err" style="text-align:left">${neg.map((r) => esc(r.name)).join("、")} 应发是负数（借支超过工资），这次不锁定，请先核对。</p>` : ""}
        <p>锁定后这些人本月工资和记录不能再改；需要时在员工明细里解锁。</p>`,
    });
    if (!ok) return;
    btn.disabled = true;
    try {
      const r = await api("payroll/settle-all", { method: "POST", json: { year, month, expected: Object.fromEntries(list.map((x) => [x.employee_id, x.net_cents])) } });
      setStatus(`已锁定 ${r.count} 人的${month}月工资`);
      reload();
    } catch (err) { setStatus(err.message, true); btn.disabled = false; }
  }

  async function upgrade(old, btn) {
    if (!(await askConfirm(`将${old.name}的${month}月工资改用自动计算？月薪保持 ¥${yuan(old.salary_cents)}，原记录保留。`))) return;
    btn.disabled = true;
    try { await api("payroll/upgrade", { method: "POST", json: { id: old.id } }); setStatus("已改用自动计算"); reload(); }
    catch (err) { setStatus(err.message, true); btn.disabled = false; }
  }
}

async function renderLegacyPayroll(view, year, month) {
  const wages = await api(`payroll?year=${year}&month=${month}`);
  if (!view.isConnected) return;
  const yuan = (cents) => (cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  view.innerHTML = `
    <div class="section-head"><h3>${month}月工资结算</h3><button id="addWage">添加员工 ›</button></div>
    <p class="hint">固定月薪 − 请假扣款 ＋ 奖金＝实发。请假扣款手填，发放状态仅作记账。</p>
    <div class="card"><div class="calc-box"><div><span>实发合计 元</span><b>${yuan(wages.total_cents)}</b></div><div><span>已发放 元</span><b>${yuan(wages.paid_cents)}</b></div><div><span>待发放 元</span><b>${yuan(wages.total_cents - wages.paid_cents)}</b></div></div></div>
    ${wages.rows.length ? wages.rows.map(r => `<div class="card"><div class="section-head"><h3>${esc(r.name)}</h3><button data-wage="${r.id}">编辑 ›</button></div>
      <div class="calc-box"><div><span>固定月薪 元</span><b>${yuan(r.salary_cents)}</b></div><div><span>请假 ${esc(r.leave_days)} 天 · 扣款</span><b>${yuan(r.deduction_cents)}</b></div><div><span>奖金 元</span><b>${yuan(r.bonus_cents)}</b></div></div>
      <div class="section-head" style="margin-top:14px"><span>${r.paid ? "已发放" : "未发放"}</span><b>实发 ¥${yuan(r.net_cents)}</b></div>${r.note ? `<p class="hint">${esc(r.note)}</p>` : ""}</div>`).join("")
      : `<div class="card empty"><h4>本月还没有工资记录</h4><p>添加员工，或从上个月带入姓名和固定月薪。</p></div>`}
    <button class="btn block" id="carryWages">从上月带入姓名和月薪</button>
    <p class="hint">只添加本月尚无记录的员工，不带入请假、扣款、奖金和发放状态。</p>`;
  $("#addWage", view).onclick = () => editWage();
  $$('[data-wage]', view).forEach(b => b.onclick = () => editWage(wages.rows.find(r => r.id === Number(b.dataset.wage))));
  $("#carryWages", view).onclick = async (e) => {
    e.target.disabled = true;
    try { const r = await api('payroll/carry', {method: 'POST', json: {year, month}}); setStatus(`已带入 ${r.added} 位员工`); invalidate('payroll'); render(); }
    catch (err) { setStatus(err.message, true); e.target.disabled = false; }
  };

  function editWage(row = null) {
    const moneyField = (name, label, cents) => `<label>${label}<input name="${name}" type="number" inputmode="decimal" min="0" step="0.01" required value="${cents == null ? '' : (cents / 100).toFixed(2)}"></label>`;
    openSheet(`<h2>${month}月 · ${row ? '编辑工资' : '添加员工'}</h2><form id="wageForm" class="sheet-form">
      <label>员工姓名<input name="name" maxlength="60" required value="${esc(row?.name || '')}" autocomplete="off"></label>
      <div class="fields-2" style="margin-top:14px">${moneyField('salary','固定月薪（元）',row?.salary_cents)}
      <label>请假天数<input name="leave_days" type="number" inputmode="decimal" min="0" max="31" step="0.1" required value="${esc(row?.leave_days || '0')}"></label>
      ${moneyField('deduction','请假扣款（元）',row?.deduction_cents || 0)}${moneyField('bonus','奖金（元）',row?.bonus_cents || 0)}</div>
      <p class="hint">请假扣款按实际金额填写。</p><p>实发工资：<b id="wageNet"></b> 元</p>
      <label>发放状态<select name="paid"><option value="false" ${!row?.paid ? 'selected' : ''}>未发放</option><option value="true" ${row?.paid ? 'selected' : ''}>已发放</option></select></label>
      <label>备注<input name="note" maxlength="1000" value="${esc(row?.note || '')}"></label>
      <p id="wageError" class="err" role="alert"></p><div class="sheet-actions"><button type="button" class="btn" id="cancelWage">取消</button><button type="submit" class="btn cta">保存</button></div>
      ${row ? '<button type="button" class="btn danger block" id="deleteWage" style="margin-top:12px">删除这条工资记录</button>' : ''}</form>`, () => {
      const form = $("#wageForm");
      const calc = () => {
        const cents = (key) => Math.round(Number(form.elements[key].value || 0) * 100);
        $("#wageNet").textContent = yuan(cents('salary') - cents('deduction') + cents('bonus'));
      };
      form.oninput = calc; calc();
      $("#cancelWage").onclick = closeSheet;
      form.onsubmit = async (e) => {
        e.preventDefault();
        const button = $('button[type=submit]', form); button.disabled = true;
        const body = Object.fromEntries(new FormData(form));
        body.year = year; body.month = month; body.paid = body.paid === 'true'; if (row) body.id = row.id;
        try { await api('payroll', { method:'POST', json: body }); closeSheet(); setStatus('工资已保存'); invalidate('payroll'); render(); }
        catch(err) { $("#wageError").textContent = err.message; button.disabled = false; }
      };
      if (row) $("#deleteWage").onclick = async (e) => {
        if (!(await askConfirm(`删除${row.name}的${month}月工资记录？`))) return;
        e.target.disabled = true;
        try { await api(`payroll/${row.id}`, { method:'DELETE' }); closeSheet(); invalidate('payroll'); render(); }
        catch(err) { $("#wageError").textContent = err.message; e.target.disabled = false; }
      };
    });
  }
}

// 老板端本月经营页、管理端首页：每分钟、以及从后台切回来时，静默刷新漏喂提示（不闪屏、不跳回顶部）
async function refreshBossLive() {
  if (document.hidden) return;
  if (state.role !== "boss") {
    if (state.page !== "home" || state.sub || !$("#feedAlert")) return;
    invalidate("feed-alert");
    try { renderFeedAlert(await cached("feed-alert", 30000)); } catch { /* 下次再试 */ }
    return;
  }
  if (state.bossTab !== "overview") return;
  if (state.bossMonth !== todayStr().slice(0, 7) || $("#sheet")?.open || $("#confirm")?.open) return;
  const host = $("#bossContent");
  if (!host || host.querySelector(".skeleton")) return;
  const [year, month] = state.bossMonth.split("-").map(Number);
  const farm = state.bossFarm;
  const same = () => state.bossTab === "overview" && state.bossFarm === farm && host.isConnected;
  try {
    if (state.farms.length > 1 && farm === "all") {  // 全部厂：刷新合计和各厂漏喂
      invalidate("boss/all");
      const data = await cached(`boss/all?year=${year}&month=${month}`, 30000, "");
      if (same()) renderBossAll(host, month, data);
    } else {
      invalidate("boss?");
      const boss = await cached(`boss?year=${year}&month=${month}`, 30000);
      if (same()) renderBossOverview(host, year, month, boss);
    }
  } catch { /* 网络不好时保留现在的内容，下次再试 */ }
}
setInterval(refreshBossLive, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshBossLive(); });

// ---------- 启动 ----------

(async function init() {
  try {
    const me = await api("me");
    state.role = me.role || "admin";
    state.loginRequired = me.login_required;
    if (me.farm) state.farm = me.farm;
    state.farms = me.farms || [];
    state.needSetup = state.role === "admin" && me.ponds === 0;
    // 只有一个厂时老板端不显示切换；记住的厂不在了就回到全部
    if (state.farms.length < 2) state.bossFarm = state.farms[0]?.id || "demo";
    else if (state.bossFarm !== "all" && !state.farms.some((f) => f.id === state.bossFarm)) state.bossFarm = "all";
    document.title = state.role === "boss" && state.farms.length > 1 ? "鳗场报表" : state.farm.title;
    $("#brandMark").textContent = state.role === "boss" && state.farms.length > 1 ? "鳗" : state.farm.mark;
  } catch (err) {
    $("#view").innerHTML = `<div class="card empty"><h4>连不上服务器</h4><p>${esc(err.message)}</p></div>`;
    return;
  }
  if (!["home", "today", "pond", "report", "more"].includes(state.page)) state.page = "home";
  $("#view").innerHTML = `<div class="skeleton"><i></i><i></i><i></i><i></i></div>`;
  render();
})();
