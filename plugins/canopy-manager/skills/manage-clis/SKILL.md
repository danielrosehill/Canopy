---
name: manage-clis
description: Install CLIs into the Canopy `cli` container from a list, and migrate logins from the host so agents can use them there. Use on "install these CLIs", "add gcloud to the server", "copy my logins into Canopy", "migrate my CLIs from the host", or when `canopy run <tool>` is not found or not logged in.
---

# Manage CLIs

The `cli` service is an Alpine container, and its home folder is `agent-data/cli`, so anything put there
survives an update and needs no restart. Which CLIs someone wants is theirs to decide, so the list stays
in their agent-data folder, outside this repo. Run this on the server.

## 1. Take stock

Ask the user which CLIs they want, or look at what the host already has. For each, find out whether the
host is really logged in by running a read-only command, such as `gcloud projects list --limit=1`. A
config folder alone proves nothing: it can exist with no login in it. Don't read or print credential files.

## 2. Write the list

`agent-data/config/cli.json`:

```json
{
  "logins": [".config/gh", ".config/gcloud", ".config/rclone"],
  "clis": [
    { "name": "gh", "via": "image", "check": "gh api user --jq .login" },
    { "name": "vercel", "via": "npm", "package": "vercel", "check": "vercel --version" },
    { "name": "hf", "via": "uv", "package": "huggingface_hub[cli]", "check": "hf auth whoami" },
    { "name": "hcloud", "via": "host", "env": { "HCLOUD_CONFIG": "$HOME/.config/hcloud/cli.toml" },
      "check": "hcloud server list" },
    { "name": "gcloud", "via": "tar", "bin": "google-cloud-sdk/bin/gcloud",
      "url": "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz",
      "check": "gcloud projects list --limit=1" }
  ]
}
```

- `logins`: paths relative to the host's home, copied into the container's home.
- `via`: `image` is already in the image, `npm` and `uv` install from a package, `tar` unpacks a release
  into the home folder, and `host` copies the host's binary. Use `host` for static binaries only: anything
  linked against glibc will not run on Alpine.
- `env`: variables to set for a `host` CLI. The container's user has no passwd entry, so a tool that finds
  its config that way needs telling where it is.
- `check`: a read-only command that needs the login, not `--version`, wherever a login is involved.

## 3. Apply and check

```bash
python3 <canopy checkout>/plugins/canopy-manager/skills/manage-clis/scripts/clis.py all
```

Set `CANOPY_AGENT_DATA` if it is not `~/canopy/agent-data`, and pass `--canopy "<command>"` if `canopy` is
wrapped on this host. `logins`, `install` or `check` runs one step. A check passes on exit code alone.

Then use a CLI with `canopy run <tool>`.

## Keeping it working

- A copied login is a snapshot. Rerun `clis.py logins` after logging in again on the host. Short-lived
  tokens refresh by themselves only if the CLI can write to its copy, which it can here.
- CLIs that use a token rather than a login get it from `secrets.env` in agent-data, which the container
  reads when it starts, so a new one needs `canopy compose up -d cli`.
- `aws`, `gh`, `rclone` and the others in `cli/Dockerfile` update with the image. Rerun `clis.py install`
  for the rest.
