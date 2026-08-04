"""KT Codex 账号与 OAuth 控制面到 Core Port 的适配器。"""

from __future__ import annotations

from collections.abc import Mapping

from core.model_provider.admin_runtime import (
    CodexAdminError,
    CodexAdminErrorCode,
)


def _account_error_code(message: str) -> CodexAdminErrorCode:
    if "不存在" in str(message):
        return CodexAdminErrorCode.ACCOUNT_NOT_FOUND
    return CodexAdminErrorCode.INVALID_ACCOUNT


class KtCodexAdminAdapter:
    def status(self) -> Mapping[str, object]:
        from nanobot_kt import codex_oauth_adapter

        return dict(codex_oauth_adapter.codex_status())

    def list_accounts(
        self,
        database: object,
    ) -> tuple[Mapping[str, object], ...]:
        from nanobot_kt import codex_accounts

        return tuple(
            dict(item) for item in codex_accounts.list_codex_account_views(database)
        )

    def update_account(
        self,
        account_id: str,
        *,
        name: str | None,
        enabled: bool | None,
        weight: int | None,
        database: object,
    ) -> Mapping[str, object]:
        from nanobot_kt import codex_accounts

        try:
            account = codex_accounts.update_codex_account(
                account_id,
                name=name,
                enabled=enabled,
                weight=weight,
                db=database,
            )
            return dict(codex_accounts.codex_account_public_view(account))
        except codex_accounts.CodexAccountError as exc:
            raise CodexAdminError(
                _account_error_code(str(exc)),
                str(exc),
            ) from exc

    def delete_account(self, account_id: str, *, database: object) -> bool:
        from nanobot_kt import codex_accounts

        try:
            return bool(codex_accounts.delete_codex_account(account_id, database))
        except codex_accounts.CodexAccountError as exc:
            raise CodexAdminError(
                _account_error_code(str(exc)),
                str(exc),
            ) from exc

    async def start_device_login(
        self,
        *,
        account_id: str,
        name: str,
        database: object,
    ) -> Mapping[str, object]:
        from nanobot_kt import codex_accounts, codex_oauth_adapter

        account = None
        created_account = False
        started = False
        try:
            if account_id:
                account = codex_accounts.get_codex_account(account_id, database)
                if account is None:
                    raise CodexAdminError(
                        CodexAdminErrorCode.ACCOUNT_NOT_FOUND,
                        "Codex 账号不存在",
                    )
            else:
                account = codex_accounts.create_codex_account(name, db=database)
                created_account = True
            result = dict(
                await codex_oauth_adapter.codex_device_login_manager.start(account.id)
            )
            started = True
            return result
        except CodexAdminError:
            raise
        except codex_accounts.CodexCredentialConfigurationError as exc:
            raise CodexAdminError(
                CodexAdminErrorCode.CREDENTIAL_UNAVAILABLE,
                str(exc),
            ) from exc
        except codex_accounts.CodexAccountError as exc:
            raise CodexAdminError(
                _account_error_code(str(exc)),
                str(exc),
            ) from exc
        except Exception as exc:
            raise CodexAdminError(
                CodexAdminErrorCode.UPSTREAM_FAILED,
                str(exc)[:300],
            ) from exc
        finally:
            if created_account and not started and account is not None:
                try:
                    codex_accounts.delete_codex_account(account.id, database)
                except Exception:
                    pass

    async def get_device_login(
        self,
        login_id: str,
    ) -> Mapping[str, object] | None:
        from nanobot_kt import codex_oauth_adapter

        result = await codex_oauth_adapter.codex_device_login_manager.get(login_id)
        return dict(result) if result is not None else None

    async def usage(self) -> Mapping[str, object]:
        from nanobot_kt import codex_oauth_adapter

        try:
            return dict(await codex_oauth_adapter.codex_usage())
        except Exception as exc:
            raise CodexAdminError(
                CodexAdminErrorCode.UPSTREAM_FAILED,
                str(exc)[:300],
            ) from exc


__all__ = ["KtCodexAdminAdapter"]
