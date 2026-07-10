class SchemaMigrationValidationError(RuntimeError):
    """迁移后的 schema 与已声明契约不一致。"""
