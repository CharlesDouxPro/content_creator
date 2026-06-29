#!/usr/bin/env python3
"""
video_agent.py — Agent orchestrateur de vidéos.

L'agent (Claude Opus 4.8 via DeepInfra, endpoint OpenAI-compatible) reçoit un texte,
charge un *skill* (type de vidéo), et décide lui-même quels *tools* appeler pour
construire la vidéo, plan par plan. Toutes les décisions sont tracées (cf. trace.py).

Usage :
    poetry run python video_agent.py "Mon texte..." --avatar image.png
    poetry run python video_agent.py --file script.txt --avatar portrait.png --scene "studio podcast"
    poetry run python video_agent.py "..." --skill avatar_story --scene
"""

import os
import json
import time
import argparse

from openai import OpenAI

from content_creator.config.config import API_KEYS
from content_creator.pipelines.modules import GCSManager, ArticleSummarizer
from content_creator.agentic.capabilities import (
    Ctx, BACKGROUND_PROMPT, download, upload_public, prepare_scene_portrait,
)
from content_creator.agentic.video_tools import VideoSession, openai_tool_schemas, dispatch
from content_creator.agentic.video_skills import get_skill
from content_creator.agentic.ltx_prompting import LTX_PROMPT_GUIDE
from content_creator.agentic.trace import Tracer

# Cerveau de l'agent (hébergé sur DeepInfra, OpenAI-compatible, function calling)
AGENT_MODEL = "anthropic/claude-opus-4-8"


def build_session(avatar: str, output_dir: str,
                  apply_scene: bool = False, scene_prompt: str = None) -> VideoSession:
    """Prépare les ressources partagées d'un run à partir d'un avatar (chemin local OU URL)."""
    gcs = GCSManager()

    # Toujours une copie locale de l'avatar (nécessaire à set_scene_background).
    if str(avatar).startswith("http"):
        avatar_local = os.path.join(output_dir, "avatar_src")
        download(avatar, avatar_local)
        avatar_is_url = True
    else:
        avatar_local = avatar
        avatar_is_url = False

    if apply_scene:
        scene = prepare_scene_portrait(
            regen=True, src=avatar_local,
            prompt=scene_prompt or BACKGROUND_PROMPT,
            out=os.path.join(output_dir, "scene_portrait.jpg"),
        )
        portrait_url = upload_public(gcs, scene, "media/test/scene_portrait.jpg")
    elif avatar_is_url:
        portrait_url = avatar                                   # déjà public
    else:
        portrait_url = upload_public(gcs, avatar_local, "media/test/avatar_portrait.png")

    ctx = Ctx(gcs=gcs, summarizer=ArticleSummarizer(), portrait_url=portrait_url,
              media=[{"type": "reference_image", "url": portrait_url}])
    return VideoSession(ctx=ctx, avatar_local=avatar_local, output_dir=output_dir)


def run_agent(content: str, skill_name: str = "avatar_story", avatar: str = "image.png",
              mood: str = None, article=None, apply_scene: bool = False, scene_prompt: str = None,
              max_steps: int = 20, label: str = None) -> dict:
    """Boucle tool-calling : l'agent construit la vidéo, chaque décision est tracée.
    `content` = message utilisateur (article à adapter OU script déjà prêt).
    `article`  = FullArticle source (permet au master d'écrire le script via write_script).
    Retourne {"video": chemin, "script": script écrit par le master}."""
    tracer = Tracer(label=label)
    tracer.start(content, skill_name, avatar)

    skill = get_skill(skill_name)
    session = build_session(avatar, tracer.dir, apply_scene, scene_prompt)
    session.article = article

    client = OpenAI(api_key=API_KEYS["deepinfra_api_key"],
                    base_url=API_KEYS["deepinfra_base_url"])
    tools = openai_tool_schemas(skill.tool_names)
    # skill (réalisation) + compétence de prompting du moteur vidéo (LTX)
    system = skill.system_prompt + "\n\n" + LTX_PROMPT_GUIDE
    if mood:   # le mood pilote les CHOIX DE RÉALISATION du master (sinon: réalisation classique)
        system += (
            f"\n\n## MOOD (priorité haute)\n"
            f"Le mood/l'intention de cette vidéo est : « {mood} ».\n"
            f"Adapte TOUS tes choix de réalisation à ce mood :\n"
            f"- le décor (`set_scene_background`),\n"
            f"- l'équilibre plans parlés / b-roll et le rythme,\n"
            f"- les `shot_description` (cadrage, lumière, mouvement, énergie),\n"
            f"- le ton général de la mise en scène.\n"
            f"Le mood prime sur les choix par défaut."
        )
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": content}]

    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=AGENT_MODEL, messages=messages, tools=tools,
            tool_choice="auto", max_tokens=4096,
        )
        msg = resp.choices[0].message
        tracer.on_assistant(msg.content, getattr(resp, "usage", None))

        tool_calls = msg.tool_calls or []
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ]
        messages.append(assistant_msg)

        if not tool_calls:
            break   # l'agent a fini

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            t0 = tracer.on_tool_call(name, args)
            result = dispatch(session, name, args)
            ok = result.get("status") == "ok"
            summary = (result.get("error") if not ok else
                       result.get("clip") or result.get("final_video") or result.get("note") or "ok")
            tracer.on_tool_result(name, ok, str(summary), (time.time() - t0) * 1000)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})

    tracer.finish(session.final_video)
    return {"video": session.final_video, "script": session.script}


def main():
    ap = argparse.ArgumentParser(description="Agent vidéo (Claude Opus 4.8 via DeepInfra)")
    ap.add_argument("text", nargs="?", help="Le script (sinon --file)")
    ap.add_argument("--file", help="Lire le script depuis un fichier")
    ap.add_argument("--avatar", default="image.png", help="Chemin local OU URL de l'avatar")
    ap.add_argument("--skill", default="avatar_story", help="Type de vidéo")
    ap.add_argument("--scene", nargs="?", const="__default__", default=None,
                    help="Active le décor FLUX Kontext. Sans valeur: décor par défaut; avec: description custom.")
    ap.add_argument("--mood", default=None, help="Section mood/ton ajoutée au system prompt")
    ap.add_argument("--max-steps", type=int, default=20)
    args = ap.parse_args()

    script = args.text
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            script = f.read()
    if not script:
        ap.error("fournis un script (argument positionnel) ou --file")

    apply_scene = args.scene is not None
    scene_prompt = None if args.scene in (None, "__default__") else args.scene

    result = run_agent(script, skill_name=args.skill, avatar=args.avatar, mood=args.mood,
                       apply_scene=apply_scene, scene_prompt=scene_prompt, max_steps=args.max_steps)
    print(f"\n🎬 Vidéo finale: {result['video']}")


if __name__ == "__main__":
    main()
