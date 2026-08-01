"""Generate an RSA key + CSR for an Apple Pass Type ID certificate.

Apple's cert wizard asks you to upload a .certSigningRequest file. This produces
the equivalent of Keychain Access's "Certificate Assistant > Request a Certificate
from a Certificate Authority", without needing a Mac or OpenSSL installed.

    python tools/make_csr.py --cn "Pass Type ID: pass.au.org.tamilar.membership" \
        --email you@example.org --out certs

Writes certs/pass-key.pem (KEEP PRIVATE, never commit) and certs/pass.csr
(upload this one to Apple).
"""

import argparse
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate a CSR for an Apple Pass Type ID cert")
    parser.add_argument("--cn", required=True,
                        help='Common Name, e.g. "Pass Type ID: pass.au.org.tamilar.membership"')
    parser.add_argument("--email", required=True, help="your Apple Developer account email")
    parser.add_argument("--out", default="certs")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    key_path = out_dir / "pass-key.pem"
    csr_path = out_dir / "pass.csr"

    if key_path.exists():
        print(f"refusing to overwrite existing {key_path}")
        print("(delete it first if you genuinely want a new key+CSR pair)")
        return 2

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, args.cn),
        x509.NameAttribute(NameOID.EMAIL_ADDRESS, args.email),
    ])
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(subject)
           .sign(key, hashes.SHA256()))

    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    csr_path.write_bytes(csr.public_bytes(serialization.Encoding.PEM))

    print(f"private key : {key_path.resolve()}   (KEEP SECRET - never commit, never email)")
    print(f"CSR         : {csr_path.resolve()}   (upload this file to Apple)")
    print()
    print("Next: Apple portal -> Certificates -> + -> Pass Type ID Certificate")
    print(f"      upload {csr_path.name}, download the issued pass.cer")
    print("      then: python tools/convert_apple_cert.py --cer pass.cer --out certs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
