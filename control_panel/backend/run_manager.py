"""
run_manager.py — Lancement des runs de la pipeline depuis le panneau + capture des logs.

Un run = `process_channel(to_pipeline_config(channel))` exécuté dans un thread. Les `print`
de la pipeline (y compris ceux de ses threads de rendu) sont capturés via redirect_stdout et
accumulés dans le buffer du run. La concurrence est limitée à 1 run à la fois (le rendu vidéo
est lourd) pour éviter l'entrelacement des logs. Suivi par polling (GET /api/runs/{id}).
"""

import io
import sys
import uuid
import threading
import contextlib
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from content_creator.config.schema import to_pipeline_config
from control_panel.backend.store import get_channel


class RunInfo(BaseModel):
    id: str
    channel: str
    status: str                     # queued | running | done | error
    started_at: str
    finished_at: str | None = None
    title: str | None = None
    video: str | None = None        # chemin local du .mp4 final
    gcs_url: str | None = None      # URL publique GCS
    error: str | None = None
    logs: str = ""                  # logs concaténés (stdout capturé)


class _RunBuffer(io.TextIOBase):
    """Sink de stdout : accumule dans une liste ET renvoie vers le vrai stdout (miroir)."""

    def __init__(self, sink: list[str], mirror) -> None:
        self._sink = sink
        self._mirror = mirror

    def write(self, s: str) -> int:  # type: ignore[override]
        if s:
            self._sink.append(s)
            try:
                self._mirror.write(s)
            except Exception:
                pass
        return len(s)


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}
        self._logs: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)  # runs sérialisés

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def start(self, channel_name: str) -> RunInfo:
        run_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._logs[run_id] = []
            self._runs[run_id] = {
                "id": run_id, "channel": channel_name, "status": "queued",
                "started_at": self._now(), "finished_at": None, "title": None,
                "video": None, "gcs_url": None, "error": None,
            }
        self._executor.submit(self._run, run_id, channel_name)
        return self.get(run_id)  # type: ignore[return-value]

    def _run(self, run_id: str, channel_name: str) -> None:
        info = self._runs[run_id]
        info["status"] = "running"
        buf = _RunBuffer(self._logs[run_id], sys.__stdout__)
        try:
            channel = get_channel(channel_name)
            if channel is None:
                raise ValueError(f"channel inconnu: {channel_name}")
            pipeline_config = to_pipeline_config(channel)
            # Import tardif : process_channel importe la pipeline (et ses deps lourdes).
            from content_creator.pipelines.pipeline_agentic import process_channel
            with contextlib.redirect_stdout(buf):
                result = process_channel(pipeline_config)
            info["title"] = result.get("title")
            info["video"] = result.get("video")
            info["gcs_url"] = result.get("gcs_url")
            if result.get("ok"):
                info["status"] = "done"
            else:
                info["status"] = "error"
                info["error"] = result.get("error") or "échec du run"
        except Exception as e:
            info["status"] = "error"
            info["error"] = str(e)
            self._logs[run_id].append("\n" + traceback.format_exc())
        finally:
            info["finished_at"] = self._now()

    def _to_info(self, run_id: str) -> RunInfo:
        info = dict(self._runs[run_id])
        info["logs"] = "".join(self._logs.get(run_id, []))
        return RunInfo(**info)

    def get(self, run_id: str) -> RunInfo | None:
        with self._lock:
            if run_id not in self._runs:
                return None
            return self._to_info(run_id)

    def list(self) -> list[RunInfo]:
        with self._lock:
            return [self._to_info(rid) for rid in self._runs]


# Singleton partagé par le router.
manager = RunManager()
