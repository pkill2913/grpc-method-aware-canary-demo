import json
import logging
import os
import random
import tempfile
import threading
import time
from concurrent import futures
from datetime import datetime, timezone

import grpc

import user_pb2
import user_pb2_grpc


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

DEFAULT_USER_NAME = os.getenv("DEFAULT_USER_NAME", "demo-user")
DEFAULT_THEME = os.getenv("DEFAULT_THEME", "blue")
USER_STORE_PATH = os.getenv("USER_STORE_PATH", "/data/users.json")
SUPPORTED_VERSIONS = frozenset({"v1", "v2"})
STORE_LOCK = threading.Lock()


def require_supported_version(context):
    version = os.getenv("APP_VERSION", "v1")
    if version not in SUPPORTED_VERSIONS:
        context.abort(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"unsupported APP_VERSION {version!r}; supported versions are v1 and v2",
        )
    return version


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_store():
    if not os.path.exists(USER_STORE_PATH):
        return {"users": {}}

    with open(USER_STORE_PATH, "r", encoding="utf-8") as store_file:
        return json.load(store_file)


def write_store(store):
    store_dir = os.path.dirname(USER_STORE_PATH) or "."
    os.makedirs(store_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=store_dir,
        delete=False,
    ) as tmp_file:
        json.dump(store, tmp_file, indent=2, sort_keys=True)
        tmp_file.write("\n")
        tmp_path = tmp_file.name

    os.replace(tmp_path, USER_STORE_PATH)


def normalize_theme(theme):
    return (theme or DEFAULT_THEME).strip().lower() or DEFAULT_THEME


def get_user_record(name, theme):
    with STORE_LOCK:
        store = read_store()
        return (
            store.get("users", {})
            .get(name, {})
            .get("themes", {})
            .get(normalize_theme(theme))
        )


def save_user_record(name, theme, version):
    normalized_theme = normalize_theme(theme)
    with STORE_LOCK:
        store = read_store()
        users = store.setdefault("users", {})
        user = users.setdefault(name, {"name": name, "themes": {}})
        themes = user.setdefault("themes", {})
        created = normalized_theme not in themes
        record = themes.get(normalized_theme, {})
        record.update(
            {
                "name": name,
                "theme": normalized_theme,
                "status": "active",
                "updated_at": utc_now(),
                "updated_by_version": version,
            }
        )
        if created:
            record["created_at"] = record["updated_at"]
            record["created_by_version"] = version
        themes[normalized_theme] = record
        write_store(store)
        return record, created


def list_user_records():
    with STORE_LOCK:
        store = read_store()
        records = []
        for user in store.get("users", {}).values():
            for record in user.get("themes", {}).values():
                records.append(record)
        return sorted(records, key=lambda record: (record.get("name", ""), record.get("theme", "")))


def get_user_status(version, stored):
    if version == "v2" and stored:
        return "premium"
    if version == "v2":
        return "preview"
    return "active"


def get_user_message(name, theme, version, stored):
    if version == "v2":
        base = (
            f"Loaded {name} with {theme} theme from local store in v2"
            if stored
            else f"Hello {name} with {theme} theme from v2"
        )
        return f"{base}. Profile insights enabled."

    return (
        f"Loaded {name} with {theme} theme from local store in v1"
        if stored
        else f"Hello {name} with {theme} theme from v1"
    )


class UserService(user_pb2_grpc.UserServiceServicer):
    def GetUser(self, request, context):
        started = time.perf_counter()
        version = require_supported_version(context)
        name = request.name or DEFAULT_USER_NAME
        theme = normalize_theme(request.theme)
        record = get_user_record(name, theme)
        stored = record is not None
        message = get_user_message(name, theme, version, stored)
        status = get_user_status(version, stored)
        points = random.randint(100, 999) if version == "v2" else 0
        elapsed_ms = (time.perf_counter() - started) * 1000
        logging.info(
            "grpc method=GetUser name=%s theme=%s version=%s status=%s points=%s stored=%s elapsed_ms=%.2f",
            name,
            theme,
            version,
            status,
            points,
            stored,
            elapsed_ms,
        )
        return user_pb2.UserResponse(
            method="GetUser",
            name=name,
            status=status,
            version=version,
            message=message,
            theme=theme,
            points=points,
        )

    def CreateUser(self, request, context):
        started = time.perf_counter()
        storage_version = require_supported_version(context)
        name = request.name or DEFAULT_USER_NAME
        theme = normalize_theme(request.theme)
        response_version = "v1"
        record, created = save_user_record(name, theme, storage_version)
        status = "created" if created else "updated"
        elapsed_ms = (time.perf_counter() - started) * 1000
        logging.info(
            "grpc method=CreateUser name=%s theme=%s storage_version=%s response_version=%s status=%s store_path=%s elapsed_ms=%.2f",
            name,
            theme,
            storage_version,
            response_version,
            status,
            USER_STORE_PATH,
            elapsed_ms,
        )
        return user_pb2.UserResponse(
            method="CreateUser",
            name=name,
            status=status,
            version=response_version,
            message=f"{status.title()} {record['name']} with {record['theme']} theme in local store from {response_version}",
            theme=record["theme"],
        )

    def ListUsers(self, request, context):
        started = time.perf_counter()
        version = require_supported_version(context)
        records = list_user_records()
        elapsed_ms = (time.perf_counter() - started) * 1000
        logging.info(
            "grpc method=ListUsers version=%s count=%s elapsed_ms=%.2f",
            version,
            len(records),
            elapsed_ms,
        )
        return user_pb2.ListUsersResponse(
            users=[
                user_pb2.UserResponse(
                    method="ListUsers",
                    name=record["name"],
                    status=record.get("status", "active"),
                    version=version,
                    message=f"Loaded {record['name']} with {record['theme']} theme from local store in {version}",
                    theme=record["theme"],
                )
                for record in records
            ]
        )


def serve():
    port = os.getenv("PORT", "50051")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    user_pb2_grpc.add_UserServiceServicer_to_server(UserService(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logging.info("user-service listening on %s store_path=%s", port, USER_STORE_PATH)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
