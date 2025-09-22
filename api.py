from fastapi import APIRouter, Response, Request, WebSocket, WebSocketDisconnect, Depends
import secrets
import uuid as uuidlib
import httpx
import hashlib
import json
import os
from sqlalchemy.orm import Session
from database import SessionLocal
from models import PendingVerification, User, Token, Avatar, Subscription
import random
from datetime import datetime
import struct
import time
from collections import defaultdict
import asyncio

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


active_connections = {}


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)


@router.get("/api/auth/id")
async def get_auth_id(username: str, db: Session = Depends(get_db)):
    existing = db.query(PendingVerification).filter_by(
        username=username).first()
    if existing:
        return Response(content=existing.id, media_type="text/plain")
    auth_id = secrets.token_hex(16)
    db.add(PendingVerification(id=auth_id, username=username))
    db.commit()
    return Response(content=auth_id, media_type="text/plain")


@router.get("/api/auth/verify")
async def verify(id: str, db: Session = Depends(get_db)):
    token_str = secrets.token_urlsafe(16)
    pv = db.query(PendingVerification).filter_by(id=id).first()
    if not pv:
        return Response(content="Invalid ID", status_code=400)
    user = db.query(User).filter_by(username=pv.username).first()
    if user:
        old_token = db.query(Token).filter_by(user_uuid=user.uuid).first()
        if old_token:
            db.delete(old_token)
        new_token = Token(token=token_str, user_uuid=user.uuid)
        db.add(new_token)
        db.delete(pv)
        db.commit()
        return Response(content=token_str, media_type="text/plain")
    auth_url = f"https://sessionserver.mojang.com/session/minecraft/hasJoined?username={pv.username}&serverId={id}"
    async with httpx.AsyncClient() as client:
        response = await client.get(auth_url)
        if response.status_code != 200:
            return Response(content="Verification failed with Mojang", status_code=403)
    user_uuid = str(uuidlib.UUID(response.json().get("id")))
    user = User(uuid=user_uuid, username=pv.username)
    db.add(user)
    db.flush()
    db.add(Token(token=token_str, user_uuid=user.uuid))
    db.delete(pv)
    db.commit()
    return Response(content=token_str, media_type="text/plain")


def get_user_by_token(token: str, db: Session):
    token_obj = db.query(Token).filter_by(token=token).first()
    if not token_obj:
        return None
    return db.query(User).filter_by(uuid=token_obj.user_uuid).first()


class S2C:
    AUTH = 0
    PING = 1
    EVENT = 2
    TOAST = 3
    CHAT = 4
    NOTICE = 5
    KEEPALIVE = 6

    class NoticeType:
        SIZE = 0
        RATE = 1

    class ToastType:
        DEFAULT = 0
        WARNING = 1
        ERROR = 2
        CHEESE = 3

    @staticmethod
    def auth():
        return bytes([S2C.AUTH])

    @staticmethod
    def ping(target_uuid: str, ping_id: int, sync: bool, data: bytes):
        uuid_bytes = uuidlib.UUID(target_uuid).bytes
        msb = int.from_bytes(uuid_bytes[:8], "big", signed=False)
        lsb = int.from_bytes(uuid_bytes[8:], "big", signed=False)
        return struct.pack(">bQQib", S2C.PING, msb, lsb, ping_id, int(sync)) + data

    @staticmethod
    def event(uuid: str):
        uuid_bytes = uuidlib.UUID(uuid).bytes
        msb = int.from_bytes(uuid_bytes[:8], "big", signed=False)
        lsb = int.from_bytes(uuid_bytes[8:], "big", signed=False)
        return struct.pack(">bQQ", S2C.EVENT, msb, lsb)

    @staticmethod
    def toast(type: int, title: str, message: str = ""):
        title_bytes = title.encode("utf-8")
        message_bytes = message.encode("utf-8")
        return struct.pack(">bb", S2C.TOAST, type) + title_bytes + b"\0" + message_bytes

    @staticmethod
    def chat(message: str):
        message_bytes = message.encode("utf-8")
        return bytes([S2C.CHAT]) + message_bytes

    @staticmethod
    def notice(type: int):
        return bytes([S2C.NOTICE, type])

    @staticmethod
    def keepalive():
        return bytes([S2C.KEEPALIVE])


@router.get("/api/assets/v2")
async def list_assets():
    assets_path = f"./{CONFIG.get('assetsDir', 'assets')}/Assets-main/"
    with open(os.path.join(assets_path, "v2.json"), "r", encoding="utf-8") as f:
        assets_index = json.load(f)
    return assets_index


@router.get("/api/assets/v2/{asset_path:path}")
async def get_asset(asset_path: str):
    local_asset_path = f"./{CONFIG.get('assetsDir', 'assets')}/Assets-main/v2/{asset_path}"
    if os.path.exists(local_asset_path):
        with open(local_asset_path, "rb") as asset_file:
            asset_data = asset_file.read()
        return Response(content=asset_data, media_type="application/octet-stream")
    else:
        return Response(content="Asset not found", status_code=404)


@router.get("/api/motd")
async def get_motd(request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("token")
    user = get_user_by_token(token, db) if token else None
    if not user:
        return Response(content="Invalid token", status_code=403)
    user.last_used = datetime.utcnow()
    user_agent = request.headers.get("user-agent", "")
    version = None
    parts = user_agent.split("/")
    if len(parts) > 1:
        version = parts[1].strip()
    if version:
        user.version = version
    db.commit()
    motds = CONFIG.get("motds", ["No MOTDs configured"])
    return Response(content=random.choice(motds), media_type="text/plain")


@router.get("/api/version")
async def get_version():
    return CONFIG.get("figuraVersions", {
        "release": "0.1.5",
        "prerelease": "0.1.5"
    })


@router.get("/api/limits")
async def get_limits(request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("token")
    user = get_user_by_token(token, db) if token else None
    if not user:
        return Response(content="Invalid token", status_code=403)
    return {
        "rate": {
            "pingSize": user.ping_size,
            "pingRate": user.ping_rate,
            "equip": user.equip,
            "download": user.download,
            "upload": user.upload
        },
        "limits": {
            "maxAvatarSize": user.max_avatar_size,
            "maxAvatars": user.max_avatars,
            "allowedBadges": {
                "special": [int(x) for x in user.special_badges.split(",")],
                "pride": [int(x) for x in user.pride_badges.split(",")]
            }
        }
    }


@router.put("/api/avatar")
async def upload_avatar(request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("token")
    user = get_user_by_token(token, db)
    if not user:
        return Response(content="Invalid token", status_code=403)
    data = await request.body()
    max_avatar_size = user.max_avatar_size
    if len(data) > max_avatar_size:
        return Response(content="Avatar too large", status_code=413)
    avatar = db.query(Avatar).filter_by(uuid=user.uuid).first()
    if avatar:
        avatar.data = data
    else:
        db.add(Avatar(uuid=user.uuid, data=data))
    db.commit()
    subs = db.query(Subscription).filter_by(target_uuid=user.uuid).all()
    event_packet = S2C.event(user.uuid)
    for sub in subs:
        if sub.user_uuid == user.uuid:
            continue
        ws = active_connections.get(sub.user_uuid)
        if ws:
            try:
                await ws.send_bytes(event_packet)
            except Exception:
                pass
    return Response(content="Avatar uploaded successfully", status_code=200)


@router.delete("/api/avatar")
async def delete_avatar(request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("token")
    user = get_user_by_token(token, db)
    if not user:
        return Response(content="Invalid token", status_code=403)
    avatar = db.query(Avatar).filter_by(uuid=user.uuid).first()
    if avatar:
        db.delete(avatar)
        db.commit()
        return Response(content="Avatar deleted successfully", status_code=200)
    else:
        return Response(content="No avatar to delete", status_code=404)


@router.post("/api/equip")
async def equip_item(request: Request, db: Session = Depends(get_db)):
    token = request.headers.get("token")
    user = get_user_by_token(token, db)
    if not user:
        return Response(content="Invalid token", status_code=403)
    return Response(content="Avatar equipped successfully", status_code=200)


@router.get("/api/{uuid}")
async def get_user_by_uuid(uuid: str, db: Session = Depends(get_db)):
    try:
        user = db.query(User).filter_by(uuid=uuid).first()
        if not user:
            return Response(content="User not found", status_code=404)
        base = {
            "uuid": user.uuid,
            "banned": False,
            "equipped": [],
            "equippedBadges": {
                "special": [int(x) for x in user.equipped_special_badges.split(",")],
                "pride": [int(x) for x in user.equipped_pride_badges.split(",")]
            },
            "lastUsed": user.last_used.isoformat() + "Z" if user.last_used else "",
            "rank": "normal",
            "version": user.version if user.version else "unknown"
        }
        avatar = db.query(Avatar).filter_by(uuid=uuid).first()
        if avatar:
            avatar_hash = hashlib.sha256(avatar.data).hexdigest()
            base["equipped"].append({
                "id": "avatar",
                "owner": user.uuid,
                "hash": avatar_hash,
            })
        return base
    except Exception:
        return Response(content="Internal Server Error", status_code=500)


@router.get("/api/{uuid}/avatar")
async def download_avatar(uuid: str, db: Session = Depends(get_db)):
    try:
        avatar = db.query(Avatar).filter_by(uuid=uuid).first()
        if not avatar:
            return Response(content="Avatar not found", status_code=404)
        return Response(content=avatar.data, media_type="application/octet-stream")
    except Exception:
        return Response(content="Internal Server Error", status_code=500)


class C2S:
    TOKEN = 0
    PING = 1
    SUB = 2
    UNSUB = 3

    @staticmethod
    def parse(msg: bytes):
        if not msg:
            return None, None
        msg_type = msg[0]
        if msg_type == C2S.TOKEN:
            return C2S.TOKEN, msg[1:].decode('utf-8').strip().replace("\x00", "")
        elif msg_type == C2S.PING:
            if len(msg) < 6:
                return C2S.PING, None
            ping_id = struct.unpack(">i", msg[1:5])[0]
            sync = msg[5] != 0
            data = msg[6:]
            return C2S.PING, {"id": ping_id, "sync": sync, "data": data}
        elif msg_type == C2S.SUB or msg_type == C2S.UNSUB:
            if len(msg) != 17:
                return msg_type, None
            target_uuid = str(uuidlib.UUID(bytes=msg[1:17]))
            return msg_type, {"uuid": target_uuid}
        else:
            return msg_type, msg[1:]


ping_stats = defaultdict(lambda: {"count": 0, "bytes": 0, "reset": 0})


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    await websocket.accept()
    user = None
    try:
        msg = await websocket.receive_bytes()
        msg_type, payload = C2S.parse(msg)
        if msg_type != C2S.TOKEN or not payload:
            await websocket.close(code=3000, reason="Authentication failure")
            return
        token = payload
        user = get_user_by_token(token, db)
        if not user:
            await websocket.close(code=3000, reason="Authentication failure")
            return
        active_connections[user.uuid] = websocket
        await websocket.send_bytes(S2C.auth())

        while True:
            try:
                msg = await websocket.receive_bytes()
                msg_type, payload = C2S.parse(msg)
                now = int(time.time())
                stats = ping_stats[user.uuid]
                if stats["reset"] != now:
                    stats["reset"] = now
                    stats["count"] = 0
                    stats["bytes"] = 0
                if msg_type == C2S.PING and payload:
                    ping_size_limit = user.ping_size
                    ping_rate_limit = user.ping_rate
                    ping_id = payload["id"]
                    sync = payload["sync"]
                    data = payload["data"]
                    total_size = len(data)
                    if stats["count"] + 1 > ping_rate_limit:
                        await websocket.send_bytes(S2C.notice(S2C.NoticeType.RATE))
                        continue
                    if stats["bytes"] + total_size > ping_size_limit:
                        await websocket.send_bytes(S2C.notice(S2C.NoticeType.SIZE))
                        continue
                    stats["count"] += 1
                    stats["bytes"] += total_size
                    target_uuid = user.uuid
                    subs = db.query(Subscription).filter_by(
                        target_uuid=target_uuid).all()
                    packet = S2C.ping(target_uuid, ping_id, sync, data)
                    for sub in subs:
                        ws = active_connections.get(sub.user_uuid)
                        if user.uuid == sub.user_uuid and not sync:
                            continue
                        if ws:
                            try:
                                await ws.send_bytes(packet)
                            except Exception:
                                pass
                elif msg_type == C2S.SUB and payload:
                    target_uuid = payload["uuid"]
                    if not db.query(Subscription).filter_by(user_uuid=user.uuid, target_uuid=target_uuid).first():
                        db.add(Subscription(user_uuid=user.uuid,
                               target_uuid=target_uuid))
                        db.commit()
                elif msg_type == C2S.UNSUB and payload:
                    target_uuid = payload["uuid"]
                    db.query(Subscription).filter_by(
                        user_uuid=user.uuid, target_uuid=target_uuid).delete()
                    db.commit()
            except WebSocketDisconnect:
                break
            except Exception:
                pass
    finally:
        if user:
            active_connections.pop(user.uuid, None)
        if websocket.client_state != WebSocket.DISCONNECTED:
            await websocket.close(code=1000, reason="Normal Closure")
