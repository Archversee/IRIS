"""HTTPS certificate for the proxy, so tablets can use the microphone.

Browsers only allow the microphone (push-to-talk) on a secure page: https, or
http://localhost. A tablet opening http://<sim-pc-ip>:3000 isn't one, so the
launcher also serves the site over https on port 3443 with the certificate
made here.

    certs/ca.crt, ca.key          a small certificate authority of this PC's own,
                                  made once. A tablet installs and trusts ca.crt
                                  once (download it from http://<sim-pc-ip>:3000/iris-ca.crt)
                                  and then accepts the site without warnings.
    certs/server.crt, server.key  the site's certificate, signed by that CA, for
                                  localhost, this PC's name and its IPv4
                                  addresses. Made again when an address changes
                                  or it is about to expire; tablets keep trusting it.

Run: python make_cert.py   (launch_all.ps1 does this on every start)
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERTS = Path(__file__).resolve().parent / "certs"
CA_CRT, CA_KEY = CERTS / "ca.crt", CERTS / "ca.key"
SRV_CRT, SRV_KEY = CERTS / "server.crt", CERTS / "server.key"
SERVER_DAYS = 800        # iOS rejects TLS certificates valid for more than 825 days
RENEW_DAYS = 30


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))


def _addresses() -> tuple[list[str], list[str]]:
    """(DNS names, IPv4 addresses) the site may be opened at."""
    host = socket.gethostname()
    ips = {"127.0.0.1"}
    try:
        ips.update(a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET))
    except OSError:
        pass
    try:                                      # the address the default route uses
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))      # nothing is sent
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    ips = {i for i in ips if not i.startswith("169.254.")}
    return sorted({"localhost", host, f"{host}.local"}), sorted(ips, key=ipaddress.ip_address)


def _ca():
    if CA_CRT.exists() and CA_KEY.exists():
        return (x509.load_pem_x509_certificate(CA_CRT.read_bytes()),
                serialization.load_pem_private_key(CA_KEY.read_bytes(), password=None))
    key = _key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"IRIS local CA ({socket.gethostname()})")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write_key(CA_KEY, key)
    CA_CRT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"made a new CA: {CA_CRT} -- tablets must install and trust it once (README.md section 8.7)")
    return cert, key


def _server_ok(names: list[str], ips: list[str]) -> bool:
    if not (SRV_CRT.exists() and SRV_KEY.exists()):
        return False
    try:
        cert = x509.load_pem_x509_certificate(SRV_CRT.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except Exception:
        return False
    have_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
    have_names = set(san.get_values_for_type(x509.DNSName))
    fresh = cert.not_valid_after_utc - _now() > dt.timedelta(days=RENEW_DAYS)
    return fresh and set(ips) <= have_ips and set(names) <= have_names and CA_CRT.stat().st_mtime <= SRV_CRT.stat().st_mtime


def main() -> int:
    CERTS.mkdir(exist_ok=True)
    ca_cert, ca_key = _ca()
    names, ips = _addresses()
    if _server_ok(names, ips):
        print(f"certificate is current ({', '.join(ips)})")
        return 0
    key = _key()
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=SERVER_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(SRV_KEY, key)
    SRV_CRT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"made the site certificate for {', '.join(names + ips)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
