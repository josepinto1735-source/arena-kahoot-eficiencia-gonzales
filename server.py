"""Arena de comunicación — juego de preguntas en tiempo real para talleres.

Sesiones en memoria para una sola instancia Uvicorn. Sin cuentas ni rastreadores.
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import secrets
import string
import time
from pathlib import Path
from typing import Any

import qrcode
from qrcode.image.svg import SvgPathImage
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
APPDIR = ROOT / "static"
app = FastAPI(title="Arena de comunicación", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=APPDIR), name="static")

QUESTIONS = [
    {"topic": "Comunicación asertiva", "q": "Te asignan más trabajo al final de tu turno. ¿Qué respondes?", "a": ["Está bien, aunque no alcanzaré a terminarlo.", "No me fastidien con eso.", "Hoy no puedo extenderme; revisemos prioridades para mañana.", "Que lo haga otra persona, no me importa."], "c": 2,
     "why": "Expresar un límite y proponer una alternativa concreta cuida el trabajo y el respeto."},
    {"topic": "Escucha activa", "q": "Tu compañero te explica una preocupación. ¿Cómo compruebas que lo comprendiste?", "a": ["Le das consejos antes de que termine.", "Resumes lo que expresó y preguntas si entendiste bien.", "Cambias de tema para evitar incomodidad.", "Le dices que está exagerando."], "c": 1,
     "why": "Parafrasear y verificar la comprensión evita suposiciones y demuestra atención."},
    {"topic": "Barreras comunicativas", "q": "¿Qué hábito dificulta más el intercambio de información?", "a": ["Hacer preguntas concretas.", "Comprobar la indicación recibida.", "Dar instrucciones precisas.", "Suponer intenciones sin preguntar."], "c": 3,
     "why": "Interpretar sin verificar introduce prejuicios y malentendidos."},
    {"topic": "Expresar desacuerdos", "q": "No compartes la decisión del equipo. ¿Cómo expresas tu postura?", "a": ["Comprendo la propuesta; me preocupa el plazo y sugiero revisarlo.", "Esa idea es absurda.", "Me quedo callado y me quejo después.", "Ustedes nunca hacen nada bien."], "c": 0,
     "why": "Una discrepancia asertiva reconoce la propuesta, explica una preocupación y ofrece una alternativa."},
    {"topic": "Respuestas impulsivas", "q": "Recibes un mensaje laboral que te molesta. ¿Qué es mejor hacer primero?", "a": ["Responder de inmediato en el mismo tono.", "Reenviar el mensaje para burlarse.", "Ignorar el asunto para siempre.", "Pausar, reconocer la emoción y pensar qué necesitas comunicar."], "c": 3,
     "why": "La pausa permite elegir una respuesta respetuosa en vez de reaccionar automáticamente."},
    {"topic": "Empatía", "q": "Una compañera dice: «Me sentí excluida de la reunión». ¿Qué respuesta ayuda más?", "a": ["No es para tanto.", "Entiendo que te haya afectado. ¿Quieres contarme qué pasó?", "Lo que sientes no importa.", "Seguro estás exagerando."], "c": 1,
     "why": "Validar la emoción abre espacio al diálogo sin juzgar ni minimizar lo que siente la otra persona."},
]
MAX_PLAYERS = 25
SECONDS = 22
ROOMS: dict[str, dict[str, Any]] = {}
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
AVATARS = ["🦊", "🐼", "🦁", "🐸", "🐯", "🐨", "🐧", "🦉", "🐙", "🐻", "🦋", "🐢", "🦄", "🐺", "🐬", "🐝", "🦖", "🐰", "🐵", "🦜"]
DEMO_NAMES = ["Alex", "Cielo", "Mateo", "Sol", "Rosa", "Leo", "Vale", "Nico", "Luna", "Dani", "Paz", "Sam"]


def err(msg: str, status: int = 400):
    return JSONResponse({"error": msg}, status_code=status)


def public_question(i: int, reveal: bool = False):
    if i < 0 or i >= len(QUESTIONS):
        return None
    q = QUESTIONS[i]
    v = {"topic": q["topic"], "text": q["q"], "options": q["a"]}
    if reveal:
        v.update({"correct": q["c"], "explanation": q["why"]})
    return v


def ranking(room: dict[str, Any]):
    players = sorted(room["players"].values(), key=lambda p: (-p["score"], -p["correct_count"], p["joined"]))
    return [
        {"id": p["id"], "name": p["name"], "avatar": p["avatar"], "score": p["score"], "correctCount": p["correct_count"], "connected": p["connected"], "bot": p["bot"], "rank": k + 1}
        for k, p in enumerate(players)
    ]


def state(room: dict[str, Any], role: str, token: str | None):
    phase = room["phase"]
    i = room["idx"]
    reveal = phase in ("reveal", "finished")
    question = public_question(i, reveal)
    players = ranking(room)
    own = room["players"].get(token or "") if role == "player" else None
    out = {
        "type": "state", "serverTime": int(time.time()*1000), "code": room["code"], "phase": phase,
        "round": i + 1, "total": len(QUESTIONS),
        "deadline": room["deadline"] if phase == "question" else None,
        "question": question, "online": sum(1 for p in room["players"].values() if p["connected"] and not p["bot"]),
        "playerCount": len(room["players"]),
        "answersCount": len(room["answers"]),
        "answerCounts": [sum(1 for v in room["answers"].values() if v["choice"] == k) for k in range(4)] if reveal else None,
        "ranking": players if role == "host" or reveal or phase == "lobby" or phase == "finished" else [],
    }
    if role == "host":
        out["players"] = [{**p, "answered": p["id"] in room["answers"]} for p in players]
        out["stats"] = {"maxPlayers": MAX_PLAYERS, "roundSeconds": SECONDS}
    elif own:
        entry = room["answers"].get(own["id"])
        out["me"] = {"id": own["id"], "name": own["name"], "avatar": own["avatar"], "score": own["score"], "rank": next((p["rank"] for p in players if p["id"] == own["id"]), 0), "choice": entry["choice"] if entry else None, "pointsWon": entry.get("points", 0) if entry and reveal else None}
    return out


async def notify(room: dict[str, Any]):
    for role in ("host", "player"):
        for ws, token in tuple(room["sockets"][role].items()):
            try:
                await ws.send_json(state(room, role, token))
            except Exception:
                room["sockets"][role].pop(ws, None)


def award(room: dict[str, Any]):
    q = QUESTIONS[room["idx"]]
    for player_id, entry in room["answers"].items():
        if entry["choice"] == q["c"]:
            bonus = round(50 * max(0, SECONDS - entry["elapsed"]) / SECONDS)
            entry["points"] = 100 + bonus
            p = room["players"].get(player_id)
            if p:
                p["score"] += entry["points"]
                p["correct_count"] += 1
        else:
            entry["points"] = 0


async def reveal(room: dict[str, Any]):
    if room["phase"] != "question":
        return
    room["phase"] = "reveal"
    room["deadline"] = None
    award(room)
    await notify(room)


async def deadline_worker(room: dict[str, Any], question_round: int):
    await asyncio.sleep(SECONDS + 0.08)
    if room["phase"] == "question" and room["idx"] == question_round:
        await reveal(room)


async def start_round(room: dict[str, Any], idx: int):
    room["idx"] = idx
    room["phase"] = "question"
    room["deadline"] = (time.time() + SECONDS) * 1000
    room["round_started"] = time.monotonic()
    room["answers"] = {}
    asyncio.create_task(deadline_worker(room, idx))
    # Bots only for demo sessions, never mixed with real participants in user guidance.
    for p in room["players"].values():
        if p["bot"]:
            asyncio.create_task(bot_answer(room, idx, p["id"]))
    await notify(room)


async def bot_answer(room: dict[str, Any], idx: int, player_id: str):
    import random
    delay = random.uniform(1.6, 13.7)
    await asyncio.sleep(delay)
    if room["phase"] == "question" and room["idx"] == idx and player_id in room["players"]:
        correct = QUESTIONS[idx]["c"]
        answer = correct if random.random() < 0.65 else random.choice([a for a in range(4) if a != correct])
        room["answers"][player_id] = {"choice": answer, "elapsed": delay}
        await notify(room)


@app.get("/")
async def home():
    return FileResponse(APPDIR / "home.html")


@app.get("/host")
async def host():
    return FileResponse(APPDIR / "host.html")


@app.get("/join/{code}")
async def join_page(code: str):
    return FileResponse(APPDIR / "player.html")


@app.get("/presentacion")
async def presentation():
    return FileResponse(APPDIR / "presentacion.html")


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/api/rooms")
async def create_room():
    if len(ROOMS) > 100:
        return err("Capacidad temporal agotada.", 503)
    code = "".join(secrets.choice(ALPHABET) for _ in range(6))
    while code in ROOMS:
        code = "".join(secrets.choice(ALPHABET) for _ in range(6))
    secret = secrets.token_urlsafe(24)
    ROOMS[code] = {"code": code, "secret": secret, "phase": "lobby", "idx": -1,
                   "players": {}, "answers": {}, "deadline": None,
                   "sockets": {"host": {}, "player": {}}, "created": time.time()}
    return {"code": code, "hostKey": secret}


@app.get("/api/rooms/{code}")
async def get_room(code: str):
    room = ROOMS.get(code.upper())
    if not room:
        return err("Esta sala ya no existe. Pide un código nuevo.", 404)
    return {"code": room["code"], "phase": room["phase"], "playerCount": len(room["players"]), "total": len(QUESTIONS)}


@app.post("/api/rooms/{code}/join")
async def join_room(code: str, req: Request):
    room = ROOMS.get(code.upper())
    if not room:
        return err("La sala no existe o se reinició.", 404)
    data = await req.json()
    token = str(data.get("token") or "")
    if token in room["players"] and not room["players"][token]["bot"]:
        p = room["players"][token]
        return {"token": p["id"], "name": p["name"], "avatar": p["avatar"]}
    if room["phase"] != "lobby":
        return err("La partida ya comenzó. Solo pueden volver quienes estaban inscritos.", 409)
    if len(room["players"]) >= MAX_PLAYERS:
        return err("La sala está llena.", 409)
    name = " ".join(str(data.get("name") or "").split()).strip()[:22]
    avatar = str(data.get("avatar") or "")
    if not 2 <= len(name) <= 22:
        return err("Escribe un nombre de 2 a 22 caracteres.")
    if avatar not in AVATARS:
        return err("Selecciona un avatar válido.")
    if any(p["name"].casefold() == name.casefold() for p in room["players"].values()):
        return err("Ese nombre ya está en uso; añade una inicial.", 409)
    token = secrets.token_urlsafe(24)
    room["players"][token] = {"id": token, "name": name, "avatar": avatar, "score": 0,
                              "correct_count": 0, "connected": False, "bot": False, "joined": time.time()}
    await notify(room)
    return {"token": token, "name": name, "avatar": avatar}


@app.get("/api/rooms/{code}/qr.svg")
async def room_qr(code: str, request: Request, url: str = ""):
    if code.upper() not in ROOMS:
        return err("Sala no encontrada", 404)
    if url and (url.startswith("https://") or url.startswith("http://")) and url.split("?")[0].endswith("/join/" + code.upper()):
        target = url[:300]
    else:
        target = str(request.base_url).rstrip("/") + "/join/" + code.upper()
    img = qrcode.make(target, image_factory=SvgPathImage, box_size=8, border=2)
    out = io.BytesIO()
    img.save(out)
    return Response(out.getvalue(), media_type="image/svg+xml", headers={"Cache-Control":"no-store"})


@app.websocket("/ws/{code}")
async def ws_room(ws: WebSocket, code: str):
    room = ROOMS.get(code.upper())
    await ws.accept()
    if not room:
        await ws.send_json({"type": "error", "error": "Sala inexistente"})
        await ws.close(code=1008)
        return
    role, token = None, None
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=12)
        role = hello.get("role")
        token = str(hello.get("token") or "")
        if role == "host":
            if not secrets.compare_digest(token, room["secret"]):
                raise ValueError("Acceso de facilitadora no autorizado")
        elif role == "player":
            if token not in room["players"] or room["players"][token]["bot"]:
                raise ValueError("Participante no registrado")
        else:
            raise ValueError("Rol inválido")
        room["sockets"][role][ws] = token
        if role == "player":
            room["players"][token]["connected"] = True
        await notify(room)
        while True:
            msg = await ws.receive_json()
            action = msg.get("action")
            if role == "player" and action == "answer":
                if room["phase"] != "question" or time.monotonic() - room["round_started"] >= SECONDS:
                    await ws.send_json({"type": "notice", "message": "Esta ronda ya terminó."})
                    continue
                if token in room["answers"]:
                    continue
                choice = msg.get("choice")
                if type(choice) is not int or choice not in (0, 1, 2, 3):
                    continue
                room["answers"][token] = {"choice": choice, "elapsed": max(0, time.monotonic() - room["round_started"])}
                await notify(room)
            elif role == "host":
                if action == "start" and room["phase"] == "lobby" and len(room["players"]) >= 2:
                    await start_round(room, 0)
                elif action == "reveal":
                    await reveal(room)
                elif action == "next" and room["phase"] == "reveal":
                    if room["idx"] + 1 == len(QUESTIONS):
                        room["phase"] = "finished"
                        await notify(room)
                    else:
                        await start_round(room, room["idx"] + 1)
                elif action == "restart":
                    room["phase"] = "lobby"
                    room["idx"] = -1
                    room["deadline"] = None
                    room["answers"] = {}
                    for p in room["players"].values():
                        p["score"] = p["correct_count"] = 0
                    await notify(room)
                elif action == "kick" and room["phase"] == "lobby":
                    pid = str(msg.get("player") or "")
                    if pid in room["players"]:
                        room["players"].pop(pid)
                        for client, client_token in list(room["sockets"]["player"].items()):
                            if client_token == pid:
                                await client.close(code=1008)
                        await notify(room)
                elif action == "demo_add" and room["phase"] == "lobby":
                    for j, name in enumerate(DEMO_NAMES):
                        if len(room["players"]) >= MAX_PLAYERS:
                            break
                        if any(p["name"].casefold() == name.casefold() for p in room["players"].values()):
                            continue
                        pid = "bot_" + secrets.token_urlsafe(7)
                        room["players"][pid] = {"id": pid, "name": name, "avatar": AVATARS[j],
                                                 "score": 0, "correct_count": 0, "connected": True,
                                                 "bot": True, "joined": time.time() + j / 100}
                    await notify(room)
                elif action == "demo_clear" and room["phase"] == "lobby":
                    room["players"] = {k:v for k,v in room["players"].items() if not v["bot"]}
                    await notify(room)
                elif action == "ping":
                    await ws.send_json({"type": "pong"})
            elif role == "player" and action == "ping":
                await ws.send_json({"type":"pong"})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except (ValueError, asyncio.TimeoutError) as exc:
        try:
            await ws.send_json({"type": "error", "error": str(exc)})
            await ws.close(code=1008)
        except Exception:
            pass
    except Exception:
        pass
    finally:
        if role in ("host", "player"):
            room["sockets"][role].pop(ws, None)
            if role == "player" and token in room["players"]:
                room["players"][token]["connected"] = any(v == token for v in room["sockets"]["player"].values())
            await notify(room)