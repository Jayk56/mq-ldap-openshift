"""Read the administrator password from its file and start 389 DS."""
import os
from pathlib import Path

password = Path("/etc/mq-ldap-admin/password").read_text(encoding="utf-8").rstrip("\r\n")
if not password or "\n" in password or "\r" in password:
    raise SystemExit("Directory Manager needs a nonempty single-line password")
os.environ["DS_DM_PASSWORD"] = password
entrypoint = "/usr/libexec/dirsrv/dscontainer"
os.execv(entrypoint, [entrypoint, "-r"])
