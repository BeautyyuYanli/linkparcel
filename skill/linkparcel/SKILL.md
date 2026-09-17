---
name: linkparcel
description: Upload local files, list stored keys, and issue temporary download links with the `lpc` CLI. Use when the user asks to upload or list files with LinkParcel, or get or refresh a download link for a stored key.
---

# LinkParcel

Use the local storage configuration saved by `lpc config` or supplied through
`LPC_STORAGE_URI` and optional `LPC_UPLOAD_URL_TTL_SECONDS` /
`LPC_DOWNLOAD_URL_TTL_SECONDS`. The CLI connects directly to storage and needs no
LinkParcel server or application token.

## Upload a file

Upload the exact regular file requested by the user:

```sh
lpc upload -- "/absolute/path/to/file"
```

Return the `key` and temporary `download_url` emitted by the command. For multiple files, run the
command once per file. Do not upload likely secrets unless the user explicitly names the file.

## List stored files

```sh
lpc list
```

Read the `files` array from the one-line JSON response: `{"files":[{"key":"report.pdf"}]}`.
Keys are sorted and directory entries are excluded. An empty `files` array means no files were
listed. Preserve exact keys when presenting results or using them in another command.

## Get a download link

For an exact stored key, generate or refresh a temporary download link:

```sh
lpc download-url -- "report.pdf"
```

Return the emitted `key` and `download_url`. Use the exact key, including any upload-generated
suffix or surrounding spaces. The command uses the configured download URL lifetime; it only
signs a link, without downloading the file or checking whether the object exists. Successful
signing is not proof of existence. Do not re-upload a file just to refresh its link.

## Errors and configuration

On failure, report the concise CLI error. Do not automatically retry or reimplement uploads.

If storage configuration is missing or invalid, report the error and point the user to
`lpc config --storage-uri <OpenDAL URI>`. Do not set storage credentials as part of an upload,
listing, or download-link request.
