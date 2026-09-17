---
name: linkparcel
description: Upload local files with the `lpc` CLI and return their keys and temporary download links. Use when the user asks to upload a file with LinkParcel or `lpc`.
---

# LinkParcel

Use the local storage configuration saved by `lpc config` or supplied through
`LPC_STORAGE_URI` and optional `LPC_UPLOAD_URL_TTL_SECONDS` /
`LPC_DOWNLOAD_URL_TTL_SECONDS`. The CLI connects directly to storage and needs no
LinkParcel server or application token.

Upload the exact regular file requested by the user:

```sh
lpc upload -- "/absolute/path/to/file"
```

Return the `key` and temporary `download_url` emitted by the command. For multiple files, run the
command once per file. Do not upload likely secrets unless the user explicitly names the file.
On failure, report the concise CLI error without retrying or reimplementing the upload.

If storage configuration is missing or invalid, report the error and point the user to
`lpc config --storage-uri <OpenDAL URI>`. Do not set storage credentials as part of an upload request.
