"""
gallery.py — Liste les vidéos produites sur GCS. Les runs uploadent sous
`<channel>/<date>/<slug>.mp4` (cf. pipeline_agentic.process_channel).
"""

from fastapi import APIRouter
from pydantic import BaseModel

from control_panel.backend import store
from control_panel.backend.gcs_util import manager, public_url, VIDEO_EXTS

router = APIRouter(prefix="/api/gallery", tags=["gallery"])


class VideoItem(BaseModel):
    channel: str
    date: str | None = None
    name: str
    blob: str
    url: str


@router.get("", response_model=list[VideoItem])
def gallery() -> list[VideoItem]:
    gcs = manager()
    items: list[VideoItem] = []
    for channel in store.list_channels():
        for blob in gcs.list_blobs_with_prefix(f"{channel.name}/"):
            if not blob.lower().endswith(VIDEO_EXTS):
                continue
            parts = blob.split("/")
            date = parts[1] if len(parts) >= 3 else None
            items.append(VideoItem(
                channel=channel.name, date=date, name=parts[-1],
                blob=blob, url=public_url(blob),
            ))
    # Plus récentes d'abord (date puis nom).
    items.sort(key=lambda v: (v.date or "", v.name), reverse=True)
    return items
