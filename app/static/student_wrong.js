/* 学生端「错题」。
   数据只来自 GET /api/wrong（服务端按 token 只看本人）。对 status=pending 的错题，
   可点「请助手讲解」→ POST /api/wrong/<id>/explain，技能关闭时后端返 503。 */
(function () {
  'use strict';
  if (!CCAuth.guard()) return;

  var CFG = window.CC_STUDENT_WRONG || {};
  var SUBJECTS = CFG.subjects || [];

  function $(id) { return document.getElementById(id); }

  function subjectLabel(code) {
    for (var i = 0; i < SUBJECTS.length; i++)
      if (SUBJECTS[i].code === code) return SUBJECTS[i].label;
    return code;
  }

  function render(items) {
    var list = $('wrong-list');
    list.textContent = '';
    $('wrong-empty').style.display = items.length ? 'none' : 'block';

    items.forEach(function (w) {
      var row = document.createElement('div');
      row.className = 'wrong';

      var h = document.createElement('div');
      h.className = 'h';
      var subj = document.createElement('span');
      subj.className = 'subj';
      subj.textContent = subjectLabel(w.subject);
      h.appendChild(subj);
      var st = document.createElement('span');
      st.className = 'st';
      st.textContent = w.status === 'explained' ? '已讲解' : '待讲解';
      h.appendChild(st);

      var q = document.createElement('div');
      q.className = 'q';
      q.textContent = w.question_text || '';
      var a = document.createElement('div');
      a.className = 'a';
      a.appendChild(document.createTextNode('我的作答：'));
      var b = document.createElement('b');
      b.textContent = w.student_answer || '';
      a.appendChild(b);

      row.appendChild(h); row.appendChild(q); row.appendChild(a);

      if (w.wrong_reason) {
        var reason = document.createElement('div');
        reason.className = 'reason';
        reason.textContent = '错因：' + w.wrong_reason;
        row.appendChild(reason);
      }

      // 解析区：已讲解直接展示；待讲解给一个按钮，点后展开
      var wrap = document.createElement('div');
      row.appendChild(wrap);

      if (w.explanation) {
        var ex = document.createElement('div');
        ex.className = 'explain';
        ex.textContent = w.explanation;
        wrap.appendChild(ex);
      } else {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn';
        btn.textContent = '请助手讲解';
        btn.addEventListener('click', function () {
          explain(w.id, btn, wrap);
        });
        wrap.appendChild(btn);
      }

      list.appendChild(row);
    });
  }

  function explain(wid, btn, wrap) {
    btn.disabled = true;
    var loading = document.createElement('div');
    loading.className = 'explain';
    loading.innerHTML = '<span class="loading">思考中…</span>';
    wrap.appendChild(loading);

    var endpoint = String(CFG.explainTpl).replace(/\/0$/, '/' + wid);
    CCAuth.authFetch(endpoint, { method: 'POST' })
      .then(function (r) {
        return r.json().then(function (d) { return { r: r, d: d }; });
      })
      .then(function (o) {
        wrap.removeChild(loading);
        if (o.r.status === 200) {
          var ex = document.createElement('div');
          ex.className = 'explain';
          ex.textContent = o.d.explanation || '';
          wrap.replaceChild(ex, btn);
        } else if (o.r.status === 503) {
          alert('错题讲解技能未启用');
          btn.disabled = false;
        } else {
          alert((o.d && (o.d.message || o.d.error)) || '讲解失败，请稍后再试');
          btn.disabled = false;
        }
      })
      .catch(function (e) {
        wrap.removeChild(loading);
        if (String(e && e.message) === 'unauthorized') return;
        alert('讲解失败，请稍后再试');
        btn.disabled = false;
      });
  }

  CCAuth.authFetch(CFG.listApi)
    .then(function (r) { if (r.status !== 200) throw new Error('bad ' + r.status); return r.json(); })
    .then(function (d) { render(d.wrong_questions || []); })
    .catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
      render([]);
    });
})();