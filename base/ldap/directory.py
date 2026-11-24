"""Set up directory accounts or load queue names. Supply the account password or LDIF file on standard input."""
import argparse
import fcntl
from pathlib import Path
import subprocess
import sys

import ldap
import ldap.dn
import ldap.modlist
import ldif

SOCKET = "ldapi://%2Fdata%2Frun%2Fslapd-localhost.socket"
ADMIN = "cn=Directory Manager"
PASSWORD_FILE = "/etc/mq-ldap-admin/password"
BASE = "dc=messaging"
CATALOG = "ou=mq," + BASE
OBJECT_FIELDS = ("javaClassName", "javaFactory", "javaReferenceAddress")


def connect(dn, password):
    conn = ldap.initialize(SOCKET)
    conn.simple_bind_s(dn, password)
    return conn


def get_entry(conn, dn, fields=None):
    try:
        return conn.search_s(dn, ldap.SCOPE_BASE, "(objectClass=*)", fields)[0][1]
    except ldap.NO_SUCH_OBJECT:
        return None


def records(stream):
    parser = ldif.LDIFRecordList(stream)
    parser.parse()
    return parser.all_records


def initialize(conn):
    backend = "cn=mqcatalog,cn=ldbm database,cn=plugins,cn=config"
    existing = get_entry(conn, backend)
    if existing is None:
        subprocess.run(["dsconf", "-D", ADMIN, "-y", PASSWORD_FILE, SOCKET,
                        "backend", "create", "--suffix", BASE, "--be-name", "mqcatalog"],
                       check=True)
    elif BASE.encode() not in existing.get("nsslapd-suffix", []):
        raise ValueError("The mqcatalog backend belongs to another suffix")

    with Path(__file__).with_name("tree.ldif").open("rb") as source:
        for dn, attrs in records(source):
            entry = get_entry(conn, dn, ["objectClass", "aci"])
            if entry is None:
                conn.add_s(dn, ldap.modlist.addModlist(attrs))
            else:
                for aci in attrs.get("aci", []):
                    if aci not in entry.get("aci", []):
                        conn.modify_s(dn, [(ldap.MOD_ADD, "aci", aci)])

def set_account(conn, uid, password):
    if not password or b"\n" in password or b"\r" in password:
        raise ValueError("Provide a nonempty, single-line password on stdin")
    dn = "uid=" + uid + ",ou=services," + BASE
    if get_entry(conn, dn) is None:
        attrs = {"objectClass": [b"top", b"account", b"simpleSecurityObject"],
                 "uid": [uid.encode()], "userPassword": [password]}
        conn.add_s(dn, ldap.modlist.addModlist(attrs))
    else:
        try:
            connect(dn, password).unbind_s()
        except ldap.INVALID_CREDENTIALS:
            conn.modify_s(dn, [(ldap.MOD_REPLACE, "userPassword", password)])
    print("Ready:", CATALOG, "and", dn)


def publish(conn, stream):
    expected = {"objectClass", "cn", *OBJECT_FIELDS}
    spelling = {key.casefold(): key for key in (*expected, "javaClassNames")}
    entries = []
    names = set()
    for dn, raw in records(stream):
        attrs = {spelling.get(key.casefold(), key): values for key, values in raw.items()}
        parts = ldap.dn.str2dn(dn)
        in_catalog = ldap.dn.dn2str(parts[1:]).casefold() == CATALOG.casefold()
        if not in_catalog or len(parts[0]) != 1 or parts[0][0][0].lower() != "cn":
            raise ValueError(f"Expected a cn immediately below {CATALOG}: {dn}")
        if set(attrs) - {"javaClassNames"} != expected or b"javanamingreference" not in [
                value.lower() for value in attrs["objectClass"]]:
            raise ValueError(f"Expected an RFC 2713 reference with fields {sorted(expected)}: {dn}")
        if dn.casefold() in names:
            raise ValueError(f"Duplicate DN in input: {dn}")
        names.add(dn.casefold())
        entries.append((dn, attrs))
    if not entries:
        raise ValueError("Expected at least one RFC 2713 entry on stdin")

    for dn, attrs in entries:
        entry = get_entry(conn, dn)
        if entry is None:
            try:
                conn.add_s(dn, ldap.modlist.addModlist(attrs))
                print("Added", dn)
                continue
            except ldap.ALREADY_EXISTS:
                entry = get_entry(conn, dn)
        if b"javanamingreference" not in [value.lower() for value in entry["objectClass"]]:
            raise ValueError(f"Existing entry has a different purpose: {dn}")
        fields = (*OBJECT_FIELDS, "javaClassNames") if "javaClassNames" in attrs else OBJECT_FIELDS
        changes = [(ldap.MOD_REPLACE, key, attrs[key]) for key in fields
                   if set(entry.get(key, [])) != set(attrs[key])]
        if changes:
            conn.modify_s(dn, changes)
        print("Updated" if changes else "Current", dn)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialize", "writer", "publish"])
    args = parser.parse_args()
    with open("/data/run/mq-catalog.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        admin = connect(ADMIN, Path(PASSWORD_FILE).read_bytes().rstrip(b"\r\n"))
        try:
            if args.action == "initialize":
                initialize(admin)
                set_account(admin, "tomcat-reader", sys.stdin.buffer.read().rstrip(b"\r\n"))
            elif args.action == "writer":
                set_account(admin, "mq-publisher", sys.stdin.buffer.read().rstrip(b"\r\n"))
            else:
                publish(admin, sys.stdin.buffer)
        finally:
            admin.unbind_s()
