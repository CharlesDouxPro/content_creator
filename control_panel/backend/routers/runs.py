"""
runs.py — Déclenche la pipeline pour un channel et expose l'état/les logs (polling).
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from control_panel.backend import store
from control_panel.backend.run_manager import manager, RunInfo

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunRequest(BaseModel):
    channel: str


@router.post("", response_model=RunInfo, status_code=202)
def launch(req: RunRequest) -> RunInfo:
    if store.get_channel(req.channel) is None:
        raise HTTPException(404, f"channel inconnu: {req.channel}")
    return manager.start(req.channel)


@router.get("", response_model=list[RunInfo])
def list_runs() -> list[RunInfo]:
    return manager.list()


@router.get("/{run_id}", response_model=RunInfo)
def get_run(run_id: str) -> RunInfo:
    info = manager.get(run_id)
    if info is None:
        raise HTTPException(404, f"run inconnu: {run_id}")
    return info
