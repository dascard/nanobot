"""
Local Python Sandbox for Nanobot.
Allows agents to execute analysis scripts against the local SQLite database.

Security Model:
- SQL queries: Run via sqlite3 directly (read-only by nature of SELECT)
- Python analysis: Run in a subprocess with restricted builtins, 
  network/filesystem isolation, and hard timeout.
"""
import sqlite3
import subprocess
import sys
import os
import json
import tempfile
import textwrap
import logging
from typing import Optional

try:
    import pandas as pd
except ImportError:
    pd = None

from config import DATABASE_URL

logger = logging.getLogger("nanobot.sandbox")

# ── Safety Constants ──
SANDBOX_TIMEOUT_SECONDS = 15
SANDBOX_MAX_OUTPUT_CHARS = 10000

# Dangerous modules that must NEVER be importable
_BLOCKED_MODULES = frozenset([
    "os", "sys", "shutil", "subprocess", "socket", "http", "urllib",
    "requests", "aiohttp", "pathlib", "glob", "signal", "ctypes",
    "importlib", "builtins", "__builtin__", "code", "codeop",
    "compileall", "multiprocessing", "threading", "asyncio",
    "webbrowser", "ftplib", "smtplib", "telnetlib", "xmlrpc",
])


class AnalysisSandbox:
    def __init__(self, db_path: str = ""):
        raw = db_path or DATABASE_URL
        self.db_path = os.path.abspath(raw.replace("sqlite:///", ""))

    def run_query(self, sql: str) -> str:
        """运行 SQL 查询并返回 Markdown 表格格式的结果 (只读)"""
        # Basic SQL injection guard: reject writes
        sql_upper = sql.strip().upper()
        if any(sql_upper.startswith(kw) for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH"]):
            return "SQL Error: Only SELECT queries are permitted in the sandbox."
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA query_only = ON")
                if pd is not None:
                    df = pd.read_sql_query(sql, conn)
                    result = df.to_markdown()
                else:
                    cursor = conn.execute(sql)
                    cols = [desc[0] for desc in cursor.description] if cursor.description else []
                    rows = cursor.fetchall()
                    if not cols:
                        result = "(no results)"
                    else:
                        header = " | ".join(cols)
                        separator = " | ".join(["---"] * len(cols))
                        body = "\n".join(" | ".join(str(v) for v in row) for row in rows)
                        result = f"{header}\n{separator}\n{body}"
                return result
        except Exception as e:
            return f"SQL Error: {str(e)}"

    def execute_python_analysis(self, code: str) -> str:
        """
        在完全隔离的 subprocess 中执行 Python 代码。
        
        Security features:
        - Runs in a separate process (no shared memory)
        - Blocked dangerous modules via custom import hook
        - Hard timeout (SIGKILL after SANDBOX_TIMEOUT_SECONDS)
        - Stdout/Stderr captured and truncated
        - Only exposes sqlite3, json, math, statistics, collections, re, datetime
        """
        # Build the sandboxed script
        sandbox_script = self._build_sandbox_script(code)
        
        try:
            result = subprocess.run(
                [sys.executable, "-c", sandbox_script],
                capture_output=True,
                text=True,
                timeout=SANDBOX_TIMEOUT_SECONDS,
                cwd=tempfile.gettempdir(),  # No access to project dir
                env=self._get_safe_env(),
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\n[STDERR]: {result.stderr}"
            
            if len(output) > SANDBOX_MAX_OUTPUT_CHARS:
                output = output[:SANDBOX_MAX_OUTPUT_CHARS] + "\n...[output truncated]"
            
            if result.returncode != 0 and not output.strip():
                output = f"Process exited with code {result.returncode}"
            
            return output if output.strip() else "(no output)"
            
        except subprocess.TimeoutExpired:
            return f"Execution Error: Code exceeded {SANDBOX_TIMEOUT_SECONDS}s timeout limit."
        except Exception as e:
            return f"Sandbox Error: {str(e)}"

    def _build_sandbox_script(self, user_code: str) -> str:
        """构建沙箱脚本：注入安全 import hook + 隔离命名空间执行用户代码。

        关键安全设计：用户代码通过 exec() 在受限 globals 命名空间中执行，
        无法访问 _setup_os、_setup_builtins 等内部变量。
        """
        db_path_escaped = self.db_path.replace("\\", "\\\\")
        blocked_json = json.dumps(list(_BLOCKED_MODULES))
        dangerous_json = json.dumps(["open", "input", "breakpoint", "eval", "exec", "compile", "__import__"])

        return textwrap.dedent(f'''\
import sys

# ── Phase 1: Install import blocker ──
_BLOCKED = set({blocked_json})

class _ImportBlocker:
    """Block dangerous module imports at the interpreter level."""
    def find_module(self, fullname, path=None):
        top_level = fullname.split(".")[0]
        if top_level in _BLOCKED:
            return self
        return None

    def load_module(self, fullname):
        raise ImportError(f"Module '{{fullname}}' is blocked in the analysis sandbox.")

sys.meta_path.insert(0, _ImportBlocker())

# ── Phase 2: Build safe import list (whitelist) ──
_safe_modules = {{}}
for _mod_name in ["sqlite3", "json", "math", "statistics", "collections", "re", "datetime"]:
    try:
        _safe_modules[_mod_name] = __import__(_mod_name)
    except Exception:
        pass

try:
    import pandas
    _safe_modules["pd"] = pandas
except Exception:
    _safe_modules["pd"] = None

# ── Phase 3: Open DB connection ──
_conn = None
_db_path = "{db_path_escaped}"
db_exists = False
try:
    # 仅做文件检查：用 open() 测试路径是否存在，用完即关
    with open(_db_path, "rb") as _f:
        db_exists = True
except Exception:
    db_exists = False

if db_exists:
    _conn = _safe_modules["sqlite3"].connect(_db_path)
    _conn.execute("PRAGMA query_only = ON")
else:
    print(f"[sandbox] DB not found at {{_db_path}} — sqlite3 available but no connection opened.")

# ── Phase 4: Build restricted execution namespace ──
_safe_ns = dict(_safe_modules)
_safe_ns["conn"] = _conn
# 移除危险 builtins，只保留安全子集
import builtins as _b
_safe_builtins = {{
    k: getattr(_b, k) for k in dir(_b)
    if not k.startswith("_") and k not in {dangerous_json}
}}
# 补充必要的 builtins
for _k in ["True", "False", "None", "int", "float", "str", "list", "dict",
           "tuple", "set", "bool", "bytes", "len", "range", "enumerate",
           "zip", "map", "filter", "sorted", "reversed", "sum", "min", "max",
           "abs", "round", "type", "isinstance", "hasattr", "getattr",
           "print", "format", "iter", "next", "slice", "Exception",
           "ValueError", "TypeError", "KeyError", "IndexError", "ZeroDivisionError"]:
    _safe_builtins[_k] = getattr(_b, _k)
_safe_ns["__builtins__"] = _safe_builtins

# ── Phase 5: Execute user code in isolated namespace ──
try:
    exec({json.dumps(user_code)}, _safe_ns)
except Exception as _e:
    print(f"Execution Error: {{type(_e).__name__}}: {{_e}}")
finally:
    if _conn is not None:
        _conn.close()
''')

    @staticmethod
    def _get_safe_env() -> dict:
        """Create a minimal environment for the subprocess."""
        safe = {}
        # Only copy essential env vars
        for key in ["PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE"]:
            if key in os.environ:
                safe[key] = os.environ[key]
        # Prevent Python from importing user site-packages configs
        safe["PYTHONDONTWRITEBYTECODE"] = "1"
        safe["PYTHONNOUSERSITE"] = "1"
        return safe
