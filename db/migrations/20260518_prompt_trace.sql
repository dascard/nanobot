-- PromptManager 与工具调用追踪表。
-- SQLite 生产环境可直接执行；SQLAlchemy init_db/create_all 会在新库自动创建同名表。

CREATE TABLE IF NOT EXISTS agent_runs (
  run_id TEXT PRIMARY KEY,
  trace_id TEXT DEFAULT '',
  session_id TEXT DEFAULT '',
  user_id TEXT DEFAULT '',
  chat_type TEXT DEFAULT '',
  group_id TEXT DEFAULT '',
  run_type TEXT DEFAULT 'chat',
  prompt_mode TEXT DEFAULT 'legacy',
  prompt_key TEXT DEFAULT '',
  model TEXT DEFAULT '',
  status TEXT DEFAULT 'running',
  input_preview TEXT DEFAULT '',
  output_preview TEXT DEFAULT '',
  error TEXT DEFAULT '',
  latency_ms INTEGER DEFAULT 0,
  meta_json TEXT DEFAULT '{}',
  started_at TIMESTAMP,
  finished_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_trace_id ON agent_runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_session_id ON agent_runs(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_id ON agent_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_prompt_key ON agent_runs(prompt_key);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_chat_type ON agent_runs(chat_type);
CREATE INDEX IF NOT EXISTS idx_agent_runs_group_id ON agent_runs(group_id);

CREATE TABLE IF NOT EXISTS tool_calls (
  tool_call_id TEXT PRIMARY KEY,
  trace_id TEXT DEFAULT '',
  run_id TEXT DEFAULT '',
  tool_name TEXT DEFAULT '',
  args_json TEXT DEFAULT '{}',
  result_preview TEXT DEFAULT '',
  status TEXT DEFAULT 'running',
  latency_ms INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  started_at TIMESTAMP,
  finished_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_trace_id ON tool_calls(trace_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run_id ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_status ON tool_calls(status);
CREATE INDEX IF NOT EXISTS idx_tool_calls_started_at ON tool_calls(started_at);

CREATE TABLE IF NOT EXISTS prompt_render_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT DEFAULT '',
  run_id TEXT DEFAULT '',
  prompt_key TEXT DEFAULT '',
  mode TEXT DEFAULT 'preview',
  variables_json TEXT DEFAULT '{}',
  rendered_preview TEXT DEFAULT '',
  token_estimate INTEGER DEFAULT 0,
  warnings_json TEXT DEFAULT '[]',
  error TEXT DEFAULT '',
  created_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_trace_id ON prompt_render_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_run_id ON prompt_render_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_prompt_key ON prompt_render_logs(prompt_key);
CREATE INDEX IF NOT EXISTS idx_prompt_render_logs_created_at ON prompt_render_logs(created_at);

CREATE TABLE IF NOT EXISTS prompt_file_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt_key TEXT DEFAULT '',
  backup_name TEXT DEFAULT '',
  content_hash TEXT DEFAULT '',
  operator TEXT DEFAULT '',
  note TEXT DEFAULT '',
  created_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_prompt_file_versions_prompt_key ON prompt_file_versions(prompt_key);
CREATE INDEX IF NOT EXISTS idx_prompt_file_versions_backup_name ON prompt_file_versions(backup_name);

-- LLM API 请求记录
CREATE TABLE IF NOT EXISTS llm_api_request_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT DEFAULT '',
  run_id TEXT DEFAULT '',
  source TEXT DEFAULT '',
  provider TEXT DEFAULT '',
  model TEXT DEFAULT '',
  url TEXT DEFAULT '',
  method TEXT DEFAULT 'POST',
  request_json TEXT DEFAULT '{}',
  request_preview TEXT DEFAULT '',
  headers_json TEXT DEFAULT '{}',
  status TEXT DEFAULT 'created',
  response_status INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  created_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_llm_api_request_logs_trace_id ON llm_api_request_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_llm_api_request_logs_run_id ON llm_api_request_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_llm_api_request_logs_source ON llm_api_request_logs(source);
CREATE INDEX IF NOT EXISTS idx_llm_api_request_logs_created_at ON llm_api_request_logs(created_at);
