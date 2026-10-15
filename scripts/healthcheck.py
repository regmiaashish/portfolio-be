"""Docker HEALTHCHECK: exit 0 when GET /health on this container answers 200, else 1.

Standard library only, so the image needs no curl. When the container serves TLS
(TLS_CERT_FILE set, see scripts/run.sh) it speaks HTTPS to itself without verifying the
certificate: it's the origin certificate for the public hostname, and this is 127.0.0.1.
"""

import os
import ssl
import sys
import urllib.request


def health_url_and_context() -> tuple[str, ssl.SSLContext | None]:
    port = os.environ.get("PORT", "8001")
    if os.environ.get("TLS_CERT_FILE"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return f"https://127.0.0.1:{port}/health", context
    return f"http://127.0.0.1:{port}/health", None


def main() -> int:
    url, context = health_url_and_context()
    try:
        with urllib.request.urlopen(url, timeout=4, context=context) as response:
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
