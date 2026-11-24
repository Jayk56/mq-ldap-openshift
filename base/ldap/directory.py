"""Set up directory accounts and manage the IBM MQ catalog."""
import argparse
from contextlib import contextmanager
import fcntl
from pathlib import Path
import subprocess
import sys
import unicodedata

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
REQUIRED_FIELDS = ("objectClass", "cn", *OBJECT_FIELDS)
CATALOG_FIELDS = (*REQUIRED_FIELDS, "javaClassNames")
FIELD_NAMES = {field.lower(): field for field in CATALOG_FIELDS}
ACTIONS = ("validate", "initialize", "writer", "publish", "check-admin")


def connect(dn, password):
    conn = ldap.initialize(SOCKET)
    conn.simple_bind_s(dn, password)
    return conn


def password_value(value):
    value = value.rstrip(b"\r\n")
    if not value or b"\n" in value or b"\r" in value:
        raise ValueError("Provide a nonempty, single-line password")
    return value


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


def catalog_attributes(raw):
    attrs = {}
    for key, values in raw.items():
        attrs.setdefault(FIELD_NAMES.get(key.lower(), key), []).extend(values)
    return attrs


def cn_key(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def check_addresses(dn, values):
    positions = set()
    for value in values:
        address = value.decode("utf-8")
        parts = address.split(address[0], 3) if address else []
        if len(parts) != 4 or not parts[1].isascii() or not parts[1].isdigit() or not parts[2]:
            raise ValueError(f"Expected a numbered javaReferenceAddress with a property name: {dn}")
        positions.add(int(parts[1]))
    if not values or positions != set(range(len(values))):
        raise ValueError(f"Number the javaReferenceAddress values once each from 0: {dn}")


def catalog_entries(stream):
    entries = []
    names = set()
    for dn, raw in records(stream):
        attrs = catalog_attributes(raw)
        try:
            parts = ldap.dn.str2dn(dn)
        except (ldap.DECODING_ERROR, ldap.INVALID_DN_SYNTAX) as error:
            raise ValueError(f"Invalid LDAP entry name (DN): {dn}") from error
        in_catalog = ldap.dn.dn2str(parts[1:]).casefold() == CATALOG.casefold()
        if not in_catalog or len(parts[0]) != 1 or parts[0][0][0].lower() != "cn":
            raise ValueError(f"Expected a cn immediately below {CATALOG}: {dn}")
        missing = [field for field in REQUIRED_FIELDS if not attrs.get(field)]
        if missing:
            raise ValueError(f"{dn} is missing {', '.join(missing)}")
        extra = sorted(set(attrs) - set(CATALOG_FIELDS))
        if extra:
            raise ValueError(f"{dn} has unsupported catalog fields: {', '.join(extra)}")
        if b"javanamingreference" not in [v.lower() for v in attrs["objectClass"]]:
            raise ValueError(f"{dn} needs objectClass: javaNamingReference")
        name = cn_key(parts[0][0][1])
        if not name or name not in {cn_key(v.decode("utf-8")) for v in attrs["cn"]}:
            raise ValueError(f"The cn attribute must include the name in the DN: {dn}")
        for key in ("javaClassName", "javaFactory"):
            if len(attrs[key]) != 1 or not attrs[key][0].strip():
                raise ValueError(f"Expected one nonempty {key}: {dn}")
        check_addresses(dn, attrs["javaReferenceAddress"])
        if name in names:
            raise ValueError(f"Duplicate DN in input: {dn}")
        names.add(name)
        entries.append((dn, attrs))
    if not entries:
        raise ValueError("Expected at least one RFC 2713 entry on stdin")
    return entries


def publish_entry(conn, dn, attrs):
    # JMS30Admin can replace a name between the read and the write.
    for _ in range(3):
        entry = get_entry(conn, dn, CATALOG_FIELDS)
        try:
            if entry is None:
                conn.add_s(dn, ldap.modlist.addModlist(attrs))
                return "Added"
            entry = catalog_attributes(entry)
            if b"javanamingreference" not in [v.lower() for v in entry["objectClass"]]:
                raise ValueError(f"Existing entry has a different purpose: {dn}")
            changes = [(ldap.MOD_REPLACE, key, attrs.get(key, []))
                       for key in (*OBJECT_FIELDS, "javaClassNames")
                       if set(entry.get(key, [])) != set(attrs.get(key, []))]
            if changes:
                conn.modify_s(dn, changes)
            return "Updated" if changes else "Current"
        except (ldap.ALREADY_EXISTS, ldap.NO_SUCH_OBJECT):
            continue
    raise ValueError(f"Could not publish {dn}. Check the catalog exists and retry with one writer.")


def admin_connection():
    return connect(ADMIN, password_value(Path(PASSWORD_FILE).read_bytes()))


def publish_entries(conn, entries):
    for dn, attrs in entries:
        print(publish_entry(conn, dn, attrs), dn)


def publish(conn, stream):
    publish_entries(conn, catalog_entries(stream))


@contextmanager
def locked_admin():
    with open("/data/run/mq-catalog.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        admin = admin_connection()
        try:
            yield admin
        finally:
            admin.unbind_s()


def require_parent(admin, dn):
    if get_entry(admin, dn, ["1.1"]) is None:
        raise ValueError(f"{dn} is missing. Run initialize with the reader password first.")


def run(action, source=None):
    if action not in ACTIONS:
        raise ValueError(f"Unknown directory action: {action}")
    if action == "check-admin":
        try:
            admin_connection().unbind_s()
        except (ldap.SERVER_DOWN, ldap.INVALID_CREDENTIALS):
            sys.exit("Waiting for the directory to accept the administrator password")
        return
    if source is None:
        source = sys.stdin.buffer
    catalog_input = action in ("publish", "validate")
    if source.isatty():
        expected = "a catalog LDIF file" if catalog_input else "a password file"
        raise ValueError(f"{action} reads {expected} from standard input. Redirect the file with <.")
    if catalog_input:
        entries = catalog_entries(source)
        if action == "validate":
            count = len(entries)
            print(f"Validated {count} catalog {'entry' if count == 1 else 'entries'}.")
        else:
            with locked_admin() as admin:
                require_parent(admin, CATALOG)
                publish_entries(admin, entries)
        return

    password = password_value(source.read())
    with locked_admin() as admin:
        if action == "initialize":
            initialize(admin)
            set_account(admin, "tomcat-reader", password)
        else:
            require_parent(admin, "ou=services," + BASE)
            set_account(admin, "mq-publisher", password)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""  validate    Check a catalog file offline.       validate < catalog.ldif
  initialize  Set up the tree and reader account. initialize < reader-password
  writer      Set the workstation account.       writer < publisher-password
  publish     Apply the catalog to 389 DS.        publish < catalog.ldif
  check-admin Check the local administrator login (used at pod startup).""")
    parser.add_argument("action", choices=ACTIONS)
    try:
        run(parser.parse_args().action)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
