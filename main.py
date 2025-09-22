from fastapi import FastAPI, Request
from database import engine, Base
from api import router
import os
import requests
from zipfile import ZipFile
from io import BytesIO
import json
import shutil
import hashlib

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

ASSETS_URL = CONFIG.get(
    "assetsUrl", "https://github.com/FiguraMC/Assets/archive/refs/heads/main.zip")
ASSETS_DIR = CONFIG.get("assetsDir", "assets")


def calculate_file_hash(file_path):
    hash_func = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_func.update(chunk)
    return hash_func.hexdigest()


def generate_file_index(directory):
    file_index = {}
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(
                file_path, directory).replace("\\", "/")
            file_index[relative_path] = calculate_file_hash(file_path)
    return file_index


def fetch_and_extract_assets():
    response = requests.get(ASSETS_URL)
    response.raise_for_status()
    with ZipFile(BytesIO(response.content)) as zip_file:
        zip_file.extractall(ASSETS_DIR)

    assets_main_dir = os.path.join(ASSETS_DIR, "Assets-main")
    file_index = generate_file_index(assets_main_dir + "/v2")

    v2_json_path = os.path.join(assets_main_dir, "v2.json")
    with open(v2_json_path, "w", encoding="utf-8") as f:
        json.dump(file_index, f, indent=4)


if not os.path.exists(ASSETS_DIR):
    os.makedirs(ASSETS_DIR, exist_ok=True)
    fetch_and_extract_assets()
else:
    shutil.rmtree(ASSETS_DIR)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    fetch_and_extract_assets()

app = FastAPI()


@app.middleware("http")
async def collapse_double_slashes(request: Request, call_next):
    path = request.url.path
    normalized_path = "/" + "/".join(filter(None, path.split("/")))
    if normalized_path != path:
        request.scope["path"] = normalized_path
    return await call_next(request)

app.include_router(router)

Base.metadata.create_all(bind=engine)
