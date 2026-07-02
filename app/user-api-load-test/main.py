#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1").rstrip("/")
REQUESTS = int(os.getenv("REQUESTS", "100"))
USER_NAME = os.getenv("USER_NAME", "demo-user")
THEME = os.getenv("THEME", "blue")
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5"))
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", "0"))
QUIET = os.getenv("QUIET", "false").lower() in ("true", "1", "yes")
CREATE_USER_STABLE_VERSION = "v1"

METHODS = (
    ("GetUser", "GET"),
    ("CreateUser", "POST"),
)


def get_user():
    query = urllib.parse.urlencode({"name": USER_NAME, "theme": THEME})
    url = f"{API_BASE_URL}/api/user?{query}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def create_user(name):
    body = json.dumps({"name": name, "theme": THEME}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE_URL}/api/user",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def call_method(method_name, index):
    if method_name == "GetUser":
        return get_user()
    return create_user(f"{USER_NAME}-{index + 1}")


def loading(index, method_name):
    frames = [".  ", ".. ", "..."]
    frame = frames[index % len(frames)]
    sys.stdout.write(f"\rLoading{frame} {method_name} {index + 1}/{REQUESTS}")
    sys.stdout.flush()


def print_result(index, method_name, data):
    version = data.get("userServiceVersion", "unknown")
    print(
        f"{index + 1:03d} [{method_name}] "
        f"name={data.get('name')} "
        f"method={data.get('method')} "
        f"version={version}"
    )


def print_summary(title, versions, errors):
    total_ok = sum(versions.values())
    print(f"\n--- {title} ---")
    print(f"Successful: {total_ok}")
    print(f"Errors: {errors}")
    for version, count in sorted(versions.items()):
        percentage = (count / total_ok * 100) if total_ok else 0
        print(f"{version}: {count} ({percentage:.1f}%)")


def validate(versions_by_method, errors_by_method):
    failures = []

    get_user_errors = errors_by_method["GetUser"]
    if get_user_errors > 0:
        failures.append(f"ERROR: GetUser had {get_user_errors} errors")

    create_user_errors = errors_by_method["CreateUser"]
    if create_user_errors > 0:
        failures.append(f"ERROR: CreateUser had {create_user_errors} errors")

    for version, count in sorted(versions_by_method["CreateUser"].items()):
        if version != CREATE_USER_STABLE_VERSION and count > 0:
            failures.append(f"ERROR: CreateUser reached an unexpected version: {version}")

    if failures:
        print("\n--- Validation ---")
        for message in failures:
            print(message)
        return 1

    print("\n--- Validation ---")
    print("PASS: GetUser completed successfully and can be served by stable or canary versions.")
    print("PASS: CreateUser remained exclusively on the stable version.")
    return 0


def main():
    versions_by_method = {method_name: Counter() for method_name, _ in METHODS}
    errors_by_method = {method_name: 0 for method_name, _ in METHODS}
    start = time.time()

    for index in range(REQUESTS):
        for method_name, _ in METHODS:
            if QUIET:
                loading(index, method_name)

            try:
                data = call_method(method_name, index)
                version = data.get("userServiceVersion", "unknown")
                versions_by_method[method_name][version] += 1

                if not QUIET:
                    print_result(index, method_name, data)
            except Exception as error:
                errors_by_method[method_name] += 1
                if not QUIET:
                    print(f"{index + 1:03d} [{method_name}] ERROR {error}")

            if SLEEP_SECONDS > 0:
                time.sleep(SLEEP_SECONDS)

    if QUIET:
        sys.stdout.write("\rDone. " + " " * 40 + "\n")

    elapsed = time.time() - start
    total_requests = REQUESTS * len(METHODS)
    total_ok = sum(sum(counter.values()) for counter in versions_by_method.values())
    total_errors = sum(errors_by_method.values())

    print("\n--- Summary ---")
    print(f"Iterations: {REQUESTS}")
    print(f"Total requests: {total_requests}")
    print(f"Successful: {total_ok}")
    print(f"Errors: {total_errors}")
    print(f"Elapsed: {elapsed:.2f}s")
    print(f"Rate: {total_requests / elapsed:.2f} req/s")

    for method_name, _ in METHODS:
        print_summary(method_name, versions_by_method[method_name], errors_by_method[method_name])

    return validate(versions_by_method, errors_by_method)


if __name__ == "__main__":
    sys.exit(main())
