"""
后台自进化任务 (KohakuTerrarium 版)。
通过 NanobotKTController 调度专门的 Sub-agents 进行日志分析、画像提炼与审计。
"""
import threading
import asyncio
import logging
from core.database import SessionLocal
from core.legacy_adapter import SQLiteMemory, UnifiedProvider, NanobotKTController
from config import (
    API_KEY_01_CHAT, OPENAI_API_KEY, OPENAI_BASE_URL, LLM_PROVIDER,
    LLM_MODEL_SMART, LLM_MODEL_FAST, LLM_MODEL_REASONING,
    NEW_API_KEY, NEW_API_BASE_URL
)

logger = logging.getLogger("nanobot.evolution")

def _evo_timeout():
    from core.settings_service import settings
    return settings.get_int("new_api.timeout", 180)

# 进化锁：同一用户不可并发触发
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_evolution_running: set[str] = set()  # 当前正在跑进化的 user_id

def _get_lock(user_id: str) -> threading.Lock:
    with _locks_guard:
        if user_id not in _locks:
            _locks[user_id] = threading.Lock()
        return _locks[user_id]

def _build_provider() -> UnifiedProvider:
    """构建统一推理提供者 (DRY: 避免在多处重复初始化)"""
    model_map = {
        "smart": LLM_MODEL_SMART,
        "fast": LLM_MODEL_FAST,
        "reasoning": LLM_MODEL_REASONING
    }
    return UnifiedProvider(
        provider_type=LLM_PROVIDER, 
        api_key=NEW_API_KEY if LLM_PROVIDER == "new-api" else (OPENAI_API_KEY or (API_KEY_01_CHAT if LLM_PROVIDER == "dify" else "")),
        base_url=NEW_API_BASE_URL if LLM_PROVIDER == "new-api" else OPENAI_BASE_URL,
        model_map=model_map,
        timeout=_evo_timeout() if LLM_PROVIDER == "new-api" else None,
    )

def _run_async(coro):
    """
    安全执行异步协程：兼容 '已有事件循环' 和 '无事件循环' 两种环境。
    BUG-02 FIX: 避免在已有 event loop 的线程中调用 asyncio.run() 导致 RuntimeError。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    if loop and loop.is_running():
        # 已在 event loop 中 (e.g. FastAPI BackgroundTask)：创建新线程执行
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        # 没有 event loop (e.g. 直接调用)：安全使用 asyncio.run
        return asyncio.run(coro)

def evolution_task(user_id: str) -> None:
    """
    进化任务入口：委托给 KT 控制器执行。
    """
    lock = _get_lock(user_id)
    if not lock.acquire(blocking=False):
        logger.debug(f"Evolution already running for [{user_id}], skipping.")
        return

    _evolution_running.add(user_id)
    memory = None
    try:
        logger.info(f"══════ KT Evolution START [{user_id}] ══════")
        
        # 初始化 KT 编排链路 (针对进化任务)
        memory = SQLiteMemory()
        logs = memory.get_unprocessed_logs(user_id)
        if not logs:
            logger.info(f"══════ KT Evolution SKIP [{user_id}] ══════")
            return

        provider = _build_provider()
        controller = NanobotKTController(provider, memory)
        
        # BUG-02 FIX: 使用安全的异步执行器
        _run_async(controller.evolve(user_id))
        
        # BUG-05 FIX: 移除外部兜底 mark_logs_processed
        # controller.evolve() 内部已负责标记日志，此处不再重复标记
        # 避免跨 Session 实例操作 detached 对象
        
        logger.info(f"══════ KT Evolution SUCCESS [{user_id}] ══════")

    except Exception as e:
        logger.error(f"KT Evolution FAILED [{user_id}]: {e}", exc_info=True)
    finally:
        if memory is not None:
            memory.close()
        _evolution_running.discard(user_id)
        lock.release()

def model_scout_task() -> None:
    """
    系统级任务：搜集 AI 模型情报并更新注册表。
    """
    memory = None
    try:
        logger.info("══════ System Model Scout START ══════")
        
        memory = SQLiteMemory()
        provider = _build_provider()
        controller = NanobotKTController(provider, memory)
        
        scout_info = "搜集最新的 AI 大模型（LLM）版本发布动态"
        _run_async(controller.model_scout.run(scout_info, provider))
        
        logger.info("══════ System Model Scout SUCCESS ══════")

    except Exception as e:
        logger.error(f"System Model Scout FAILED: {e}", exc_info=True)
    finally:
        if memory is not None:
            memory.close()

if __name__ == "__main__":
    # 简单的本地测试入口
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "scout":
        model_scout_task()
    else:
        evolution_task("default_user")
