# A Canopy overlay

Your own Canopy content: config, your MCP services, settings, and how to get secrets. Copy this folder
to a new **private** git repo. It never holds a secret, so the repo is your whole deployment apart from
the data.

```
config/servers.json         MCP upstreams and groups. `${VAR}` placeholders come from secrets
config/marketplaces.json    plugin marketplaces to index
compose.override.yaml       your own services, on top of Canopy's compose.yaml
settings.env.example        copy to settings.env: settings that are not secret. Committed
env.sh.example              copy to env.sh: prints secrets from a secrets manager. Committed, no values
secrets.env.example         copy to secrets.env on one machine, if you have no secrets manager. Never committed
```

## Make it a private repo

```bash
cp -r examples/overlay ~/my-canopy && cd ~/my-canopy
cp settings.env.example settings.env
cp env.sh.example env.sh            # then fill in one secrets manager, or use secrets.env
git init -b main && git add -A && git commit -m "My Canopy overlay"
gh repo create my-canopy --private --source . --push
```

## Point Canopy at it

By URL, so Canopy keeps its own clone in `$CANOPY_DATA/overlay` and pulls it on `canopy up` and
`canopy apply`:

```bash
export CANOPY_OVERLAY=git@github.com:you/my-canopy.git
export CANOPY_OVERLAY_REF=main        # optional: a branch, tag or commit
canopy up
canopy overlay status
```

The machine needs read access to the repo (an SSH or deploy key, a credential helper, or `GITHUB_TOKEN`
for an `https://github.com/` URL), and the one credential `env.sh` uses. Nothing else is copied to it.
Change things by committing here, then `canopy apply`. Never edit the clone: `canopy` refuses to pull
over local changes.

Or as a folder you keep yourself: `export CANOPY_AGENT_DATA=~/my-canopy`.

See [Add your own things](../../docs/customising.md) for both, and for what lives where.
