# Self-hosting LinkParcel

The HTTP server lets other applications list files and request temporary upload
and download links using a shared API token. File contents transfer directly
between clients and storage.

For uploads and link sharing from your own machine, follow the
[CLI guide](../README.md).

Run the commands below from the repository root with Docker Compose installed.

## Use Cloudflare R2

Create a configuration file:

```sh
cp -n .env.r2.example .env.r2
```

Edit `.env.r2` and replace the bucket, account ID, and storage credential
placeholders in `LPC_STORAGE_URI`. Percent-encode URI query values. Set
`LPC_API_TOKEN` to a secret of your own, then start the server:

```sh
docker compose --env-file .env.r2 -f compose.r2.yaml up --build -d
docker compose --env-file .env.r2 -f compose.r2.yaml ps
```

The API is available on port `59000`. Stop it with:

```sh
docker compose --env-file .env.r2 -f compose.r2.yaml down
```

Stopping the API leaves files in your R2 bucket.

## Use bundled SeaweedFS storage

The default Compose configuration runs the API and a private SeaweedFS mini
instance. It creates the bucket before starting the API.

```sh
cp -n .env.example .env
```

Edit `.env` and replace `LPC_API_TOKEN`, `AWS_ACCESS_KEY_ID`, and
`AWS_SECRET_ACCESS_KEY` with your own values. The supplied storage credentials
are public development examples. Then start the services:

```sh
docker compose up --build -d
docker compose ps
```

The API uses port `59000`, and SeaweedFS exposes its S3 API on port `59001`.
Both ports bind to all host interfaces; SeaweedFS administration ports are not
published. Data is stored in the `seaweedfs-data` volume.

To upload from the host using the CLI:

```sh
set -a
. ./.env
set +a
export LPC_STORAGE_URI="s3://${S3_BUCKET:-lpc}?endpoint=http%3A%2F%2Fseaweedfs.localhost%3A59001&region=us-east-1&access_key_id=${AWS_ACCESS_KEY_ID:-lpcdev}&secret_access_key=${AWS_SECRET_ACCESS_KEY:-lpc-seaweedfs-password}"
lpc upload ./report.pdf
```

The endpoint `seaweedfs.localhost:59001` points to SeaweedFS inside the Compose
network and must resolve to `127.0.0.1` on the host. Configure your hosts file or
local DNS if your system does not resolve `*.localhost` automatically. This
configuration is for local use. To share links with remote recipients, use a
storage endpoint reachable by both the signing application and the recipient.
Credentials and bucket names must be URL-safe because the configuration embeds
them in a URI.

Stop the services while retaining stored objects:

```sh
docker compose down
```

## HTTP API

Every endpoint requires `Authorization: Bearer <LPC_API_TOKEN>`:

| Endpoint | Request | Result |
| --- | --- | --- |
| `GET /api/v1/files` | No body | Stored file keys |
| `POST /api/v1/uploads` | `{"name":"report.pdf"}` | Final key, upload URL, HTTP method, and headers |
| `POST /api/v1/downloads` | `{"key":"report.pdf"}` | Key and download URL |

Upload clients must use the returned method, URL, and headers, supplying the file
body and its `Content-Length`. Download links can be used directly.

Missing or invalid tokens return `401`, invalid keys return `422`, and storage
failures return `503`. Signed responses use `Cache-Control: no-store`.

The API token grants access to every service operation. Issued storage URLs can
remain usable until they expire, even if you change the API token.

## Storage and configuration

The server reads settings from its process environment:

| Variable | Purpose | Default |
| --- | --- | --- |
| `LPC_STORAGE_URI` | OpenDAL storage URI | Required |
| `LPC_API_TOKEN` | Shared Bearer token for API clients | Required |
| `LPC_UPLOAD_URL_TTL_SECONDS` | Upload URL lifetime | `300` seconds |
| `LPC_DOWNLOAD_URL_TTL_SECONDS` | Download URL lifetime | `3600` seconds |

Use a private backend that supports OpenDAL's `list`, `presign_write`, and
`presign_read` operations. The server checks backend support and connectivity
at startup. Local CLI configuration does not configure the server.

Keys must be single filenames of at most 200 Unicode code points. File listings
include only files at the storage root. Upload names are chosen from a listing
without reserving them, so concurrent uploads of the same name may collide.
Signing a download link does not check whether the object exists.

Keep storage URIs and presigned URLs out of logs. Environment files named `.env`,
`.env.*`, or `*.env` are excluded from Git and Docker build contexts; only the
placeholder example templates belong in version control.
