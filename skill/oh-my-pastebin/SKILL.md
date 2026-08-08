---
name: oh-my-pastebin
description: Upload local files with the `omp` CLI and return their keys and temporary download links. Use when the user asks to upload a file with Oh My Pastebin or `omp`.
---

# Oh My Pastebin

Upload the exact regular file requested by the user:

```sh
omp upload -- "/absolute/path/to/file"
```

Return the `key` and temporary `download_url` emitted by the command. For multiple files, run the
command once per file. Do not upload likely secrets unless the user explicitly names the file.
On failure, report the concise CLI error without retrying or reimplementing the upload.
