"""Config, memory and the Claude API connection."""
import json
import os
import re
import threading

import anthropic

from .paths import CONFIG_DIR, DATA_DIR

CONFIG_FILE = CONFIG_DIR / "config.json"
KEY_FILE = CONFIG_DIR / "api_key"
HISTORY_FILE = DATA_DIR / "history.json"

DEFAULTS = {
    "model": "claude-opus-5",
    "effort": "low",          # low | medium | high — chat doesn't need deep thinking
    "size": 1.0,              # pet scale
    "wander": True,           # hop around the bottom of the screen now and then
    "sleep_after": 300,       # seconds without attention before napping
    "history_messages": 40,   # how much past chat is sent with each message
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
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except FileNotFoundError:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
    except (OSError, ValueError) as e:
        print(f"shisa: ignoring bad config ({e})")
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

    def _context(self):
        msgs = self.history[-int(self.cfg["history_messages"]):]
        while msgs and msgs[0]["role"] != "user":
            msgs = msgs[1:]
        return msgs

    # --- talking -----------------------------------------------------------

    def _client(self):
        if self.client is None:
            key = os.environ.get("ANTHROPIC_API_KEY") or saved_key()
            self.client = anthropic.Anthropic(api_key=key)
        return self.client

    def reset_client(self):
        self.client = None

    def ask(self, text, emit):
        """Stream a reply in a background thread.

        emit(kind, value) is called from that thread with kind in
        mood / delta / done / error; the caller hops back to the UI thread.
        """
        threading.Thread(target=self._ask, args=(text, emit), daemon=True).start()

    def _ask(self, text, emit):
        def show(mood, chunk):
            if mood:
                emit("mood", mood if mood in MOODS else "neutral")
            if chunk:
                emit("delta", chunk)

        with self.lock:
            self.history.append({"role": "user", "content": text})
            split = _MoodSplitter()
            reply = ""
            error = None
            try:
                extra = {}
                if self.cfg.get("effort"):
                    extra["output_config"] = {"effort": self.cfg["effort"]}
                with self._client().messages.stream(
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
                        reply += chunk
                        show(*split.feed(chunk))
                    final = stream.get_final_message()
                show(*split.flush())
                if final.stop_reason == "refusal":
                    error = "refusal"
            except anthropic.AuthenticationError:
                error = "auth"
            except anthropic.PermissionDeniedError:
                error = "permission"
            except anthropic.NotFoundError:
                error = "model"
            except anthropic.RateLimitError:
                error = "ratelimit"
            except anthropic.APIStatusError as e:
                msg = str(getattr(e, "message", e))
                error = "credit" if "credit balance" in msg.lower() else f"api:{msg}"
            except anthropic.APIConnectionError:
                error = "offline"
            except Exception as e:  # never leave the pet stuck "thinking"
                error = f"api:{e}"

            if error:
                self.history.pop()
                emit("error", error)
                return
            self.history.append({"role": "assistant", "content": reply.strip() or "[neutral] ..."})
            self._save_history()
            emit("done", None)
