import os
from fastapi import FastAPI, Response, Request, WebSocket, WebSocketDisconnect
import secrets
import uuid
import httpx
import hashlib

app = FastAPI()

pending_verifications = {}
verified_users = {}
active_connections = {}


@app.middleware("http")
async def collapse_double_slashes(request: Request, call_next):
    path = request.url.path
    normalized_path = "/" + "/".join(filter(None, path.split("/")))
    if normalized_path != path:
        request.scope["path"] = normalized_path
    return await call_next(request)


# @app.get("/api/assets/{asset_path:path}")
# async def get_asset(asset_path: str):
#     url = f"https://figura.moonlight-devs.org/api/assets/{asset_path}"
#     local_asset_path = f"./assets/{asset_path}"
#     if os.path.exists(local_asset_path):
#         with open(local_asset_path, "rb") as asset_file:
#             asset_data = asset_file.read()
#         return Response(content=asset_data, media_type="application/octet-stream")
#     async with httpx.AsyncClient() as client:
#         response = await client.get(url)
#         if response.status_code == 200:
#             os.makedirs(os.path.dirname(local_asset_path), exist_ok=True)
#             with open(local_asset_path, "wb") as asset_file:
#                 asset_file.write(response.content)
#         return Response(content=response.content, status_code=response.status_code, headers=dict(response.headers))


@app.get("/api/auth/id")
async def get_auth_id(username: str):
    auth_id = secrets.token_hex(16)

    pending_verifications[auth_id] = username

    return Response(content=auth_id, media_type="text/plain")


@app.get("/api/auth/verify")
async def verify(id: str):
    token = secrets.token_urlsafe(16)

    server_id = id
    username = pending_verifications.get(id)

    if not username:
        return Response(content="Invalid ID", status_code=400)

    for existing_token, user_data in verified_users.items():
        if user_data["username"] == username:
            return Response(content=existing_token, media_type="text/plain")

    auth_url = f"https://sessionserver.mojang.com/session/minecraft/hasJoined?username={username}&serverId={server_id}"
    async with httpx.AsyncClient() as client:
        response = await client.get(auth_url)
        if response.status_code != 200:
            return Response(content="Verification failed with Mojang", status_code=403)

    verified_users[token] = {
        "uuid": str(uuid.UUID(response.json().get("id"))), "username": pending_verifications[id]}
    pending_verifications.pop(id, None)

    return Response(content=token, media_type="text/plain")


@app.get("/api/motd")
async def get_motd():
    return Response(content="Custom Figura backend jumpscare!!", media_type="text/plain")


@app.get("/api/version")
async def get_version():
    return {
        "release": "0.1.5",
        "prerelease": "0.1.5"
    }


@app.get("/api/limits")
async def get_limits():
    return {
        "rate": {
            "pingSize": 1024,
            "pingRate": 32,
            "equip": 1,
            "download": 50,
            "upload": 1
        },
        "limits": {
            "maxAvatarSize": 100000,
            "maxAvatars": 10,
            "allowedBadges": {
                "special": [0, 0, 0, 0, 0, 0],
                "pride": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
            }
        }
    }


@app.put("/api/avatar")
async def upload_avatar(request: Request):
    token = request.headers.get("token")
    userinfo = verified_users.get(token)
    if not userinfo:
        return Response(content="Invalid token", status_code=403)

    os.makedirs("./avatars", exist_ok=True)
    with open(f"./avatars/{userinfo['uuid']}.moon", "wb") as f:
        f.write(await request.body())

    return Response(content="Avatar uploaded successfully", status_code=200)


@app.post("/api/equip")
async def equip_item(request: Request):
    token = request.headers.get("token")
    userinfo = verified_users.get(token)
    if not userinfo:
        return Response(content="Invalid token", status_code=403)

    return Response(content="Avatar equipped successfully", status_code=200)


@app.get("/api/{uuid}")
async def get_user_by_uuid(uuid: str):
    try:
        user = next((user for user in verified_users.values()
                    if user["uuid"] == uuid), None)
        if not user:
            return Response(content="User not found", status_code=404)
        base = {
            "uuid": user["uuid"],
            "banned": False,
            "equipped": [
            ],
            "equippedBadges": {
                "special": [0, 0, 0, 0, 0, 0],
                "pride": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
            },
            "lastUsed": "2025-09-19T21:36:13.255Z",
            "rank": "normal",
            "version": "0.1.5+1.21.1"
        }
        if os.path.exists(f"./avatars/{uuid}.moon"):
            avatar_path = f"./avatars/{uuid}.moon"
            with open(avatar_path, "rb") as avatar_file:
                avatar_hash = hashlib.sha256(avatar_file.read()).hexdigest()
            base["equipped"].append({
                "id": "avatar",
                "owner": user["uuid"],
                "hash": avatar_hash,
            })
        return base
    except Exception as e:
        return Response(content="Internal Server Error", status_code=500)


@app.get("/api/{uuid}/avatar")
async def download_avatar(uuid: str):
    try:
        avatar_path = f"./avatars/{uuid}.moon"
        if not os.path.exists(avatar_path):
            return Response(content="Avatar not found", status_code=404)

        with open(avatar_path, "rb") as avatar_file:
            avatar_data = avatar_file.read()

        return Response(content=avatar_data, media_type="application/octet-stream")
    except Exception as e:
        return Response(content="Internal Server Error", status_code=500)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    try:
        token = await websocket.receive_bytes()
        token = token.decode('utf-8').strip().replace("\x00", "")

        user = verified_users.get(token)
        if not user:
            await websocket.close(code=3000, reason="Authentication failure")
            return

        active_connections[user["uuid"]] = websocket

        await websocket.send_bytes(b'\x00')

        while True:
            try:
                data = await websocket.receive()
            except WebSocketDisconnect:
                active_connections.pop(user["uuid"], None)
                break
            except Exception as e:
                active_connections.pop(user["uuid"], None)
                break

    except Exception as e:
        await websocket.close(code=1006, reason="Abnormal Closure")
        raise e
