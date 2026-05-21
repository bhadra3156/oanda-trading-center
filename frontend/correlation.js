/* ═══════════════════════════════════════════════════════════════
   OANDA Trading Center — Correlation Warning System
   File: frontend/correlation.js
   Linked by: index.html AND calculator.html (one <script src> each)

   REQUIRES: API_BASE must be defined before this script loads.
   e.g.  const API_BASE = 'https://your-render-app.onrender.com';
   ═══════════════════════════════════════════════════════════════ */

const correlationChecker = {

  /* ── Cached open positions (populated on page load) ─────────── */
  openPositions: [],

  /* ── Load open positions from your existing /api/trades ──────── */
  async loadOpenPositions() {
    try {
      const res  = await fetch(`${API_BASE}/api/trades`);
      const data = await res.json();

      /* Handle both response shapes your backend might return */
      const raw = data.trades || data.positions || [];
      this.openPositions = raw.map(p => ({
        instrument: p.instrument || '',
        direction:  p.direction  ||
                    (parseFloat(p.units || p.currentUnits || 0) > 0 ? 'BUY' : 'SELL'),
      }));
    } catch (e) {
      console.warn('[Correlation] Could not load open positions:', e);
      this.openPositions = [];
    }
  },

  /* ── Main check — call BEFORE showing the trade confirm modal ── */
  async check(instrument, direction) {
    try {
      const res = await fetch(`${API_BASE}/api/check-correlation`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          new_instrument: instrument,
          new_direction:  direction,
          open_positions: this.openPositions,
        }),
      });
      return await res.json();
    } catch (e) {
      console.warn('[Correlation] Check failed:', e);
      return { safe: true, warnings: [], block_trade: false, summary: '' };
    }
  },

  /* ── Render warning banners inside the trade confirm modal ───── */
  /* containerEl = the <div id="corr-warnings"> inside your modal  */
  renderInModal(result, containerEl) {
    if (!containerEl) return;
    containerEl.innerHTML = '';
    if (!result || result.safe || !result.warnings?.length) return;

    const { warnings, block_trade } = result;

    const danger  = warnings.filter(w => ['VERY_STRONG', 'STRONG'].includes(w.severity));
    const caution = warnings.filter(w => w.severity === 'MODERATE');
    const info    = warnings.filter(w => w.severity === 'INFO');

    const makeBanner = (cls, icon, title, items) => {
      if (!items.length) return '';
      const rows = items.map(w =>
        `<div class="corr-item">
           <strong>${w.open_instrument} ${w.open_direction}</strong> — ${w.message}
         </div>`
      ).join('');
      return `
        <div class="corr-banner ${cls} visible">
          <div class="corr-title">${icon} ${title}</div>
          ${rows}
        </div>`;
    };

    let html = '';
    if (block_trade) {
      html += makeBanner('danger', '⛔', 'Trade blocked — very strong correlation conflict', danger);
    } else if (danger.length) {
      html += makeBanner('danger', '⚠️', 'Strong correlation conflict with open position', danger);
    }
    if (caution.length) {
      html += makeBanner('warning', '⚡', 'Moderate correlation caution', caution);
    }
    if (info.length) {
      html += makeBanner('info', 'ℹ️', 'These positions partially hedge each other', info);
    }

    containerEl.innerHTML = html;
  },

  /* ── Wire up the confirm button (disable if block_trade) ─────── */
  /* confirmBtn = the button DOM element in your modal             */
  applyBlockState(result, confirmBtn) {
    if (!confirmBtn) return;
    if (result && result.block_trade) {
      confirmBtn.disabled = true;
      confirmBtn.classList.add('btn-blocked');
      confirmBtn.dataset._origText = confirmBtn.textContent;
      confirmBtn.textContent = '⛔ Blocked — high correlation risk';
    } else {
      confirmBtn.disabled = false;
      confirmBtn.classList.remove('btn-blocked');
      if (confirmBtn.dataset._origText) {
        confirmBtn.textContent = confirmBtn.dataset._origText;
      }
    }
  },

  /* ── Add a small CORR badge to a signal card element ─────────── */
  addBadgeToCard(cardEl, result) {
    if (!cardEl || !result || result.safe || !result.warnings?.length) return;

    /* Remove old badge first */
    cardEl.querySelector('.corr-badge')?.remove();

    const top = result.warnings.find(w => ['VERY_STRONG','STRONG'].includes(w.severity))
             || result.warnings.find(w => w.severity === 'MODERATE')
             || result.warnings[0];

    const clsMap = { VERY_STRONG: 'danger', STRONG: 'danger', MODERATE: 'warning', INFO: 'info' };
    const cls    = clsMap[top.severity] || 'info';

    const badge = document.createElement('span');
    badge.className   = `corr-badge ${cls}`;
    badge.textContent = 'CORR';
    badge.title       = top.message;
    badge.onclick     = e => {
      e.stopPropagation();
      alert('⚠️ Correlation Warnings\n\n' +
        result.warnings.map(w => `• ${w.message}`).join('\n\n'));
    };

    /* Try to insert next to the BUY/SELL direction tag */
    const anchor = cardEl.querySelector(
      '.signal-tag, .badge, [class*="buy"], [class*="sell"], .direction-tag'
    );
    if (anchor) {
      anchor.parentNode.insertBefore(badge, anchor.nextSibling);
    } else {
      const header = cardEl.querySelector('h3, h4, .card-header, .signal-header');
      if (header) header.appendChild(badge);
      else cardEl.prepend(badge);
    }
  },
};

/* ═══════════════════════════════════════════════════════════════
   CORRELATION MAP PANEL
   Call showCorrelationMap('CORN_USD') to populate the panel.
   The panel HTML must exist in your page — see index.html below.
   ═══════════════════════════════════════════════════════════════ */
async function showCorrelationMap(instrument) {
  const panel    = document.getElementById('corr-map-panel');
  const itemsEl  = document.getElementById('corr-map-items');
  const labelEl  = document.getElementById('corr-map-instrument');
  if (!panel) return;

  if (labelEl) labelEl.textContent = instrument.replace('_', '/');
  panel.style.display = 'block';
  itemsEl.innerHTML = '<span style="color:#666;font-size:12px;">Loading…</span>';

  try {
    const res  = await fetch(`${API_BASE}/api/correlation-map/${instrument}`);
    const data = await res.json();

    if (!data.correlations?.length) {
      itemsEl.innerHTML =
        '<span style="color:#666;font-size:12px;">No correlations tracked for this instrument.</span>';
      return;
    }

    const strengthIcon = { VERY_STRONG: '🔴', STRONG: '🟠', MODERATE: '🟡' };
    const strengthCls  = { VERY_STRONG: 'very-strong', STRONG: 'strong', MODERATE: 'moderate' };

    itemsEl.innerHTML = data.correlations.map(g => `
      <div class="corr-map-group ${strengthCls[g.strength] || ''}">
        <div class="corr-map-strength">
          ${strengthIcon[g.strength] || '⚪'} ${g.group} — ${g.strength.replace('_', ' ')}
        </div>
        <div class="corr-map-chips">
          ${g.correlated_with.map(i =>
            `<span class="corr-map-chip">${i.replace(/_USD|_GBP|_EUR/g, '')}</span>`
          ).join('')}
        </div>
        <div class="corr-map-reason">${g.reason}</div>
      </div>
    `).join('');
  } catch (e) {
    itemsEl.innerHTML =
      '<span style="color:#e24b4a;font-size:12px;">Failed to load correlation map.</span>';
  }
}