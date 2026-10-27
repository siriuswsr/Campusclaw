/* Shared JWT client plumbing: token storage, guarded fetch, page guard, download.
   The token lives in sessionStorage — it survives in-tab navigation but is dropped
   when the tab/browser closes, so every fresh visit starts at the login page.
   Every API call carries it as a Bearer header.
   Any 401 clears the stored identity and bounces to the login page. */
(function () {
  var TOKEN_KEY = 'token';
  var USER_KEY = 'user';
  var LOGIN_URL = '/login';

  /* sessionStorage, NOT localStorage: 「每次打开都必须重新登录」.
     localStorage 会跨浏览器重启保留，sessionStorage 的生命周期只到标签页关闭。 */
  function token() { return sessionStorage.getItem(TOKEN_KEY); }

  function user() {
    try { return JSON.parse(sessionStorage.getItem(USER_KEY) || 'null'); }
    catch (e) { return null; }
  }

  function save(data) {
    sessionStorage.setItem(TOKEN_KEY, data.token);
    sessionStorage.setItem(USER_KEY, JSON.stringify(data.user || {}));
  }

  function clear() {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
  }

  function toLogin() {
    clear();
    location.replace(LOGIN_URL);
  }

  /* Fetch with Authorization: Bearer; 401 ⇒ drop identity and go to /login. */
  function authFetch(url, opts) {
    opts = opts || {};
    var headers = Object.assign({}, opts.headers || {});
    var t = token();
    if (t) headers['Authorization'] = 'Bearer ' + t;
    opts.headers = headers;
    return fetch(url, opts).then(function (r) {
      if (r.status === 401) { toLogin(); throw new Error('unauthorized'); }
      return r;
    });
  }

  /* Page guard: protected pages must not run without a token. */
  function guard() {
    if (!token()) { location.replace(LOGIN_URL); return false; }
    return true;
  }

  /* Bearer-authenticated download — a plain <a href> would carry no token. */
  function download(url, filename) {
    return authFetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error('download failed: ' + r.status);
        return r.blob();
      })
      .then(function (blob) {
        var href = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = href;
        if (filename) a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(href); }, 4000);
      });
  }

  function logout() { toLogin(); }

  window.CCAuth = {
    token: token,
    user: user,
    save: save,
    clear: clear,
    toLogin: toLogin,
    authFetch: authFetch,
    guard: guard,
    download: download,
    logout: logout
  };
})();