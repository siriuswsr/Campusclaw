/* 教师端「作业与改分」。
   GET /api/assignments 拿本班作业，GET /api/submissions 拿本班提交（均按 token 班级
   过滤），前端按学科分组渲染；「改分」→ PUT /api/submissions/<id>/grade 覆盖终评。 */
(function () {
  'use strict';
  if (!CCAuth.guard()) return;

  var CFG = window.CC_TEACHER_GRADING || {};
  var SUBJECTS = CFG.subjects || [];
  var current = SUBJECTS.length ? SUBJECTS[0].code : 'math';

  var state = { assignments: [], submissions: [] };

  function $(id) { return document.getElementById(id); }

  // 非教师：不发任何请求（真正的强制在各 /api 接口的 @role_required / teacher_only）
  var user = CCAuth.user() || {};
  if (user.role !== 'teacher') {
    $('denied').style.display = 'block';
    $('grading-body').style.display = 'none';
    return;
  }

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
    else if (status === 'failed') { el.classList.add('st-failed'); el.textContent = '失败'; }
    else { el.classList.add('st-progress'); el.textContent = '待批改'; }
    return el;
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
        render();
      });
      box.appendChild(b);
    });
  }

  function render() {
    var assign = state.assignments.filter(function (a) { return a.subject === current; });
    var subs = state.submissions.filter(function (s) { return s.subject === current; });

    var byAssign = {};
    subs.forEach(function (s) {
      (byAssign[s.assignment_id] = byAssign[s.assignment_id] || []).push(s);
    });

    var list = $('grading-list');
    list.textContent = '';
    $('grading-empty').style.display = assign.length ? 'none' : 'block';

    assign.forEach(function (a) {
      var block = document.createElement('div');
      block.className = 'asg-block';

      var head = document.createElement('div');
      head.className = 'head';
      var t = document.createElement('span');
      t.className = 't';
      t.textContent = a.title || '未命名作业';
      var subj = document.createElement('span');
      subj.className = 'subj';
      subj.textContent = subjectLabel(a.subject);
      var dl = document.createElement('span');
      dl.className = 'dl';
      dl.textContent = a.deadline ? ('截止 ' + fmtDeadline(a.deadline)) : '';
      head.appendChild(t); head.appendChild(subj); head.appendChild(dl);
      block.appendChild(head);

      var rows = byAssign[a.id] || [];
      if (!rows.length) {
        var empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = '暂无提交';
        block.appendChild(empty);
      } else {
        rows.forEach(function (s) { block.appendChild(renderSubmission(s)); });
      }

      list.appendChild(block);
    });
  }

  function renderSubmission(s) {
    var row = document.createElement('div');
    row.className = 'sub';

    var top = document.createElement('div');
    top.className = 'top';
    var name = document.createElement('span');
    name.className = 'name';
    name.textContent = s.student_name || ('学生 #' + s.student_id);
    top.appendChild(name);
    top.appendChild(pill(s.status));

    var scores = document.createElement('span');
    scores.className = 'scores';
    var g = s.grade;
    if (g) {
      var ai = document.createElement('span');
      ai.className = 'ai';
      ai.textContent = 'AI ' + (g.ai_score === null || g.ai_score === undefined ? '—' : g.ai_score);
      scores.appendChild(ai);
      if (g.final_score !== null && g.final_score !== undefined) {
        var fin = document.createElement('span');
        fin.className = 'fin';
        fin.textContent = '　终评 ' + g.final_score;
        scores.appendChild(fin);
      }
    }
    top.appendChild(scores);

    if (s.status === 'done') {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'link-btn';
      btn.textContent = '改分';
      btn.addEventListener('click', function () { overrideGrade(s); });
      top.appendChild(btn);
    }

    row.appendChild(top);

    if (s.answer_text) {
      var ans = document.createElement('div');
      ans.className = 'ans';
      ans.textContent = '作答：' + s.answer_text;
      row.appendChild(ans);
    }
    var cmt = (g && (g.final_comment || g.ai_comment)) || '';
    if (s.status === 'failed' && s.error_msg) cmt = '批改失败：' + s.error_msg;
    if (cmt) {
      var c = document.createElement('div');
      c.className = 'cmt';
      c.textContent = cmt.trim();
      row.appendChild(c);
    }

    return row;
  }

  function overrideGrade(s) {
    var g = s.grade || {};
    var curScore = (g.final_score !== null && g.final_score !== undefined)
      ? g.final_score : g.ai_score;
    var scoreStr = prompt('输入终评分数（0-100）', curScore === null || curScore === undefined ? '' : String(curScore));
    if (scoreStr === null) return;
    var score = Number(scoreStr);
    if (!isFinite(score) || score < 0 || score > 100) { alert('分数需为 0-100 的数字'); return; }

    var comment = prompt('输入终评评语', g.final_comment || '');
    if (comment === null) return;

    var endpoint = String(CFG.gradeTpl).replace(/\/0$/, '/' + s.id);
    CCAuth.authFetch(endpoint, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ final_score: score, final_comment: comment })
    })
      .then(function (r) { return r.json().then(function (d) { return { r: r, d: d }; }); })
      .then(function (o) {
        if (o.r.status === 200) { return loadAll(); }
        alert((o.d && (o.d.message || o.d.error)) || ('改分失败（' + o.r.status + '）'));
      })
      .catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        alert('改分失败，请稍后再试');
      });
  }

  function loadAll() {
    function get(url) {
      return CCAuth.authFetch(url)
        .then(function (r) { if (r.status !== 200) throw new Error('bad ' + r.status); return r.json(); })
        .catch(function () { return null; });
    }
    return Promise.all([get(CFG.assignmentsApi), get(CFG.submissionsApi)])
      .then(function (res) {
        state.assignments = (res[0] && res[0].assignments) || [];
        state.submissions = (res[1] && res[1].submissions) || [];
        render();
      });
  }

  renderTabs();
  loadAll();
})();