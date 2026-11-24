#!/usr/bin/env python3
"""Interactively change Math School passwords; keep existing device tokens."""
import getpass
import json
import os
import pathlib
import sys
import tempfile
import time

path = pathlib.Path("/etc/math-school/credentials.json")
if os.geteuid() != 0:
    sys.exit("Run as root.")
original = json.loads(path.read_text())
st = path.stat()
for role in ("student", "parent"):
    if not isinstance(original[role]["token"], str):
        sys.exit("Invalid credentials file. No changes made.")

def ask(label):
    while True:
        password = getpass.getpass(label + " (Enter = keep current): ")
        if not password:
            return None
        if len(password) < 12 or len(password) > 128 or not password.isascii() or not password.isprintable() or any(c.isspace() for c in password):
            print("Use 12-128 characters: Latin letters, digits or symbols, no spaces.")
            continue
        if getpass.getpass("Repeat password: ") != password:
            print("Passwords do not match. Try again.")
            continue
        return password

print("Math School: change passwords. Typed passwords are not displayed.")
try:
    student = ask("Student")
    parent = ask("Parent")
except (KeyboardInterrupt, EOFError):
    sys.exit("\nCancelled. No changes made.")
if not student and not parent:
    sys.exit("No changes requested.")
if (student or original["student"]["password"]) == (parent or original["parent"]["password"]):
    sys.exit("Student and Parent passwords must be different. No changes made.")
for role, password in (("student", student), ("parent", parent)):
    if password:
        original[role]["password"] = password
backup = pathlib.Path("/root") / ("math-school-credentials-" + str(time.time_ns()) + ".json.bak")
with backup.open("xb") as f:
    os.fchmod(f.fileno(), 0o600)
    f.write(path.read_bytes())
fd, tmp = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
try:
    with os.fdopen(fd, "w") as f:
        os.fchown(f.fileno(), st.st_uid, st.st_gid)
        os.fchmod(f.fileno(), st.st_mode & 0o777)
        json.dump(original, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
print("PASSWORDS UPDATED. Existing device sessions are preserved.")
