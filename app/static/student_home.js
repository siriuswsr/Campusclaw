/* 学生端「本班首页」。
   数据只来自 GET /materials/api —— 服务端已按 token 里的 class_id 过滤并按时间倒序，
   前端不携带任何班级参数。取不到数据时各块降级显示，不整页报错。 */
(function () {
  'use strict';

  if (!CCAuth.guard()) return;

  var CFG = window.CC_STUDENT_HOME || {};
  var user = CCAuth.user() || {};

  function $(id) { return document.getElementById(id); }

  function fmtDate(v) {
    if (!v) return '';
    var d = new Date(v);
    if (isNaN(d.getTime())) return String(v);
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }

  function detailUrl(mid) {
    return String(CFG.detailTpl || '').replace(/\/0$/, '/' + mid);
  }

  $('greet').textContent = '你好，' + (user.name || user.username || '同学')
    + ' 👋 · 这里是本班的学习空间，所有内容仅限本班可见。';

  function renderQuick() {
    var box = $('quick');
    [['去讲义与检索', CFG.materials], ['去助手对话', CFG.chat]].forEach(function (pair) {
      var a = document.createElement('a');
      a.className = 'btn';
      a.href = pair[1] || '#';
      a.textContent = pair[0];
      box.appendChild(a);
    });
  }

  function renderRecent(items) {
    var box = $('recent');
    box.textContent = '';
    if (!items.length) {
      $('recent-empty').style.display = 'block';
      return;
    }
    $('recent-empty').style.display = 'none';
    items.slice(0, 5).forEach(function (m) {
      var row = document.createElement('div');
      row.className = 'recent';

      var a = document.createElement('a');
      a.className = 'nm';
      a.href = detailUrl(m.id);
      a.textContent = '📄 ' + (m.title || '');

      var dt = document.createElement('span');
      dt.className = 'dt';
      dt.textContent = fmtDate(m.created_at);

      row.appendChild(a);
      row.appendChild(dt);
      box.appendChild(row);
    });
  }

  renderQuick();

  CCAuth.authFetch(CFG.listApi)
    .then(function (r) {
      if (r.status !== 200) throw new Error('list failed: ' + r.status);
      return r.json();
    })
    .then(function (items) {
      items = items || [];
      $('stat-materials').textContent = String(items.length);
      $('stat-updated').textContent = items.length ? fmtDate(items[0].created_at) : '—';
      renderRecent(items);
    })
    .catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
      renderRecent([]);            // 各计数卡保持「—」，列表走空态
    });
})();