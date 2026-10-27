/* 学生 · 学习助手 —— 会话侧栏 + 智能体 tab + SSE 四类事件（meta/delta/done/error）。
   class_id 由服务端从 token 解出，前端只提交 {agent_id}（新建会话）与 {question}（提问）。
   提问走 POST /api/sessions/<id>/messages；引用/技能等信息由 done 事件的 refs 提供。 */
(function () {
  'use strict';

  if (!CCAuth.guard()) return;

  var CFG = window.CC_STUDENT_CONFIG || {};
  var SESSIONS_API = CFG.sessionsApi || '/api/sessions';
  var AGENTS_API = CFG.agentsApi || '/api/agents';

  function $(id) { return document.getElementById(id); }

  var messages = $('messages');
  var input = $('input');
  var sendBtn = $('send');
  var newBtn = $('new-session');

  var agents = [];          // 启用中的智能体（服务端已按角色过滤）
  var sessions = [];        // 本人会话（服务端已按 user 过滤）
  var currentAgentId = null;
  var currentSessionId = null;
  var sending = false;

  function scrollToBottom() { window.scrollTo(0, document.body.scrollHeight); }

  function showEmptyHint(on) {
    var hint = $('chat-empty');
    if (!hint) return;
    hint.style.display = on ? 'block' : 'none';
  }

  // =========================================================================
  // 智能体 tab
  // =========================================================================

  function highlightTab(id) {
    Array.prototype.forEach.call($('agent-tabs').children, function (b) {
      b.classList.toggle('active', b.getAttribute('data-id') === String(id));
    });
  }

  function renderTabs() {
    var box = $('agent-tabs');
    box.textContent = '';
    $('agent-empty').style.display = agents.length ? 'none' : 'block';
    newBtn.disabled = agents.length === 0;

    agents.forEach(function (a) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'tab' + (a.id === currentAgentId ? ' active' : '');
      b.setAttribute('data-id', String(a.id));
      b.textContent = a.name;
      b.addEventListener('click', function () { selectAgent(a.id); });
      box.appendChild(b);
    });
  }

  /* 切 tab：把「下一条提问」指向该智能体 —— 清掉当前会话，下次发送自动新建
     一个绑定该智能体的会话（提示词 F 第 4 步：新对话按钮选当前 tab 的智能体）。 */
  function selectAgent(id) {
    if (id === currentAgentId && currentSessionId !== null) return;
    currentAgentId = id;
    currentSessionId = null;
    highlightTab(id);
    messages.textContent = '';
    showEmptyHint(true);
    markActiveSession(null);
  }

  function loadAgents() {
    return CCAuth.authFetch(AGENTS_API)
      .then(function (r) {
        if (r.status !== 200) return { agents: [] };
        return r.json().catch(function () { return { agents: [] }; });
      })
      .then(function (d) {
        agents = d.agents || [];
        if (agents.length && currentAgentId === null) currentAgentId = agents[0].id;
        renderTabs();
      })
      .catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        renderTabs();
      });
  }

  // =========================================================================
  // 会话侧栏
  // =========================================================================

  function markActiveSession(id) {
    Array.prototype.forEach.call($('session-list').children, function (el) {
      if (el.getAttribute('data-id')) {
        el.classList.toggle('active', el.getAttribute('data-id') === String(id));
      }
    });
  }

  function renderSessionList() {
    var box = $('session-list');
    box.textContent = '';
    if (!sessions.length) {
      var e = document.createElement('div');
      e.className = 's-empty';
      e.textContent = '还没有会话，点「＋ 新对话」开始。';
      box.appendChild(e);
      return;
    }
    sessions.forEach(function (s) {
      var el = document.createElement('div');
      el.className = 's-item' + (s.id === currentSessionId ? ' active' : '');
      el.setAttribute('data-id', String(s.id));
      var t = document.createElement('div');
      t.className = 's-title';
      t.textContent = s.agent_name || ('会话 #' + s.id);
      var m = document.createElement('div');
      m.className = 's-meta';
      m.textContent = (s.message_count || 0) + ' 条消息';
      el.appendChild(t);
      el.appendChild(m);
      el.addEventListener('click', function () { selectSession(s.id); });
      box.appendChild(el);
    });
  }

  function loadSessions(preferId) {
    return CCAuth.authFetch(SESSIONS_API)
      .then(function (r) {
        if (r.status !== 200) return { sessions: [] };
        return r.json().catch(function () { return { sessions: [] }; });
      })
      .then(function (d) {
        sessions = d.sessions || [];
        renderSessionList();
        if (preferId != null) return selectSession(preferId);
        if (currentSessionId != null &&
            sessions.some(function (s) { return s.id === currentSessionId; })) return;
        if (sessions.length) return selectSession(sessions[0].id);
        currentSessionId = null;
        messages.textContent = '';
        showEmptyHint(true);
      })
      .catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        renderSessionList();
      });
  }

  /* 读取一个历史会话：最近 6 条 + 智能体快照，整体重渲染。 */
  function selectSession(id) {
    return CCAuth.authFetch(SESSIONS_API + '/' + id)
      .then(function (r) {
        if (r.status !== 200) return null;
        return r.json().catch(function () { return null; });
      })
      .then(function (d) {
        currentSessionId = id;
        markActiveSession(id);
        messages.textContent = '';
        if (!d) { showEmptyHint(true); return; }
        var aid = d.session && d.session.agent_id;
        if (aid != null) {
          currentAgentId = aid;
          if (agents.some(function (a) { return a.id === aid; })) highlightTab(aid);
        }
        renderHistory(d.messages || []);
        scrollToBottom();
      })
      .catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
      });
  }

  function renderHistory(list) {
    if (!list.length) { showEmptyHint(true); return; }
    showEmptyHint(false);
    list.forEach(function (m) {
      if (m.role === 'user') appendUser(m.content);
      else appendAiStatic(m.content);
    });
  }

  // =========================================================================
  // 气泡
  // =========================================================================

  function appendUser(text) {
    showEmptyHint(false);
    var row = document.createElement('div');
    row.className = 'row user';
    var b = document.createElement('div');
    b.className = 'bubble user-bubble';
    b.textContent = text;
    row.appendChild(b);
    messages.appendChild(row);
    scrollToBottom();
  }

  /* 历史里的 AI 消息：没有存引用，只渲染正文。 */
  function appendAiStatic(text) {
    showEmptyHint(false);
    var row = document.createElement('div');
    row.className = 'row ai';
    var bubble = document.createElement('div');
    bubble.className = 'bubble ai-bubble';
    var body = document.createElement('div');
    body.className = 'body';
    body.textContent = text;
    bubble.appendChild(body);
    row.appendChild(bubble);
    messages.appendChild(row);
    scrollToBottom();
  }

  function createAiBubble() {
    showEmptyHint(false);
    var row = document.createElement('div');
    row.className = 'row ai';
    var bubble = document.createElement('div');
    bubble.className = 'bubble ai-bubble';

    var steps = document.createElement('details');
    steps.className = 'steps';
    steps.style.display = 'none';
    var summary = document.createElement('summary');
    summary.textContent = '执行过程';
    var stepList = document.createElement('ol');
    steps.appendChild(summary);
    steps.appendChild(stepList);

    var body = document.createElement('div');
    body.className = 'body';
    var thinking = document.createElement('span');
    thinking.className = 'thinking';
    thinking.textContent = '思考中…';
    var text = document.createElement('span');
    var caret = document.createElement('span');
    caret.className = 'caret';
    caret.style.display = 'none';
    var err = document.createElement('div');
    err.className = 'err';
    err.style.display = 'none';
    body.appendChild(thinking);
    body.appendChild(text);
    body.appendChild(caret);
    body.appendChild(err);

    var sources = document.createElement('div');
    sources.className = 'sources';
    sources.style.display = 'none';

    bubble.appendChild(steps);
    bubble.appendChild(body);
    bubble.appendChild(sources);
    row.appendChild(bubble);
    messages.appendChild(row);
    scrollToBottom();

    var answer = '';
    var errored = false;

    function addStep(label) {
      if (steps.style.display === 'none') { steps.style.display = ''; steps.open = true; }
      var li = document.createElement('li');
      li.textContent = label;
      stepList.appendChild(li);
    }

    return {
      meta: function (ctx) {
        if (ctx && ctx.skill_id != null) addStep('命中技能 #' + ctx.skill_id);
        else addStep('未命中技能 · 按智能体提示词作答');
        if (ctx && ctx.model) addStep('模型 ' + ctx.model);
      },

      token: function (t) {
        if (errored) return;
        thinking.style.display = 'none';
        caret.style.display = '';
        answer += t;
        text.textContent = answer;
        scrollToBottom();
      },

      /* done 事件的 refs：[{n, title, loc}]；空数组 → 不显示来源区 */
      sources: function (refs) {
        if (!refs || !refs.length) return;
        sources.textContent = '';
        refs.forEach(function (s) {
          var d = document.createElement('div');
          d.className = 'src';
          var no = document.createElement('span');
          no.className = 'no';
          no.textContent = '[' + (s.n != null ? s.n : '') + ']';
          var title = document.createElement('span');
          title.textContent = s.title || '';
          var sec = document.createElement('span');
          sec.className = 'sec';
          sec.textContent = s.loc || '';
          d.appendChild(no);
          d.appendChild(title);
          d.appendChild(sec);
          sources.appendChild(d);
        });
        sources.dataset.ready = '1';
      },

      error: function (msg) {
        errored = true;
        thinking.style.display = 'none';
        caret.style.display = 'none';
        err.textContent = '❌ ' + (msg || '服务暂时不可用，请稍后再试');
        err.style.display = 'block';
      },

      finish: function () {
        thinking.style.display = 'none';
        caret.style.display = 'none';
        if (errored) return;
        if (!answer) { text.textContent = '（未获得回复）'; }
        if (sources.dataset.ready === '1') sources.style.display = 'block';
        scrollToBottom();
      }
    };
  }

  // =========================================================================
  // SSE —— 按 event: 行分帧（meta / delta / done / error）
  // =========================================================================

  function createSseParser(handler) {
    var buf = '';
    var ev = null;
    var dataLines = [];

    function flush() {
      if (ev === null && dataLines.length === 0) return;
      var name = ev || 'message';
      var raw = dataLines.join('\n');
      var data = null;
      if (raw) { try { data = JSON.parse(raw); } catch (e) { data = null; } }
      handler(name, data);
      ev = null;
      dataLines = [];
    }

    return {
      push: function (chunk) {
        buf += chunk;
        var lines = buf.split('\n');
        buf = lines.pop();
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i];
          if (line === '') { flush(); continue; }
          if (line.charAt(0) === ':') continue;               /* SSE 注释 */
          if (line.indexOf('event:') === 0) { ev = line.slice(6).trim(); }
          else if (line.indexOf('data:') === 0) {
            dataLines.push(line.slice(5).replace(/^ /, ''));
          }
        }
      },
      end: function () {
        if (buf) { buf += '\n'; }
        flush();
      }
    };
  }

  function streamMessage(url, body, ai) {
    return CCAuth.authFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (response) {
      if (response.status !== 200 || !response.body) {
        ai.error('服务暂时不可用（' + response.status + '）');
        return null;
      }
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var parser = createSseParser(function (name, data) {
        if (name === 'meta') ai.meta(data || {});
        else if (name === 'delta') { if (data && data.text) ai.token(data.text); }
        else if (name === 'done') ai.sources((data && data.refs) || []);
        else if (name === 'error') ai.error((data && data.message) || '服务暂时不可用');
      });

      function pump() {
        return reader.read().then(function (chunk) {
          if (chunk.done) { parser.end(); return null; }
          parser.push(decoder.decode(chunk.value, { stream: true }));
          return pump();
        });
      }
      return pump().catch(function () { ai.error('响应流中断'); });
    }).catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return null;
      ai.error('网络请求失败');
      return null;
    });
  }

  // =========================================================================
  // 新建 / 发送
  // =========================================================================

  function createSession() {
    if (currentAgentId === null) return Promise.resolve(null);
    return CCAuth.authFetch(SESSIONS_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: currentAgentId })
    }).then(function (r) {
      if (r.status !== 201) return null;
      return r.json().catch(function () { return null; });
    }).then(function (d) { return d ? d.id : null; })
      .catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return null;
        return null;
      });
  }

  /* 「＋ 新对话」：绑定当前 tab 的智能体，落库后加入侧栏并选中。 */
  function onNewSession() {
    if (sending) return;
    return createSession().then(function (id) {
      if (id === null) return;
      currentSessionId = id;
      messages.textContent = '';
      showEmptyHint(true);
      return loadSessions(id).then(function () { input.focus(); });
    });
  }

  function setBusy(on) {
    sendBtn.disabled = on;
    sendBtn.textContent = on ? '发送中…' : '发 送';
  }

  function autoGrow() {
    input.style.height = '46px';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  }

  async function send() {
    if (sending) return;
    var q = input.value.trim();
    if (!q) return;

    // 没有当前会话 → 用当前 tab 的智能体懒创建一个
    if (currentSessionId === null) {
      if (currentAgentId === null) return;
      var id = await createSession();
      if (id === null) return;
      currentSessionId = id;
      await loadSessions(id);
      messages.textContent = '';
    }

    sending = true;
    setBusy(true);
    input.value = '';
    autoGrow();

    appendUser(q);
    var ai = createAiBubble();
    try {
      await streamMessage(SESSIONS_API + '/' + currentSessionId + '/messages',
                          { question: q }, ai);
    } finally {
      sending = false;
      setBusy(false);
      input.focus();
    }
    ai.finish();
    loadSessions();   // 刷新侧栏顺序与消息数
  }

  newBtn.addEventListener('click', onNewSession);
  sendBtn.addEventListener('click', send);

  input.addEventListener('input', autoGrow);
  input.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.shiftKey) return;
    if (e.isComposing) return;
    e.preventDefault();
    send();
  });

  loadAgents()
    .then(function () { return loadSessions(); })
    .then(function () { input.focus(); });
})();
