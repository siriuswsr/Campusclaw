/* 教师端「本班首页」。
   两个数据源各自独立降级：材料数来自 GET /materials/api（服务端按 token 的班级过滤），
   技能数来自 GET /api/assistant/skills（仅教师可访问）。任何一路失败只影响自己那张卡。 */
(function () {
  'use strict';

  if (!CCAuth.guard()) return;

  var CFG = window.CC_TEACHER_HOME || {};
  var user = CCAuth.user() || {};

  function $(id) { return document.getElementById(id); }

  // 非教师：不发任何请求（接口层同样会对非教师返回 403）
  if (user.role !== 'teacher') {
    $('denied').style.display = 'block';
    $('home-body').style.display = 'none';
    return;
  }

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

  $('greet').textContent = (user.name || user.username || '老师')
    + ' · 教师工作台，你只能管理本班数据。';

  function renderSubjects() {
    var box = $('subjects');
    (CFG.subjects || []).forEach(function (s) {
      var a = document.createElement('a');
      a.className = 'chip';
      a.href = CFG.config + '?subject=' + encodeURIComponent(s.code);
      a.textContent = s.label;
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

  function toJson(r) {
    return r.json().catch(function () { return {}; })
      .then(function (d) { return { status: r.status, data: d }; });
  }

  renderSubjects();

  CCAuth.authFetch(CFG.listApi).then(toJson).then(function (res) {
    if (res.status !== 200) throw new Error('list failed: ' + res.status);
    var items = res.data || [];
    $('stat-materials').textContent = String(items.length);
    renderRecent(items);
  }).catch(function (e) {
    if (String(e && e.message) === 'unauthorized') return;
    renderRecent([]);            // 计数卡留「—」，列表走空态
  });

  CCAuth.authFetch(CFG.skillsApi).then(toJson).then(function (res) {
    if (res.status !== 200) return;              // 保持「—」
    var rows = res.data.skills || [];
    var on = rows.filter(function (s) { return !!s.enabled; }).length;
    $('stat-skills').textContent = String(on);
  }).catch(function (e) {
    if (String(e && e.message) === 'unauthorized') return;
  });
})();