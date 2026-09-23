"""In-memory chat sessions (one per widget visitor). Sessions expire 24 h after the last activity."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

SESSION_TTL = 24 * 3600
# the id is the only key to a visitor's cart/proposal, so client-chosen ids must be long enough not to be guessable
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


@dataclass
class Session:
    """Conversation state. `messages` are Anthropic message dicts (user/assistant turns incl. tool blocks)."""

    id: str
    lang: str = "ru"
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_products: list[dict[str, Any]] = field(default_factory=list)
    page_url: str | None = None
    sdk_session_id: str | None = None  # Claude Code session to resume (claude_code provider)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # serializes turns on one session: concurrent chat/confirm calls would interleave the LLM history
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionStore:
    """Dict-backed session registry with lazy expiry."""

    def __init__(self, ttl: int = SESSION_TTL) -> None:
        self._sessions: dict[str, Session] = {}
        self._ttl = ttl

    def get_or_create(self, session_id: str | None) -> Session:
        """Return the session for `session_id`, creating it (with that id if it is well-formed) when unknown.

        Keeping a client-supplied id lets the widget survive backend restarts without losing its cart link;
        ids shorter than 16 characters are replaced by a server-minted uuid4 (the widget always uses ours).
        """
        self._purge()
        sid = session_id if session_id and SESSION_ID_RE.match(session_id) else uuid.uuid4().hex
        session = self._sessions.get(sid)
        if session is None:
            session = Session(id=sid)
            self._sessions[sid] = session
        session.touch()
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def _purge(self) -> None:
        cutoff = time.time() - self._ttl
        for sid in [s for s, sess in self._sessions.items() if sess.updated_at < cutoff]:
            del self._sessions[sid]


session_store = SessionStore()
