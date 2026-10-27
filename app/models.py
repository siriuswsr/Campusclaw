"""作业流程数据层：4 张业务表 + 1 张审计表的表结构定义。

本仓库不使用 ORM（认证是 JWT、数据访问一律走 app/db.py 的 pymysql），因此
「模型」在这里就是表结构本身：DDL 是单一事实来源，由 scripts/init_db.py 在
每个容器启动时幂等执行（docker-entrypoint.sh → `python scripts/init_db.py`）。

字段类型 / 索引 / 唯一约束与 openspec/changes/homework-flow/specs 一一对应：

    assignments 1─n submissions 1─1 grades
                                 └─n wrong_questions
    所有写入接口都会往 audit_log 落一行（app/audit.py）

注意：assignments 在第 4 课是占位表 (class_id, title, due_date)，表名相同会让
CREATE TABLE IF NOT EXISTS 整条跳过；init_db.py 里先删旧结构再建新结构。
"""

_ASSIGNMENTS_DDL = """
CREATE TABLE IF NOT EXISTS assignments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    class_id INT NOT NULL,
    teacher_id INT NOT NULL,
    title VARCHAR(200) NOT NULL,
    body TEXT NOT NULL,
    subject VARCHAR(20) NOT NULL,
    deadline DATETIME NOT NULL,
    status VARCHAR(20) DEFAULT 'published',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_assignments_class_id (class_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_SUBMISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS submissions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    assignment_id INT NOT NULL,
    student_id INT NOT NULL,
    class_id INT NOT NULL,
    answer_text TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    error_msg VARCHAR(200),
    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    graded_at DATETIME,
    INDEX idx_submissions_assignment (assignment_id),
    INDEX idx_submissions_student (student_id),
    UNIQUE KEY uix_assignment_student (assignment_id, student_id),
    CONSTRAINT fk_submissions_assignment FOREIGN KEY (assignment_id) REFERENCES assignments(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_GRADES_DDL = """
CREATE TABLE IF NOT EXISTS grades (
    id INT AUTO_INCREMENT PRIMARY KEY,
    submission_id INT NOT NULL UNIQUE,
    ai_score INT,
    ai_comment TEXT,
    ai_citations JSON,
    final_score INT,
    final_comment TEXT,
    graded_by INT,
    CONSTRAINT fk_grades_submission FOREIGN KEY (submission_id) REFERENCES submissions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_WRONG_QUESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS wrong_questions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    submission_id INT NOT NULL,
    class_id INT NOT NULL,
    subject VARCHAR(20) NOT NULL,
    question_text TEXT NOT NULL,
    student_answer TEXT NOT NULL,
    wrong_reason TEXT,
    explanation TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_wrong_student (student_id),
    CONSTRAINT fk_wrong_submission FOREIGN KEY (submission_id) REFERENCES submissions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_AUDIT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    class_id INT,
    role VARCHAR(20),
    action VARCHAR(50) NOT NULL,
    target_id INT,
    fields JSON,
    ts DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_audit_user (user_id),
    INDEX idx_audit_action (action),
    INDEX idx_audit_ts (ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# 建表顺序即依赖顺序：assignments → submissions → grades / wrong_questions。
# CREATE TABLE IF NOT EXISTS 是幂等的，重复执行无副作用。
HOMEWORK_SCHEMA = [
    _ASSIGNMENTS_DDL,
    _SUBMISSIONS_DDL,
    _GRADES_DDL,
    _WRONG_QUESTIONS_DDL,
    _AUDIT_LOG_DDL,
]

# 删表（init_db.py --force 用）：子表在前，父表在后，否则外键挡住 DROP。
HOMEWORK_TABLES = [
    "wrong_questions",
    "grades",
    "submissions",
    "audit_log",
    "assignments",
]


# ---------------------------------------------------------------------------
# 辅导 Agent 与技能（提示词 C）—— 同样用原生 DDL，init_db.py 幂等执行。
#
#   tutor_agents  1─n tutor_agent_skills n─1 skills
#                1─n tutor_agent_materials n─1 materials
#   solve_sessions.agent_id → tutor_agents.id (nullable)
#
# skills 与 tutor_agents 都由 (class_id, name) 唯一 —— 班级是数据边界。
# 关联表的两个外键均为 ON DELETE CASCADE：删 agent/skill/材料时自动清关联。
# ---------------------------------------------------------------------------

_SKILLS_DDL = """
CREATE TABLE IF NOT EXISTS skills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    class_id INT NOT NULL,
    name VARCHAR(32) NOT NULL,
    trigger_text VARCHAR(120) NOT NULL,
    description TEXT NOT NULL,
    tools_json VARCHAR(200) NOT NULL DEFAULT '[]',
    source_text TEXT NOT NULL,
    subject_text VARCHAR(64) NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    seed_key VARCHAR(64) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_skills_class_id (class_id),
    UNIQUE KEY uq_skill_class_name (class_id, name),
    CONSTRAINT fk_skills_class FOREIGN KEY (class_id) REFERENCES classes(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_TUTOR_AGENTS_DDL = """
CREATE TABLE IF NOT EXISTS tutor_agents (
    id INT AUTO_INCREMENT PRIMARY KEY,
    class_id INT NOT NULL,
    name VARCHAR(64) NOT NULL,
    subject VARCHAR(32) NULL,
    prompt TEXT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tutor_agents_class_id (class_id),
    UNIQUE KEY uq_agent_class_name (class_id, name),
    CONSTRAINT fk_tutor_agents_class FOREIGN KEY (class_id) REFERENCES classes(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_TUTOR_AGENT_SKILLS_DDL = """
CREATE TABLE IF NOT EXISTS tutor_agent_skills (
    agent_id INT NOT NULL,
    skill_id INT NOT NULL,
    PRIMARY KEY (agent_id, skill_id),
    CONSTRAINT fk_tas_agent FOREIGN KEY (agent_id) REFERENCES tutor_agents(id) ON DELETE CASCADE,
    CONSTRAINT fk_tas_skill FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_TUTOR_AGENT_MATERIALS_DDL = """
CREATE TABLE IF NOT EXISTS tutor_agent_materials (
    agent_id INT NOT NULL,
    material_id INT NOT NULL,
    PRIMARY KEY (agent_id, material_id),
    CONSTRAINT fk_tam_agent FOREIGN KEY (agent_id) REFERENCES tutor_agents(id) ON DELETE CASCADE,
    CONSTRAINT fk_tam_material FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_SOLVE_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS solve_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    agent_id INT NULL,
    student_id INT NOT NULL,
    class_id INT NOT NULL,
    status VARCHAR(20) DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_solve_sessions_agent (agent_id),
    INDEX idx_solve_sessions_student (student_id),
    CONSTRAINT fk_solve_sessions_agent FOREIGN KEY (agent_id) REFERENCES tutor_agents(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# 会话消息（提示词 F）：一次提问/回答落一行，历史窗口取最近 6 条。
_SOLVE_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS solve_messages (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT NOT NULL,
    role VARCHAR(16) NOT NULL,
    content MEDIUMTEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_solve_messages_session (session_id, id),
    CONSTRAINT fk_solve_messages_session FOREIGN KEY (session_id) REFERENCES solve_sessions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# 建表顺序即依赖顺序：skills / tutor_agents → 两张关联表 → solve_sessions → solve_messages。
TUTOR_SCHEMA = [
    _SKILLS_DDL,
    _TUTOR_AGENTS_DDL,
    _TUTOR_AGENT_SKILLS_DDL,
    _TUTOR_AGENT_MATERIALS_DDL,
    _SOLVE_SESSIONS_DDL,
    _SOLVE_MESSAGES_DDL,
]

# 删表顺序（--force 用）：子表在前、父表在后，否则外键挡住 DROP。
TUTOR_TABLES = [
    "solve_messages",
    "solve_sessions",
    "tutor_agent_materials",
    "tutor_agent_skills",
    "tutor_agents",
    "skills",
]


# ---------------------------------------------------------------------------
# MCP 服务器登记与工具发现缓存（提示词 B）—— 原生 DDL，init_db.py 幂等执行。
#
#   mcp_servers 1─n mcp_tools
#
# auth_token 只存服务端，任何接口响应都不得带回（列表只给 has_auth 布尔值）。
# 班级是数据边界：登记/读取/连接一律带 class_id。
# ---------------------------------------------------------------------------

_MCP_SERVERS_DDL = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    class_id INT NOT NULL,
    name VARCHAR(64) NOT NULL,
    base_url VARCHAR(512) NOT NULL,
    auth_token VARCHAR(255) NOT NULL,
    connected TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_mcp_servers_class (class_id),
    UNIQUE KEY uq_mcp_server_class_name (class_id, name),
    CONSTRAINT fk_mcp_servers_class FOREIGN KEY (class_id) REFERENCES classes(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_MCP_TOOLS_DDL = """
CREATE TABLE IF NOT EXISTS mcp_tools (
    id INT AUTO_INCREMENT PRIMARY KEY,
    server_id INT NOT NULL,
    name VARCHAR(64) NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_mcp_tools_server (server_id),
    UNIQUE KEY uq_mcp_tool_server_name (server_id, name),
    CONSTRAINT fk_mcp_tools_server FOREIGN KEY (server_id) REFERENCES mcp_servers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_MCP_TOOL_CALLS_DDL = """
CREATE TABLE IF NOT EXISTS mcp_tool_calls (
    id INT AUTO_INCREMENT PRIMARY KEY,
    class_id INT NOT NULL,
    server_id INT NULL,
    server_name VARCHAR(64) NOT NULL,
    tool_name VARCHAR(64) NOT NULL,
    tool_ref VARCHAR(160) NOT NULL,
    status ENUM('ok','denied','error') NOT NULL,
    user_id INT NOT NULL,
    called_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_mcp_calls_class_time (class_id, called_at),
    INDEX idx_mcp_calls_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

MCP_SCHEMA = [
    _MCP_SERVERS_DDL,
    _MCP_TOOLS_DDL,
    _MCP_TOOL_CALLS_DDL,
]

# 删表顺序（--force 用）：子表在前、父表在后。
MCP_TABLES = [
    "mcp_tool_calls",
    "mcp_tools",
    "mcp_servers",
]