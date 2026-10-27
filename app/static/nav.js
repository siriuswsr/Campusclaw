/* 左侧导航渲染。
   为什么在客户端渲染：页面是「服务端 200 空壳 + 前端守卫」，浏览器做页面跳转时
   不会带 Authorization，服务端拿不到 role，菜单只能是「学生 7 项 / 教师 5 项」的
   差异只能由 sessionStorage 里的 user.role 决定。
   课程范围外的条目保留文字但置灰：渲染成没有 href 的 <span>，不可聚焦、点击无跳转。 */
(function () {
  'use strict';

  var URLS = window.CC_NAV_URLS || {};

  var MENU = {
    student: [
      { key: 'student-home',     ic: '🏠', label: '本班首页',   url: URLS.studentHome },
      { key: 'materials',        ic: '🔎', label: '讲义与检索', url: URLS.materials },
      { key: 'student-chat',     ic: '💬', label: '助手对话',   url: URLS.studentChat },
      { key: 'student-homework', ic: '📤', label: '交作业',     url: URLS.studentHomework },
      { key: 'student-wrong',    ic: '❗', label: '错题',       url: URLS.studentWrong },
      { ic: '💗', label: '关怀式对话', disabled: true },
      { ic: '📈', label: '学情分析',   disabled: true }
    ],
    teacher: [
      { key: 'teacher-home',      ic: '🏠', label: '本班首页', url: URLS.teacherHome },
      { key: 'teacher-materials', ic: '📚', label: '讲义管理', url: URLS.teacherMats },
      { key: 'teacher-config',    ic: '🤖', label: '助手配置', url: URLS.teacherConfig },
      { key: 'teacher-grading',   ic: '📝', label: '作业与改分', url: URLS.teacherGrading },
      { ic: '📊', label: '审计与统计', disabled: true }
    ]
  };

  /* 当前页高亮：按路径判定，避免依赖模板变量。
     教师落在 /materials（登录页写死了这个落地页）时高亮「讲义管理」。 */
  function activeKey(role) {
    var p = location.pathname.replace(/\/+$/, '') || '/';
    if (p === '/student/home') return 'student-home';
    if (p === '/student/assistant') return 'student-chat';
    if (p === '/student/homework') return 'student-homework';
    if (p === '/student/wrong') return 'student-wrong';
    if (p === '/teacher/home') return 'teacher-home';
    if (p === '/teacher/materials') return 'teacher-materials';
    if (p === '/teacher/assistant-config') return 'teacher-config';
    if (p === '/teacher/grading') return 'teacher-grading';
    if (p === '/materials' || /^\/materials\/\d+$/.test(p)) {
      return role === 'teacher' ? 'teacher-materials' : 'materials';
    }
    return '';
  }

  function buildItem(it, active) {
    // 置灰项刻意不用 <a>：没有 href 就没有跳转，也不进入 Tab 焦点链
    var el = document.createElement(it.disabled ? 'span' : 'a');
    el.className = 'nav-item'
      + (it.disabled ? ' disabled' : '')
      + (it.key && it.key === active ? ' active' : '');
    if (it.disabled) el.setAttribute('aria-disabled', 'true');
    else el.href = it.url || '#';

    var ic = document.createElement('span');
    ic.className = 'ic';
    ic.textContent = it.ic || '';
    var tx = document.createElement('span');
    tx.className = 't';
    tx.textContent = it.label;
    el.appendChild(ic);
    el.appendChild(tx);
    return el;
  }

  function render() {
    if (!window.CCAuth) return;
    if (!CCAuth.guard()) return;           // 无 token → 跳登录页

    var user = CCAuth.user() || {};
    var role = user.role === 'teacher' ? 'teacher' : 'student';
    var active = activeKey(role);

    var box = document.getElementById('cc-nav');
    if (box) {
      box.textContent = '';
      (MENU[role] || []).forEach(function (it) { box.appendChild(buildItem(it, active)); });
    }

    var who = document.getElementById('cc-who');
    if (who) who.textContent = user.name || user.username || '';
    var whoTop = document.getElementById('cc-who-top');
    if (whoTop) whoTop.textContent = user.name || user.username || '';

    /* 侧栏底部与右上角两个入口共用同一个登出动作 */
    ['cc-logout', 'cc-logout-top'].forEach(function (id) {
      var out = document.getElementById(id);
      if (!out) return;
      out.addEventListener('click', function (e) {
        e.preventDefault();
        CCAuth.logout();
      });
    });
  }

  window.CCNav = { render: render, MENU: MENU };
  render();
})();