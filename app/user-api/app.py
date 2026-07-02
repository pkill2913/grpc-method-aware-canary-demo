import logging
import os
import sys

import grpc
import requests
from flask import Flask, jsonify, request

import user_pb2
import user_pb2_grpc


def configure_logging():
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )


configure_logging()
logger = logging.getLogger("user-api")

app = Flask(__name__)

AVATAR_SERVICE_URL = os.getenv("AVATAR_SERVICE_URL", "http://avatar-service:8080").rstrip("/")
USER_SERVICE_HOST = os.getenv("USER_SERVICE_HOST", "user-service:50051")
DEFAULT_USER_NAME = os.getenv("DEFAULT_USER_NAME", "demo-user")
DEFAULT_THEME = os.getenv("DEFAULT_THEME", "blue")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "3"))


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "user-api"})


@app.get("/api/user")
def get_user():
    name = request.args.get("name") or DEFAULT_USER_NAME
    selected_theme = request.args.get("theme") or DEFAULT_THEME
    logger.info(
        "GET /api/user name=%s theme=%s avatar_url=%s grpc_host=%s",
        name,
        selected_theme,
        AVATAR_SERVICE_URL,
        USER_SERVICE_HOST,
    )

    try:
        avatar = fetch_json(AVATAR_SERVICE_URL, "/avatar")
        user = call_user_service("GetUser", name, selected_theme)
    except requests.RequestException as exc:
        logger.error(
            "avatar-service HTTP request failed url=%s/avatar timeout=%ss error=%s",
            AVATAR_SERVICE_URL,
            REQUEST_TIMEOUT,
            exc,
            exc_info=True,
        )
        return jsonify({"error": "internal HTTP service request failed", "detail": str(exc)}), 502
    except ValueError as exc:
        logger.error(
            "avatar-service returned invalid JSON url=%s/avatar error=%s",
            AVATAR_SERVICE_URL,
            exc,
            exc_info=True,
        )
        return jsonify({"error": "internal HTTP service returned invalid JSON", "detail": str(exc)}), 502
    except (grpc.RpcError, grpc.FutureTimeoutError) as exc:
        return grpc_error_response(exc, method="GetUser", name=name, theme=selected_theme)

    logger.info("GET /api/user succeeded name=%s theme=%s", name, selected_theme)
    return jsonify(build_user_payload(user, avatar=avatar))


@app.post("/api/user")
def create_user():
    body = request.get_json(silent=True) or {}
    name = body.get("name") or request.args.get("name") or DEFAULT_USER_NAME
    theme = body.get("theme") or request.args.get("theme")
    logger.info(
        "POST /api/user name=%s theme=%s grpc_host=%s",
        name,
        theme,
        USER_SERVICE_HOST,
    )

    try:
        user = call_user_service("CreateUser", name, theme)
    except (grpc.RpcError, grpc.FutureTimeoutError) as exc:
        return grpc_error_response(exc, method="CreateUser", name=name, theme=theme)

    logger.info("POST /api/user succeeded name=%s", name)
    return jsonify(build_user_payload(user))


@app.get("/api/users")
def list_users():
    logger.info("GET /api/users grpc_host=%s", USER_SERVICE_HOST)
    try:
        users = call_user_service_list()
    except (grpc.RpcError, grpc.FutureTimeoutError) as exc:
        return grpc_error_response(exc, method="ListUsers")

    logger.info("GET /api/users succeeded count=%d", len(users))
    return jsonify(
        {
            "users": [build_user_payload(user) for user in users],
            "versions": {
                "userService": users[0]["version"] if users else "unknown",
            },
        }
    )


def fetch_json(base_url, path):
    url = f"{base_url}{path}"
    logger.info("HTTP GET %s timeout=%ss", url, REQUEST_TIMEOUT)
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    logger.info(
        "HTTP GET %s status=%s elapsed=%.3fs",
        url,
        response.status_code,
        response.elapsed.total_seconds(),
    )
    response.raise_for_status()
    return response.json()


def call_user_service(method_name, name, theme=None):
    logger.info(
        "gRPC %s host=%s name=%s theme=%s timeout=%ss",
        method_name,
        USER_SERVICE_HOST,
        name,
        theme,
        REQUEST_TIMEOUT,
    )
    channel = grpc.insecure_channel(USER_SERVICE_HOST)
    try:
        logger.debug("gRPC waiting for channel ready host=%s", USER_SERVICE_HOST)
        grpc.channel_ready_future(channel).result(timeout=REQUEST_TIMEOUT)
        logger.debug("gRPC channel ready host=%s", USER_SERVICE_HOST)
        stub = user_pb2_grpc.UserServiceStub(channel)
        method = getattr(stub, method_name)
        response = method(user_pb2.UserRequest(name=name, theme=theme or ""), timeout=REQUEST_TIMEOUT)
        logger.info("gRPC %s succeeded host=%s name=%s", method_name, USER_SERVICE_HOST, name)
        return grpc_response_to_dict(response)
    finally:
        channel.close()


def call_user_service_list():
    logger.info(
        "gRPC ListUsers host=%s timeout=%ss",
        USER_SERVICE_HOST,
        REQUEST_TIMEOUT,
    )
    channel = grpc.insecure_channel(USER_SERVICE_HOST)
    try:
        logger.debug("gRPC waiting for channel ready host=%s", USER_SERVICE_HOST)
        grpc.channel_ready_future(channel).result(timeout=REQUEST_TIMEOUT)
        logger.debug("gRPC channel ready host=%s", USER_SERVICE_HOST)
        stub = user_pb2_grpc.UserServiceStub(channel)
        response = stub.ListUsers(user_pb2.ListUsersRequest(), timeout=REQUEST_TIMEOUT)
        logger.info("gRPC ListUsers succeeded host=%s count=%d", USER_SERVICE_HOST, len(response.users))
        return [grpc_response_to_dict(user) for user in response.users]
    finally:
        channel.close()


def grpc_response_to_dict(response):
    payload = {
        "method": response.method,
        "name": response.name,
        "status": response.status,
        "version": response.version,
        "message": response.message,
        "theme": response.theme,
    }
    if response.points > 0:
        payload["points"] = response.points
    return payload


def build_user_payload(user, avatar=None):
    payload = {
        "method": user["method"],
        "name": user["name"],
        "status": user["status"],
        "userServiceVersion": user["version"],
        "message": user["message"],
        "theme": user.get("theme"),
        "versions": {
            "userService": user["version"],
        },
    }
    if "points" in user:
        payload["points"] = user["points"]

    if avatar:
        payload["avatar"] = avatar.get("avatar")
        payload["versions"]["avatarService"] = avatar.get("version")

    return payload


def grpc_error_response(exc, method=None, name=None, theme=None):
    if isinstance(exc, grpc.RpcError):
        code = exc.code().name if exc.code() else "UNKNOWN"
        detail = exc.details()
        logger.error(
            "gRPC %s failed host=%s name=%s theme=%s code=%s detail=%s",
            method or "unknown",
            USER_SERVICE_HOST,
            name,
            theme,
            code,
            detail,
            exc_info=True,
        )
    else:
        code = "UNAVAILABLE"
        detail = f"timed out connecting to {USER_SERVICE_HOST}"
        logger.error(
            "gRPC %s connection timeout host=%s name=%s theme=%s timeout=%ss detail=%s",
            method or "unknown",
            USER_SERVICE_HOST,
            name,
            theme,
            REQUEST_TIMEOUT,
            detail,
            exc_info=True,
        )

    return (
        jsonify(
            {
                "error": "user-service gRPC request failed",
                "grpcCode": code,
                "detail": detail,
            }
        ),
        502,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    logger.info(
        "starting user-api port=%s avatar_url=%s grpc_host=%s timeout=%ss log_level=%s",
        port,
        AVATAR_SERVICE_URL,
        USER_SERVICE_HOST,
        REQUEST_TIMEOUT,
        os.getenv("LOG_LEVEL", "INFO"),
    )
    app.run(host="0.0.0.0", port=port)
