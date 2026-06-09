// ==UserScript==
// @name         PoE2 Trade Presets (cx)
// @namespace    cx.poe2.trade
// @version      0.2.0
// @description  Open preset trade2 searches from the in-page panel OR via a cx hand-off (#cxq=...). The POST runs inside your logged-in browser, so Cloudflare clearance and session are always valid - cx never POSTs.
// @author       cx
// @match        https://www.pathofexile.com/trade2/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  // Manual presets - buttons in the panel. `query` is the exact JSON the trade
  // site POSTs to /api/trade2/search/poe2/<league>. cx-driven presets arrive at
  // runtime via the #cxq= hand-off instead (see consumeHandoff).
  var PRESETS = [
    {
      name: 'Helmet: +EleRes & +ColdRes, req lvl <=20, <=15c',
      query: {
        query: {
          status: { option: 'available' },
          stats: [
            { type: 'and', filters: [
              { id: 'pseudo.pseudo_total_elemental_resistance' },
              { id: 'pseudo.pseudo_total_cold_resistance' }
            ] }
          ],
          filters: {
            req_filters:   { filters: { lvl: { max: 20 } } },
            type_filters:  { filters: { category: { option: 'armour.helmet' } } },
            trade_filters: { filters: { price: { option: 'chaos', max: 15 } } }
          }
        },
        sort: { price: 'asc' }
      }
    }
  ];

  var DEFAULT_LEAGUE = 'HC%20Runes%20of%20Aldur';

  function currentLeague() {
    var m = location.pathname.match(/\/trade2\/(?:search|exchange)\/poe2\/([^/]+)/);
    return m ? m[1] : null;
  }

  // POST a query, return the stored-search URL (throws on failure).
  async function searchUrl(query, league) {
    var res = await fetch(location.origin + '/api/trade2/search/poe2/' + league, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-requested-with': 'XMLHttpRequest' },
      credentials: 'include',
      body: JSON.stringify(query)
    });
    if (!res.ok) {
      var txt = await res.text().catch(function () { return ''; });
      throw new Error('HTTP ' + res.status + ' ' + res.statusText + ' ' + txt.slice(0, 300));
    }
    var data = await res.json();
    if (!data || !data.id) throw new Error('no search id: ' + JSON.stringify(data).slice(0, 200));
    return location.origin + '/trade2/search/poe2/' + league + '/' + data.id;
  }

  // Manual panel button.
  async function runPreset(preset, btn) {
    var league = currentLeague() || DEFAULT_LEAGUE;
    var tab = window.open('', '_blank'); // open synchronously to keep the click-gesture
    var old = btn.textContent; btn.textContent = '...'; btn.disabled = true;
    try {
      var url = await searchUrl(preset.query, league);
      if (tab) tab.location = url; else window.open(url, '_blank');
    } catch (e) {
      if (tab) tab.close();
      alert('PoE2 trade preset failed: ' + (e.message || e));
    } finally { btn.textContent = old; btn.disabled = false; }
  }

  // cx hand-off:  #cxq=<base64url(JSON query)>
  // cx opens .../trade2/search/poe2/<league>#cxq=<payload> via webbrowser.open;
  // decode it here, POST in-browser, navigate THIS tab to the results.
  function b64urlDecode(s) {
    s = s.replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bytes = Uint8Array.from(atob(s), function (c) { return c.charCodeAt(0); });
    return new TextDecoder().decode(bytes);
  }

  async function consumeHandoff() {
    var m = (location.hash || '').match(/[#&]cxq=([^&]+)/);
    if (!m) return;
    var league = currentLeague() || DEFAULT_LEAGUE;
    history.replaceState(null, '', location.pathname + location.search);
    var query;
    try { query = JSON.parse(b64urlDecode(m[1])); }
    catch (e) { alert('cx hand-off: bad payload - ' + (e.message || e)); return; }
    try {
      var url = await searchUrl(query, league);
      location.assign(url);
    } catch (e) {
      alert('cx hand-off search failed: ' + (e.message || e));
    }
  }

  function buildPanel() {
    if (document.getElementById('cx-poe2-presets')) return;
    var panel = document.createElement('div');
    panel.id = 'cx-poe2-presets';
    panel.innerHTML =
      '<div class="cx-hd">PoE2 presets <span class="cx-min" title="Collapse">_</span></div>' +
      '<div class="cx-body"></div>';
    var body = panel.querySelector('.cx-body');
    PRESETS.forEach(function (p) {
      var b = document.createElement('button');
      b.className = 'cx-btn';
      b.textContent = p.name;
      b.title = 'Open in a new tab';
      b.addEventListener('click', function () { runPreset(p, b); });
      body.appendChild(b);
    });
    panel.querySelector('.cx-min').addEventListener('click', function () {
      body.style.display = (body.style.display === 'none') ? '' : 'none';
    });
    document.body.appendChild(panel);
    var css = document.createElement('style');
    css.textContent =
      '#cx-poe2-presets{position:fixed;right:12px;bottom:12px;z-index:99999;' +
      'background:#15171c;color:#e6e6e6;border:1px solid #3a3f4b;border-radius:8px;' +
      'font:12px/1.3 system-ui,Segoe UI,Arial;box-shadow:0 4px 16px rgba(0,0,0,.5);' +
      'max-width:320px;overflow:hidden}' +
      '#cx-poe2-presets .cx-hd{display:flex;justify-content:space-between;align-items:center;' +
      'gap:8px;padding:7px 10px;background:#1d2027;font-weight:600}' +
      '#cx-poe2-presets .cx-min{cursor:pointer;padding:0 6px;opacity:.7}' +
      '#cx-poe2-presets .cx-min:hover{opacity:1}' +
      '#cx-poe2-presets .cx-body{display:flex;flex-direction:column;gap:6px;padding:8px}' +
      '#cx-poe2-presets .cx-btn{all:unset;cursor:pointer;padding:7px 10px;border-radius:6px;' +
      'background:#2a2f3a;border:1px solid #3a4150;color:#e6e6e6;text-align:left}' +
      '#cx-poe2-presets .cx-btn:hover{background:#343b48}' +
      '#cx-poe2-presets .cx-btn:disabled{opacity:.5;cursor:default}';
    document.head.appendChild(css);
  }

  consumeHandoff();
  buildPanel();
})();
