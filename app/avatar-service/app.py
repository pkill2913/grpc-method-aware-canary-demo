import os

from flask import Flask, jsonify


app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "avatar-service"})


@app.get("/avatar")
def get_avatar():
    return jsonify(
        {
            "avatar": "\U0001F464",
            "version": os.getenv("APP_VERSION", "v1"),
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
