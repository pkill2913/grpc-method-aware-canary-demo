#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter


METHODS = (
    ("GetUser", "GET"),
    ("CreateUser", "POST"),
)

DEFAULT_DISTRIBUTION = "GetUser=v1:90,v2:10;CreateUser=v1:100"
DEFAULT_MARGIN = 5.0


def env_bool(name, default="false"):
    return os.getenv(name, default).lower() in ("true", "1", "yes")


def parse_distribution(raw):
    """
    Format: GetUser=v1:90,v2:10;CreateUser=v1:100
    Weights are expected percentages and should sum to ~100 per method.
    """
    expected = {}
    for method_part in raw.split(";"):
        method_part = method_part.strip()
        if not method_part:
            continue
        if "=" not in method_part:
            raise ValueError(f"invalid distribution entry {method_part!r}; expected Method=v1:90,v2:10")

        method_name, weights_raw = method_part.split("=", 1)
        method_name = method_name.strip()
        weights = {}
        total = 0.0

        for weight_part in weights_raw.split(","):
            weight_part = weight_part.strip()
            if not weight_part:
                continue
            if ":" not in weight_part:
                raise ValueError(f"invalid weight {weight_part!r} for {method_name}; expected version:percent")

            version, percent_raw = weight_part.split(":", 1)
            version = version.strip()
            percent = float(percent_raw.strip())
            if percent < 0 or percent > 100:
                raise ValueError(f"percent out of range for {method_name}/{version}: {percent}")
            weights[version] = percent
            total += percent

        if abs(total - 100.0) > 0.01:
            raise ValueError(f"{method_name} weights must sum to 100, got {total}")

        expected[method_name] = weights

    return expected


def parse_args():
    parser = argparse.ArgumentParser(
        description="Load-test user-api and validate method-aware traffic distribution.",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("API_BASE_URL", "http://127.0.0.1").rstrip("/"),
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=int(os.getenv("REQUESTS", "100")),
    )
    parser.add_argument(
        "--user-name",
        default=os.getenv("USER_NAME", "demo-user"),
    )
    parser.add_argument(
        "--theme",
        default=os.getenv("THEME", "blue"),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("REQUEST_TIMEOUT", "5")),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=float(os.getenv("SLEEP_SECONDS", "0")),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=env_bool("QUIET"),
    )
    parser.add_argument(
        "--distribution",
        default=os.getenv("DISTRIBUTION", DEFAULT_DISTRIBUTION),
        help=f'Expected split per method, e.g. "{DEFAULT_DISTRIBUTION}"',
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=float(os.getenv("MARGIN", str(DEFAULT_MARGIN))),
        help=(
            "Absolute percentage-point tolerance around each expected weight. "
            f"Default {DEFAULT_MARGIN:g}: absolute percentage-point tolerance "
            "around each expected weight."
        ),
    )
    return parser.parse_args()


def get_user(api_base_url, user_name, theme, timeout):
    query = urllib.parse.urlencode({"name": user_name, "theme": theme})
    url = f"{api_base_url}/api/user?{query}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def create_user(api_base_url, name, theme, timeout):
    body = json.dumps({"name": name, "theme": theme}).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base_url}/api/user",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def call_method(method_name, index, args):
    if method_name == "GetUser":
        return get_user(args.api_base_url, args.user_name, args.theme, args.timeout)
    return create_user(
        args.api_base_url,
        f"{args.user_name}-{index + 1}",
        args.theme,
        args.timeout,
    )


def loading(index, method_name, requests_total):
    frames = [".  ", ".. ", "..."]
    frame = frames[index % len(frames)]
    sys.stdout.write(f"\rLoading{frame} {method_name} {index + 1}/{requests_total}")
    sys.stdout.flush()


def print_result(index, method_name, data):
    version = data.get("userServiceVersion", "unknown")
    print(
        f"{index + 1:03d} [{method_name}] "
        f"name={data.get('name')} "
        f"method={data.get('method')} "
        f"version={version}"
    )


def observed_percentages(versions):
    total_ok = sum(versions.values())
    if total_ok == 0:
        return {}, 0
    return {version: (count / total_ok) * 100 for version, count in versions.items()}, total_ok


def print_summary(title, versions, errors, expected=None, margin=None):
    percentages, total_ok = observed_percentages(versions)
    print(f"\n--- {title} ---")
    print(f"Successful: {total_ok}")
    print(f"Errors: {errors}")
    versions_to_show = set(percentages) | set(expected or {})
    for version in sorted(versions_to_show):
        observed = percentages.get(version, 0.0)
        count = versions.get(version, 0)
        line = f"{version}: {count} ({observed:.1f}%)"
        if expected is not None and version in expected:
            line += f" expected={expected[version]:g}% ±{margin:g}pp"
        print(line)


def within_margin(observed, expected, margin):
    return abs(observed - expected) <= margin


def validate(versions_by_method, errors_by_method, expected_by_method, margin):
    failures = []

    for method_name, _ in METHODS:
        errors = errors_by_method[method_name]
        if errors > 0:
            failures.append(f"ERROR: {method_name} had {errors} errors")

        if method_name not in expected_by_method:
            failures.append(f"ERROR: no expected distribution configured for {method_name}")
            continue

        expected = expected_by_method[method_name]
        percentages, total_ok = observed_percentages(versions_by_method[method_name])
        if total_ok == 0:
            failures.append(f"ERROR: {method_name} produced no successful responses")
            continue

        all_versions = set(percentages) | set(expected)
        for version in sorted(all_versions):
            observed = percentages.get(version, 0.0)
            expected_pct = expected.get(version, 0.0)
            if not within_margin(observed, expected_pct, margin):
                failures.append(
                    f"ERROR: {method_name} version {version} observed {observed:.1f}% "
                    f"but expected {expected_pct:g}% ±{margin:g}pp"
                )

    print("\n--- Validation ---")
    print(f"Margin: ±{margin:g} percentage points")
    for method_name, weights in expected_by_method.items():
        rendered = ", ".join(f"{version}={percent:g}%" for version, percent in sorted(weights.items()))
        print(f"Expected {method_name}: {rendered}")

    if failures:
        for message in failures:
            print(message)
        return 1

    print("PASS: observed traffic matched the expected distribution within the margin.")
    return 0


def main():
    args = parse_args()
    try:
        expected_by_method = parse_distribution(args.distribution)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if args.margin < 0:
        print("ERROR: --margin must be >= 0", file=sys.stderr)
        return 2

    versions_by_method = {method_name: Counter() for method_name, _ in METHODS}
    errors_by_method = {method_name: 0 for method_name, _ in METHODS}
    start = time.time()

    for index in range(args.requests):
        for method_name, _ in METHODS:
            if args.quiet:
                loading(index, method_name, args.requests)

            try:
                data = call_method(method_name, index, args)
                version = data.get("userServiceVersion", "unknown")
                versions_by_method[method_name][version] += 1

                if not args.quiet:
                    print_result(index, method_name, data)
            except Exception as error:
                errors_by_method[method_name] += 1
                if not args.quiet:
                    print(f"{index + 1:03d} [{method_name}] ERROR {error}")

            if args.sleep > 0:
                time.sleep(args.sleep)

    if args.quiet:
        sys.stdout.write("\rDone. " + " " * 40 + "\n")

    elapsed = time.time() - start
    total_requests = args.requests * len(METHODS)
    total_ok = sum(sum(counter.values()) for counter in versions_by_method.values())
    total_errors = sum(errors_by_method.values())

    print("\n--- Summary ---")
    print(f"Iterations: {args.requests}")
    print(f"Total requests: {total_requests}")
    print(f"Successful: {total_ok}")
    print(f"Errors: {total_errors}")
    print(f"Elapsed: {elapsed:.2f}s")
    print(f"Rate: {total_requests / elapsed:.2f} req/s")

    for method_name, _ in METHODS:
        print_summary(
            method_name,
            versions_by_method[method_name],
            errors_by_method[method_name],
            expected=expected_by_method.get(method_name),
            margin=args.margin,
        )

    return validate(versions_by_method, errors_by_method, expected_by_method, args.margin)


if __name__ == "__main__":
    sys.exit(main())
