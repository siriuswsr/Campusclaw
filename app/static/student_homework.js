/* 学生端「交作业」。
   只读本人数据：待交作业来自 GET /api/assignments（服务端已按 token 班级过滤，
   前端再按学科过滤），我的提交来自 GET /api/submissions（只看本人）。
   提交后轮询 GET /api/submissions/<sid>，状态 pending→processing→done/failed。 */
(function () {
  'use strict';
  if (!CCAuth.guard()) return;

  var CFG = window.CC_STUDENT_HOMEWORK || {};
  var SUBJECTS = CFG.subjects || [];
  var current = SUBJECTS.length ? SUBJECTS[0].code : 'math';

  var state = { assignments: [], submissions: [], selectedId: null };

  function $(id) { return document.getElementById(id); }

  function subjectLabel(code) {
    for (var i = 0; i < SUBJECTS.length; i++)
      if (SUBJECTS[i].code === code) return SUBJECTS[i].label;
    return code;
  }

  function fmtDeadline(v) {
    if (!v) return '';
    return String(v).replace('T', ' ').slice(0, 16);
  }

  function pill(status) {
    var el = document.createElement('span');
    el.className = 'pill ';
    if (status === 'done') { el.classList.add('st-done'); el.textContent = '已批改'; }
    else if (status === 'failed') { el.classList.add('st-failed'); el.textContent = '批改失败'; }
    else { el.classList.add('st-progress'); el.textContent = '批改中'; }
    return el;
  }

  function scoreOf(g) {
    if (!g) return null;
    if (g.final_score !== null && g.final_score !== undefined) return g.final_score;
    return g.ai_score;
  }

  function commentOf(g) {
    if (!g) return '';
    return (g.final_comment || g.ai_comment || '').trim();
  }

  function submittedMap() {
    var m = {};
    state.submissions.forEach(function (s) { m[s.assignment_id] = s; });
    return m;
  }

  function renderTabs() {
    var box = $('subject-tabs');
    box.textContent = '';
    SUBJECTS.forEach(function (s) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'tab' + (s.code === current ? ' active' : '');
      b.textContent = s.label;
      b.addEventListener('click', function () {
        current = s.code;
        renderTabs();
        renderAll();
      });
      box.appendChild(b);
    });
  }

  function renderAssignments() {
    var map = submittedMap();
    var pending = state.assignments.filter(function (a) {
      return a.subject === current && !map[a.id];
    });
    var list = $('assignment-list');
    list.textContent = '';
    $('assignment-empty').style.display = pending.length ? 'none' : 'block';

    pending.forEach(function (a) {
      var row = document.createElement('div');
      row.className = 'asg';

      var h = document.createElement('div');
      h.className = 'h';
      var t = document.createElement('span');
      t.className = 't';
      t.textContent = a.title || '未命名作业';
      var subj = document.createElement('span');
      subj.className = 'subj';
      subj.textContent = subjectLabel(a.subject);
      var dl = document.createElement('span');
      dl.className = 'dl';
      dl.textContent = a.deadline ? ('截止 ' + fmtDeadline(a.deadline)) : '';
      h.appendChild(t); h.appendChild(subj); h.appendChild(dl);

      var b = document.createElement('div');
      b.className = 'b';
      b.textContent = a.body || '';

      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn';
      btn.textContent = '去作答';
      btn.addEventListener('click', function () { openForm(a); });

      row.appendChild(h); row.appendChild(b); row.appendChild(btn);
      list.appendChild(row);
    });
  }

  function renderSubmissions() {
    var subs = state.submissions.filter(function (s) { return s.subject === current; });
    var list = $('submission-list');
    list.textContent = '';
    $('submission-empty').style.display = subs.length ? 'none' : 'block';

    subs.forEach(function (s) {
      var row = document.createElement('div');
      row.className = 'sub-row';

      var top = document.createElement('div');
      top.className = 'top';
      var t = document.createElement('span');
      t.className = 't';
      t.textContent = (s.assignment_title || '作业') + ' · ' + subjectLabel(s.subject);
      var sc = document.createElement('span');
      sc.className = 'score';
      var score = scoreOf(s.grade);
      sc.textContent = (score === null || score === undefined) ? '' : score + ' 分';
      top.appendChild(t); top.appendChild(pill(s.status)); top.appendChild(sc);
      row.appendChild(top);

      var cmtText = commentOf(s.grade);
      if (s.status === 'failed' && s.error_msg) cmtText = '批改失败：' + s.error_msg;
      if (cmtText) {
        var cmt = document.createElement('div');
        cmt.className = 'cmt';
        cmt.textContent = cmtText;
        row.appendChild(cmt);
      }

      list.appendChild(row);
    });
  }

  function renderAll() { renderAssignments(); renderSubmissions(); }

  function openForm(a) {
    state.selectedId = a.id;
    $('assignment-title').textContent = a.title || '未命名作业';
    $('assignment-body').textContent = a.body || '';
    $('answer-text').value = '';
    $('submit-form').style.display = 'block';
    var btn = $('submit-btn'); btn.disabled = false; btn.textContent = '提交';
    $('submit-form').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function closeForm() {
    state.selectedId = null;
    $('submit-form').style.display = 'none';
    $('answer-text').value = '';
  }

  function loadAssignments() {
    return CCAuth.authFetch(CFG.assignmentsApi)
      .then(function (r) { if (r.status !== 200) throw new Error('bad ' + r.status); return r.json(); })
      .then(function (d) { state.assignments = d.assignments || []; })
      .catch(function () { state.assignments = []; });
  }

  function loadSubmissions() {
    return CCAuth.authFetch(CFG.submissionsApi)
      .then(function (r) { if (r.status !== 200) throw new Error('bad ' + r.status); return r.json(); })
      .then(function (d) { state.submissions = d.submissions || []; })
      .catch(function () { state.submissions = []; });
  }

  function refreshAll() {
    return Promise.all([loadAssignments(), loadSubmissions()]).then(renderAll);
  }

  function submit() {
    var text = $('answer-text').value.trim();
    if (!text) { alert('请先填写你的作答'); return; }
    var btn = $('submit-btn');
    btn.disabled = true;
    btn.textContent = '提交中…';

    CCAuth.authFetch(CFG.submitApi, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assignment_id: state.selectedId, answer_text: text })
    })
      .then(function (r) { return r.json().then(function (d) { return { r: r, d: d }; }); })
      .then(function (o) {
        if (o.r.status === 201) {
          var sid = o.d.submission_id;
          closeForm();
          return refreshAll().then(function () { pollGrade(sid); });
        }
        var msg = (o.d && (o.d.message || o.d.error)) || ('提交失败（' + o.r.status + '）');
        alert(msg);
        btn.disabled = false; btn.textContent = '提交';
      })
      .catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        alert('提交失败，请稍后再试');
        btn.disabled = false; btn.textContent = '提交';
      });
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  async function pollGrade(sid) {
    var endpoint = String(CFG.submissionApi).replace(/\/0$/, '/' + sid);
    for (var i = 0; i < 30; i++) {
      await sleep(1000);
      try {
        var r = await CCAuth.authFetch(endpoint);
        if (r.status !== 200) continue;
        var s = await r.json();
        if (s.status === 'done' || s.status === 'failed') { await refreshAll(); return; }
      } catch (e) { return; }
    }
    await refreshAll();
  }

  $('submit-btn').addEventListener('click', submit);
  $('cancel-btn').addEventListener('click', closeForm);

  renderTabs();
  refreshAll();
})();