"""Convert Apple's downloaded pass.cer (DER) to PEM, and fetch the WWDR intermediate.

    python tools/convert_apple_cert.py --cer "C:/Users/me/Downloads/pass.cer" --out certs

Writes:
    certs/pass-cert.pem   - the Pass Type ID certificate, PEM
    certs/wwdr.pem        - Apple WWDR G4 intermediate, PEM (downloaded automatically)

Run tools/make_csr.py FIRST - certs/pass-key.pem must already exist from that step.
This script also sanity-checks that the downloaded cert matches your key.
"""

import argparse
import sys
import urllib.request
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Apple's current WWDR G4 intermediate (DER). If Apple rotates this, download the
# latest from https://www.apple.com/certificateauthority/ and pass --wwdr-der.
WWDR_G4_URL = "https://www.apple.com/certificateauthority/AppleWWDRCAG4.cer"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Convert Apple's pass.cer to PEM + fetch WWDR")
    parser.add_argument("--cer", required=True, help="the pass.cer Apple gave you (DER)")
    parser.add_argument("--out", default="certs")
    parser.add_argument("--wwdr-der", default="", help="local WWDR .cer if you already have one")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    key_path = out_dir / "pass-key.pem"
    cer_path = Path(args.cer)

    if not key_path.exists():
        print(f"missing {key_path} - run tools/make_csr.py first")
        return 2
    if not cer_path.exists():
        print(f"not found: {cer_path}")
        return 2

    # --- pass certificate: DER -> PEM ---
    der = cer_path.read_bytes()
    cert = x509.load_der_x509_certificate(der)
    cert_pem_path = out_dir / "pass-cert.pem"
    cert_pem_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"wrote {cert_pem_path.resolve()}")

    # --- sanity check: cert's public key must match our private key ---
    private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    cert_pub_numbers = cert.public_key().public_numbers()
    key_pub_numbers = private_key.public_key().public_numbers()
    if isinstance(private_key, rsa.RSAPrivateKey) and cert_pub_numbers == key_pub_numbers:
        print("OK   certificate matches certs/pass-key.pem")
    else:
        print("MISMATCH: this certificate does not correspond to certs/pass-key.pem")
        print("          (likely uploaded a CSR generated from a different key)")
        return 1

    # --- WWDR intermediate ---
    wwdr_pem_path = out_dir / "wwdr.pem"
    if args.wwdr_der:
        wwdr_der = Path(args.wwdr_der).read_bytes()
    else:
        print(f"downloading WWDR G4 from {WWDR_G4_URL} ...")
        with urllib.request.urlopen(WWDR_G4_URL, timeout=30) as response:  # noqa: S310
            wwdr_der = response.read()
    wwdr_cert = x509.load_der_x509_certificate(wwdr_der)
    wwdr_pem_path.write_bytes(wwdr_cert.public_bytes(serialization.Encoding.PEM))
    print(f"wrote {wwdr_pem_path.resolve()}")

    print()
    print("Ready. Build a pass with:")
    print("  python tools/build_local_pass.py --csv samples/first_send.csv \\")
    print(f"    --cert {cert_pem_path} --key {key_path} --wwdr {wwdr_pem_path} \\")
    print("    --team-id YOUR_TEAM_ID --pass-type-id YOUR_PASS_TYPE_ID \\")
    print(f'    --photos "{Path.home() / "Downloads"}" --out dist')
    return 0


if __name__ == "__main__":
    sys.exit(main())
