# Pipe1 License Server

This repository contains the Pipe1 production license server, admin portal, training data intake API, and Lightsail deployment assets.

## Local test

```sh
python -m pytest -q
```

## Lightsail deployment

See `docs/License_Server_Command_Guide.md` and `deploy/lightsail/pipe1`.

## App releases

Windows installers are served from the Lightsail host directory configured by
`PIPE1_DOWNLOADS_DIR`, mounted in containers as `/srv/pipe1-downloads`, and
exposed as `/downloads/*` by Caddy.

```sh
sudo mkdir -p /opt/pipe1-downloads
sudo cp Pipe1-1.2.3-x64.msi /opt/pipe1-downloads/
./pipe1 release create \
  --version 1.2.3 \
  --download-url https://license.example.com/downloads/Pipe1-1.2.3-x64.msi \
  --file /srv/pipe1-downloads/Pipe1-1.2.3-x64.msi
./pipe1 release publish --version 1.2.3
```

Clients check the latest release with:

```text
GET /app/releases/latest?platform=windows&arch=x64&channel=stable&current_version=0.1.0
```
