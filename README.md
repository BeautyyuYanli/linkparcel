# LinkParcel

一个使用私有存储的文件分享工具，提供共享的 `services` 和两个入口：

- `lpc` CLI 在本机调用 services，使用本机存储凭据选择对象 key、签发链接并直传文件，无需启动 LinkParcel server。
- FastAPI server 调用同一套 services，保留共享 token 鉴权，提供文件列表和上传/下载授权接口，不代理文件内容。

业务逻辑位于 `src/linkparcel/services/`；HTTP 和命令行适配分别位于
`src/linkparcel/controllers/server.py` 和 `cli.py`。services 不依赖 FastAPI、Typer、应用 token 或 XDG 路径。

## 准备环境

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。安装依赖：

```sh
uv sync
```

CLI 和 server 都需要一个 OpenDAL URI。对应 backend 必须支持 `list`、`presign_write` 和
`presign_read`，并由 backend 自己的配置方式提供存储凭据。存储必须保持私有。
`LPC_STORAGE_URI` 会原样交给 OpenDAL，可能包含敏感配置，不应写入日志或提交版本库。

## CLI 配置与上传

将存储 URI 保存到本机配置，再上传文件：

```sh
uv run lpc config --storage-uri 's3://bucket?endpoint=https%3A%2F%2Fstorage.example&region=us-east-1&access_key_id=replace-me&secret_access_key=replace-me'
uv run lpc upload ./report.pdf
```

`config` 不连接存储，每次写入完整的三个设置，成功时只输出配置文件路径。
可通过 `--upload-url-ttl-seconds` 和 `--download-url-ttl-seconds` 设置正整数秒数，
未指定时分别写入 `300` 和 `3600`。

配置保存在 `${XDG_CONFIG_HOME:-$HOME/.config}/lpc/env`，目录权限为 `0700`，文件权限为
`0600`。写入会拒绝符号链接和非普通文件，保留无关配置，并移除旧 CLI 的 `LPC_API_URL` 和
`LPC_API_TOKEN`。配置里的 URI 不进行 dotenv 变量展开，以保留凭据中的特殊字符。

上传时，进程环境中的 `LPC_STORAGE_URI`、`LPC_UPLOAD_URL_TTL_SECONDS` 和
`LPC_DOWNLOAD_URL_TTL_SECONDS` 各自覆盖文件中的对应值，未设置的 TTL 使用默认值。
也可以只配置环境变量，不创建配置文件。CLI 不使用 `LPC_API_URL` 或 `LPC_API_TOKEN`。

成功时 stdout 只有一行 JSON：

```json
{"key":"report-1.pdf","download_url":"https://storage.example/presigned-read/..."}
```

上传前检查本地普通文件，再创建和检查本次调用的 OpenDAL operator。共享 services 基于一次
存储列表选择 `-1`、`-2` 等重名后缀，签发上传请求，然后流式传输文件并生成下载链接。
文件与 HTTP client 在使用后关闭；operator 不跨调用或事件循环复用。
不协调并发重名上传，也不重试失败的上传或签名请求。

backend 支持相应能力时，上传对象使用 `application/octet-stream`，并用最终 key 设置附件文件名；
不支持时跳过对应元数据。文件已上传但下载链接签发失败时，命令以非零状态退出，stdout 为空，
stderr 会包含已经上传的 key。

## HTTP server

需要为其他客户端提供授权接口时，可以单独运行 server。复制 `.env.example`，填写存储配置和
`LPC_API_TOKEN`，再将变量导入服务端进程环境：

```sh
cp .env.example .env
set -a
. ./.env
set +a
make serve
```

server 只读取进程环境，不读取 CLI 的 XDG 配置。启动时创建并检查一个 operator，在请求间复用。
以下接口均要求 `Authorization: Bearer <LPC_API_TOKEN>`：

| 接口 | 功能 |
| --- | --- |
| `GET /api/v1/files` | 列出存储根目录下的文件 |
| `POST /api/v1/uploads` | 根据 `{"name":"report.pdf"}` 签发上传请求 |
| `POST /api/v1/downloads` | 根据 `{"key":"report.pdf"}` 签发下载链接 |

缺失或无效 token 返回 `401`，非法 key 返回 `422`，存储操作失败返回不包含凭据的 `503`。
签名响应使用 `Cache-Control: no-store`。调用者根据授权中的 method、headers 和 URL 直接访问存储，
自行提供上传内容及 `Content-Length`。

## 安全语义

CLI 持有存储凭据，权限由存储端配置决定。server 的 `LPC_API_TOKEN` 是部署级共享权限，
不代表用户身份；任何持有者都拥有该服务的全部业务权限。

预签名 URL 是 bearer capability，持有 URL 即可在有效期内访问对应对象，不能写入日志或随意转发。
撤销应用 token 不会撤销已经签发的 URL，而且 URL 在 backend 规定的过期时间前可能被重复使用，
不能视为一次性链接。

## Docker Compose

### SeaweedFS mini

内置的 Compose 配置会构建 API、启动私有 SeaweedFS mini，并在 API 启动前创建 bucket。
先创建配置并至少替换 `LPC_API_TOKEN`：

```sh
cp -n .env.example .env
docker compose up --build -d
docker compose ps
```

宿主机发布 API 的 `59000` 和 SeaweedFS S3 API 的 `59001`，绑定所有宿主机接口；
SeaweedFS 管理端口不发布。CLI 可在宿主机运行，使用与 Compose 相同的存储配置：

```sh
set -a
. ./.env
set +a
export LPC_STORAGE_URI="s3://${S3_BUCKET:-lpc}?endpoint=http%3A%2F%2Fseaweedfs.localhost%3A59001&region=us-east-1&access_key_id=${AWS_ACCESS_KEY_ID:-lpcdev}&secret_access_key=${AWS_SECRET_ACCESS_KEY:-lpc-seaweedfs-password}"
uv run lpc upload ./report.pdf
```

这条 CLI 上传链路只依赖 SeaweedFS。预签名 URL 使用 `seaweedfs.localhost:59001`：该名称在
Compose 网络中指向 SeaweedFS，在当前宿主机上指向 loopback，因此 API 容器和宿主机 CLI
使用同一个 endpoint。凭据和 bucket 名需保持 URL-safe，因为上述配置将它们嵌入 URI。
停止服务但保留对象数据：

```sh
docker compose down
```

SeaweedFS mini 使用 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY` 和 `S3_BUCKET`
配置凭据和自动创建的 bucket，数据保存在独立的 `seaweedfs-data` 卷。
从旧版 MinIO Compose 升级时，需要将 `.env` 中的 `MINIO_ROOT_USER`、
`MINIO_ROOT_PASSWORD`、`MINIO_BUCKET` 分别改为上述变量。
旧 `minio-data` 卷不会自动迁移，也不能直接供 SeaweedFS 使用；如需保留已有文件，
请先通过 S3 接口复制到 SeaweedFS，验证后再停用旧 MinIO。

### Cloudflare R2

只使用 CLI 时，将 R2 的 OpenDAL URI 和可选 TTL 放入 `.dev.r2.env`，即可直接上传：

```sh
uv run --env-file .dev.r2.env lpc upload ./report.pdf
```

如果还需要 HTTP 授权接口，在同一文件中配置 `LPC_API_TOKEN` 并启动 server：

```sh
docker compose --env-file .dev.r2.env -f compose.r2.yaml up --build -d
docker compose --env-file .dev.r2.env -f compose.r2.yaml ps
```

停止 R2 server：

```sh
docker compose --env-file .dev.r2.env -f compose.r2.yaml down
```

## 从旧 CLI 架构和名称迁移

`lpc auth` 已替换为 `lpc config`，CLI 不再调用 LinkParcel server。
旧应用 token 无法转换为存储凭据，请运行 `lpc config --storage-uri <OpenDAL URI>`
重新配置；原 XDG 文件路径不变，旧 API 配置项会在写入时清理。server 的应用 token 仍需保留。
Python 入口已改为 `linkparcel.controllers.cli:app` 和 `linkparcel.controllers.server:app`，
引用旧模块路径的启动脚本需同步更新。

项目原名 Oh My Pastebin。命令由 `omp` 改为 `lpc`，Python 包由 `oh_my_pastebin` 改为
`linkparcel`，配置变量前缀由 `OMP_` 改为 `LPC_`。旧命令和旧变量不再受支持；请同步修改部署环境
与脚本中的引用。CLI 配置目录为 `${XDG_CONFIG_HOME:-$HOME/.config}/lpc`，Skill 目录与调用名为
`linkparcel` / `$linkparcel`。

仅迁移项目名称时，已有部署应保留原存储 URI、存储凭据和 bucket 名，只修改变量名称。
继续使用旧 MinIO Compose 的部署需显式设置 `MINIO_BUCKET=omp`、`MINIO_ROOT_USER=ompdev`
和原来的 `MINIO_ROOT_PASSWORD`，避免切换到新 bucket。
若重命名本地目录，请通过 `docker compose -p <原项目名>` 保持已有容器和数据卷的归属；
R2 的默认 Compose 项目名已改为 `linkparcel-r2`，已有部署可用
`-p oh-my-pastebin-r2` 继续管理原项目。

## 开发检查

```sh
make check
```

该命令依次执行 Ruff、Pyright 和本地 Pytest 契约测试。
