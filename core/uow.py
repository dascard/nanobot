"""旧导入路径兼容；新代码应从 ``core.db`` 获取 UnitOfWork。"""

from core.db.uow import UnitOfWork

__all__ = ["UnitOfWork"]
