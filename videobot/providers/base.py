"""Общий интерфейс видео-провайдера. Кнопки бота зовут его, не вендора."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskStatus:
    state: str
    percent: int = 0
    stage: str = ""
    url: str = ""
    error: str = ""
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.state in ("done", "COMPLETED", "SUCCEEDED")

    @property
    def failed(self) -> bool:
        return self.state in ("failed", "FAILED", "ERROR", "CANCELLED", "CANCELED")


class VideoProvider(ABC):
    name: str = ""

    @abstractmethod
    async def generate_reference_image(
        self,
        prompt: str,
        references: list[str] | None = None,
    ) -> str:
        """URL still."""

    @abstractmethod
    async def generate_video(
        self,
        prompt: str,
        start_frame: str | None,
        duration: int,
        aspect_ratio: str = "9:16",
    ) -> str:
        """Поставить задачу, вернуть task_id (fal request_id или Runway task)."""

    @abstractmethod
    async def poll_status(self, task_id: str) -> TaskStatus:
        """Нормализованный прогресс {percent, stage}."""

    @abstractmethod
    async def lip_sync(self, video_url: str, audio_url: str) -> str:
        """URL видео после lip-sync. Пустая строка — шаг не поддерживается."""
