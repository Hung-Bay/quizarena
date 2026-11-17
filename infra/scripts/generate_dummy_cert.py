#!/usr/bin/env python3
"""Tạo chứng chỉ SSL/TLS tự ký (Self-signed) phục vụ bootstrap ban đầu và CI testing.

Giúp Nginx khởi động được cổng 443 ngay cả khi chưa xin chứng chỉ thật từ Let's Encrypt.
Sử dụng thư viện `cryptography` thuần Python, không phụ thuộc vào file cấu hình openssl.cnf của OS.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_self_signed_cert(output_dir: Path, domain: str = "localhost") -> tuple[Path, Path]:
    """Tạo cặp khóa RSA 2048 và chứng chỉ x509 tự ký."""
    output_dir.mkdir(parents=True, exist_ok=True)
    privkey_path = output_dir / "privkey.pem"
    fullchain_path = output_dir / "fullchain.pem"

    # Tạo RSA Private Key 2048-bit
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )

    # Tạo thông tin chủ thể và cấp phát (Subject / Issuer)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, domain),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "QuizArena Self-Signed Bootstrap"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(domain),
                x509.DNSName("localhost"),
                x509.DNSName("*.localhost"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256(), default_backend())
    )

    # Ghi file privkey.pem
    privkey_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    privkey_path.write_bytes(privkey_bytes)

    # Ghi file fullchain.pem
    fullchain_bytes = cert.public_bytes(serialization.Encoding.PEM)
    fullchain_path.write_bytes(fullchain_bytes)

    return privkey_path, fullchain_path


def main() -> int:
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("infra/nginx/dummy_certs/quizarena")
    domain = sys.argv[2] if len(sys.argv) > 2 else "localhost"

    privkey, fullchain = generate_self_signed_cert(target_dir, domain)
    print(f"Đã tạo chứng chỉ tự ký thành công tại: {target_dir}")
    print(f"  - Private Key : {privkey}")
    print(f"  - Certificate : {fullchain}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
