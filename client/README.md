# proxy-lab client integration

The pieces a Claude Code session needs to TALK to the proxy (the proxy itself
runs from `releases/current`, see the repo root). Everything here fails soft:
proxy down ⇒ statusline renders `cache ∅`, hooks exit 0, the warm-cache
command self-diagnoses. Requires `jq` + `curl`.

| File | What it does |
|---|---|
| `warm-cache.md` | The `/warm-cache <hours\|off>` slash command — arms N hours of idle keep-warm insurance on the proxy (echo-forward ack; default-dead without the proxy). |
| `status-line.sh` | Statusline: context heaviness (tokens/turns/big tool_result), cache warmth + hold horizon polled from `/_status` (`🔥cache→HH:MM +Np→HH:MM` / `❄️` / `∅`), CLI-vs-proxy cost side by side. |
| `cache-expiry-hook.sh` | UserPromptSubmit hook: when the prefix cache just lapsed, blocks once with "cheapest moment to /compact" (resubmit passes; warmth judged by the proxy ledger, never guessed). |
| `cache-state-hook.sh` | Stop hook: publishes per-session heaviness stats to TMPDIR once per turn so the statusline never re-scans a long transcript per render (fallback path for unproxied sessions). |
| `settings.example.json` | Project-level wiring of all of the above. |
| `install.sh` | Symlinks `warm-cache.md` into `~/.claude/commands/` (pointing through `releases/current` so it upgrades with releases) and prints the settings snippets that still need manual merge. |

## Install

1. `./install.sh` — installs the `/warm-cache` command user-level (all
   projects), as a symlink through `releases/current`.
2. Per project that should route through the proxy: merge
   `settings.example.json` into the project's `.claude/settings.json`
   (env + statusLine + the two hooks). The script paths point at
   `releases/current/client/`, so projects pick up improvements when a new
   release is cut — no per-project file copies.
3. User-level (once, in `~/.claude/settings.json`): the SessionEnd→`/_end`
   hook, so the proxy disarms holds and marks ended sessions:

```json
"SessionEnd": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "jq -r '\"\\(.session_id) \\(.reason // \"unknown\")\"' | { read -r sid reason; curl -s -m 2 \"http://127.0.0.1:7800/_end?session=${sid}&reason=${reason}\"; } 2>/dev/null || true",
        "timeout": 5
      }
    ]
  }
]
```

## Tunables (env, e.g. in settings.json `env`)

`CC_PROXY_URL` (default `http://localhost:7800`) · `CC_PROXY_TIMEOUT` (0.2s —
a dead proxy must never stall a render) · `CC_PROXY_LOG_DIR` (default
`~/projects/proxy-lab/logs_main`, for the proxy-side cost figure) ·
heaviness thresholds `CC_WARN_TOKENS` / `CC_HEAVY_TOKENS` / `CC_WARN_TURNS` /
`CC_HEAVY_TURNS` / `CC_BIG_TOOL_TOKENS` · `CC_CACHE_WARN` (yellow when ≤ this
many seconds of TTL left) · expiry-hook: `CC_CACHE_HOOK_BLOCK` (1 block /
0 advisory), `CC_CACHE_MIN_TOKENS`, `CC_CACHE_MIN_TURNS`.

These are the canonical copies; `~/tmp/proxy-sl-test/.claude/` was the
development sandbox. Edit HERE, then cut a release to ship.
