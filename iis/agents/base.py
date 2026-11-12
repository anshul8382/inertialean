from __future__ import annotations

from abc import ABC, abstractmethod

from iis.models.session import ResearchSession


class BaseAgent(ABC):
    agent_id: str

    @abstractmethod
    def run(self, session: ResearchSession) -> ResearchSession:
        ...
