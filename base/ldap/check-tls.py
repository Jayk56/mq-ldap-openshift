"""Check that the directory presents a trusted certificate for the name its clients use."""
import os
from pathlib import Path
import socket
import ssl

trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
for cert in Path("/data/tls/ca").glob("*.crt"):
    trust.load_verify_locations(cafile=str(cert))
hostname = f"mq-ldap.{os.environ['POD_NAMESPACE']}.svc"
with socket.create_connection(("127.0.0.1", 3636), timeout=3) as sock:
    with trust.wrap_socket(sock, server_hostname=hostname):
        pass
