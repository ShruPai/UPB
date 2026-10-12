#!/usr/bin/env python3
"""Check every dependency in a requirements.txt against the PyPI JSON API.

Usage: python3 check_requirements.py [path/to/requirements.txt]
(on macOS, `pip install truststore` if you hit CERTIFICATE_VERIFY_FAILED)
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:  # use the OS certificate store (fixes SSL errors on macOS)
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

URL = "https://pypi.org/pypi/{}/json"
# package name, optional [extras], then the version specifier
REQ = re.compile(r"^([A-Za-z0-9][\w.-]*)\s*(?:\[[^\]]*\])?\s*(.*)")
PIN = re.compile(r"===?\s*([^\s,;]+)")  # exact pin: == or ===


def norm(name):
    """PyPI treats Flask, flask, typing_extensions, typing-extensions alike."""
    return re.sub(r"[-_.]+", "-", name).lower()


def canon(version):
    """Make 1.0 and 1.0.0 compare equal."""
    return re.sub(r"(\.0)+$", "", version.lower().lstrip("v"))


def parse(path):
    """Return ([(name, pinned_version_or_None), ...], number_of_skipped_lines)."""
    reqs, skipped = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = re.split(r"\s#|;", line)[0].strip()  # drop comments / markers
            if not line or line.startswith("#"):
                continue
            m = REQ.match(line)
            # skip options (-r, -e), paths, URLs and "name @ url" lines
            if not m or "://" in line or " @ " in line:
                skipped += 1
                continue
            pin = PIN.search(m.group(2))
            version = pin.group(1) if pin and "*" not in pin.group(1) else None
            reqs.append((m.group(1), version))
    return reqs, skipped


def fetch(name):
    """Project JSON, None if the project doesn't exist (404). Raises OSError otherwise."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(URL.format(norm(name)), timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except OSError as e:  # network, SSL, timeout
            err = e
        time.sleep(attempt + 1)
    raise err


def lookup(name):
    try:
        return name, fetch(name)
    except OSError as e:
        return name, e  # keep the error so it isn't mistaken for "not found"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "requirements.txt"
    try:
        reqs, skipped = parse(path)
    except FileNotFoundError:
        print(f"File not found: {path}", file=sys.stderr)
        return 2

    # one request per unique package, run in parallel
    with ThreadPoolExecutor(max_workers=10) as pool:
        projects = dict(pool.map(lookup, {norm(n) for n, _ in reqs}))

    no_package = no_version = errors = 0
    last_error = None
    oldest = None  # (upload_time, name, version)

    for name, version in reqs:
        project = projects[norm(name)]
        if isinstance(project, OSError):
            errors, last_error = errors + 1, project
            continue
        if project is None:
            no_package += 1
            continue
        if version is None:  # not pinned: only existence is checked
            continue

        releases = project["releases"]
        key = next((k for k in releases if canon(k) == canon(version)), None)
        if key is None:
            no_version += 1
            continue

        files = releases[key]
        if files:  # earliest upload time of the release (ISO strings sort correctly)
            uploaded = min(f["upload_time_iso_8601"] for f in files)
            if oldest is None or uploaded < oldest[0]:
                oldest = (uploaded, name, version)

    print(f"Dependencies checked: {len(reqs)}")
    print(f"Packages not on PyPI: {no_package}")
    print(f"Version not on PyPI: {no_version}")
    if oldest:
        print(f"Oldest pinned release: {oldest[1]}=={oldest[2]} ({oldest[0][:4]})")
    else:
        print("Oldest pinned release: none")
    if skipped:
        print(f"Skipped lines (options, URLs, paths): {skipped}")
    if errors:
        print(f"Could not check due to network errors: {errors} (last: {last_error})")

    return 1 if (no_package or no_version or errors) else 0


if __name__ == "__main__":
    sys.exit(main())