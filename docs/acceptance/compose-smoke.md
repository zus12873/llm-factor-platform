# Compose 冒烟

**日期：** 2026-08-26T15:07:04Z  
**主机：** `huanmengdeMacBook-Air.local`（macOS 26.6.2, Darwin 25.6.0 arm64）  
**仓库路径：** `/Users/huanmeng/Downloads/Projects/llm_source_catch`  
**分支：** `feat/remaining-p1-p2-gaps`  
**结果：** `SKIPPED` — Docker CLI 不存在。这不是通过，也不是假通过。

---

## 闸门（Gate）

计划要求：`docker info` 失败则把**原文错误**写入本文件并停止，不得冒充冒烟通过。

```text
$ /bin/zsh -lc 'docker info'
zsh:1: command not found: docker
EXIT:127
```

`command -v docker` 无输出。下列常见安装路径均不存在：

- `/usr/local/bin/docker`
- `/opt/homebrew/bin/docker`
- `/usr/bin/docker`
- `~/.docker/bin/docker`

因此 **没有** 复制 `deploy/compose.env.example` → `deploy/compose.env`，**没有** `docker compose up`，**没有** 打镜像，**没有** 请求 `/api/health`。

---

## 契约测试（已跑，离线）

`deploy/compose.yaml` 未改。Worker 仍是 `network_mode: none`，无发布端口；前端仍绑定 `127.0.0.1:8080`。

```text
$ uv run --project backend pytest backend/tests/deploy/test_compose_contract.py -v
```

```text
============================= test session starts ==============================
platform darwin -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0 -- /Users/huanmeng/Downloads/Projects/llm_source_catch/backend/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /Users/huanmeng/Downloads/Projects/llm_source_catch/backend
configfile: pyproject.toml
plugins: cov-7.1.0, asyncio-1.4.0, anyio-4.14.2, respx-0.23.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 13 items

backend/tests/deploy/test_compose_contract.py::test_the_worker_has_no_network PASSED [  7%]
backend/tests/deploy/test_compose_contract.py::test_the_worker_holds_no_database_or_model_credential PASSED [ 15%]
backend/tests/deploy/test_compose_contract.py::test_the_worker_holds_only_the_verification_key PASSED [ 23%]
backend/tests/deploy/test_compose_contract.py::test_the_worker_shares_only_the_job_and_artifact_volumes PASSED [ 30%]
backend/tests/deploy/test_compose_contract.py::test_the_worker_does_not_publish_a_port PASSED [ 38%]
backend/tests/deploy/test_compose_contract.py::test_only_the_frontend_is_published PASSED [ 46%]
backend/tests/deploy/test_compose_contract.py::test_the_frontend_binds_to_loopback_only PASSED [ 53%]
backend/tests/deploy/test_compose_contract.py::test_the_backend_is_not_published_directly PASSED [ 61%]
backend/tests/deploy/test_compose_contract.py::test_no_secret_value_is_written_into_the_compose_file PASSED [ 69%]
backend/tests/deploy/test_compose_contract.py::test_the_env_example_has_no_filled_values PASSED [ 76%]
backend/tests/deploy/test_compose_contract.py::test_every_service_runs_as_a_non_root_user PASSED [ 84%]
backend/tests/deploy/test_compose_contract.py::test_the_proxy_does_not_buffer_the_event_stream PASSED [ 92%]
backend/tests/deploy/test_compose_contract.py::test_the_proxy_caps_upload_size PASSED [100%]

============================== 13 passed in 0.04s ==============================
```

13 passed。契约通过 **不等于** Compose 冒烟通过。

---

## 冒烟命令（未执行）

有 Docker 的机器应按下列步骤跑一遍，并把**真实输出**替换本节。在此之前不要把本节标成 PASS。

### 1. 本地 env（勿提交）

```bash
cp deploy/compose.env.example deploy/compose.env
# 生成 SESSION_COOKIE_SECRET 与 MANIFEST_SIGNING_KEY（32+ 随机字节）。
# 保持 WIND_ENABLED=false；Wind / 模型密钥留空即可。
```

`deploy/compose.env` 不得入库。`WIND_ENABLED=false` 时健康检查可以报告 Wind/LLM 为 disabled / unconfigured / down，这可接受。

### 2. 启动

```bash
docker compose --env-file deploy/compose.env -f deploy/compose.yaml up --build -d
```

### 3. 健康检查

```bash
curl -fsS http://127.0.0.1:8080/api/health
```

预期（离线、无凭据时的形状，不是本次实测）：

- HTTP 成功（`curl -fsS` 退出 0）
- JSON 里 `status` 可为 `"ok"`（仅 database `down` 会把整体打成 down）
- `wind.status` 为 `"disabled"`（`WIND_ENABLED is false`）
- `llm.status` 为 `"unconfigured"`（`no provider key set`）
- 前端继续只绑 `127.0.0.1:8080`，不得改成 `0.0.0.0`

### 4. Worker 隔离

```bash
docker compose -f deploy/compose.yaml ps
docker inspect "$(docker compose -f deploy/compose.yaml ps -q worker)" --format '{{.HostConfig.NetworkMode}} {{json .NetworkSettings.Ports}}'
```

预期：

- `NetworkMode` == `none`
- 无已发布端口（`Ports` 为空 / `null`）
- worker 服务没有 `ports:` 映射

### 5. 拆除

```bash
docker compose -f deploy/compose.yaml down
```

---

## 本次未声称的事项

- 镜像能否构建：未知
- `127.0.0.1:8080/api/health` 运行时行为：未知
- Worker 容器实际 `NetworkMode`：未知（仅契约文件断言 `network_mode: none`）
- 未改 `deploy/compose.yaml`、Dockerfiles、`handoff.md`、`docs/使用说明.md`

有 Docker 后再跑本节命令，用真实输出覆盖「未执行」部分，再把结果改成 `PASS` 或真实失败。
