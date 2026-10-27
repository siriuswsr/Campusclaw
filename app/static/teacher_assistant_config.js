/* 教师 · 智能体管理页 —— 智能体 CRUD + 技能文件管理。
   所有请求走 CCAuth.authFetch（自动带 Bearer；401 清空身份并跳 /login）。
   页面是 200 空壳，真正权限由 /api/agents、/api/skills 上的 @require_auth + 教师校验负责。 */
(function () {
  'use strict';

  if (!CCAuth.guard()) return;

  var CFG = window.CC_ASSISTANT_CONFIG || {};
  var user = CCAuth.user() || {};

  function $(id) { return document.getElementById(id); }

  function showMsg(el, kind, text) {
    el.className = 'msg ' + kind;
    el.textContent = text || '';
    el.style.display = 'block';
  }
  function hideMsg(el) { el.style.display = 'none'; el.textContent = ''; }

  function toJson(r) {
    return r.json().catch(function () { return {}; })
      .then(function (d) { return { status: r.status, data: d }; });
  }

  // 非教师：不发任何请求，直接提示（接口层同样对非教师返回 403）
  if (user.role !== 'teacher') {
    $('denied').style.display = 'block';
    $('config-body').style.display = 'none';
    return;
  }

  // ---- 状态 ----
  var agents = [];       // GET /api/agents
  var skills = [];       // GET /api/skills
  var materials = [];    // GET /materials/api
  var editingAgentId = null;
  var editingSkillId = null;
  var mcpServers = [];        // GET /api/mcp/servers
  var mcpTools = {};          // server_id -> 已发现工具 [{name, description}]
  var editingServerId = null;

  function agentById(id) {
    for (var i = 0; i < agents.length; i++) if (agents[i].id === id) return agents[i];
    return null;
  }

  // =========================================================================
  // 智能体列表
  // =========================================================================

  function renderAgents() {
    var box = $('agent-list');
    var empty = $('agent-empty');
    box.textContent = '';
    empty.style.display = agents.length ? 'none' : 'block';
    agents.forEach(function (a) {
      var row = document.createElement('div');
      row.className = 'row';

      var nm = document.createElement('span');
      nm.className = 'name';
      nm.textContent = a.name;

      var badge = document.createElement('span');
      badge.className = 'badge' + (a.enabled ? '' : ' off');
      badge.textContent = a.enabled ? '启用' : '停用';

      var spacer = document.createElement('span');
      spacer.className = 'spacer';

      var edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'btn';
      edit.textContent = '编辑';
      edit.addEventListener('click', function () { startEditAgent(a.id); });

      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'btn danger';
      del.textContent = '删除';
      del.addEventListener('click', function () { removeAgent(a.id, a.name); });

      row.appendChild(nm);
      row.appendChild(badge);
      row.appendChild(spacer);
      row.appendChild(edit);
      row.appendChild(del);
      box.appendChild(row);
    });
  }

  function renderAgentCandidates() {
    var sk = $('agent-skills');
    var mt = $('agent-materials');
    sk.textContent = '';
    mt.textContent = '';

    skills.filter(function (s) { return s.enabled; }).forEach(function (s) {
      sk.appendChild(checkItem('skill', s.id, s.name));
    });
    materials.forEach(function (m) {
      mt.appendChild(checkItem('material', m.id, m.title || m.name));
    });
  }

  function checkItem(kind, id, label) {
    var lab = document.createElement('label');
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = String(id);
    cb.setAttribute('data-kind', kind);
    cb.setAttribute('data-id', String(id));
    var txt = document.createElement('span');
    txt.textContent = label;
    lab.appendChild(cb);
    lab.appendChild(txt);
    return lab;
  }

  function setChecked(kind, ids) {
    var map = {};
    (ids || []).forEach(function (x) { map[String(x)] = true; });
    var box = kind === 'skill' ? $('agent-skills') : $('agent-materials');
    Array.prototype.forEach.call(box.querySelectorAll('input[type=checkbox]'), function (cb) {
      cb.checked = !!map[cb.getAttribute('data-id')];
    });
  }

  function collectChecked(kind) {
    var box = kind === 'skill' ? $('agent-skills') : $('agent-materials');
    var out = [];
    Array.prototype.forEach.call(box.querySelectorAll('input[type=checkbox]'), function (cb) {
      if (cb.checked) out.push(parseInt(cb.getAttribute('data-id'), 10));
    });
    return out;
  }

  // =========================================================================
  // 智能体表单
  // =========================================================================

  function resetAgentForm() {
    editingAgentId = null;
    $('agent-form-title').textContent = '新建智能体';
    $('agent-name').value = '';
    $('agent-prompt').value = '';
    $('agent-enabled').checked = true;
    $('agent-cancel').style.display = 'none';
    hideMsg($('agent-msg'));
    hideMsg($('agent-saved'));
    renderAgentCandidates();
    updatePromptCount();
  }

  function startEditAgent(id) {
    var a = agentById(id);
    if (!a) return;
    editingAgentId = id;
    $('agent-form-title').textContent = '编辑智能体';
    $('agent-name').value = a.name || '';
    $('agent-prompt').value = a.prompt || '';
    $('agent-enabled').checked = !!a.enabled;
    $('agent-cancel').style.display = '';
    hideMsg($('agent-msg'));
    hideMsg($('agent-saved'));
    renderAgentCandidates();
    setChecked('skill', a.skill_ids);
    setChecked('material', a.material_ids);
    updatePromptCount();
    window.scrollTo(0, $('agent-form-title').getBoundingClientRect().top + window.pageYOffset - 20);
  }

  function updatePromptCount() {
    $('agent-prompt-count').textContent = ($('agent-prompt').value.length) + ' / 2000';
  }

  function saveAgent() {
    var name = $('agent-name').value.trim();
    if (!name) { showMsg($('agent-msg'), 'err', '名称必填'); return; }
    var prompt = $('agent-prompt').value;
    if (prompt.length > 2000) { showMsg($('agent-msg'), 'err', '角色提示词超过 2000 字上限'); return; }

    var payload = {
      name: name,
      prompt: prompt,
      enabled: $('agent-enabled').checked,
      skill_ids: collectChecked('skill'),
      material_ids: collectChecked('material')
    };
    var isEdit = editingAgentId !== null;
    var url = isEdit ? (CFG.agentsApi + '/' + editingAgentId) : CFG.agentsApi;

    hideMsg($('agent-msg'));
    hideMsg($('agent-saved'));

    CCAuth.authFetch(url, {
      method: isEdit ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(toJson).then(function (res) {
      if (res.status === 200 || res.status === 201) {
        $('agent-saved').style.display = 'inline';
        loadAll();
        resetAgentForm();
        return;
      }
      var msg = res.data && (res.data.message || res.data.error);
      if (res.status === 403) msg = '仅教师可修改配置';
      showMsg($('agent-msg'), 'err', msg || '保存失败，请稍后重试');
    }).catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
      showMsg($('agent-msg'), 'err', '网络错误，保存失败');
    });
  }

  function removeAgent(id, name) {
    if (!window.confirm('确定删除智能体「' + name + '」？其技能与材料绑定将一并清空。')) return;
    CCAuth.authFetch(CFG.agentsApi + '/' + id, { method: 'DELETE' }).then(toJson).then(function (res) {
      if (res.status === 200) { loadAll(); if (editingAgentId === id) resetAgentForm(); }
      else if (res.status === 404) { loadAll(); }
    }).catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
    });
  }

  // =========================================================================
  // 技能文件管理
  // =========================================================================

  function renderSkills() {
    var box = $('skill-list');
    var empty = $('skill-empty');
    box.textContent = '';
    empty.style.display = skills.length ? 'none' : 'block';
    skills.forEach(function (s) {
      var row = document.createElement('div');
      row.className = 'row';

      var nm = document.createElement('span');
      nm.className = 'name';
      nm.textContent = s.name;

      var meta = document.createElement('span');
      meta.className = 'dim';
      meta.textContent = '适用：' + (s.trigger_text || '') + (s.subject_text ? ' · 资料：' + s.subject_text : '');

      var badge = document.createElement('span');
      badge.className = 'badge' + (s.enabled ? '' : ' off');
      badge.textContent = s.enabled ? '启用' : '停用';

      var spacer = document.createElement('span');
      spacer.className = 'spacer';

      var edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'btn';
      edit.textContent = '编辑';
      edit.addEventListener('click', function () { startEditSkill(s.id); });

      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'btn danger';
      del.textContent = '删除';
      del.addEventListener('click', function () { removeSkill(s.id, s.name, s.binding_count || 0); });

      row.appendChild(nm);
      row.appendChild(meta);
      row.appendChild(badge);
      row.appendChild(spacer);
      row.appendChild(edit);
      row.appendChild(del);
      box.appendChild(row);
    });
  }

  function resetSkillForm() {
    editingSkillId = null;
    $('skill-form-title').textContent = '上传技能文件';
    $('skill-source').value = '';
    $('skill-cancel').style.display = 'none';
    hideMsg($('skill-saved'));
    hideMsg($('skill-msg'));
  }

  function startEditSkill(id) {
    var s = null;
    for (var i = 0; i < skills.length; i++) if (skills[i].id === id) s = skills[i];
    if (!s) return;
    editingSkillId = id;
    $('skill-form-title').textContent = '编辑技能文件';
    $('skill-source').value = s.source_text || '';
    $('skill-cancel').style.display = '';
    hideMsg($('skill-saved'));
    hideMsg($('skill-msg'));
    window.scrollTo(0, $('skill-form-title').getBoundingClientRect().top + window.pageYOffset - 20);
  }

  function saveSkill() {
    var text = $('skill-source').value;
    if (!text || !text.trim()) { showMsg($('skill-msg'), 'err', '请粘贴技能文件全文'); return; }

    var isEdit = editingSkillId !== null;
    var url = isEdit ? (CFG.skillsApi + '/' + editingSkillId) : CFG.skillsApi;

    hideMsg($('skill-saved'));
    hideMsg($('skill-msg'));

    CCAuth.authFetch(url, {
      method: isEdit ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      body: text
    }).then(toJson).then(function (res) {
      if (res.status === 200 || res.status === 201) {
        $('skill-saved').style.display = 'inline';
        loadAll();
        resetSkillForm();
        return;
      }
      var msg = res.data && (res.data.message || res.data.error);
      if (res.status === 403) msg = '仅教师可修改配置';
      showMsg($('skill-msg'), 'err', msg || '提交失败，请稍后重试');
    }).catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
      showMsg($('skill-msg'), 'err', '网络错误，提交失败');
    });
  }

  function removeSkill(id, name, bindCount) {
    var tip = bindCount > 0 ? ('同时清掉 ' + bindCount + ' 个绑定，') : '';
    if (!window.confirm(tip + '确定删除技能「' + name + '」？')) return;
    CCAuth.authFetch(CFG.skillsApi + '/' + id, { method: 'DELETE' }).then(toJson).then(function (res) {
      if (res.status === 200) { loadAll(); if (editingSkillId === id) resetSkillForm(); }
      else if (res.status === 404) { loadAll(); }
    }).catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
    });
  }

  // =========================================================================
  // MCP 服务器管理
  // =========================================================================

  function mcpMsg(kind, text) {
    showMsg($('mcp-msg'), kind, text);
  }

  function renderMcpServers() {
    var box = $('mcp-servers');
    box.textContent = '';
    $('mcp-empty').style.display = mcpServers.length ? 'none' : 'block';

    mcpServers.forEach(function (s) {
      var item = document.createElement('div');
      item.className = 'mcp-item';

      var nm = document.createElement('div');
      nm.className = 'mcp-name';
      nm.textContent = s.name;

      var url = document.createElement('div');
      url.className = 'mcp-url';
      url.textContent = s.base_url + ' · ' + (s.connected ? '已连接' : '未连接')
        + ' · ' + (s.tool_count || 0) + ' 个工具'
        + (s.has_auth ? '' : ' · 未配置令牌');

      var actions = document.createElement('div');
      actions.className = 'actions';

      var connect = document.createElement('button');
      connect.type = 'button';
      connect.className = 'btn primary';
      connect.textContent = '连接并发现';
      connect.addEventListener('click', function () { connectServer(s.id, connect); });

      var disc = document.createElement('button');
      disc.type = 'button';
      disc.className = 'btn';
      disc.textContent = '断开';
      disc.style.display = s.connected ? '' : 'none';
      disc.addEventListener('click', function () { disconnectServer(s.id); });

      var edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'btn';
      edit.textContent = '编辑';
      edit.addEventListener('click', function () { startEditServer(s.id); });

      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'btn danger';
      del.textContent = '删除';
      del.addEventListener('click', function () { removeServer(s.id, s.name); });

      actions.appendChild(connect);
      actions.appendChild(disc);
      actions.appendChild(edit);
      actions.appendChild(del);

      item.appendChild(nm);
      item.appendChild(url);
      item.appendChild(actions);

      var tools = mcpTools[s.id];
      if (tools && tools.length) {
        var tbox = document.createElement('div');
        tbox.className = 'mcp-tools';
        tools.forEach(function (t) {
          var line = document.createElement('div');
          line.className = 'tool';
          var b = document.createElement('b');
          b.textContent = t.name;
          line.appendChild(b);
          if (t.description) {
            var sp = document.createElement('span');
            sp.textContent = ' — ' + t.description;
            line.appendChild(sp);
          }
          tbox.appendChild(line);
        });
        item.appendChild(tbox);
      }

      box.appendChild(item);
    });
  }

  function renderMcpAudit(calls) {
    var box = $('mcp-audit');
    box.textContent = '';
    $('mcp-audit-empty').style.display = calls.length ? 'none' : 'block';
    if (!calls.length) return;

    var table = document.createElement('table');
    table.className = 'audit';
    var thead = document.createElement('thead');
    var htr = document.createElement('tr');
    ['时间', '工具@服务器', '状态', '用户'].forEach(function (t) {
      var th = document.createElement('th');
      th.textContent = t;
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    calls.forEach(function (c) {
      var tr = document.createElement('tr');

      var at = document.createElement('td');
      at.textContent = String(c.called_at || '').replace('T', ' ').slice(0, 19);

      var ref = document.createElement('td');
      ref.textContent = c.tool_ref || ((c.tool_name || '') + '@' + (c.server_name || ''));

      var st = document.createElement('td');
      var badge = document.createElement('span');
      badge.className = 'badge' + (c.status === 'ok' ? '' : (c.status === 'denied' ? ' off' : ' warn'));
      badge.textContent = c.status || '';
      st.appendChild(badge);

      var usr = document.createElement('td');
      usr.textContent = '用户 #' + (c.user_id || 0);

      tr.appendChild(at);
      tr.appendChild(ref);
      tr.appendChild(st);
      tr.appendChild(usr);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);
  }

  function loadMcp() {
    return Promise.all([
      CCAuth.authFetch(CFG.mcpApi + '/servers').then(toJson),
      CCAuth.authFetch(CFG.mcpApi + '/tool-calls?limit=20').then(toJson)
    ]).then(function (res) {
      if (res[0].status === 403) {
        mcpMsg('err', '仅教师可管理 MCP 服务器');
        return;
      }
      mcpServers = (res[0].data && res[0].data.servers) || [];
      var calls = (res[1].data && res[1].data.calls) || [];
      mcpTools = {};
      renderMcpServers();
      renderMcpAudit(calls);

      // 已连接的服务器逐个取回工具缓存，便于在界面看到工具名
      var jobs = mcpServers.filter(function (s) { return s.connected; })
        .map(function (s) {
          return CCAuth.authFetch(CFG.mcpApi + '/servers/' + s.id + '/tools')
            .then(toJson).then(function (r) {
              if (r.status === 200) mcpTools[s.id] = (r.data && r.data.tools) || [];
            }).catch(function () {});
        });
      return Promise.all(jobs).then(renderMcpServers);
    }).catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
      mcpMsg('err', 'MCP 列表加载失败，请稍后重试');
    });
  }

  function connectServer(id, btn) {
    hideMsg($('mcp-msg'));
    btn.disabled = true;
    CCAuth.authFetch(CFG.mcpApi + '/servers/' + id + '/connect', { method: 'POST' })
      .then(toJson).then(function (res) {
        if (res.status === 200) {
          var tools = (res.data && res.data.tools) || [];
          mcpMsg('ok', '已连接「' + (res.data.server_name || '') + '」，发现 '
            + tools.length + ' 个工具：' + tools.map(function (t) { return t.name; }).join('、'));
          return loadMcp();
        }
        var msg = res.data && (res.data.message || res.data.error);
        if (res.status === 502) msg = msg || '连接失败，请检查 Base URL 与令牌';
        mcpMsg('err', msg || '连接失败，请稍后重试');
      }).catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        mcpMsg('err', '网络错误，连接失败');
      }).then(function () { btn.disabled = false; });
  }

  function disconnectServer(id) {
    if (!window.confirm('断开后将清空已发现的工具缓存，绑定该服务器工具的技能将无法调用。确定断开？')) return;
    hideMsg($('mcp-msg'));
    CCAuth.authFetch(CFG.mcpApi + '/servers/' + id + '/disconnect', { method: 'POST' })
      .then(toJson).then(function (res) {
        if (res.status === 200) {
          mcpMsg('ok', '已断开，工具缓存已清空');
          return loadMcp();
        }
        var msg = res.data && (res.data.message || res.data.error);
        mcpMsg('err', msg || '断开失败，请稍后重试');
      }).catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        mcpMsg('err', '网络错误，断开失败');
      });
  }

  function removeServer(id, name) {
    if (!window.confirm('确定删除 MCP 服务器「' + name + '」？其工具缓存将一并清空。')) return;
    hideMsg($('mcp-msg'));
    CCAuth.authFetch(CFG.mcpApi + '/servers/' + id, { method: 'DELETE' })
      .then(toJson).then(function (res) {
        if (res.status === 200) {
          if (editingServerId === id) resetServerForm();
          return loadMcp();
        }
        if (res.status === 404) return loadMcp();
        var msg = res.data && (res.data.message || res.data.error);
        mcpMsg('err', msg || '删除失败，请稍后重试');
      }).catch(function (e) {
        if (String(e && e.message) === 'unauthorized') return;
        mcpMsg('err', '网络错误，删除失败');
      });
  }

  function serverById(id) {
    for (var i = 0; i < mcpServers.length; i++) if (mcpServers[i].id === id) return mcpServers[i];
    return null;
  }

  function resetServerForm() {
    editingServerId = null;
    $('mcp-form-title').textContent = '登记 MCP 服务器';
    $('mcp-name').value = '';
    $('mcp-base').value = '';
    $('mcp-token').value = '';
    $('mcp-token').placeholder = '只保存在服务端，保存后不会回显';
    $('mcp-save').textContent = '登记服务器';
    $('mcp-cancel').style.display = 'none';
    hideMsg($('mcp-msg'));
  }

  function startEditServer(id) {
    var s = serverById(id);
    if (!s) return;
    editingServerId = id;
    $('mcp-form-title').textContent = '编辑 MCP 服务器';
    $('mcp-name').value = s.name || '';
    $('mcp-base').value = s.base_url || '';
    $('mcp-token').value = '';
    $('mcp-token').placeholder = '留空表示不改（令牌不会回显）';
    $('mcp-save').textContent = '保存修改';
    $('mcp-cancel').style.display = '';
    hideMsg($('mcp-msg'));
    window.scrollTo(0, $('mcp-form-title').getBoundingClientRect().top + window.pageYOffset - 20);
  }

  function saveServer() {
    var name = $('mcp-name').value.trim();
    var base = $('mcp-base').value.trim();
    var token = $('mcp-token').value.trim();
    var isEdit = editingServerId !== null;

    hideMsg($('mcp-msg'));
    $('mcp-saved').style.display = 'none';
    if (!name) { mcpMsg('err', '服务器名称必填'); return; }
    if (!/^https?:\/\//i.test(base)) { mcpMsg('err', 'Base URL 必须以 http:// 或 https:// 开头'); return; }
    if (!isEdit && !token) { mcpMsg('err', '新建时必须填写 Bearer 令牌'); return; }

    var payload = { name: name, base_url: base };
    if (token) payload.auth_token = token;

    var url = isEdit ? (CFG.mcpApi + '/servers/' + editingServerId) : (CFG.mcpApi + '/servers');
    $('mcp-save').disabled = true;
    CCAuth.authFetch(url, {
      method: isEdit ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(toJson).then(function (res) {
      if (res.status === 200 || res.status === 201) {
        $('mcp-saved').style.display = 'inline';
        resetServerForm();
        return loadMcp();
      }
      var msg = res.data && (res.data.message || res.data.error);
      if (res.status === 403) msg = '仅教师可管理 MCP 服务器';
      mcpMsg('err', msg || '保存失败，请稍后重试');
    }).catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
      mcpMsg('err', '网络错误，保存失败');
    }).then(function () { $('mcp-save').disabled = false; });
  }

  // =========================================================================
  // 页签切换（学生 / 资料 是独立页面）
  // =========================================================================

  var TABS = ['agent', 'skill', 'mcp'];

  function showTab(name) {
    if (TABS.indexOf(name) < 0) name = 'agent';
    Array.prototype.forEach.call(document.querySelectorAll('#config-tabs .tab[data-tab]'), function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-tab') === name);
    });
    TABS.forEach(function (t) {
      var panel = $('panel-' + t);
      if (panel) panel.classList.toggle('active', t === name);
    });
    if (('#' + name) !== location.hash) history.replaceState(null, '', '#' + name);
    if (name === 'mcp') loadMcp();
  }

  // =========================================================================
  // 加载
  // =========================================================================

  function loadAll() {
    return Promise.all([
      CCAuth.authFetch(CFG.agentsApi).then(toJson),
      CCAuth.authFetch(CFG.skillsApi).then(toJson),
      CCAuth.authFetch(CFG.materialsApi).then(toJson)
    ]).then(function (res) {
      agents = (res[0].data.agents || []);
      skills = (res[1].data.skills || []);
      materials = Array.isArray(res[2].data) ? res[2].data : [];
      renderAgents();
      renderSkills();
      renderAgentCandidates();
      if (editingAgentId !== null && !agentById(editingAgentId)) resetAgentForm();
      if (!skills.length && !$('skill-empty').style.display) renderSkills();
    }).catch(function (e) {
      if (String(e && e.message) === 'unauthorized') return;
      showMsg($('agent-msg'), 'err', '配置加载失败，请稍后重试');
    });
  }

  // ---- 事件绑定 ----
  $('agent-prompt').addEventListener('input', updatePromptCount);
  $('agent-save').addEventListener('click', saveAgent);
  $('agent-cancel').addEventListener('click', resetAgentForm);
  $('skill-save').addEventListener('click', saveSkill);
  $('skill-cancel').addEventListener('click', resetSkillForm);
  $('mcp-save').addEventListener('click', saveServer);
  $('mcp-cancel').addEventListener('click', resetServerForm);

  Array.prototype.forEach.call(document.querySelectorAll('#config-tabs .tab[data-tab]'), function (btn) {
    btn.addEventListener('click', function () { showTab(btn.getAttribute('data-tab')); });
  });
  window.addEventListener('hashchange', function () {
    showTab((location.hash || '').replace('#', ''));
  });

  // ---- 启动 ----
  resetAgentForm();
  resetSkillForm();
  resetServerForm();
  loadAll();
  showTab((location.hash || '').replace('#', ''));
})();