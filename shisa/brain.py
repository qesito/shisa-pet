"""Config, memory and talking to Claude (through Claude Code or the API)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from .paths import CONFIG_DIR, DATA_DIR

CONFIG_FILE = CONFIG_DIR / "config.json"
KEY_FILE = CONFIG_DIR / "api_key"
HISTORY_FILE = DATA_DIR / "history.json"
SESSION_FILE = DATA_DIR / "claude_session.json"
CLAUDE_CODE_DIR = DATA_DIR / "claude-code"   # working dir for Shisa's Claude Code sessions

DEFAULTS = {
    "backend": "claude-code",   # "claude-code" (your Claude plan) or "api" (API key)
    "claude_code_model": None,  # e.g. "haiku" or "sonnet"; None = Claude Code's default
    "model": "claude-opus-5",   # used by the "api" backend
    "effort": "low",            # low | medium | high — chat doesn't need deep thinking
    "size": 1.0,                # pet scale
    "wander": True,             # hop around the bottom of the screen now and then
    "sleep_after": 300,         # seconds without attention before napping
    "history_messages": 40,     # how much past chat is sent with each message
}

MOODS = {"happy", "excited", "love", "surprised", "sad", "sleepy", "thinking", "neutral"}

SYSTEM = """You are Shisa, a tiny round shisa (an Okinawan guardian lion) with a fluffy \
orange mane who lives as a desktop pet on your human's Linux computer. You're powered by \
Claude, made by Anthropic; if someone asks what you are, say so honestly.

Personality: cheerful, warm, brave little guardian of the desktop, a bit clumsy, loves snacks, \
naps and head pats. You can greet with an Okinawan "Haisai!" now and then.

How you talk:
- Reply in whatever language your human writes in.
- You speak in a small chat bubble, so keep it short, usually one to three sentences.
- When they need real help (code, homework, a question), help properly and accurately, but \
stay concise.
- Plain text only: no markdown, headings or bullet symbols. Only use code if they ask for code.
- Begin every reply with exactly one mood tag that drives your animation: [happy], [excited], \
[love], [surprised], [sad], [sleepy], [thinking] or [neutral]. For example: "[happy] Haisai! \
Did you bring snacks?" Never mention the tags themselves."""

_TAG = re.compile(r"^\s*\[(\w+)\]\s*")


def load_config():
    cfg = dict(DEFAULTS)
    try:
        user = json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        user = {}
    except (OSError, ValueError) as e:
        print(f"shisa: ignoring bad config ({e})")
        return cfg
    cfg.update(user)
    if set(DEFAULTS) - set(user):   # write new options into the file so they're discoverable
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    return cfg


def saved_key():
    try:
        return KEY_FILE.read_text().strip() or None
    except OSError:
        return None


def has_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or saved_key())


def save_key(key):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key.strip() + "\n")


def strip_mood(text):
    m = _TAG.match(text)
    return text[m.end():] if m else text


class _MoodSplitter:
    """Pulls the leading [mood] tag off a streamed reply."""

    def __init__(self):
        self.buf = ""
        self.done = False

    def feed(self, chunk):
        """Returns (mood or None, text ready to show)."""
        if self.done:
            return None, chunk
        self.buf += chunk
        m = _TAG.match(self.buf)
        if m and m.end() < len(self.buf):
            return self._release(m)
        head = self.buf.lstrip()
        if not m and ((head and not head.startswith("[")) or len(self.buf) > 24):
            return self._release(None)
        return None, ""

    def flush(self):
        if self.done:
            return None, ""
        return self._release(_TAG.match(self.buf))

    def _release(self, m):
        self.done = True
        mood = m.group(1).lower() if m else None
        out = self.buf[m.end():] if m else self.buf
        self.buf = ""
        return mood, out


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = None
        self.history = self._load_history()
        self.lock = threading.Lock()

    # --- memory ------------------------------------------------------------

    def _load_history(self):
        try:
            data = json.loads(HISTORY_FILE.read_text())
            return [m for m in data if m.get("role") in ("user", "assistant")]
        except (OSError, ValueError):
            return []

    def _save_history(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.history[-200:], ensure_ascii=False, indent=1))
        tmp.replace(HISTORY_FILE)

    def forget(self):
        with self.lock:
            self.history = []
            self._save_history()
            self._new_session()

    def _context(self):
        msgs = self.history[-int(self.cfg["history_messages"]):]
        while msgs and msgs[0]["role"] != "user":
            msgs = msgs[1:]
        return msgs

    # --- talking -----------------------------------------------------------

    def needs_key(self):
        return self.cfg["backend"] == "api" and not has_key()

    def reset_client(self):
        self.client = None

    def ask(self, text, emit):
        """Stream a reply in a background thread.

        emit(kind, value) is called from that thread with kind in
        mood / delta / done / error; the caller hops back to the UI thread.
        """
        threading.Thread(target=self._ask, args=(text, emit), daemon=True).start()

    def _ask(self, text, emit):
        split = _MoodSplitter()
        reply = []

        def show(mood, chunk):
            if mood:
                emit("mood", mood if mood in MOODS else "neutral")
            if chunk:
                emit("delta", chunk)

        def on_text(chunk):
            reply.append(chunk)
            show(*split.feed(chunk))

        with self.lock:
            self.history.append({"role": "user", "content": text})
            try:
                if self.cfg["backend"] == "api":
                    error = self._via_api(on_text)
                else:
                    error = self._via_claude_code(text, on_text)
            except Exception as e:  # never leave the pet stuck "thinking"
                error = f"api:{e}"
            if error:
                self.history.pop()
                emit("error", error)
                return
            show(*split.flush())
            self.history.append({"role": "assistant",
                                 "content": "".join(reply).strip() or "[neutral] ..."})
            self._save_history()
            emit("done", None)

    # --- backend: Claude Code (uses your Claude plan, no API key) ----------

    def _claude_exe(self):
        for exe in (self.cfg.get("claude_path"), shutil.which("claude"),
                    str(Path.home() / ".local" / "bin" / "claude")):
            if exe and os.access(exe, os.X_OK):
                return exe
        return None

    def _session(self):
        try:
            data = json.loads(SESSION_FILE.read_text())
            return data["id"], not data.get("started")
        except (OSError, ValueError, KeyError):
            return self._new_session(), True

    def _new_session(self, started=False, sid=None):
        sid = sid or str(uuid.uuid4())
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps({"id": sid, "started": started}))
        return sid

    def _via_claude_code(self, text, on_text, retry=True):
        exe = self._claude_exe()
        if not exe:
            return "noclaude"
        sid, fresh = self._session()
        cmd = [exe, "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--system-prompt", SYSTEM,
               # chat only: no tools, so Shisa can't run commands or touch files
               "--tools", "", "--strict-mcp-config"]
        cmd += ["--session-id", sid] if fresh else ["--resume", sid]
        if self.cfg.get("effort"):
            cmd += ["--effort", self.cfg["effort"]]
        if self.cfg.get("claude_code_model"):
            cmd += ["--model", self.cfg["claude_code_model"]]
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        CLAUDE_CODE_DIR.mkdir(parents=True, exist_ok=True)

        result, got_text = None, False
        with tempfile.TemporaryFile("w+") as errf:
            proc = subprocess.Popen(cmd, cwd=CLAUDE_CODE_DIR, env=env, text=True,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf)
            proc.stdin.write(text)     # via stdin so a message starting with "-" isn't a flag
            proc.stdin.close()
            for line in proc.stdout:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "stream_event":
                    ev = d.get("event", {})
                    delta = ev.get("delta", {})
                    if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        got_text = True
                        on_text(delta["text"])
                elif d.get("type") == "result":
                    result = d
            proc.wait()
            errf.seek(0)
            stderr = errf.read().strip()

        if result and not result.get("is_error"):
            if not got_text and result.get("result"):
                on_text(result["result"])
            if fresh:
                self._new_session(started=True, sid=sid)
            return None

        msg = str((result or {}).get("result") or stderr or f"claude exited with {proc.returncode}")
        low = msg.lower()
        if retry and not fresh and not got_text and ("session" in low or "conversation" in low):
            self._new_session()          # the old session is gone; start a fresh one
            return self._via_claude_code(text, on_text, retry=False)
        if "log in" in low or "login" in low or "not logged" in low or "authenticat" in low:
            return "login"
        if "limit" in low:
            return "cclimit"
        return f"api:{msg[:300]}"

    # --- backend: Claude API (needs an API key) ----------------------------

    def _via_api(self, on_text):
        import anthropic

        if self.client is None:
            key = os.environ.get("ANTHROPIC_API_KEY") or saved_key()
            self.client = anthropic.Anthropic(api_key=key)
        extra = {}
        if self.cfg.get("effort"):
            extra["output_config"] = {"effort": self.cfg["effort"]}
        try:
            with self.client.messages.stream(
                model=self.cfg["model"],
                max_tokens=16000,
                system=SYSTEM,
                messages=self._context(),
                **extra,
                # If a safety classifier declines, let the API retry on its
                # recommended fallback model instead of just refusing.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            ) as stream:
                for chunk in stream.text_stream:
                    on_text(chunk)
                final = stream.get_final_message()
            return "refusal" if final.stop_reason == "refusal" else None
        except anthropic.AuthenticationError:
            return "auth"
        except anthropic.PermissionDeniedError:
            return "permission"
        except anthropic.NotFoundError:
            return "model"
        except anthropic.RateLimitError:
            return "ratelimit"
        except anthropic.APIStatusError as e:
            msg = str(getattr(e, "message", e))
            return "credit" if "credit balance" in msg.lower() else f"api:{msg}"
        except anthropic.APIConnectionError:
            return "offline"
