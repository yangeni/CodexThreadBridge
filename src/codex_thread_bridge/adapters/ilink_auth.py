from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from codex_thread_bridge.adapters.ilink_client import IlinkHttpClient


DEFAULT_ILINK_BOT_BASE_URL = "https://ilinkai.weixin.qq.com/ilink/bot"


class IlinkAuthError(RuntimeError):
    pass


class IlinkAuthTransientError(IlinkAuthError):
    pass


class IlinkLoginTimeoutError(IlinkAuthError):
    pass


class UrlGetTransport(Protocol):
    def get_json(self, url: str, timeout: float) -> dict:
        ...


class UrllibGetTransport:
    def get_json(self, url: str, timeout: float) -> dict:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise IlinkAuthTransientError(
                "iLink login HTTP request failed: %s" % reason
            ) from exc
        try:
            result = json.loads(payload)
        except ValueError as exc:
            raise IlinkAuthTransientError("iLink login response was not JSON") from exc
        if not isinstance(result, dict):
            raise IlinkAuthTransientError("iLink login response was not an object")
        return result


@dataclass(frozen=True)
class IlinkLoginQRCode:
    qrcode: str
    qrcode_url: str


@dataclass(frozen=True)
class IlinkCredentials:
    bot_token: str
    account_id: str
    base_url: str


@dataclass(frozen=True)
class IlinkLoginStatus:
    status: str
    credentials: Optional[IlinkCredentials] = None


class IlinkAuthClient:
    def __init__(
        self,
        base_url: str = DEFAULT_ILINK_BOT_BASE_URL,
        transport: Optional[UrlGetTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport or UrllibGetTransport()

    def start_login(
        self,
        *,
        bot_type: str = "3",
        timeout_seconds: float = 30.0,
    ) -> IlinkLoginQRCode:
        response = self._get(
            "get_bot_qrcode",
            {"bot_type": bot_type},
            timeout_seconds=timeout_seconds,
        )
        self._raise_for_ret(response, "get_bot_qrcode")
        qrcode = _required_string(response, "qrcode", "get_bot_qrcode")
        qrcode_url = _first_string(
            response,
            ("qrcode_img_content", "qrcode_url", "url"),
        )
        if qrcode_url is None:
            raise IlinkAuthError("iLink get_bot_qrcode failed: missing QR URL")
        return IlinkLoginQRCode(qrcode=qrcode, qrcode_url=qrcode_url)

    def poll_status(
        self,
        qrcode: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> IlinkLoginStatus:
        if not qrcode:
            raise IlinkAuthError("qrcode must not be empty")
        response = self._get(
            "get_qrcode_status",
            {"qrcode": qrcode},
            timeout_seconds=timeout_seconds,
        )
        self._raise_for_ret(response, "get_qrcode_status")
        status = _first_string(response, ("status", "qrcode_status", "state"))
        if status is None:
            raise IlinkAuthError("iLink get_qrcode_status failed: missing status")
        normalized = status.strip().lower()
        if normalized == "confirmed":
            return IlinkLoginStatus(
                status=normalized,
                credentials=self._credentials_from_response(response),
            )
        return IlinkLoginStatus(status=normalized)

    def wait_for_login(
        self,
        qrcode: str,
        *,
        timeout_seconds: float = 480.0,
        poll_interval_seconds: float = 2.0,
        request_timeout_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> IlinkCredentials:
        deadline = monotonic() + timeout_seconds
        while monotonic() <= deadline:
            result = self.poll_status(qrcode, timeout_seconds=request_timeout_seconds)
            if result.credentials is not None:
                return result.credentials
            if result.status in {"expired", "cancelled", "canceled"}:
                raise IlinkAuthError("iLink QR login %s" % result.status)
            if result.status not in {"waiting", "scaned", "scanned"}:
                raise IlinkAuthError("unsupported iLink QR login status: %s" % result.status)
            sleep(poll_interval_seconds)
        raise IlinkLoginTimeoutError("iLink QR login timed out")

    def _credentials_from_response(self, response: dict) -> IlinkCredentials:
        return IlinkCredentials(
            bot_token=_required_string(response, "bot_token", "get_qrcode_status"),
            account_id=_required_string(response, "ilink_bot_id", "get_qrcode_status"),
            base_url=str(response.get("baseurl") or self.base_url).rstrip("/"),
        )

    def _get(
        self,
        endpoint: str,
        query: dict[str, str],
        *,
        timeout_seconds: float,
    ) -> dict:
        url = "%s/%s?%s" % (
            self.base_url,
            endpoint,
            urllib.parse.urlencode(query),
        )
        return self._transport.get_json(url, float(timeout_seconds))

    def _raise_for_ret(self, response: dict, operation: str) -> None:
        if "ret" not in response:
            raise IlinkAuthError("iLink %s failed: missing integer ret" % operation)
        ret = response["ret"]
        if not isinstance(ret, int) or isinstance(ret, bool):
            raise IlinkAuthError("iLink %s failed: malformed integer ret" % operation)
        if ret != 0:
            message = response.get("errmsg") or response.get("errcode") or ret
            raise IlinkAuthError("iLink %s failed: %s" % (operation, message))


class IlinkCredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> IlinkCredentials:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError as exc:
            raise IlinkAuthError("iLink credentials file does not exist") from exc
        except ValueError as exc:
            raise IlinkAuthError("iLink credentials file is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise IlinkAuthError("iLink credentials file must contain an object")
        try:
            credentials = IlinkCredentials(
                bot_token=_required_string(raw, "bot_token", "credentials"),
                account_id=_required_string(raw, "account_id", "credentials"),
                base_url=_required_string(raw, "base_url", "credentials").rstrip("/"),
            )
        except IlinkAuthError as exc:
            raise IlinkAuthError("invalid iLink credentials: %s" % exc) from exc
        return credentials

    def save(self, credentials: IlinkCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bot_token": credentials.bot_token,
            "account_id": credentials.account_id,
            "base_url": credentials.base_url.rstrip("/"),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        os.chmod(self.path, 0o600)


def render_env_lines(
    *,
    credentials_path: Path,
    owner_user_ids: Sequence[str],
) -> str:
    lines = [
        "ILINK_CREDENTIALS_PATH=%s" % credentials_path,
        "ILINK_OWNER_USER_IDS=%s" % ",".join(owner_user_ids),
    ]
    return "\n".join(lines)


def summarize_update_senders(batch: dict) -> str:
    msgs = batch.get("msgs", ())
    if not isinstance(msgs, list) or not msgs:
        return "No iLink messages found in this update batch."
    lines = []
    for index, msg in enumerate(msgs, start=1):
        if not isinstance(msg, dict):
            lines.append("%s. malformed message: expected object" % index)
            continue
        lines.append(
            "%s. from_user_id=%s conversation_type=%s message_id=%s text=%s"
            % (
                index,
                _display_value(msg.get("from_user_id")),
                _display_value(
                    msg.get("conversation_type") or _infer_conversation_type(msg)
                ),
                _display_value(msg.get("message_id")),
                _message_preview(msg),
            )
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="OpenClaw-free iLink QR login helper"
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_ILINK_BOT_BASE_URL,
        help="iLink bot API base URL",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="scan QR and save credentials")
    login_parser.add_argument(
        "--credentials-path",
        type=Path,
        default=Path("data/local/ilink_credentials.json"),
        help="local credentials JSON path",
    )
    login_parser.add_argument("--bot-type", default="3")
    login_parser.add_argument("--timeout-seconds", type=float, default=480.0)
    login_parser.add_argument("--poll-interval-seconds", type=float, default=2.0)

    env_parser = subparsers.add_parser("show-env", help="print safe env settings")
    env_parser.add_argument(
        "--credentials-path",
        type=Path,
        default=Path("data/local/ilink_credentials.json"),
        help="local credentials JSON path",
    )
    env_parser.add_argument(
        "--owner-user-id",
        action="append",
        default=[],
        help="owner sender id; may be passed more than once",
    )

    probe_parser = subparsers.add_parser(
        "probe-updates",
        help="print recent sender ids without sending replies",
    )
    probe_parser.add_argument(
        "--credentials-path",
        type=Path,
        default=Path("data/local/ilink_credentials.json"),
        help="local credentials JSON path",
    )
    probe_parser.add_argument("--timeout-seconds", type=float, default=35.0)

    args = parser.parse_args(argv)
    if args.command == "login":
        client = IlinkAuthClient(base_url=args.base_url)
        qrcode = client.start_login(bot_type=args.bot_type)
        print("Scan this QR URL with WeChat:")
        print(qrcode.qrcode_url)
        credentials = client.wait_for_login(
            qrcode.qrcode,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        IlinkCredentialStore(args.credentials_path).save(credentials)
        print("Saved iLink credentials to: %s" % args.credentials_path)
        print("Token is stored locally and was not printed.")
        return 0
    if args.command == "show-env":
        print(
            render_env_lines(
                credentials_path=args.credentials_path,
                owner_user_ids=tuple(args.owner_user_id),
            )
        )
        return 0
    if args.command == "probe-updates":
        credentials = IlinkCredentialStore(args.credentials_path).load()
        client = IlinkHttpClient(credentials.base_url, credentials.bot_token)
        batch = client.get_updates("", timeout_seconds=args.timeout_seconds)
        print(summarize_update_senders(batch))
        return 0
    raise AssertionError("unreachable command")


def _required_string(source: dict, key: str, operation: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IlinkAuthError("iLink %s failed: missing %s" % (operation, key))
    return value.strip()


def _first_string(source: dict, keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _display_value(value: object) -> str:
    if value is None:
        return "<missing>"
    text = str(value).replace("\n", " ").strip()
    return text or "<blank>"


def _infer_conversation_type(msg: dict) -> str:
    to_user_id = msg.get("to_user_id")
    if isinstance(to_user_id, str) and to_user_id.startswith("group"):
        return "group"
    return "private"


def _message_preview(msg: dict) -> str:
    item_list = msg.get("item_list", ())
    if not isinstance(item_list, list):
        return "<non-text>"
    texts = []
    for item in item_list:
        if not isinstance(item, dict) or item.get("type") != 1:
            continue
        text_item = item.get("text_item")
        if not isinstance(text_item, dict):
            continue
        text = text_item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip().replace("\n", " "))
    if not texts:
        return "<non-text>"
    preview = " ".join(texts)
    if len(preview) > 120:
        return preview[:117] + "..."
    return preview


if __name__ == "__main__":
    raise SystemExit(main())
