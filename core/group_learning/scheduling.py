"""群学习调度的代码所有策略。"""

from __future__ import annotations

from dataclasses import dataclass

from core.registry import RegistryBuilder, RegistrySnapshot


@dataclass(frozen=True, slots=True)
class GroupLearningSchedulePolicy:
    """白名单调度、增量加载和消息预算的确定性边界。"""

    policy_id: str
    version: str
    default_interval_minutes: int
    min_interval_minutes: int
    max_interval_minutes: int
    default_window_hours: int
    min_window_hours: int
    max_window_hours: int
    context_message_limit: int
    min_new_messages: int
    max_new_messages: int
    job_schedule_policy_id: str

    @property
    def registry_namespace(self) -> str:
        return "group_learning_schedule_policy"

    @property
    def registry_id(self) -> str:
        return self.policy_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "default_interval_minutes": (
                self.default_interval_minutes
            ),
            "min_interval_minutes": self.min_interval_minutes,
            "max_interval_minutes": self.max_interval_minutes,
            "default_window_hours": self.default_window_hours,
            "min_window_hours": self.min_window_hours,
            "max_window_hours": self.max_window_hours,
            "context_message_limit": self.context_message_limit,
            "min_new_messages": self.min_new_messages,
            "max_new_messages": self.max_new_messages,
            "job_schedule_policy_id": self.job_schedule_policy_id,
        }

    def validate_interval(self, value: object) -> int:
        if type(value) is not int:
            raise ValueError("interval_minutes 必须是整数")
        if not self.min_interval_minutes <= value <= (
            self.max_interval_minutes
        ):
            raise ValueError("interval_minutes 超出群学习策略范围")
        return value

    def validate_window(self, value: object) -> int:
        if type(value) is not int:
            raise ValueError("window_hours 必须是整数")
        if not self.min_window_hours <= value <= self.max_window_hours:
            raise ValueError("window_hours 超出群学习策略范围")
        return value


def _build_schedule_policy_registry(
) -> RegistrySnapshot[GroupLearningSchedulePolicy]:
    builder = RegistryBuilder[GroupLearningSchedulePolicy](
        "group_learning_schedule_policy"
    )
    builder.register(
        GroupLearningSchedulePolicy(
            policy_id="group_learning.schedule.v1",
            version="1.0.0",
            default_interval_minutes=1440,
            min_interval_minutes=15,
            max_interval_minutes=10080,
            default_window_hours=24,
            min_window_hours=1,
            max_window_hours=720,
            context_message_limit=20,
            min_new_messages=3,
            max_new_messages=500,
            job_schedule_policy_id="background.long.v1",
        )
    )
    return builder.freeze()


GROUP_LEARNING_SCHEDULE_POLICY_REGISTRY = (
    _build_schedule_policy_registry()
)
GROUP_LEARNING_SCHEDULE_POLICY = (
    GROUP_LEARNING_SCHEDULE_POLICY_REGISTRY.require(
        "group_learning.schedule.v1"
    )
)


__all__ = [
    "GROUP_LEARNING_SCHEDULE_POLICY",
    "GROUP_LEARNING_SCHEDULE_POLICY_REGISTRY",
    "GroupLearningSchedulePolicy",
]
