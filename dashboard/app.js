import { initializeApp } from "https://www.gstatic.com/firebasejs/11.10.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut }
  from "https://www.gstatic.com/firebasejs/11.10.0/firebase-auth.js";
import { getFirestore, doc, getDoc, setDoc, updateDoc, collection, query, orderBy, limit, onSnapshot }
  from "https://www.gstatic.com/firebasejs/11.10.0/firebase-firestore.js";
import { DEFAULTS } from "./defaults.js";

const REPO = "Peper0007/olx-free-finder";
const WORKFLOW = "finder.yml";
const SOURCE_NAMES = { olx: "OLX", bazar: "Bazar.bg", alo: "Alo.bg" };
const STATUS = { new: "новое", interesting: "интересно", taken: "забрал", skip: "мимо" };

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Firebase Hosting serves the project's web config at this reserved URL.
const config = await (await fetch("/__/firebase/init.json")).json();
const app = initializeApp(config);
const auth = getAuth(app);
const db = getFirestore(app);

let settings = structuredClone(DEFAULTS);
let finds = [];
let unsubFinds = null, unsubRuns = null;

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 4000);
}

// ---------- auth ----------
$("#btn-login").onclick = async () => {
  try { await signInWithPopup(auth, new GoogleAuthProvider()); }
  catch (e) { $("#login-error").textContent = e.message; }
};
$("#btn-logout").onclick = () => signOut(auth);

onAuthStateChanged(auth, async (user) => {
  if (!user) {
    $("#login").classList.remove("hidden"); $("#app").classList.add("hidden");
    unsubFinds?.(); unsubRuns?.();
    return;
  }
  $("#user-email").textContent = user.email;
  try {
    await loadSettings();
  } catch (e) {
    if (e?.code === "permission-denied") {
      $("#login-error").textContent = "Нет доступа для " + user.email + ". Дашборд открыт только владельцу.";
      await signOut(auth);
      return;
    }
    console.error(e);
    toast("Ошибка загрузки настроек: " + (e?.message || e) + " — обнови страницу (Ctrl+F5).", true);
  }
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  subscribeFinds(); subscribeRuns(); renderGhState();
});

// ---------- tabs ----------
document.querySelectorAll(".tabs button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + b.dataset.tab));
  };
});

// ---------- settings ----------
function deepMerge(base, over) {
  const out = structuredClone(base);
  for (const [k, v] of Object.entries(over || {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && typeof out[k] === "object" && !Array.isArray(out[k])) out[k] = deepMerge(out[k], v);
    else if (v !== null && v !== undefined) out[k] = v;
  }
  return out;
}

async function loadSettings() {
  const snap = await getDoc(doc(db, "config", "settings"));
  settings = deepMerge(DEFAULTS, snap.exists() ? snap.data() : {});
  fillForms();
}

function lines(text) { return String(text ?? "").split("\n").map((s) => s.trim()).filter(Boolean); }
function setVal(sel, v) { const el = $(sel); if (el) el.value = v ?? ""; }
function setChk(sel, v) { const el = $(sel); if (el) el.checked = !!v; }
function getVal(sel) { return $(sel)?.value ?? ""; }

function fillForms() {
  const s = settings;
  setChk("#s-paused", s.paused);
  setChk("#s-telegram", s.telegram_enabled !== false);
  setVal("#s-min", s.min_score); if ($("#s-min-val")) $("#s-min-val").textContent = s.min_score;
  setVal("#s-maxai", s.max_ai_per_run);
  setVal("#s-model", s.model);
  setChk("#s-olx", s.sources.olx.enabled); setVal("#s-olx-pages", s.sources.olx.pages);
  setChk("#s-bazar", s.sources.bazar.enabled); setVal("#s-bazar-pages", s.sources.bazar.pages);
  setChk("#s-alo", s.sources.alo.enabled); setVal("#s-alo-q", (s.sources.alo.queries || []).join("\n"));
  setVal("#w-skip", (s.skip_words || []).join("\n"));
  setVal("#w-keep", (s.keep_words || []).join("\n"));
  setVal("#w-url", (s.skip_url_parts || []).join("\n"));
  setVal("#c-text", s.criteria);
  setVal("#f-score", s.min_score); if ($("#f-score-val")) $("#f-score-val").textContent = s.min_score;
  renderBotState();
}
$("#s-min").oninput = (e) => ($("#s-min-val").textContent = e.target.value);

function renderBotState() {
  const p = $("#bot-state");
  p.textContent = settings.paused ? "⏸ на паузе" : "● работает · порог " + settings.min_score;
  p.className = "pill " + (settings.paused ? "warn" : "ok");
}

async function save(part) {
  let patch;
  if (part === "settings") {
    patch = {
      paused: $("#s-paused").checked,
      telegram_enabled: $("#s-telegram").checked,
      min_score: +$("#s-min").value,
      max_ai_per_run: Math.max(1, Math.min(200, +$("#s-maxai").value || 100)),
      model: $("#s-model").value.trim() || DEFAULTS.model,
      sources: {
        olx: { enabled: $("#s-olx").checked, pages: Math.max(1, Math.min(25, +$("#s-olx-pages").value || 25)) },
        bazar: { enabled: $("#s-bazar").checked, pages: Math.max(1, Math.min(30, +$("#s-bazar-pages").value || 10)) },
        alo: { enabled: $("#s-alo").checked, queries: lines($("#s-alo-q").value) },
      },
    };
  } else if (part === "filters") {
    patch = { skip_words: lines(getVal("#w-skip")), keep_words: lines(getVal("#w-keep")), skip_url_parts: lines(getVal("#w-url")) };
  } else {
    patch = { criteria: $("#c-text").value.trim() || DEFAULTS.criteria };
  }
  try {
    settings = deepMerge(settings, patch);
    await setDoc(doc(db, "config", "settings"), { ...settings, updated_at: new Date() });
    renderBotState(); renderFinds();
    toast("Сохранено. Бот применит при следующем запуске.");
  } catch (e) { toast("Ошибка сохранения: " + e.message, true); }
}
document.querySelectorAll("[data-save]").forEach((b) => (b.onclick = () => save(b.dataset.save)));
$("#c-reset").onclick = () => { $("#c-text").value = DEFAULTS.criteria; toast("Текст по умолчанию вставлен — нажми «Сохранить»."); };

// ---------- word-filter tester (Python-like \b for Cyrillic) ----------
const W = "[\\p{L}\\p{N}_]";
function pyRegex(src) {
  const conv = src.replace(/\\b/g, `(?:(?<=${W})(?!${W})|(?<!${W})(?=${W}))`);
  try { return new RegExp(conv, "iu"); } catch { return new RegExp(src.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "iu"); }
}
function testTitle(title) {
  const skip = lines($("#w-skip").value).find((w) => pyRegex(w).test(title));
  const keep = lines($("#w-keep").value).find((w) => pyRegex(w).test(title));
  if (!title) return "";
  if (skip && !keep) return `<span class="bad">✖ мусор</span> — сработало «${esc(skip)}»`;
  if (skip && keep) return `<span class="good">✔ к ИИ</span> — «${esc(skip)}», но спасло исключение «${esc(keep)}»`;
  return `<span class="good">✔ к ИИ</span> — ни одно мусорное слово не сработало`;
}
$("#w-test").oninput = () => ($("#w-test-res").innerHTML = testTitle($("#w-test").value.trim()));

// ---------- finds ----------
function subscribeFinds() {
  unsubFinds?.();
  const q = query(collection(db, "finds"), orderBy("analyzed_at", "desc"), limit(+$("#f-limit").value));
  unsubFinds = onSnapshot(q, (snap) => {
    finds = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
    renderFinds();
  }, (e) => toast("Ошибка загрузки находок: " + e.message, true));
}
$("#f-limit").onchange = subscribeFinds;
["#f-score", "#f-source", "#f-status", "#f-search"].forEach((s) => ($(s).oninput = () => {
  $("#f-score-val").textContent = $("#f-score").value; renderFinds();
}));

function fmtDate(ts) {
  const d = ts?.toDate ? ts.toDate() : ts ? new Date(ts) : null;
  return d ? d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
}
function scoreClass(s) { return s >= 7 ? "s-high" : s >= 4 ? "s-mid" : "s-low"; }

function renderFinds() {
  const minS = +$("#f-score").value, src = $("#f-source").value, st = $("#f-status").value;
  const q = $("#f-search").value.trim().toLowerCase();
  const list = finds.filter((f) =>
    (f.score ?? 0) >= minS && (!src || f.source === src) && (!st || (f.status || "new") === st) &&
    (!q || `${f.title} ${f.location} ${f.items}`.toLowerCase().includes(q)));

  const today = new Date().toDateString();
  const hits = finds.filter((f) => (f.score ?? 0) >= settings.min_score);
  $("#stats").innerHTML = `
    <div><b>${finds.length}</b><span>оценено (последние)</span></div>
    <div><b>${hits.length}</b><span>находок ≥ ${settings.min_score}</span></div>
    <div><b>${finds.filter((f) => f.analyzed_at?.toDate?.().toDateString() === today).length}</b><span>оценено сегодня</span></div>
    <div><b>${finds.filter((f) => f.status === "interesting").length}</b><span>отмечено «интересно»</span></div>
    <div><b>${finds.filter((f) => f.status === "taken").length}</b><span>забрал</span></div>`;

  $("#finds-empty").classList.toggle("hidden", list.length > 0);
  $("#finds").innerHTML = list.map((f) => `
    <article class="find ${f.status === "skip" ? "dim" : ""}">
      <a class="ph" href="${esc(f.url)}" target="_blank" rel="noopener">
        ${f.photos?.[0] ? `<img loading="lazy" referrerpolicy="no-referrer" src="${esc(f.photos[0])}" alt="">` : `<div class="nophoto">нет фото</div>`}
        <span class="score ${scoreClass(f.score)}">${f.score}/10</span>
        ${f.sent ? `<span class="tg" title="Отправлено в Telegram">✈</span>` : ""}
      </a>
      <div class="body">
        <a class="title" href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.title)}</a>
        ${f.items ? `<p>🔎 ${esc(f.items)}</p>` : ""}
        ${f.why ? `<p class="muted">💡 ${esc(f.why)}</p>` : ""}
        <p class="price">💰 ${f.price_etsy || f.price_ebay ? `Etsy <b>${esc(f.price_etsy || "—")}</b> · eBay <b>${esc(f.price_ebay || "—")}</b>` : esc(f.price || "—")}</p>
        <p class="meta">🚚 ${esc(f.pickup || "—")}</p>
        <p class="meta">📍 ${esc(f.location || "?")} · ${SOURCE_NAMES[f.source] || f.source} · ${fmtDate(f.analyzed_at)}</p>
        <div class="status">
          ${Object.entries(STATUS).map(([k, v]) => `<button data-id="${esc(f.id)}" data-st="${k}" class="${(f.status || "new") === k ? "on" : ""}">${v}</button>`).join("")}
        </div>
      </div>
    </article>`).join("");
}
$("#finds").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-st]");
  if (!b) return;
  try { await updateDoc(doc(db, "finds", b.dataset.id), { status: b.dataset.st }); }
  catch (err) { toast("Не удалось сменить статус: " + err.message, true); }
});

// ---------- runs ----------
const STATUS_RUN = { running: "⏳ идёт", ok: "✔ ок", failed: "✖ ошибка" };
function subscribeRuns() {
  unsubRuns?.();
  const q = query(collection(db, "runs"), orderBy("started_at", "desc"), limit(40));
  unsubRuns = onSnapshot(q, (snap) => {
    $("#runs").innerHTML = snap.docs.map((d) => {
      const r = d.data();
      const dur = r.finished_at && r.started_at ? Math.round((r.finished_at.toMillis() - r.started_at.toMillis()) / 60000 * 10) / 10 + " мин" : "";
      const counts = r.counts ? Object.entries(r.counts).map(([k, v]) => `${SOURCE_NAMES[k] || k}: ${v}`).join("<br>") : "";
      return `<tr class="${r.status}">
        <td>${fmtDate(r.started_at)}</td><td>${r.trigger === "manual" ? "вручную" : r.trigger === "schedule" ? "расписание" : esc(r.trigger)}</td>
        <td>${STATUS_RUN[r.status] || esc(r.status)}</td><td>${counts}</td><td>${r.new ?? ""}</td><td>${r.junk ?? ""}${r.duplicates ? ` <span class="muted small">+${r.duplicates} дубл.</span>` : ""}</td>
        <td>${r.candidates ?? ""}</td><td>${r.analyzed ?? ""}</td><td><b>${r.sent ?? ""}</b></td><td>${r.backlog ?? ""}</td>
        <td>${dur}</td><td class="err small">${esc((r.errors || []).join("; "))}${r.failed_sources?.length ? " нет ответа: " + esc(r.failed_sources.join(", ")) : ""}</td></tr>`;
    }).join("") || `<tr><td colspan="12" class="muted">Запусков пока нет — данные появятся после первого запуска бота с Firestore.</td></tr>`;
    const last = snap.docs[0]?.data();
    if (last) $("#btn-run").title = "Последний запуск: " + fmtDate(last.started_at) + " — " + (STATUS_RUN[last.status] || last.status);
  }, (e) => toast("Ошибка загрузки запусков: " + e.message, true));
}

// ---------- run now (GitHub workflow_dispatch) ----------
const TOKEN_KEY = "gh_token";
function getToken() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } }
function renderGhState() { $("#gh-state").textContent = getToken() ? "токен сохранён" : "токен не задан"; }
$("#gh-save").onclick = () => {
  const v = $("#gh-token").value.trim();
  if (!v) return;
  try { localStorage.setItem(TOKEN_KEY, v); } catch {}
  $("#gh-token").value = ""; renderGhState(); toast("Токен сохранён в этом браузере.");
};
$("#gh-clear").onclick = () => { try { localStorage.removeItem(TOKEN_KEY); } catch {} renderGhState(); };

$("#btn-run").onclick = async () => {
  const token = getToken();
  if (!token) {
    document.querySelector('.tabs button[data-tab="runs"]').click();
    toast("Сначала вставь токен GitHub в разделе «Запуски».", true);
    return;
  }
  $("#btn-run").disabled = true;
  try {
    const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
      body: JSON.stringify({ ref: "main", inputs: { mode: "run" } }),
    });
    if (r.status === 204) toast("Проверка запущена. Через ~1 минуту она появится в «Запусках», находки — по мере оценки.");
    else toast(`GitHub ответил ${r.status}: ${(await r.text()).slice(0, 200)}`, true);
  } catch (e) { toast("Не удалось запустить: " + e.message, true); }
  finally { setTimeout(() => ($("#btn-run").disabled = false), 5000); }
};
