#!/usr/bin/env python3
"""
trace.py — Monitoring / observabilité de l'agent vidéo.

Pour chaque run, un Tracer écrit dans runs/run_<ts>/ :
  - console "director log" en direct (décisions + tools + résultats)
  - trace.jsonl : 1 ligne par événement (greppable / rejouable)
  - report.md   : récap des choix + tokens & coût + durée

Zéro dépendance externe.
"""

import os
import json
import time
from datetime import datetime

# Tarif anthropic/claude-opus-4-8 sur DeepInfra ($ / token)
PRICE_IN = 5.0 / 1_000_000
PRICE_OUT = 25.0 / 1_000_000


def _cell(v) -> str:
    """Nettoie une valeur pour une cellule de tableau Markdown."""
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


class Tracer:
    """Capture, affiche et persiste les décisions de l'agent."""

    def __init__(self, base_dir: str = "runs", run_id: str = None, label: str = None):
        run_id = run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.label = label or ""
        self.dir = os.path.join(base_dir, run_id)
        os.makedirs(self.dir, exist_ok=True)
        self.jsonl_path = os.path.join(self.dir, "trace.jsonl")
        self.report_path = os.path.join(self.dir, "report.md")
        self.events = []
        self.step = 0
        self.in_tokens = 0
        self.out_tokens = 0
        self.t0 = time.time()

    def _p(self, msg: str):
        """Print préfixé par le label (channel) pour des logs lisibles en parallèle."""
        prefix = f"[{self.label}] " if self.label else ""
        print(prefix + msg, flush=True)

    # --- écriture ---
    def _write(self, event: dict):
        event.setdefault("ts", datetime.now().isoformat())
        self.events.append(event)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # --- événements ---
    def start(self, script: str, skill: str, avatar: str):
        self._p("=" * 50)
        self._p(f"🎬 AGENT — skill={skill}  avatar={avatar}  trace={self.dir}")
        self._write({"type": "start", "skill": skill, "avatar": avatar, "script": script})

    def on_assistant(self, text: str, usage=None):
        """Raisonnement / décision du modèle + comptage tokens."""
        self.step += 1
        if usage is not None:
            self.in_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.out_tokens += getattr(usage, "completion_tokens", 0) or 0
        if text and text.strip():
            self._p(f"🧠 [step {self.step}] {text.strip()[:300]}")
        self._write({"type": "assistant", "step": self.step, "text": text or "",
                     "in_tokens": self.in_tokens, "out_tokens": self.out_tokens})

    def on_tool_call(self, name: str, args: dict) -> float:
        """Affiche l'appel de tool. Retourne un t0 à repasser à on_tool_result."""
        preview = ", ".join(f"{k}={str(v)[:50]!r}" for k, v in (args or {}).items())
        self._p(f"   → {name}({preview})")
        self._write({"type": "tool_call", "step": self.step, "tool": name, "args": args or {}})
        return time.time()

    def on_tool_result(self, name: str, ok: bool, summary: str, ms: float):
        icon = "✓" if ok else "✗"
        self._p(f"   {icon} {summary}  ({ms/1000:.1f}s)")
        self._write({"type": "tool_result", "step": self.step, "tool": name,
                     "ok": bool(ok), "summary": summary, "ms": round(ms)})

    # --- coût ---
    @property
    def cost(self) -> float:
        return self.in_tokens * PRICE_IN + self.out_tokens * PRICE_OUT

    # --- clôture ---
    def finish(self, final_video: str):
        wall = time.time() - self.t0
        self._write({"type": "end", "final_video": final_video,
                     "in_tokens": self.in_tokens, "out_tokens": self.out_tokens,
                     "cost_usd": round(self.cost, 4), "wall_s": round(wall, 1)})
        self._write_report(final_video, wall)
        self._p(f"💰 tokens {self.in_tokens}/{self.out_tokens} (~${self.cost:.4f}) · ⏱ {wall:.0f}s · 📄 {self.report_path}")
        if final_video:
            self._p(f"🎬 {final_video}")

    def _write_report(self, final_video: str, wall: float):
        lines = [
            "# Rapport de run\n",
            f"- **Vidéo finale** : `{final_video}`",
            f"- **Tokens** in/out : {self.in_tokens} / {self.out_tokens}",
            f"- **Coût orchestration** : ~${self.cost:.4f}",
            f"- **Durée totale** : {wall:.1f}s\n",
            "## Décisions de l'agent\n",
            "| Step | Type | Tool | Détail |",
            "|---|---|---|---|",
        ]
        for e in self.events:
            if e["type"] == "assistant" and e.get("text", "").strip():
                lines.append(f"| {e['step']} | 🧠 décision | | {_cell(e['text'][:160])} |")
            elif e["type"] == "tool_call":
                args = ", ".join(f"{k}={str(v)[:40]}" for k, v in (e.get("args") or {}).items())
                lines.append(f"| {e['step']} | → call | {_cell(e['tool'])} | {_cell(args)} |")
            elif e["type"] == "tool_result":
                lines.append(f"| {e['step']} | {'✓' if e['ok'] else '✗'} result | "
                             f"{_cell(e['tool'])} | {_cell(e['summary'])} ({e['ms']/1000:.1f}s) |")
        with open(self.report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
