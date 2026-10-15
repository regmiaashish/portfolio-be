"""Docker HEALTHCHECK: exit 0 when GET /health on this container answers 200, else 1.

Standard library only, so the image needs no curl.
"""

import os
import sys
import urllib.request


def main() -> int:
    url = f"http://127.0.0.1:{os.environ.get('PORT', '8001')}/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
