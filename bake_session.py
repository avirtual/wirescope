#!/usr/bin/env python3
"""Compat shim: bake_session moved into the proxylab package (v0.6.25) so the
vendored package ships it (/_compact 500'd on the .24 vendor bundle, which
copies only proxylab/). Offline CLI usage keeps working from the repo root:
`python3 bake_session.py <transcript.jsonl> [--apply]`."""
from proxylab.bake_session import *          # noqa: F401,F403
from proxylab.bake_session import main, bake, validate, compact_file, _is_thinking_only  # noqa: F401

if __name__ == "__main__":
    main()
