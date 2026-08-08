# Oh My Pastebin

一个单租户、私有存储的 pastebin。FastAPI 只负责共享 token 鉴权、选择最终对象 key，
以及签发短期上传/下载请求；文件内容由 CLI 与存储后端直接传输。

## 准备环境

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。安装依赖：

```sh
uv sync
```

服务端需要一个 OpenDAL URI。对应 backend 必须支持 `list`、`presign_write`、
`presign_read` 和 `write_with_content_type`，并由 backend 自己的配置方式提供存储凭据。
存储必须保持私有。

复制 `.env.example` 后填写真实配置，再将变量导入服务端进程环境：

```sh
cp .env.example .env
set -a
. ./.env
set +a
uv run fastapi dev src/oh_my_pastebin/app.py
```

`OMP_STORAGE_URI` 会原样交给 OpenDAL，可能包含敏感配置，不应写入日志或提交版本库。

## 安全语义

`OMP_API_TOKEN` 是部署级共享权限，不代表用户身份；任何持有者都拥有该 pastebin 的全部
业务权限。预签名 URL 是 bearer capability，持有 URL 即可在有效期内访问对应对象，不能
写入日志或随意转发。撤销业务 token 不会撤销已经签发的 URL，而且 URL 在 backend 规定的
过期时间前可能被重复使用，不能视为一次性链接。

## 上传

CLI 只需要控制面地址和业务 token，不需要存储凭据：

```sh
export OMP_API_URL=http://127.0.0.1:59000
export OMP_API_TOKEN=replace-with-a-secret
uv run omp upload ./report.pdf
```

成功时 stdout 只有一行 JSON：

```json
{"key":"report-1.pdf","download_url":"https://storage.example/presigned-read/..."}
```

同名对象由服务端基于一次存储列表选择 `-1`、`-2` 等后缀。首版不协调并发上传，
也不重试失败的上传或签名请求。

## Docker Compose

### MinIO

内置的 Compose 配置会构建 API、启动私有 MinIO，并在 API 启动前创建 bucket。
先创建配置并至少替换 `OMP_API_TOKEN`：

```sh
cp -n .env.example .env
docker compose up --build -d
docker compose ps
```

宿主机只发布运行链路必需的两个端口：API 使用 `59000`，CLI 直传和下载使用 MinIO API
的 `59001`。两个端口绑定所有宿主机接口，MinIO Console 不发布。CLI 可在宿主机运行：

```sh
set -a
. ./.env
set +a
uv run omp upload ./report.pdf
```

预签名 URL 使用 `minio.localhost:59001`：该名称在 Compose 网络中指向 MinIO，在
当前宿主机上指向 loopback，因此 API 容器和宿主机 CLI 使用的是同一个 endpoint。
停止服务但保留对象数据：

```sh
docker compose down
```

### Cloudflare R2

R2 版本只运行 API，启动时通过 `--env-file` 指定服务端配置：

```sh
docker compose --env-file .dev.r2.env -f compose.r2.yaml up --build -d
docker compose --env-file .dev.r2.env -f compose.r2.yaml ps
```

宿主机 CLI 可加载同一配置文件：

```sh
uv run --env-file .dev.r2.env omp upload ./report.pdf
```

停止 R2 版本：

```sh
docker compose --env-file .dev.r2.env -f compose.r2.yaml down
```

## 开发检查

```sh
make check
```

该命令依次执行 Ruff、Pyright 和本地 Pytest 契约测试。
