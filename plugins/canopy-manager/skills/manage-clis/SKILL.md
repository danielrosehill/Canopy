---
name: manage-clis
description: Choose tools for the Canopy `cli` container from the library, install them, and migrate logins from the host so agents can use them there. Use on "install these CLIs", "what tools can I add", "add gcloud to the server", "copy my logins into Canopy", or when `canopy run <tool>` is not found or not logged in.
---

# Manage CLIs

The `cli` service is an Alpine container, so agents get their tools there and the host stays clean. It starts
small. The user picks from a library of tools, `config/cli-library.json`, and their choices stay in their
agent-data folder, outside this repo. Run this on the server. Nothing here restarts the stack.

## 1. Choose

A person runs `canopy clis choose`, which is interactive. As an agent, show the options with `canopy clis list`,
ask which they want, then `canopy clis add <name>...` or `canopy clis remove <name>...`.

`canopy clis install` applies the choices, and `canopy clis check` tests them.

## 2. Logins

For each tool, find out whether the host is really logged in by running a read-only command, such as
`gcloud projects list --limit=1`. A config folder alone proves nothing: it can exist with no login in it. Don't
read or print credential files. Then list the paths and run `canopy clis logins`.

## 3. The file

`agent-data/config/cli.json`. `canopy clis add` writes `tools`. The rest is by hand:

```json
{
  "tools": ["wrangler", "gcloud", "nmap"],
  "logins": [".config/gh", ".config/gcloud", ".config/rclone"],
  "clis": [
    { "name": "gcloud", "via": "tar", "bin": "google-cloud-sdk/bin/gcloud",
      "url": "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz",
      "check": "gcloud projects list --limit=1" },
    { "name": "hcloud", "via": "host", "check": "hcloud server list" }
  ]
}
```

- `tools`: names from the library.
- `logins`: paths relative to the host's home, copied into the container's home.
- `clis`: anything not in the library, or a replacement for an entry that is, usually for a stricter `check`.
- `via`: `image` is already in the image, `apk` installs an Alpine package, `npm` and `uv` a package, `bin` downloads
  one file, `tar` unpacks a release into the home folder, and `host` copies the host's binary. Use `host` for static
  binaries only: anything linked against glibc will not run on Alpine.
- `repo`: for an `apk` entry, a third-party Alpine repo named in `apk_repos`, in the library or in this file (same
  shape: `url`, `key_url`, `key_name`, `signer_sha256` (SHA-256 of the public key)). `install` downloads the key, refuses if its SHA-256 differs,
  puts it in `/etc/apk/keys/`, and passes the repo to `apk add -X`, leaving `/etc/apk/repositories` alone. The library
  ships one: `danielrosehill` (`https://apps.danielrosehill.com/alpine`, source
  github.com/danielrosehill/Alpine-Public-Repo) for `orexplorer`, `hubexplorer` and `eml-toolkit`.
- `env`: variables to set for a `host` CLI, such as a token read from a file in the home folder.
- `check`: a read-only command. Where a login matters, use one that needs it, not `--version`. It passes on exit code alone.

## Keeping it working

- A copied login is a snapshot. Rerun `canopy clis logins` after logging in again on the host. Short-lived
  tokens refresh by themselves only if the CLI can write to its copy, which it can here.
- CLIs that use a token rather than a login get it from `secrets.env` in agent-data, which the container
  reads when it starts, so a new one needs `canopy compose up -d cli`.
- `apk` tools live in the container and are lost when it is recreated, which an update does. Run
  `canopy clis install` after every update. Everything else is in the home folder and stays.
- Tools marked `needs: lan` (`arp-scan`, `tcpdump`) install, but on the container's bridged network they see
  only Docker's subnet. `nmap` and the rest reach the LAN normally.
- The container has an `ssh` client and no keys. Keep keys on the host unless agents must connect from here.
