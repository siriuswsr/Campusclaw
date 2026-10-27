/* 材料列表 / 上传 / 重新索引 —— 学生「讲义与检索」页与教师「讲义管理」页共用。
   差异全部由页面注入的 window.CC_MATERIALS_CFG 决定：
     listApi      列表接口（服务端按 token 的 class_id 过滤）
     detailTpl    详情页模板，形如 /materials/0
     downloadTpl  下载接口模板，形如 /materials/api/0/download
     reindexTpl   重新索引接口模板（教师页才给），形如 /materials/api/0/reindex
     canUpload    true 才接管上传表单（教师页）
     canReindex   true 才在每行渲染「重新索引」（教师页）
   所有请求都走 CCAuth.authFetch，浏览器不会替我们带 Bearer。 */
(function () {
  'use strict';

  var CFG = window.CC_MATERIALS_CFG || {};

  function $(id) { return document.getElementById(id); }

  /* 模板里的 0 是占位 id，替换成真实 mid。 */
  function withMid(tpl, mid, tail) {
    if (!tpl) return '';
    return tpl.replace(/\/0(?=\/|$)/, '/' + mid) + (tail || '');
  }

  function fmtDate(v) {
    if (!v) return '';
    var d = new Date(v);
    if (isNaN(d.getTime())) return String(v);
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' '
         + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function showMsg(id, text, ok) {
    var el = $(id);
    if (!el) return;
    if (!text) { el.style.display = 'none'; el.textContent = ''; return; }
    el.className = 'msg ' + (ok ? 'ok' : 'err');
    el.textContent = text;
    el.style.display = 'block';
  }

  /* 打开材料：下载要带 Bearer 头，普通 <a href> 拿不到 token。 */
  function openMaterial(mid, title) {
    CCAuth.download(withMid(CFG.downloadTpl, mid), title || '')
      .catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        alert('打开材料失败，请稍后重试');
      });
  }

  function reindex(mid, btn) {
    btn.disabled = true;
    var label = btn.textContent;
    btn.textContent = '索引中…';
    /* 后端规约：reindex 必须是 POST（GET 会 405） */
    CCAuth.authFetch(withMid(CFG.reindexTpl, mid), { method: 'POST' })
      .then(function (r) {
        return r.json().catch(function () { return {}; })
          .then(function (d) { return { status: r.status, data: d }; });
      })
      .then(function (res) {
        btn.disabled = false;
        btn.textContent = label;
        if (res.status !== 200) { showMsg('list-msg', '重新索引失败，请稍后重试', false); return; }
        var d = res.data || {};
        showMsg('list-msg', '重新索引完成：ready ' + (d.n_ready || 0)
                          + ' / failed ' + (d.n_failed || 0), true);
      })
      .catch(function (e) {
        btn.disabled = false;
        btn.textContent = label;
        if (String(e && e.message) === 'unauthorized') return;
        showMsg('list-msg', '网络错误，重新索引失败', false);
      });
  }

  function renderList(items) {
    var tb = $('mtbody');
    if (!tb) return;
    tb.textContent = '';
    if (!items.length) {
      $('mtable').style.display = 'none';
      $('mempty').style.display = 'block';
      return;
    }
    $('mempty').style.display = 'none';
    $('mtable').style.display = '';

    items.forEach(function (m) {
      var tr = document.createElement('tr');
      [m.title, m.subject || '未分类', m.uploader_name || '', fmtDate(m.created_at)].forEach(function (v) {
        var td = document.createElement('td');
        td.textContent = v;
        tr.appendChild(td);
      });

      var td = document.createElement('td');
      var a = document.createElement('a');
      a.href = '#';
      a.textContent = '查看 / 下载';
      a.addEventListener('click', function (ev) {
        ev.preventDefault();
        openMaterial(m.id, m.title);
      });
      td.appendChild(a);

      if (CFG.canReindex && CFG.reindexTpl) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'link-btn';
        btn.textContent = '重新索引';
        btn.addEventListener('click', function () { reindex(m.id, btn); });
        td.appendChild(btn);
      }

      tr.appendChild(td);
      tb.appendChild(tr);
    });
  }

  function load(subject) {
    showMsg('list-msg', '');
    var url = CFG.listApi;
    if (subject) url += (url.indexOf('?') >= 0 ? '&' : '?') + 'subject=' + encodeURIComponent(subject);
    return CCAuth.authFetch(url)
      .then(function (r) {
        if (r.status !== 200) throw new Error('list failed: ' + r.status);
        return r.json();
      })
      .then(function (items) { renderList(items || []); })
      .catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        showMsg('list-msg', '材料列表加载失败，请稍后重试', false);
      });
  }

  /* 学科过滤条：仅学生页（#subject-chips 存在）渲染。
     chip 列表 = 「全部」+ 服务端注入的学科清单（去掉「未分类」，它等价于不过滤）。 */
  function wireSubjectChips() {
    var box = $('subject-chips');
    if (!box) return;
    var subjects = (CFG.filterSubjects || []).filter(function (s) { return s !== '未分类'; });
    var labels = ['全部'].concat(subjects);
    var active = null;

    labels.forEach(function (label) {
      var c = document.createElement('span');
      c.className = 'chip' + (label === '全部' ? ' active' : '');
      c.textContent = label;
      if (label === '全部') active = c;
      c.addEventListener('click', function () {
        if (active) active.classList.remove('active');
        c.classList.add('active');
        active = c;
        load(label === '全部' ? null : label);
      });
      box.appendChild(c);
    });
  }

  /* 上传：FormData 直接交给 fetch，不手写 Content-Type（boundary 由浏览器生成） */
  function wireUpload() {
    var form = $('upload-form');
    if (!form) return;
    var btn = $('upload-btn');

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var file = $('upload-file');
      var subjectSel = $('upload-subject');
      if (!file || !file.files.length) {
        showMsg('upload-msg', '请选择要上传的文件（.txt / .md）', false);
        return;
      }
      if (subjectSel && !subjectSel.value) {
        showMsg('upload-msg', '请选择学科', false);
        return;
      }
      var fd = new FormData(form);
      btn.disabled = true;
      var label = btn.textContent;
      btn.textContent = '上传中…';
      showMsg('upload-msg', '');

      CCAuth.authFetch(form.getAttribute('action'), { method: 'POST', body: fd })
        .then(function (r) {
          return r.json().catch(function () { return {}; })
            .then(function (d) { return { status: r.status, data: d }; });
        })
        .then(function (res) {
          btn.disabled = false;
          btn.textContent = label;
          if (res.status !== 201) {
            showMsg('upload-msg', '上传失败：' + ((res.data && res.data.error) || ('HTTP ' + res.status)), false);
            return;
          }
          var title = ($('upload-title') || {}).value || '';
          form.reset();
          showMsg('upload-msg', '上传成功：' + (title || ('材料 #' + (res.data && res.data.material_id))), true);
          load();
        })
        .catch(function (e) {
          btn.disabled = false;
          btn.textContent = label;
          if (String(e && e.message) === 'unauthorized') return;
          showMsg('upload-msg', '网络错误，上传失败', false);
        });
    });
  }

  if (!window.CCAuth || !CCAuth.guard()) return;
  if (CFG.canUpload) wireUpload();
  wireSubjectChips();
  load();

  window.CCMaterials = { reload: load };
})();