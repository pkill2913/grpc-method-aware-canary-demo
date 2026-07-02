import os

import requests
from flask import Flask, Response, jsonify, render_template, request


app = Flask(__name__, static_folder="static", template_folder="templates")

USER_API_URL = os.getenv("USER_API_URL", "http://user-api:8080").rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "3"))


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "frontend"})


@app.get("/api/user")
def get_user():
    return proxy_to_user_api("GET", "/api/user")


@app.post("/api/user")
def create_user():
    return proxy_to_user_api("POST", "/api/user")


@app.get("/api/users")
def list_users():
    return proxy_to_user_api("GET", "/api/users")


def proxy_to_user_api(method, path):
    target = f"{USER_API_URL}{path}"
    try:
        if method == "GET":
            upstream = requests.get(target, params=request.args, timeout=REQUEST_TIMEOUT)
        else:
            upstream = requests.post(
                target,
                params=request.args,
                json=request.get_json(silent=True) or {},
                timeout=REQUEST_TIMEOUT,
            )
    except requests.RequestException as exc:
        return (
            jsonify(
                {
                    "error": "user-api unavailable",
                    "detail": str(exc),
                }
            ),
            502,
        )

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("content-type", "application/json"),
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
