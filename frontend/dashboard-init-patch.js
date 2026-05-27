// FILE: frontend/js/dashboard-init-patch.js
// ─────────────────────────────────────────────────────────────────────────────
// DROP-IN FIX for the "Initialising..." stuck state.
//
// HOW TO USE:
//   1. Add this <script> tag to your dashboard HTML, BEFORE your existing JS:
//      <script src="/js/dashboard-init-patch.js"></script>
//
//   2. In your existing dashboard JS, replace your init call:
//      BEFORE:  fetch('https://oanda-trading-center.onrender.com/dashboard/init')
//      AFTER:   dashboardInit()
//
// That's it. No other changes needed.
// ─────────────────────────────────────────────────────────────────────────────

const BACKEND = "https://oanda-trading-center.onrender.com";

// ── Helpers ───────────────────────────────────────────────────────────────────
function setInitStatus(msg, color = "#94a3b8") {
  // Targets the "Initialising..." text — update selector to match your HTML
  const selectors = [
    "#init-status",
    ".init-status",
    "#connection-status",
    "#status-text",
  ];

  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) { el.textContent = msg; el.style.color = color; }
  }

  // Also target the top-right LIVE indicator
  const liveEl = document.querySelector(".live-indicator, #live-status, .status-live");
  if (liveEl) {
    liveEl.textContent = msg.length > 12 ? "..." : msg;
    liveEl.style.color = color;
  }

  console.log(`[INIT] ${msg}`);
}

function setDot(color) {
  const dot = document.querySelector(".status-dot, #status-dot, .live-dot");
  if (dot) dot.style.background = color;
}

// ── Fetch with timeout ────────────────────────────────────────────────────────
async function timedFetch(url, ms = 10000, opts = {}) {
  const ctrl = new AbortController();
  const tid  = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(url, { ...opts, signal: ctrl.signal });
    clearTimeout(tid);
    return res;
  } catch (e) {
    clearTimeout(tid);
    throw e;
  }
}

// ── Main dashboard initialisation with retry ──────────────────────────────────
window.dashboardInit = async function(initEndpoint = "/dashboard/init") {
  const url = BACKEND + initEndpoint;

  setInitStatus("Connecting...", "#64748b");
  setDot("#64748b");

  // Attempt 1 — quick try (backend might already be warm)
  try {
    const res = await timedFetch(url, 8000);
    if (res.ok) {
      const data = await res.json();
      setInitStatus("LIVE", "#10b981");
      setDot("#10b981");
      return data;
    }
  } catch (_) {
    // Fall through to cold-start handling
  }

  // Attempt 2+ — backend cold, show warm-up UI
  showWarmupBanner();
  setInitStatus("Starting up...", "#f59e0b");
  setDot("#f59e0b");

  const delays = [5000, 8000, 10000, 12000, 15000, 20000];

  for (let i = 0; i < delays.length; i++) {
    const remaining = delays.slice(i).reduce((a, b) => a + b, 0) / 1000;
    setInitStatus(`Starting... (~${Math.round(remaining)}s)`, "#f59e0b");
    updateWarmupProgress(i, delays.length);

    await new Promise(r => setTimeout(r, delays[i]));

    try {
      const res = await timedFetch(url, 15000);
      if (res.ok) {
        const data = await res.json();
        setInitStatus("LIVE", "#10b981");
        setDot("#10b981");
        hideWarmupBanner();
        return data;
      }
    } catch (_) {
      // Keep waiting
    }
  }

  // All retries exhausted
  setInitStatus("Offline", "#ef4444");
  setDot("#ef4444");
  showOfflineBanner();
  return null;
};

// ── Warm-up banner (shown during cold start) ──────────────────────────────────
function showWarmupBanner() {
  if (document.getElementById("warmup-banner")) return;

  const b = document.createElement("div");
  b.id = "warmup-banner";
  b.innerHTML = `
    <div style="
      position:fixed; top:0; left:0; right:0; z-index:9998;
      background:linear-gradient(135deg,#78350f,#92400e);
      border-bottom:2px solid #f59e0b;
      color:#fef3c7; padding:10px 20px;
      display:flex; align-items:center; gap:16px;
      font-family:'Inter',sans-serif; font-size:13px;
    ">
      <span id="warmup-spinner" style="
        display:inline-block; width:16px; height:16px;
        border:2px solid rgba(255,255,255,0.3);
        border-top-color:#f59e0b; border-radius:50%;
        animation:spin 0.8s linear infinite; flex-shrink:0;
      "></span>
      <div style="flex:1">
        <strong>Backend starting up</strong> — Render free tier cold start (~30–60s).
        Your data is safe. Dashboard will load automatically.
      </div>
      <div id="warmup-bar-wrap" style="width:160px; height:4px; background:rgba(255,255,255,0.15); border-radius:2px; flex-shrink:0">
        <div id="warmup-bar" style="height:4px; background:#f59e0b; border-radius:2px; width:0%; transition:width 1s ease;"></div>
      </div>
    </div>
    <style>@keyframes spin{to{transform:rotate(360deg)}}</style>
  `;
  document.body.prepend(b);
}

function updateWarmupProgress(step, total) {
  const bar = document.getElementById("warmup-bar");
  if (bar) bar.style.width = `${Math.round((step / total) * 100)}%`;
}

function hideWarmupBanner() {
  const b = document.getElementById("warmup-banner");
  if (b) {
    b.style.opacity = "0";
    b.style.transition = "opacity 0.5s";
    setTimeout(() => b.remove(), 600);
  }
}

// ── Offline banner (shown if all retries fail) ────────────────────────────────
function showOfflineBanner() {
  hideWarmupBanner();
  if (document.getElementById("offline-banner")) return;

  const b = document.createElement("div");
  b.id = "offline-banner";
  b.innerHTML = `
    <div style="
      position:fixed; top:0; left:0; right:0; z-index:9999;
      background:linear-gradient(135deg,#7f1d1d,#991b1b);
      border-bottom:2px solid #f87171;
      color:#fef2f2; padding:12px 20px;
      display:flex; justify-content:space-between; align-items:center;
      font-family:'Inter',sans-serif; font-size:13px;
    ">
      <span>🔴 <strong>Backend unavailable.</strong> Render may be restarting. Your trades are unaffected.</span>
      <button onclick="location.reload()" style="
        background:rgba(255,255,255,0.15);
        border:1px solid rgba(255,255,255,0.35);
        color:#fff; padding:7px 18px; border-radius:6px;
        cursor:pointer; font-size:12px; font-weight:700;
        transition:background 0.15s;
      " onmouseover="this.style.background='rgba(255,255,255,0.25)'"
         onmouseout="this.style.background='rgba(255,255,255,0.15)'">
        ↺ Refresh
      </button>
    </div>
  `;
  document.body.prepend(b);
}

// ── Auto-run on page load if window.AUTO_INIT is set ─────────────────────────
// Set window.AUTO_INIT = true in your HTML before this script if you want
// it to fire automatically without changing your existing JS.
document.addEventListener("DOMContentLoaded", () => {
  if (window.AUTO_INIT) {
    dashboardInit(window.INIT_ENDPOINT || "/dashboard/init");
  }
});