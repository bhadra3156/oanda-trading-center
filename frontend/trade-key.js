// trade-key.js — adds your private trade key to every order request.
// Order, close and stop-change requests to the backend carry an X-Trade-Key header.
// The backend only checks it once you set TRADE_KEY on Render (see README-BRIDGE.md).
// The key is kept in this browser only (localStorage), never in the code.
(function () {
  var GUARDED = /\/api\/(place-order|close-trade|modify-sl)\b/;
  var STORE = 'otc-trade-key';
  var origFetch = window.fetch.bind(window);

  function getKey() { try { return localStorage.getItem(STORE) || ''; } catch (e) { return ''; } }
  function setKey(k) { try { if (k) localStorage.setItem(STORE, k); else localStorage.removeItem(STORE); } catch (e) {} }
  window.OTCTradeKey = { get: getKey, set: setKey, clear: function () { setKey(''); } };

  function withKey(init, key) {
    init = Object.assign({}, init || {});
    var h = new Headers(init.headers || {});
    if (key) h.set('X-Trade-Key', key);
    init.headers = h;
    return init;
  }

  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (!GUARDED.test(url) || method !== 'POST') return origFetch(input, init);
    return origFetch(input, withKey(init, getKey())).then(function (res) {
      if (res.status !== 401) return res;
      var k = window.prompt('Enter your trade key (the TRADE_KEY you set on Render). It is saved in this browser only.');
      if (!k) return res;
      setKey(k.trim());
      return origFetch(input, withKey(init, k.trim()));
    });
  };
})();
