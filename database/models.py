"""Typed records shared by JARVIS modules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Memory:
    id: int
    content: str
    category: str
    confidence: float
    relevance: int
