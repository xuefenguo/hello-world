#!/usr/bin/env python3
"""OpenClaw Desktop Orchestrator (V1+).

Features:
- health/status endpoint
- config read/write + connectivity test
- local/cloud chat completion + fallback
- session CRUD + export
- config snapshots + restore
- diagnostics export + basic logs
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

HOST = "0.0.0.0"
PORT = 8765
BASE_DIR = Path(__file__).resolve().parents[2]
WEB_PAGE = BASE_DIR / "apps" / "desktop-ui" / "status.html"
CONFIG_DIR = Path.home() / ".openclaw-desktop"
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = CONFIG_DIR / "data"
SESSIONS_FILE = DATA_DIR / "sessions.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DIAG_DIR = DATA_DIR / "diagnostics"
LOG_FILE = DATA_DIR / "app.log"

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {"name": "openclaw-desktop", "version": "0.2.0"},
    "chat": {
        "cloud_fallback_to_local": True,
        "max_history_messages": 40,
    },
    "providers": {
        "local": {
            "enabled": True,
            "model": "local-echo",
            "endpoint": "",
        },
        "cloud": {
            "enabled": False,
            "base_url": "https://api.openai.com/v1/chat/completions",
            "api_key": "",
            "model": "gpt-4o-mini",
            "timeout_s": 45,
        },
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(event: str, payload: dict[str, Any] | None = None) -> None:
    payload = payload or {}
    line = f"{now_iso()} | {event} | {json.dumps(payload, ensure_ascii=False)}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def ensure_paths() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    if not SESSIONS_FILE.exists():
        SESSIONS_FILE.write_text("[]", encoding="utf-8")
    if not LOG_FILE.exists():
        LOG_FILE.write_text("", encoding="utf-8")


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict[str, Any]:
    data = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return DEFAULT_CONFIG
    return data


def save_config(cfg: dict[str, Any]) -> None:
    save_json(CONFIG_FILE, cfg)


def load_sessions() -> list[dict[str, Any]]:
    data = load_json(SESSIONS_FILE, [])
    if not isinstance(data, list):
        return []
    return data


def save_sessions(items: list[dict[str, Any]]) -> None:
    save_json(SESSIONS_FILE, items)


def find_or_create_session(session_id: str | None, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    if session_id:
        for session in sessions:
            if session.get("id") == session_id:
                return session
    session = {
        "id": str(uuid4()),
        "title": "新会话",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "messages": [],
    }
    sessions.append(session)
    return session


def get_session_by_id(session_id: str, sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for session in sessions:
        if session.get("id") == session_id:
            return session
    return None


def sanitize_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")
    return clean or f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def list_snapshots() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for p in sorted(SNAPSHOT_DIR.glob("*.json"), reverse=True):
        items.append({"name": p.stem, "path": str(p), "updated_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat()})
    return items


def local_generate(messages: list[dict[str, str]], model: str) -> str:
    user_messages = [m.get("content", "") for m in messages if m.get("role") == "user"]
    latest = user_messages[-1] if user_messages else ""
    return f"[本地模型:{model}] 已收到：{latest}"


def cloud_generate(messages: list[dict[str, str]], cfg: dict[str, Any], model: str) -> str:
    providers = cfg.get("providers", {})
    cloud = providers.get("cloud", {}) if isinstance(providers, dict) else {}
    base_url = str(cloud.get("base_url", "")).strip()
    api_key = str(cloud.get("api_key", "")).strip()
    timeout_s = int(cloud.get("timeout_s", 45))
    if not base_url or not api_key:
        raise ValueError("云端模型未配置 base_url 或 api_key")

    payload = {"model": model, "messages": messages, "temperature": 0.7}
    req = urllib.request.Request(
        base_url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return str(body.get("choices", [{}])[0].get("message", {}).get("content", ""))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"云端调用失败: {exc}") from exc


def validate_provider(provider: str) -> bool:
    return provider in {"local", "cloud"}


def chat_generate(provider: str, model: str, messages: list[dict[str, Any]], cfg: dict[str, Any]) -> tuple[str, str]:
    if provider == "local":
        return local_generate(messages, model), "local"

    try:
        return cloud_generate(messages, cfg, model), "cloud"
    except Exception:
        fallback = bool(cfg.get("chat", {}).get("cloud_fallback_to_local", True))
        if not fallback:
            raise
        local_model = str(cfg.get("providers", {}).get("local", {}).get("model", "local-echo"))
        return local_generate(messages, local_model), "local-fallback"


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenClawOrchestrator/0.3"

    def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: bytes, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(
                {
                    "status": "ok",
                    "runtime": "running",
                    "version": "0.3.0",
                    "timestamp": now_iso(),
                    "config_path": str(CONFIG_FILE),
                    "sessions_path": str(SESSIONS_FILE),
                    "log_path": str(LOG_FILE),
                }
            )
            return

        if self.path == "/api/config":
            self._send_json({"config": load_config()})
            return

        if self.path == "/api/sessions":
            self._send_json({"sessions": load_sessions()})
            return

        if self.path.startswith("/api/sessions/") and self.path.endswith("/export"):
            session_id = self.path.split("/")[3]
            sessions = load_sessions()
            session = get_session_by_id(session_id, sessions)
            if not session:
                self._send_json({"error": "session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"session": session})
            return

        if self.path == "/api/snapshots":
            self._send_json({"snapshots": list_snapshots()})
            return

        if self.path == "/api/diagnostics/export":
            sessions = load_sessions()
            diag = {
                "generated_at": now_iso(),
                "health": {"status": "ok", "version": "0.3.0"},
                "config": load_config(),
                "session_count": len(sessions),
                "latest_sessions": [{"id": s.get("id"), "title": s.get("title")} for s in sessions[-5:]],
            }
            diag_name = f"diag_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            diag_path = DIAG_DIR / diag_name
            save_json(diag_path, diag)
            self._send_json({"ok": True, "diagnostic_path": str(diag_path), "diagnostic": diag})
            return

        if self.path in {"/", "/index.html", "/status"}:
            if WEB_PAGE.exists():
                self._send_html(WEB_PAGE.read_bytes())
            else:
                self._send_html(b"<h1>UI page not found</h1>", status=HTTPStatus.NOT_FOUND)
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/config":
            body = self._read_json()
            config = body.get("config")
            if not isinstance(config, dict):
                self._send_json({"error": "invalid config"}, status=HTTPStatus.BAD_REQUEST)
                return
            save_config(config)
            log_event("config_saved")
            self._send_json({"ok": True, "config": config})
            return

        if self.path == "/api/sessions":
            sessions = load_sessions()
            session = find_or_create_session(None, sessions)
            save_sessions(sessions)
            self._send_json({"ok": True, "session": session})
            return

        if self.path.startswith("/api/sessions/") and self.path.endswith("/rename"):
            session_id = self.path.split("/")[3]
            body = self._read_json()
            title = str(body.get("title", "")).strip()
            if not title:
                self._send_json({"error": "title is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            sessions = load_sessions()
            session = get_session_by_id(session_id, sessions)
            if not session:
                self._send_json({"error": "session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            session["title"] = title
            session["updated_at"] = now_iso()
            save_sessions(sessions)
            self._send_json({"ok": True, "session": session})
            return

        if self.path == "/api/snapshots":
            body = self._read_json()
            name = sanitize_name(str(body.get("name", "")))
            target = SNAPSHOT_DIR / f"{name}.json"
            save_json(target, load_config())
            self._send_json({"ok": True, "snapshot": {"name": name, "path": str(target)}})
            return

        if self.path.startswith("/api/snapshots/") and self.path.endswith("/restore"):
            name = self.path.split("/")[3]
            target = SNAPSHOT_DIR / f"{sanitize_name(name)}.json"
            if not target.exists():
                self._send_json({"error": "snapshot not found"}, status=HTTPStatus.NOT_FOUND)
                return
            config = load_json(target, DEFAULT_CONFIG)
            if not isinstance(config, dict):
                self._send_json({"error": "invalid snapshot"}, status=HTTPStatus.BAD_REQUEST)
                return
            save_config(config)
            self._send_json({"ok": True, "config": config})
            return

        if self.path == "/v1/config/test":
            body = self._read_json()
            provider = str(body.get("provider", "local"))
            config = load_config()
            try:
                if provider == "local":
                    local = config.get("providers", {}).get("local", {})
                    model = str(local.get("model", "local-echo"))
                    _ = local_generate([{"role": "user", "content": "ping"}], model)
                elif provider == "cloud":
                    cloud = config.get("providers", {}).get("cloud", {})
                    model = str(cloud.get("model", "gpt-4o-mini"))
                    _ = cloud_generate([{"role": "user", "content": "ping"}], config, model)
                else:
                    raise ValueError("unsupported provider")
            except Exception as exc:  # noqa: BLE001
                self._send_json({"ok": False, "error_message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True})
            return

        if self.path == "/v1/chat/completions":
            body = self._read_json()
            provider = str(body.get("provider", "local"))
            model = str(body.get("model", "local-echo"))
            session_id = body.get("session_id")
            messages = body.get("messages", [])

            if not validate_provider(provider):
                self._send_json({"error": "invalid provider"}, status=HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(messages, list):
                self._send_json({"error": "messages must be list"}, status=HTTPStatus.BAD_REQUEST)
                return

            config = load_config()
            sessions = load_sessions()
            session = find_or_create_session(str(session_id) if session_id else None, sessions)

            try:
                content, actual_provider = chat_generate(provider, model, messages, config)
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            assistant_msg = {"role": "assistant", "content": content}
            max_history = int(config.get("chat", {}).get("max_history_messages", 40))
            session_messages = (messages + [assistant_msg])[-max_history:]
            session["messages"] = session_messages
            session["updated_at"] = now_iso()
            if session.get("title") == "新会话":
                first_user = next((m.get("content") for m in messages if m.get("role") == "user"), "新会话")
                session["title"] = str(first_user)[:24]
            save_sessions(sessions)
            log_event("chat", {"session_id": session.get("id"), "provider": provider, "actual_provider": actual_provider})

            self._send_json(
                {
                    "session_id": session["id"],
                    "provider": provider,
                    "actual_provider": actual_provider,
                    "model": model,
                    "content": content,
                    "usage": {
                        "prompt_tokens": sum(len(str(m.get("content", ""))) for m in messages),
                        "completion_tokens": len(content),
                    },
                }
            )
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.startswith("/api/sessions/"):
            session_id = self.path.split("/")[3]
            sessions = load_sessions()
            new_sessions = [s for s in sessions if s.get("id") != session_id]
            if len(new_sessions) == len(sessions):
                self._send_json({"error": "session not found"}, status=HTTPStatus.NOT_FOUND)
                return
            save_sessions(new_sessions)
            self._send_json({"ok": True})
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    ensure_paths()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"OpenClaw Orchestrator listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
