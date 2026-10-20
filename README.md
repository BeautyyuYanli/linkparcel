# LinkParcel

Upload a file to your private storage and share a temporary download link.
Use the `lpc` command yourself, or let your AI assistant handle it with the bundled
skill. Recipients can download in a browser or with `curl`; they only need the link.

## Install the CLI and skill

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/BeautyyuYanli/linkparcel.git
cd linkparcel
uv tool install .
```

To use LinkParcel with Codex, install the bundled skill:

```sh
mkdir -p "$HOME/.agents/skills"
cp -R skill/linkparcel "$HOME/.agents/skills/"
```

For other agents, copy `skill/linkparcel/` into their supported skill directory.
After configuring storage below, you can ask Codex:

```text
Use $linkparcel to upload ./report.pdf and give me a download link.
```

Make sure `lpc` is on your agent's `PATH`. If the skill does not appear, restart
Codex. See the [official OpenAI skill documentation](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)
for supported installation locations.

## Connect your storage

You need a private storage bucket and credentials that can list, upload, and read
files. A storage URI combines the bucket, endpoint, region, and credentials into one
connection string.

For example, to connect to Cloudflare R2:

```sh
lpc config --storage-uri \
  's3://BUCKET?endpoint=https%3A%2F%2FACCOUNT_ID.r2.cloudflarestorage.com&region=auto&access_key_id=ACCESS_KEY_ID&secret_access_key=SECRET_ACCESS_KEY'
```

Replace `BUCKET`, `ACCOUNT_ID`, `ACCESS_KEY_ID`, and `SECRET_ACCESS_KEY` with your
own values. Percent-encode special characters in URI values. For another S3-compatible
provider, use its endpoint and region.

Configuration is saved on your machine, so you only need to set it up once.
Treat the storage URI as a secret. Keep the bucket private and share the download
links you generate.

## Upload and share

```sh
lpc upload ./report.pdf
```

The command uploads the file and returns its stored name (`key`) and download link:

```json
{"key":"report.pdf","download_url":"https://storage.example/..."}
```

Send the `download_url` to your recipient. They can open it in a browser or download
from another machine:

```sh
curl -fL 'PASTE_DOWNLOAD_URL_HERE' -o report.pdf
```

Download links last **one hour by default**. Anyone with the link can use it while
it is valid, so share it only with intended recipients. Link expiration does not
delete the uploaded file.

If the filename is already in use, LinkParcel adds a suffix such as `report-1.pdf`.
Use the returned `key` when referring to that file later.

## List your files

```sh
lpc list
```

The result contains the stored file names:

```json
{"files":[{"key":"report-1.pdf"},{"key":"report.pdf"}]}
```

Files are listed in name order. An empty store returns `{"files":[]}`.

## Get a fresh download link

Use a stored key from your upload result or `lpc list`:

```sh
lpc download-url report.pdf
```

This returns a new `download_url` for the same file, so you can share it again
without uploading it again. Generating a link does not check whether the file exists.

If an upload reports that the file was stored but its download link could not be
created, use the reported key with `lpc download-url`.

## Choose how long links last

Set the download lifetime when saving your storage configuration. For example,
to request links that last 24 hours:

```sh
lpc config --storage-uri '<your storage URI>' \
  --download-url-ttl-seconds 86400
```

## Configuration reference

CLI settings are stored in `~/.config/lpc/env`, or `$XDG_CONFIG_HOME/lpc/env` when
`XDG_CONFIG_HOME` is set. Environment variables override the corresponding saved
settings:

| Variable | Purpose | Default |
| --- | --- | --- |
| `LPC_STORAGE_URI` | Storage connection and credentials | Required |
| `LPC_DOWNLOAD_URL_TTL_SECONDS` | Download link lifetime | `3600` (1 hour) |
| `LPC_UPLOAD_URL_TTL_SECONDS` | Upload link lifetime | `300` (5 minutes) |

Both lifetimes must be positive integers. Each `lpc config` call saves the storage
URI and both lifetimes; omitted lifetime options use the defaults above.

## Optional: run your own server

The CLI uploads files and creates links directly with your storage provider.
Run the optional HTTP server when other applications need API access to file
listings or upload/download links. See the [self-hosting guide](docs/self-hosting.md)
for Cloudflare R2 and bundled SeaweedFS setup instructions.

## Contributing

For local development, run `uv sync` and then `make check`.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
