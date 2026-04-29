# iLink Standalone Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OpenClaw-free iLink QR login bootstrapper that saves local credentials for the existing CodexThreadBridge WeChat private-chat runtime.

**Architecture:** Keep Gateway Core unchanged. Add a small `ilink_auth` adapter module for QR login/status polling and local credential persistence, then let `OpeniLinkRuntimeConfig` load credentials from that file when explicit environment variables are not set. The runtime still talks to `getupdates` and `sendmessage` through the existing `IlinkHttpClient`.

**Tech Stack:** Python standard library only, pytest, existing `src` package layout, local JSON credentials ignored by git.

---

## File Structure

- Create `src/codex_thread_bridge/adapters/ilink_auth.py`: QR login HTTP GET transport, status polling, credential model, credential JSON store, env-file rendering helper, owner-id update probe, CLI entry point.
- Create `tests/test_ilink_auth.py`: unit tests for QR endpoint calls, status handling, timeout behavior, safe credential persistence, and `.env` rendering.
- Modify `src/codex_thread_bridge/config.py`: allow `OpeniLinkRuntimeConfig.from_env()` to load `ILINK_CREDENTIALS_PATH` when `ILINK_BASE_URL` or `ILINK_BOT_TOKEN` are not set.
- Modify `.env.example`: add standalone login variables and keep secrets as placeholders.
- Modify `README.md` and `docs/03_v0.3_OpeniLink运行说明.md`: document v0.4 path and the no-OpenClaw boundary.
- Create `docs/04_v0.4_iLink独立登录.md`: operator instructions for QR login, owner id discovery, and runtime startup.

## Task 1: Credential Model And QR Login Client

**Files:**
- Create: `src/codex_thread_bridge/adapters/ilink_auth.py`
- Test: `tests/test_ilink_auth.py`

- [ ] **Step 1: Write failing tests**

```python
def test_start_login_fetches_qrcode_from_default_ilink_host() -> None:
    transport = RecordingGetTransport([
        {"ret": 0, "qrcode": "qr-1", "qrcode_img_content": "https://scan.example/qr"}
    ])
    client = IlinkAuthClient(transport=transport)

    result = client.start_login()

    assert result.qrcode == "qr-1"
    assert result.qrcode_url == "https://scan.example/qr"
    assert transport.requests[0][0] == (
        "https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3"
    )


def test_poll_login_returns_credentials_when_confirmed() -> None:
    transport = RecordingGetTransport([
        {
            "ret": 0,
            "status": "confirmed",
            "bot_token": "token-1",
            "ilink_bot_id": "bot-1",
            "baseurl": "https://ilinkai.weixin.qq.com/ilink/bot",
        }
    ])
    client = IlinkAuthClient(transport=transport)

    result = client.poll_status("qr-1")

    assert result.status == "confirmed"
    assert result.credentials == IlinkCredentials(
        bot_token="token-1",
        account_id="bot-1",
        base_url="https://ilinkai.weixin.qq.com/ilink/bot",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_ilink_auth.py -q`

Expected: FAIL because `codex_thread_bridge.adapters.ilink_auth` does not exist.

- [ ] **Step 3: Implement minimal auth client**

Add dataclasses `IlinkLoginQRCode`, `IlinkCredentials`, `IlinkLoginStatus`, a `UrlGetTransport` protocol, `UrllibGetTransport`, and `IlinkAuthClient.start_login()` / `poll_status()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/test_ilink_auth.py -q`

Expected: PASS for QR and confirmed-login behavior.

## Task 2: Polling, Timeout, And Credential Persistence

**Files:**
- Modify: `src/codex_thread_bridge/adapters/ilink_auth.py`
- Test: `tests/test_ilink_auth.py`

- [ ] **Step 1: Write failing tests**

```python
def test_wait_for_login_ignores_waiting_status_and_returns_confirmed() -> None:
    transport = RecordingGetTransport([
        {"ret": 0, "status": "waiting"},
        {
            "ret": 0,
            "status": "confirmed",
            "bot_token": "token-1",
            "ilink_bot_id": "bot-1",
            "baseurl": "https://ilinkai.weixin.qq.com/ilink/bot",
        },
    ])
    sleeper = RecordingSleeper()
    client = IlinkAuthClient(transport=transport)

    credentials = client.wait_for_login(
        "qr-1",
        timeout_seconds=10.0,
        poll_interval_seconds=0.1,
        monotonic=sleeper.monotonic,
        sleep=sleeper.sleep,
    )

    assert credentials.bot_token == "token-1"
    assert sleeper.sleeps == [0.1]


def test_credential_store_writes_private_json(tmp_path: Path) -> None:
    path = tmp_path / "ilink_credentials.json"
    store = IlinkCredentialStore(path)
    store.save(IlinkCredentials("token-1", "bot-1", "https://host/ilink/bot"))

    assert store.load().bot_token == "token-1"
    assert json.loads(path.read_text()) == {
        "bot_token": "token-1",
        "account_id": "bot-1",
        "base_url": "https://host/ilink/bot",
    }
    assert oct(path.stat().st_mode & 0o777) == "0o600"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_ilink_auth.py -q`

Expected: FAIL because wait/store helpers do not exist.

- [ ] **Step 3: Implement wait and store**

Add `wait_for_login()` with explicit timeout and status handling for `waiting`, `scaned`, `scanned`, `expired`, and `confirmed`. Add `IlinkCredentialStore.load()` and `save()` with parent directory creation and `0600` permissions.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src pytest tests/test_ilink_auth.py -q`

Expected: PASS.

## Task 3: Runtime Config Loads Saved Credentials

**Files:**
- Modify: `src/codex_thread_bridge/config.py`
- Test: `tests/test_ilink_config.py`

- [ ] **Step 1: Write failing tests**

```python
def test_openilink_config_loads_credentials_path(monkeypatch, tmp_path: Path) -> None:
    credentials_path = tmp_path / "ilink_credentials.json"
    credentials_path.write_text(json.dumps({
        "bot_token": "token-1",
        "account_id": "bot-1",
        "base_url": "https://ilinkai.weixin.qq.com/ilink/bot",
    }))
    monkeypatch.setenv("CTB_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("ILINK_CREDENTIALS_PATH", str(credentials_path))
    monkeypatch.setenv("ILINK_OWNER_USER_IDS", "owner-1")

    config = OpeniLinkRuntimeConfig.from_env()

    assert config.base_url == "https://ilinkai.weixin.qq.com/ilink/bot"
    assert config.bot_token == "token-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_ilink_config.py::test_openilink_config_loads_credentials_path -q`

Expected: FAIL because config requires `ILINK_BASE_URL` and `ILINK_BOT_TOKEN`.

- [ ] **Step 3: Implement credentials-path fallback**

If either `ILINK_BASE_URL` or `ILINK_BOT_TOKEN` is missing, read `ILINK_CREDENTIALS_PATH`. Explicit env values override the credential file.

- [ ] **Step 4: Run config tests**

Run: `PYTHONPATH=src pytest tests/test_ilink_config.py -q`

Expected: PASS.

## Task 4: Login CLI And Docs

**Files:**
- Modify: `src/codex_thread_bridge/adapters/ilink_auth.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/03_v0.3_OpeniLink运行说明.md`
- Create: `docs/04_v0.4_iLink独立登录.md`
- Test: `tests/test_ilink_auth.py`

- [ ] **Step 1: Write failing tests for env rendering**

```python
def test_render_env_lines_redacts_token() -> None:
    lines = render_env_lines(
        credentials_path=Path("/private/ctb/ilink_credentials.json"),
        owner_user_ids=("owner-1",),
    )

    assert "ILINK_CREDENTIALS_PATH=/private/ctb/ilink_credentials.json" in lines
    assert "ILINK_OWNER_USER_IDS=owner-1" in lines
    assert "ILINK_BOT_TOKEN" not in lines
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_ilink_auth.py::test_render_env_lines_redacts_token -q`

Expected: FAIL because `render_env_lines` does not exist.

- [ ] **Step 3: Implement CLI**

Add `main()` with subcommands:

```text
login --credentials-path data/local/ilink_credentials.json
show-env --credentials-path data/local/ilink_credentials.json --owner-user-id <id>
probe-updates --credentials-path data/local/ilink_credentials.json
```

`login` prints the QR URL, waits for confirmation, saves credentials, and never prints the token.
`probe-updates` reads one iLink update batch, prints `from_user_id` summaries, and never sends replies or Codex messages.

- [ ] **Step 4: Update docs**

Document:

```text
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.ilink_auth login
PYTHONPATH=src python3 -m codex_thread_bridge.adapters.openilink_runtime --once
```

Explain that OpenClaw is not installed or started, and open-source WeChat/iLink projects are only protocol references.

- [ ] **Step 5: Run full verification**

Run: `PYTHONPATH=src pytest -q`

Expected: all tests pass.

## Self-Review

- Spec coverage: QR login, token persistence, owner id discovery, OpenClaw-free runtime handoff, and docs are covered.
- Explicit non-goals: media upload, group runtime, file sending, OpenClaw install, and token auto-refresh are not in this v0.4 slice.
- Secret handling: token is saved only in ignored local JSON or local `.env`; generated docs and examples never contain a real token.
