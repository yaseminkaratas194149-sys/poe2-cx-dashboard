// ==UserScript==
// @name         PoE2 Trade Presets (cx)
// @namespace    cx.poe2.trade
// @version      0.3.0
// @description  Open preset trade2 searches (in-page panel or cx #cxq hand-off) AND capture the filters you set in the trade2 UI back into cx. Capture copies a #cxq link to the clipboard - paste it into cx's From-link box, hit Read, Save preset. Every POST runs inside your logged-in browser, so Cloudflare clearance + session are always valid - cx never POSTs.
// @author       cx
// @match        https://www.pathofexile.com/trade2/*
// @run-at       document-start
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

  // ---- capture state: the last search body the trade2 site itself sent -------
  // The site POSTs the full {query, sort} JSON to /api/trade2/search/poe2/<league>
  // whenever you hit Search. We hook fetch + XHR at document-start (before any
  // page script runs) and stash that body verbatim - it IS the filter set you
  // built by hand, no DOM scraping. The Capture button later encodes it as a
  // #cxq hand-off link and copies it to the clipboard (paste into cx).
  var lastQuery = null;
  var SEARCH_API = /\/api\/trade2\/search\/poe2\//;

  function stashBody(url, method, body) {
    if (!SEARCH_API.test(url) || String(method).toUpperCase() !== 'POST') return;
    if (typeof body !== 'string') return;  // create-search bodies are JSON strings
    try { lastQuery = JSON.parse(body); } catch (e) {}
  }

  function installHooks() {
    var _fetch = window.fetch;
    if (typeof _fetch === 'function') {
      window.fetch = function (input, init) {
        try {
          var url = (typeof input === 'string') ? input : (input && input.url) || '';
          var method = (init && init.method) || (input && input.method) || 'GET';
          stashBody(url, method, init && init.body);
        } catch (e) {}
        return _fetch.apply(this, arguments);
      };
    }
    var _open = XMLHttpRequest.prototype.open;
    var _send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      this.__cxMethod = method; this.__cxUrl = url;
      return _open.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function (body) {
      try { stashBody(this.__cxUrl || '', this.__cxMethod || 'GET', body); } catch (e) {}
      return _send.apply(this, arguments);
    };
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

  // reverse of b64urlDecode: JSON string -> base64url (the cx #cxq payload form).
  function b64urlEncode(str) {
    var bytes = new TextEncoder().encode(str);
    var bin = '';
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
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

  // ---- capture: filters in the trade2 UI -> a cx #cxq link on the clipboard --
  // Fallback when the live POST wasn't seen (e.g. captured after a page reload):
  // a stored-search URL carries an <id>; GET it back to recover the exact query.
  async function queryFromUrlId(league) {
    var m = location.pathname.match(/\/trade2\/search\/poe2\/[^/]+\/([^/]+)\/?$/);
    if (!m) return null;
    try {
      var res = await fetch(location.origin + '/api/trade2/search/poe2/' + league + '/' + m[1], {
        credentials: 'include', headers: { 'x-requested-with': 'XMLHttpRequest' }
      });
      if (!res.ok) return null;
      var data = await res.json();
      if (data && data.query) return { query: data.query, sort: data.sort || { price: 'asc' } };
    } catch (e) {}
    return null;
  }

  // body = {query, sort} -> self-contained cx hand-off link (same form cx mints).
  function captureLink(body, league) {
    return location.origin + '/trade2/search/poe2/' + league +
           '#cxq=' + b64urlEncode(JSON.stringify(body));
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (e) {}
    try {  // fallback for when the async clipboard API is blocked
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.top = '-1000px'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
  }

  async function runCapture(btn) {
    var league = currentLeague() || DEFAULT_LEAGUE;
    var old = btn.textContent; btn.disabled = true;
    try {
      var body = lastQuery || await queryFromUrlId(league);  // live POST, else by id
      if (!body || !body.query) {
        btn.textContent = 'run a search first';
        return;
      }
      var link = captureLink(body, league);
      var ok = await copyText(link);
      btn.textContent = ok ? 'copied - paste in cx' : 'copy failed (see console)';
      if (!ok) console.log('[cx capture] paste this into cx From-link:\n' + link);
    } catch (e) {
      btn.textContent = 'capture failed';
      console.log('[cx capture] error:', e);
    } finally {
      btn.disabled = false;
      setTimeout(function () { btn.textContent = old; }, 2200);
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
    // capture: pull whatever filters you've set in the trade2 UI back into cx.
    var sep = document.createElement('div');
    sep.className = 'cx-sep';
    body.appendChild(sep);
    var cap = document.createElement('button');
    cap.className = 'cx-btn cx-cap';
    cap.textContent = '↓ Capture current filters';
    cap.title = "Copy a #cxq link of the search you've set up - paste it into cx's " +
                'From-link box, hit Read, then Save preset';
    cap.addEventListener('click', function () { runCapture(cap); });
    body.appendChild(cap);
    var hint = document.createElement('div');
    hint.className = 'cx-hint';
    hint.textContent = 'hit Search once, then Capture → paste in cx';
    body.appendChild(hint);
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
      '#cx-poe2-presets .cx-btn:disabled{opacity:.5;cursor:default}' +
      '#cx-poe2-presets .cx-sep{height:1px;background:#3a4150;margin:2px 0}' +
      '#cx-poe2-presets .cx-cap{background:#243a2a;border-color:#356046}' +
      '#cx-poe2-presets .cx-cap:hover{background:#2c4633}' +
      '#cx-poe2-presets .cx-hint{font-size:11px;opacity:.6;padding:0 2px}';
    document.head.appendChild(css);
  }

  function onReady(fn) {
    if (document.body) { fn(); return; }
    document.addEventListener('DOMContentLoaded', fn, { once: true });
  }

  installHooks();              // at document-start: before any page script runs
  onReady(function () {
    consumeHandoff();
    buildPanel();
  });
})();
