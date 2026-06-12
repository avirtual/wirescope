"""proxylab — the logproxy implementation, split from the monolithic
logproxy.py (mechanical refactor 2026-06-11). Submodules are imported in
the original file's top-to-bottom order so import-time side effects
(env-flag parsing, writer-thread start, _restore_state) keep their
original sequence. The repo-root `logproxy.py` shim forwards attribute
access here for back-compat (uvicorn logproxy:app, import logproxy).
"""
from proxylab import core  # noqa: F401,E402
from proxylab import store  # noqa: F401,E402
from proxylab import codex  # noqa: F401,E402
from proxylab import transforms  # noqa: F401,E402
from proxylab import canary  # noqa: F401,E402
from proxylab import writer  # noqa: F401,E402
from proxylab import warmth  # noqa: F401,E402
from proxylab import subs  # noqa: F401,E402
from proxylab import meta  # noqa: F401,E402
from proxylab import pinger  # noqa: F401,E402
from proxylab import hold  # noqa: F401,E402
from proxylab import billing  # noqa: F401,E402
from proxylab import restore  # noqa: F401,E402
from proxylab import status  # noqa: F401,E402
from proxylab import views  # noqa: F401,E402
from proxylab import server  # noqa: F401,E402
