# LinkParcel

Private file sharing with a CLI and an optional HTTP API.

## Install the skill

With Python 3.12+ and [uv](https://docs.astral.sh/uv/) installed, clone the repository,
install the CLI, and copy the bundled skill into Codex's user skill directory:

```sh
git clone https://github.com/BeautyyuYanli/linkparcel.git
cd linkparcel
uv tool install .
mkdir -p "$HOME/.agents/skills"
cp -R skill/linkparcel "$HOME/.agents/skills/"
```

Codex discovers skills in `~/.agents/skills`, as described in the
[official OpenAI documentation](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).
For other agents, copy `skill/linkparcel/` into their supported skill directory.

Configure your private storage before using the skill:

```sh
lpc config --storage-uri '<OpenDAL URI>'
```

Make sure `lpc` is on your agent's `PATH`, then ask it to use the `linkparcel` skill
to upload files, list stored keys, or create download links. In Codex, invoke it with
`$linkparcel`. If the skill does not appear, restart Codex.

## How it works

Share files through private object storage using either of two entry points:

- The `lpc` CLI uses local storage credentials to upload files, list stored keys, and
  issue temporary download links. It runs independently of the HTTP server.
- The optional FastAPI server provides authenticated file listings and upload/download
  grants. Clients transfer file contents directly to and from storage.

Shared storage, naming, listing, and signing logic lives in `src/linkparcel/services/`.
The HTTP controller is `src/linkparcel/controllers/server.py`; the CLI controller and
upload workflow are in `src/linkparcel/controllers/cli.py`. Services are independent of
FastAPI, Typer, application tokens, and XDG configuration paths.

## Requirements

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/), then install dependencies:

```sh
uv sync
```

Both entry points require an OpenDAL URI for a private storage backend that supports
`list`, `presign_write`, and `presign_read`. Supply credentials through the backend's
configuration options.

`LPC_STORAGE_URI` is passed unchanged to OpenDAL and may contain credentials. Keep it
out of logs and version control. Git and Docker exclude `.env`, `.env.*`, and `*.env`
files; only placeholder templates named `.env.example` or `.env.*.example` belong in
the repository.

## CLI configuration and file operations

Save your storage URI locally, then upload a file:

```sh
uv run lpc config --storage-uri 's3://bucket?endpoint=https%3A%2F%2Fstorage.example&region=us-east-1&access_key_id=replace-me&secret_access_key=replace-me'
uv run lpc upload ./report.pdf
```

`config` saves all three storage settings without connecting to storage and prints only
the configuration file path. Set positive URL lifetimes with `--upload-url-ttl-seconds`
and `--download-url-ttl-seconds`; their defaults are `300` and `3600` seconds.

Configuration is stored at `${XDG_CONFIG_HOME:-$HOME/.config}/lpc/env`, with directory
mode `0700` and file mode `0600`. Writes reject symbolic links and non-regular files
and preserve unrelated settings. Dotenv variable expansion is disabled when loading
the CLI configuration so special characters in credentials remain unchanged.

For `upload`, `list`, and `download-url`, each process environment value overrides its
corresponding file setting: `LPC_STORAGE_URI`, `LPC_UPLOAD_URL_TTL_SECONDS`, and
`LPC_DOWNLOAD_URL_TTL_SECONDS`. Missing lifetimes use the defaults. Environment-only
configuration is also supported.

A successful upload writes one JSON record to stdout:

```json
{"key":"report-1.pdf","download_url":"https://storage.example/presigned-read/..."}
```

The CLI checks the local file, creates and checks an OpenDAL operator, chooses an
available key from one storage listing, obtains a signed upload request, streams the
file, and issues a download link. Name collisions use suffixes such as `-1` and `-2`.
File and HTTP client handles close after use; operators belong to individual command
invocations. Concurrent uploads do not reserve names, and failed uploads or signing
requests are not retried.

When supported by the backend, uploaded objects use `application/octet-stream` and
an attachment filename based on the final key. Unsupported metadata is omitted.
If the upload succeeds but download signing fails, the command exits with a nonzero
status, leaves stdout empty, and reports the stored key on stderr.

List files at the storage root, or issue a fresh link for an exact key:

```sh
uv run lpc list
uv run lpc download-url report-1.pdf
```

`list` returns one JSON record with keys in ascending order and directory entries
excluded. An empty store returns `{"files":[]}`:

```json
{"files":[{"key":"report-1.pdf"},{"key":"report.pdf"}]}
```

`download-url` returns the same `{"key":"...","download_url":"..."}` shape as an upload
and uses `LPC_DOWNLOAD_URL_TTL_SECONDS` (default `3600` seconds). Keys must be single
filenames; they are not renamed or trimmed. The command signs a link without fetching
the file or checking whether the object exists.

Both commands create and check their own operator and call local services directly.
Configuration, storage, and key errors produce a nonzero exit status, empty stdout,
and an error message on stderr that excludes storage credentials.

## HTTP server

To provide authorization endpoints for other clients, copy `.env.example`, fill in
the storage settings and `LPC_API_TOKEN`, and load them into the server's environment:

```sh
cp .env.example .env
set -a
. ./.env
set +a
make serve
```

The server reads process environment variables. At startup it creates and checks one
operator, then shares it across requests. All endpoints below require
`Authorization: Bearer <LPC_API_TOKEN>`:

| Endpoint | Behavior |
| --- | --- |
| `GET /api/v1/files` | List files at the storage root |
| `POST /api/v1/uploads` | Issue an upload request for `{"name":"report.pdf"}` |
| `POST /api/v1/downloads` | Issue a download URL for `{"key":"report.pdf"}` |

Missing or invalid tokens return `401`, invalid keys return `422`, and storage
failures return a `503` response without credentials. Signed responses use
`Cache-Control: no-store`. Clients use the granted method, headers, and URL to access
storage directly, supplying the upload body and `Content-Length` themselves.

## Security

CLI permissions come from its storage credentials. The server's `LPC_API_TOKEN` is a
shared deployment credential: every holder has access to all service operations.

Anyone holding a presigned URL can access the corresponding object within its
lifetime. Keep these URLs out of logs and share them deliberately. Revoking the
application token does not revoke issued URLs, which may be reusable until the
backend's expiration time.

## Docker Compose

### SeaweedFS mini

The bundled Compose configuration builds the API and starts a private SeaweedFS mini
instance, creating the bucket before the API starts. Create a configuration file and
replace `LPC_API_TOKEN`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` with your own
values before starting the services:

```sh
cp -n .env.example .env
docker compose up --build -d
docker compose ps
```

The API is published on port `59000` and the SeaweedFS S3 API on port `59001`, both on
all host interfaces. SeaweedFS administration ports are not published. Run the CLI
on the host with the same storage configuration:

```sh
set -a
. ./.env
set +a
export LPC_STORAGE_URI="s3://${S3_BUCKET:-lpc}?endpoint=http%3A%2F%2Fseaweedfs.localhost%3A59001&region=us-east-1&access_key_id=${AWS_ACCESS_KEY_ID:-lpcdev}&secret_access_key=${AWS_SECRET_ACCESS_KEY:-lpc-seaweedfs-password}"
uv run lpc upload ./report.pdf
```

CLI uploads require only SeaweedFS. Presigned URLs use `seaweedfs.localhost:59001`:
this name points to SeaweedFS inside the Compose network and must resolve to
`127.0.0.1` on the host. Configure your hosts file or local DNS if your system does
not resolve `*.localhost` automatically. This example is for local development;
remote downloads require a storage endpoint reachable by both the signer and the
recipient. Credentials and bucket names must be URL-safe because the configuration
embeds them in a URI.

Stop the services while retaining stored objects:

```sh
docker compose down
```

SeaweedFS mini uses `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `S3_BUCKET` to
configure credentials and create the bucket. Object data is stored in the
`seaweedfs-data` volume.

### Cloudflare R2

Create a configuration file from the template and replace the bucket, account ID,
and storage credential placeholders. Percent-encode URI query values. CLI use
requires only storage settings:

```sh
cp -n .env.r2.example .env.r2
# Edit .env.r2 with your R2 settings before running:
uv run --env-file .env.r2 lpc upload ./report.pdf
```

To also run the HTTP authorization API, set `LPC_API_TOKEN` in the same file and start
the server:

```sh
docker compose --env-file .env.r2 -f compose.r2.yaml up --build -d
docker compose --env-file .env.r2 -f compose.r2.yaml ps
```

Stop the R2 server:

```sh
docker compose --env-file .env.r2 -f compose.r2.yaml down
```

## Development checks

```sh
make check
```

This runs Ruff linting and formatting checks, strict Pyright type checking, and local
Pytest contract tests.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
