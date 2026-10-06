"""The AI CLIs a player can run Maple Helper on, each with their own account: Claude Code, Codex, Antigravity (Gemini),
Grok Build, or Oh My Pi (Z.AI's GLM-5.3, Meta's Muse Spark)."""
from __future__ import annotations

from .base import Provider
from .claude import Claude
from .codex import Codex
from .gemini import Gemini
from .grok import Grok
from .omp import Muse, Zai

PROVIDERS: dict[str, Provider] = {"claude": Claude(), "codex": Codex(), "gemini": Gemini(), "grok": Grok(),
                                  "zai": Zai(), "muse": Muse()}
DEFAULT = "claude"


def get(name: str | None) -> Provider:
    """The provider by name; unknown or missing names (old settings) mean Claude."""
    return PROVIDERS.get(name or DEFAULT) or PROVIDERS[DEFAULT]
