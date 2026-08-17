"""
channels.py — CRUD des channels. La validation Pydantic (schema.Channel) se fait à
l'écriture : un payload invalide (skill/provider inconnu, champ en trop) est rejeté en 422.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from content_creator.config.schema import Channel, resolve_pool
from control_panel.backend import store

router = APIRouter(prefix="/api/channels", tags=["channels"])


class EnhancedPrompt(BaseModel):
    prompt: str


@router.post("/enhance-prompt", response_model=EnhancedPrompt)
def enhance_prompt(channel: Channel) -> EnhancedPrompt:
    """Améliore le brief (`context.prompt`) du channel via son `master_mind`, calibré sur le moteur
    vidéo cible (`video_generator`). N'écrit RIEN : renvoie juste le prompt amélioré, que le front
    injecte dans le champ (l'utilisateur sauvegarde ensuite comme d'habitude)."""
    # Import paresseux : garde le démarrage du backend léger (build_prompt_guide tire video_tools).
    from content_creator.agentic.prompt_enhancer import enhance_prompt as _enhance

    if not channel.context.prompt.strip():
        raise HTTPException(422, "prompt vide — rien à améliorer")
    try:
        models = resolve_pool(channel.models)
        enhanced = _enhance(
            prompt=channel.context.prompt,
            models_config=models,
            skill_name=channel.skill,
            mood=channel.context.mood or None,
            characters={
                name: c.model_dump(exclude_none=True)
                for name, c in channel.context.characters.items()
            },
        )
    except HTTPException:
        raise
    except Exception as e:  # provider KO, modèle indispo, réseau… -> 502 exploitable côté front
        raise HTTPException(502, f"échec de l'amélioration du prompt : {e}")
    return EnhancedPrompt(prompt=enhanced)


@router.get("", response_model=list[Channel])
def list_channels() -> list[Channel]:
    return store.list_channels()


@router.post("", response_model=Channel, status_code=201)
def create_channel(channel: Channel) -> Channel:
    if store.get_channel(channel.name) is not None:
        raise HTTPException(409, f"channel déjà existant: {channel.name}")
    return store.upsert_channel(channel)


@router.get("/{name}", response_model=Channel)
def get_channel(name: str) -> Channel:
    channel = store.get_channel(name)
    if channel is None:
        raise HTTPException(404, f"channel inconnu: {name}")
    return channel


@router.put("/{name}", response_model=Channel)
def update_channel(name: str, channel: Channel) -> Channel:
    if store.get_channel(name) is None:
        raise HTTPException(404, f"channel inconnu: {name}")
    # Renommage autorisé : refuse si le nouveau nom heurte un AUTRE channel.
    if channel.name != name and store.get_channel(channel.name) is not None:
        raise HTTPException(409, f"channel déjà existant: {channel.name}")
    return store.upsert_channel(channel, original_name=name)


@router.delete("/{name}", status_code=204)
def delete_channel(name: str) -> None:
    if not store.delete_channel(name):
        raise HTTPException(404, f"channel inconnu: {name}")
