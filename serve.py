"""Container entrypoint (docs/specs/host-on-fe-pro-Q.md §6.5). Deliberately
thin -- everything worth testing lives in jobs.drain(). app.py's app.run()
stays the laptop dev loop's entrypoint; this neither replaces nor imports it."""

import logging
import signal
import sys

import waitress

import jobs
from app import create_app
from config import APP_PORT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("serve")


def _handle_sigterm(signum, frame):
    logger.info("SIGTERM received, draining active job")
    drained = jobs.drain()
    logger.info("drain %s", "completed" if drained else "timed out")
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    app = create_app()
    # 0.0.0.0 is correct here: it's the address inside the container's own
    # network namespace. The compose file's 127.0.0.1:45660:45660 publish is
    # what decides reachability from outside (docs/specs/host-on-fe-pro-Q.md
    # §3.3) -- this is not a wider bind than the laptop dev loop.
    waitress.serve(app, host="0.0.0.0", port=APP_PORT)
