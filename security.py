"""TLS for Escape Plan.

Every byte between client and server goes through TLS, so nobody on the same
Wi-Fi can read or change the moves. The server owns a private key and a
self-signed certificate in ``certs/``. Clients keep a copy of the certificate
only (never the key) and refuse to talk to any server that can't prove it has
the matching key. That is "certificate pinning": no public CA is involved, and
it keeps working even if the server's IP address changes.

Run ``python3 security.py`` to create the certificate ahead of time, or
``python3 security.py --force`` to replace it (clients then need the new
``certs/server.crt``).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import ipaddress
import os
import shutil
import socket
import ssl
import subprocess
from pathlib import Path


CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT_FILE = CERT_DIR / "server.crt"
KEY_FILE = CERT_DIR / "server.key"

# One JSON message per line. Anything longer than this is not a real message.
MAX_MESSAGE_BYTES = 4096


class CertificateMissing(RuntimeError):
    pass


class UntrustedServer(ssl.SSLError):
    pass


def local_addresses() -> list[str]:
    """IPv4 addresses this computer is likely reachable on."""

    found = {"127.0.0.1"}
    try:
        # Connecting a UDP socket sends nothing; it just picks the LAN route.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(found)


def ensure_certificate(force: bool = False, extra_hosts: list[str] | None = None) -> bool:
    """Create the server key + certificate if needed. Returns True if created."""

    if CERT_FILE.exists() and KEY_FILE.exists() and not force:
        return False

    CERT_DIR.mkdir(exist_ok=True)
    hosts = local_addresses() + list(extra_hosts or [])
    try:
        _create_with_cryptography(hosts)
    except ImportError:
        _create_with_openssl(hosts)
    os.chmod(KEY_FILE, 0o600)
    return True


def _create_with_cryptography(hosts: list[str]) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Escape Plan server")])
    alt_names: list[x509.GeneralName] = [x509.DNSName("localhost")]
    for host in dict.fromkeys(hosts):
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            alt_names.append(x509.DNSName(host))

    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    KEY_FILE.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    CERT_FILE.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def _create_with_openssl(hosts: list[str]) -> None:
    if shutil.which("openssl") is None:
        raise RuntimeError(
            "Can't create a certificate: install the 'cryptography' package "
            "(pip install cryptography) or the openssl command-line tool."
        )
    alt = ["DNS:localhost"]
    for host in dict.fromkeys(hosts):
        try:
            ipaddress.ip_address(host)
            alt.append("IP:" + host)
        except ValueError:
            alt.append("DNS:" + host)
    subprocess.run(
        [
            "openssl", "req", "-x509", "-nodes", "-days", "365",
            "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
            "-subj", "/CN=Escape Plan server",
            "-addext", "subjectAltName=" + ",".join(alt),
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "extendedKeyUsage=serverAuth",
        ],
        check=True,
        capture_output=True,
    )


def fingerprint(der_bytes: bytes) -> str:
    digest = hashlib.sha256(der_bytes).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def pinned_fingerprint(cert_file: Path = CERT_FILE) -> str:
    pem = cert_file.read_text(encoding="ascii")
    return fingerprint(ssl.PEM_cert_to_DER_cert(pem))


def short_fingerprint(value: str) -> str:
    """First few byte pairs; enough to compare by eye on two screens."""

    return ":".join(value.split(":")[:6])


def server_context(cert_file: Path = CERT_FILE, key_file: Path = KEY_FILE) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    return context


def client_context(cert_file: Path = CERT_FILE) -> ssl.SSLContext:
    if not cert_file.exists():
        raise CertificateMissing(
            "certs/server.crt is missing. Copy it from the server computer "
            "(only the .crt file, never server.key)."
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Trust exactly one certificate: the server's own. The IP address in
    # config.py can change between demos, so identity comes from the pinned
    # certificate (checked again by fingerprint below), not from the hostname.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(cert_file))
    return context


def check_pinned(tls_socket: ssl.SSLSocket, cert_file: Path = CERT_FILE) -> str:
    """Make sure the server showed exactly the pinned certificate."""

    presented = tls_socket.getpeercert(binary_form=True)
    if not presented:
        raise UntrustedServer("The server did not present a certificate.")
    actual = fingerprint(presented)
    if actual != pinned_fingerprint(cert_file):
        raise UntrustedServer("The server's certificate does not match certs/server.crt.")
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the Escape Plan TLS certificate.")
    parser.add_argument("--force", action="store_true", help="replace an existing certificate")
    parser.add_argument("--host", action="append", default=[], help="extra IP or hostname to include")
    args = parser.parse_args()

    created = ensure_certificate(force=args.force, extra_hosts=args.host)
    print(("Created " if created else "Keeping existing ") + str(CERT_FILE))
    print("Fingerprint:", pinned_fingerprint())
    print("Copy certs/server.crt (not server.key) to every client computer.")


if __name__ == "__main__":
    main()
