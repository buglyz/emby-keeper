from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import List, Optional
from urllib.parse import parse_qsl, quote, unquote, urlparse

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError
import tomli as tomllib
from tomlkit import document, dumps, parse

from embykeeper.apprise import AppriseStream
from embykeeper.config import config as default_config
from embykeeper.schema import (
    CheckinerConfig,
    EmbyConfig,
    NotifierConfig,
    ProxyConfig,
    RegistrarConfig,
    SiteConfig,
)

from .automation_runtime import automation_runtime as default_automation_runtime
from .models import (
    AutomationConfigResponse,
    AutomationConfigUpdate,
    AutomationRegistrarSchedule,
    ConfigBackupResponse,
    ConfigBackupItem,
    ConfigExportResponse,
    ConfigRestoreRequest,
    ConfigRestoreResponse,
    GlobalConfigResponse,
    GlobalConfigUpdate,
    NotifierConfigResponse,
    NotifierConfigUpdate,
)
from .scheduler_bridge import bridge as default_bridge
from .validation import validate_schedule_fields

logger = logger.bind(scheme="embykeeperapi")
REDACTED_VALUE = "***REDACTED***"
SECRET_CONFIG_KEYS = {
    "password",
    "apprise_uri",
    "mongodb",
    "token",
    "access_token",
    "encrypted_token",
    "secret",
}
SECRET_CONFIG_KEY_PARTS = ("token", "secret", "password", "credential", "apikey", "api_key")
BACKUP_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z(?:-\d+)?$")
BOT_USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
TEMPL_A_PATTERN = re.compile(r"^templ_a<@?(.+?)>$")


def model_fields_set(model) -> set:
    fields_set = getattr(model, "model_fields_set", None)
    if fields_set is not None:
        return fields_set
    return getattr(model, "__fields_set__", set())


def normalize_schedule_text(field: str, value):
    if value is None:
        raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
    return value


def normalize_optional_positive_int(field: str, value):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be an integer")
    if value <= 0:
        raise HTTPException(status_code=400, detail=f"{field} must be greater than 0")
    return value


def normalize_optional_text(field: str, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    value = value.strip()
    return value or None


def normalize_required_text(field: str, value, *, max_length: int = 128, allow_whitespace: bool = True):
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{field} must be a string")
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
    if len(value) > max_length:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    if not allow_whitespace and re.search(r"\s", value):
        raise HTTPException(status_code=400, detail=f"{field} must not contain whitespace")
    return value


def normalize_bot_username(value: str) -> str:
    value = normalize_required_text("bot_username", value, max_length=128, allow_whitespace=False)
    value = re.sub(r"^https?://t\.me/", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0].strip().strip("/").lstrip("@")
    if not BOT_USERNAME_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="bot_username is invalid")
    return value


def templ_a_site(bot_username: str) -> str:
    return f"templ_a<{bot_username}>"


def templ_a_bot(site_name: str):
    if not isinstance(site_name, str):
        return None
    match = TEMPL_A_PATTERN.fullmatch(site_name)
    return match.group(1) if match else None


def normalize_site_list(field: str, values: Optional[List[str]], *, allow_all: bool = False) -> List[str]:
    if not values:
        return []
    normalized = []
    seen = set()
    for index, value in enumerate(values):
        site = normalize_required_text(f"{field}[{index}]", value, max_length=80, allow_whitespace=False)
        if site == "all" and not allow_all:
            raise HTTPException(status_code=400, detail=f"{field}[{index}] is invalid")
        if site not in seen:
            seen.add(site)
            normalized.append(site)
    return normalized


def normalize_time_list(field: str, values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    normalized = []
    for index, value in enumerate(values):
        time_value = normalize_required_text(f"{field}[{index}]", value, max_length=80)
        try:
            validate_schedule_fields("1", time_value, use_defaults=False)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"{field}[{index}] is invalid: {e}")
        normalized.append(time_value)
    return normalized


def telegram_uri(bot_token: str, chat_id: str) -> str:
    token = quote(bot_token, safe=":")
    target = quote(chat_id, safe="@:-")
    return f"tgram://{token}/{target}"


def telegram_chat_id_from_uri(uri: str):
    if not isinstance(uri, str) or not uri.startswith("tgram://"):
        return None
    parsed = urlparse(uri)
    path_parts = [unquote(part) for part in parsed.path.split("/") if part]
    return path_parts[1] if len(path_parts) >= 2 else (path_parts[0] if path_parts else None)


def is_telegram_uri(uri: str) -> bool:
    return bool(telegram_chat_id_from_uri(uri))


def set_toml_value(table, key: str, value):
    if value is None:
        table.pop(key, None)
    else:
        table[key] = value


def write_text_atomic(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        try:
            tmp_path.chmod(0o600)
        except OSError:
            pass
        tmp_path.replace(path)
    except Exception:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def is_secret_key(key) -> bool:
    normalized = str(key).lower()
    return normalized in SECRET_CONFIG_KEYS or any(part in normalized for part in SECRET_CONFIG_KEY_PARTS)


def is_sensitive_url(value: str) -> bool:
    parsed = urlparse(value)
    if not parsed.scheme:
        return False
    if parsed.username or parsed.password:
        return True
    return any(is_secret_key(key) for key, _value in parse_qsl(parsed.query, keep_blank_values=True))


def redact_scalar_value(value):
    if isinstance(value, str) and is_sensitive_url(value):
        return REDACTED_VALUE
    return value


def redact_toml_value(value):
    if isinstance(value, MutableMapping):
        for key in list(value.keys()):
            if is_secret_key(key):
                value[key] = REDACTED_VALUE
            else:
                child = value[key]
                if isinstance(child, (MutableMapping, list)):
                    redact_toml_value(child)
                else:
                    value[key] = redact_scalar_value(child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (MutableMapping, list)):
                redact_toml_value(item)
            else:
                value[index] = redact_scalar_value(item)


def redact_config_toml(content: str) -> str:
    doc = parse(content)
    redact_toml_value(doc)
    return dumps(doc)


def redact_plain_mapping(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            redacted[key] = (
                REDACTED_VALUE if is_secret_key(key) else redact_plain_mapping(redact_scalar_value(item))
            )
        return redacted
    if isinstance(value, list):
        return [redact_plain_mapping(item) for item in value]
    return redact_scalar_value(value)


def registrar_schedule_from_config(site_name: str, site_config: dict):
    bot_username = templ_a_bot(site_name)
    if not bot_username:
        return None
    times = site_config.get("times")
    if isinstance(times, str):
        times = [times]
    elif not isinstance(times, list):
        times = []
    interval_minutes = site_config.get("interval_minutes")
    mode = "interval" if interval_minutes else "times"
    return AutomationRegistrarSchedule(
        bot_username=bot_username,
        mode=mode,
        times=times or None,
        interval_minutes=interval_minutes,
        timeout=site_config.get("timeout"),
        retries=site_config.get("retries"),
    )


def normalize_registrar_schedules(schedules: Optional[List[AutomationRegistrarSchedule]]):
    normalized = []
    seen = set()
    for index, item in enumerate(schedules or []):
        bot_username = normalize_bot_username(item.bot_username)
        bot_key = bot_username.casefold()
        if bot_key in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate registrar bot: {bot_username}")
        seen.add(bot_key)
        site_name = templ_a_site(bot_username)
        if item.mode == "interval":
            if item.interval_minutes is None:
                raise HTTPException(
                    status_code=400, detail=f"registrar_schedules[{index}].interval_minutes is required"
                )
            site_config = {"interval_minutes": item.interval_minutes}
        elif item.mode == "times":
            times = normalize_time_list(f"registrar_schedules[{index}].times", item.times)
            if not times:
                raise HTTPException(status_code=400, detail=f"registrar_schedules[{index}].times is required")
            site_config = {"times": times}
        else:
            raise HTTPException(status_code=400, detail=f"registrar_schedules[{index}].mode is invalid")
        if item.timeout is not None:
            site_config["timeout"] = item.timeout
        if item.retries is not None:
            site_config["retries"] = item.retries
        normalized.append((site_name, site_config))
    return normalized


class ConfigService:
    def __init__(self, config_manager=None, scheduler_bridge=None, automation_runtime=None):
        self.config = config_manager if config_manager is not None else default_config
        self.bridge = scheduler_bridge if scheduler_bridge is not None else default_bridge
        self.automation_runtime = (
            automation_runtime if automation_runtime is not None else default_automation_runtime
        )

    def config_file_path(self) -> Path:
        return (
            Path(self.config._conf_file)
            if self.config._conf_file
            else Path(self.config.basedir) / "config.toml"
        )

    def web_accounts_file_path(self) -> Path:
        basedir = getattr(self.bridge.web_accounts, "basedir", None)
        if basedir:
            return Path(basedir) / "web_accounts.json"
        return Path(self.config.basedir) / "web_accounts.json"

    def backup_root(self) -> Path:
        return Path(self.config.basedir) / "backups"

    def prepare_backup_root(self) -> Path:
        backup_root = self.backup_root()
        self.ensure_backup_root_safe(backup_root)
        try:
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_root.chmod(0o700)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to prepare backup directory: {e}")
        return backup_root

    @staticmethod
    def ensure_backup_root_safe(backup_root: Path):
        if backup_root.is_symlink():
            raise HTTPException(status_code=500, detail="Backup directory must not be a symlink")

    @staticmethod
    def source_file_exists(path: Path) -> bool:
        if path.is_symlink():
            raise HTTPException(status_code=500, detail=f"Config source must not be a symlink: {path.name}")
        try:
            return path.is_file()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to inspect config source {path.name}: {e}")

    def backup_source_files(self) -> List[Path]:
        sources = []
        for source in (self.config_file_path(), self.web_accounts_file_path()):
            if self.source_file_exists(source):
                sources.append(source)
        return sources

    @staticmethod
    def backup_created_at(backup_id: str):
        timestamp = backup_id.split("-", 1)[0]
        try:
            return datetime.strptime(timestamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def backup_dir_from_id(self, backup_id: str) -> Path:
        if not isinstance(backup_id, str) or not BACKUP_ID_PATTERN.fullmatch(backup_id):
            raise HTTPException(status_code=404, detail="Backup not found")
        backup_root = self.backup_root()
        self.ensure_backup_root_safe(backup_root)
        backup_dir = backup_root / backup_id
        try:
            backup_dir.relative_to(backup_root)
        except ValueError:
            raise HTTPException(status_code=404, detail="Backup not found")
        if backup_dir.is_symlink() or not backup_dir.is_dir():
            raise HTTPException(status_code=404, detail="Backup not found")
        return backup_dir

    @staticmethod
    def backup_files(backup_dir: Path) -> List[str]:
        try:
            return sorted(
                path.name for path in backup_dir.iterdir() if path.is_file() and not path.is_symlink()
            )
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to read backup directory: {e}")

    def create_backup_snapshot(self, *, raise_if_empty: bool = True) -> Optional[ConfigBackupResponse]:
        backup_root = self.prepare_backup_root()
        sources = self.backup_source_files()
        if not sources:
            if raise_if_empty:
                raise HTTPException(status_code=404, detail="No config files found to back up")
            return None

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = backup_root / timestamp
        for counter in range(100):
            candidate = backup_dir if counter == 0 else backup_root / f"{timestamp}-{counter}"
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                try:
                    candidate.chmod(0o700)
                except OSError:
                    pass
                backup_dir = candidate
                break
            except FileExistsError:
                continue
            except OSError as e:
                raise HTTPException(status_code=500, detail=f"Failed to create backup directory: {e}")
        else:
            raise HTTPException(status_code=500, detail="Failed to create unique backup directory")

        copied = []
        try:
            for source in sources:
                target = backup_dir / source.name
                shutil.copy2(source, target)
                try:
                    target.chmod(0o600)
                except OSError:
                    pass
                copied.append(source.name)
        except OSError as e:
            shutil.rmtree(backup_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"Failed to back up {source.name}: {e}")

        return ConfigBackupResponse(status="created", backup_dir=str(backup_dir), files=copied)

    async def reload_restored_runtime(self, restored_files: List[str]):
        config_file = self.config_file_path()
        should_merge_accounts = False
        if config_file.name in restored_files and config_file.is_file():
            if not await self.config.reload_conf(config_file):
                raise HTTPException(status_code=500, detail="Restored config.toml is invalid")
            if self.bridge.web_accounts and self.config._cache and self.config._cache.emby:
                self.bridge._base_emby_accounts = list(self.config._cache.emby.account or [])
                should_merge_accounts = True
        if self.bridge.web_accounts and self.web_accounts_file_path().name in restored_files:
            from .scheduler_bridge import WebAccountData

            self.bridge.web_accounts = WebAccountData(self.web_accounts_file_path().parent)
            should_merge_accounts = True
        if self.bridge.web_accounts and should_merge_accounts:
            self.bridge._merge_accounts()

    def validate_restore_sources(self, restored):
        config_file = self.config_file_path()
        web_accounts_file = self.web_accounts_file_path()
        for source, target in restored:
            if target.name == config_file.name:
                try:
                    data = tomllib.loads(source.read_text(encoding="utf-8"))
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Backup config.toml is invalid: {e}")
                if not self.config.validate_config(data):
                    raise HTTPException(status_code=400, detail="Backup config.toml failed validation")
            elif target.name == web_accounts_file.name:
                try:
                    data = json.loads(source.read_text(encoding="utf-8"))
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Backup web_accounts.json is invalid: {e}")
                if not isinstance(data, dict):
                    raise HTTPException(status_code=400, detail="Backup web_accounts.json must be an object")
                from .scheduler_bridge import _sanitize_account_record

                if any(_sanitize_account_record(value) is None for value in data.values()):
                    raise HTTPException(
                        status_code=400, detail="Backup web_accounts.json has invalid accounts"
                    )

    @staticmethod
    def stage_restore_files(restored):
        staged = []
        current_tmp_path = None
        try:
            for source, target in restored:
                current_tmp_path = None
                target.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(
                    "wb",
                    dir=target.parent,
                    prefix=f".{target.name}.restore.",
                    suffix=".tmp",
                    delete=False,
                ) as tmp:
                    tmp_path = Path(tmp.name)
                current_tmp_path = tmp_path
                shutil.copy2(source, tmp_path)
                try:
                    tmp_path.chmod(0o600)
                except OSError:
                    pass
                staged.append((tmp_path, target))
                current_tmp_path = None
            return staged
        except Exception:
            if current_tmp_path is not None:
                try:
                    current_tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            for tmp_path, _target in staged:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    @staticmethod
    def replace_staged_restore_files(staged):
        replaced = []
        try:
            for tmp_path, target in staged:
                tmp_path.replace(target)
                replaced.append(target.name)
            return replaced
        except OSError as e:
            for tmp_path, _target in staged:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail=f"Failed to restore files: {e}")

    def persist_global_config(self, next_config=None):
        target_config = next_config if next_config is not None else self.config._cache
        if target_config is None:
            raise HTTPException(status_code=503, detail="Config not loaded")
        config_file = self.config_file_path()
        if config_file.is_file():
            try:
                doc = parse(config_file.read_text(encoding="utf-8"))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to parse config.toml: {e}")
        else:
            doc = document()

        if "emby" not in doc or not isinstance(doc["emby"], MutableMapping):
            doc["emby"] = {}
        emby = target_config.emby or EmbyConfig()
        set_toml_value(doc["emby"], "time_range", emby.time_range)
        set_toml_value(doc["emby"], "interval_days", emby.interval_days)
        set_toml_value(doc["emby"], "concurrency", emby.concurrency)

        if target_config.proxy:
            if "proxy" not in doc or not isinstance(doc["proxy"], MutableMapping):
                doc["proxy"] = {}
            proxy = target_config.proxy
            set_toml_value(doc["proxy"], "hostname", proxy.hostname)
            set_toml_value(doc["proxy"], "port", proxy.port)
            set_toml_value(doc["proxy"], "scheme", proxy.scheme)
        else:
            doc.pop("proxy", None)

        notifier = target_config.notifier
        if notifier:
            if "notifier" not in doc or not isinstance(doc["notifier"], MutableMapping):
                doc["notifier"] = {}
            set_toml_value(doc["notifier"], "enabled", notifier.enabled)
            set_toml_value(doc["notifier"], "method", notifier.method)
            set_toml_value(doc["notifier"], "apprise_uri", notifier.apprise_uri)
        else:
            doc.pop("notifier", None)

        try:
            write_text_atomic(config_file, dumps(doc))
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to write config.toml: {e}")

    def persist_automation_config(self, next_config=None, registrar_sites=None):
        target_config = next_config if next_config is not None else self.config._cache
        if target_config is None:
            raise HTTPException(status_code=503, detail="Config not loaded")
        config_file = self.config_file_path()
        if config_file.is_file():
            try:
                doc = parse(config_file.read_text(encoding="utf-8"))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to parse config.toml: {e}")
        else:
            doc = document()

        if "checkiner" not in doc or not isinstance(doc["checkiner"], MutableMapping):
            doc["checkiner"] = {}
        checkiner = target_config.checkiner or CheckinerConfig()
        set_toml_value(doc["checkiner"], "time_range", checkiner.time_range)
        set_toml_value(doc["checkiner"], "interval_days", checkiner.interval_days)
        set_toml_value(doc["checkiner"], "timeout", checkiner.timeout)
        set_toml_value(doc["checkiner"], "retries", checkiner.retries)
        set_toml_value(doc["checkiner"], "concurrency", checkiner.concurrency)
        set_toml_value(doc["checkiner"], "random_start", checkiner.random_start)

        if "site" not in doc or not isinstance(doc["site"], MutableMapping):
            doc["site"] = {}
        site = target_config.site or SiteConfig()
        set_toml_value(doc["site"], "checkiner", site.checkiner or None)
        set_toml_value(doc["site"], "registrar", site.registrar or None)

        if "registrar" not in doc or not isinstance(doc["registrar"], MutableMapping):
            doc["registrar"] = {}
        registrar = target_config.registrar or RegistrarConfig()
        set_toml_value(doc["registrar"], "concurrency", registrar.concurrency)
        generated_sites = list(dict.fromkeys(registrar_sites or []))
        generated_site_set = set(generated_sites)
        for site_name in list(doc["registrar"].keys()):
            if templ_a_bot(site_name) and site_name not in generated_site_set:
                doc["registrar"].pop(site_name, None)
        for site_name in generated_sites:
            set_toml_value(doc["registrar"], site_name, registrar.get_site_config(site_name) or None)

        try:
            write_text_atomic(config_file, dumps(doc))
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to write config.toml: {e}")

    def get_global_config(self) -> GlobalConfigResponse:
        if not self.config._cache:
            return GlobalConfigResponse()
        emby = self.config._cache.emby
        proxy = self.config._cache.proxy
        return GlobalConfigResponse(
            emby_time_range=emby.time_range if emby else None,
            emby_interval_days=emby.interval_days if emby else None,
            emby_concurrency=emby.concurrency if emby else None,
            proxy_hostname=proxy.hostname if proxy else None,
            proxy_port=proxy.port if proxy else None,
            proxy_scheme=proxy.scheme if proxy else None,
        )

    def export_config_bundle(self) -> ConfigExportResponse:
        config_file = self.config_file_path()
        accounts_file = self.web_accounts_file_path()
        config_toml = None
        if self.source_file_exists(config_file):
            try:
                config_toml = redact_config_toml(config_file.read_text(encoding="utf-8"))
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to redact config.toml: {e}")
        web_accounts = {}
        if self.bridge.web_accounts:
            try:
                web_accounts = redact_plain_mapping(self.bridge.web_accounts.get_all())
            except OSError as e:
                raise HTTPException(status_code=500, detail=f"Failed to read web accounts: {e}")
        return ConfigExportResponse(
            generated_at=datetime.now(timezone.utc),
            config_path=str(config_file),
            web_accounts_path=str(accounts_file),
            config_toml=config_toml,
            web_accounts=web_accounts,
            redacted=True,
        )

    def list_config_backups(self) -> List[ConfigBackupItem]:
        backup_root = self.backup_root()
        self.ensure_backup_root_safe(backup_root)
        if not backup_root.is_dir():
            return []
        try:
            backup_dirs = [
                path
                for path in backup_root.iterdir()
                if path.is_dir() and not path.is_symlink() and BACKUP_ID_PATTERN.fullmatch(path.name)
            ]
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to list backups: {e}")
        backup_dirs.sort(
            key=lambda path: self.backup_created_at(path.name) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return [
            ConfigBackupItem(
                id=backup_dir.name,
                backup_dir=str(backup_dir),
                created_at=self.backup_created_at(backup_dir.name),
                files=self.backup_files(backup_dir),
            )
            for backup_dir in backup_dirs
        ]

    async def restore_config_backup(self, backup_id: str, req: ConfigRestoreRequest) -> ConfigRestoreResponse:
        if not req.confirm:
            raise HTTPException(status_code=400, detail="confirm must be true")
        backup_dir = self.backup_dir_from_id(backup_id)
        targets = (self.config_file_path(), self.web_accounts_file_path())
        restored = []
        for target in targets:
            source = backup_dir / target.name
            if source.is_file() and not source.is_symlink():
                restored.append((source, target))
        if not restored:
            raise HTTPException(status_code=404, detail="No restorable files found in backup")

        self.validate_restore_sources(restored)
        safety_backup = self.create_backup_snapshot(raise_if_empty=False)
        try:
            staged = self.stage_restore_files(restored)
            restored_files = self.replace_staged_restore_files(staged)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to restore {target.name}: {e}")

        await self.reload_restored_runtime(restored_files)
        await self.refresh_automation_runtime()
        await self.refresh_notifier()
        return ConfigRestoreResponse(
            status="restored",
            backup_dir=str(backup_dir),
            restored_files=restored_files,
            safety_backup_dir=safety_backup.backup_dir if safety_backup else None,
        )

    def update_global_config(self, req: GlobalConfigUpdate):
        if not self.config._cache:
            raise HTTPException(status_code=503, detail="Config not loaded")

        new_config = self.config._cache.model_copy(deep=True)
        if new_config.emby is None:
            new_config.emby = EmbyConfig()

        fields_set = model_fields_set(req)

        if "emby_time_range" in fields_set:
            new_config.emby.time_range = normalize_schedule_text("emby_time_range", req.emby_time_range)
        if "emby_interval_days" in fields_set:
            new_config.emby.interval_days = normalize_schedule_text(
                "emby_interval_days", req.emby_interval_days
            )
        if "emby_concurrency" in fields_set:
            new_config.emby.concurrency = normalize_optional_positive_int(
                "emby_concurrency", req.emby_concurrency
            )

        if "proxy" in fields_set:
            if req.proxy is None:
                new_config.proxy = None
            else:
                existing_proxy = new_config.proxy or ProxyConfig()
                proxy_fields_set = model_fields_set(req.proxy)
                if "hostname" in proxy_fields_set:
                    if req.proxy.hostname is None:
                        hostname = None
                    elif not isinstance(req.proxy.hostname, str):
                        raise HTTPException(status_code=400, detail="proxy.hostname must be a string")
                    else:
                        hostname = req.proxy.hostname.strip()
                    existing_proxy.hostname = hostname or None
                if "port" in proxy_fields_set:
                    existing_proxy.port = req.proxy.port
                if "scheme" in proxy_fields_set:
                    existing_proxy.scheme = req.proxy.scheme
                if (
                    existing_proxy.hostname is not None
                    or existing_proxy.port is not None
                    or existing_proxy.scheme is not None
                ):
                    new_config.proxy = existing_proxy
                else:
                    new_config.proxy = None

        if new_config.emby.concurrency is not None and new_config.emby.concurrency <= 0:
            raise HTTPException(status_code=400, detail="emby_concurrency must be greater than 0")
        if "emby_time_range" in fields_set or "emby_interval_days" in fields_set:
            validate_schedule_fields(
                new_config.emby.interval_days,
                new_config.emby.time_range,
                use_defaults=False,
            )
        if new_config.proxy is not None:
            try:
                new_config.proxy = ProxyConfig.model_validate(new_config.proxy.model_dump(exclude_none=True))
            except ValidationError as e:
                raise HTTPException(status_code=400, detail=e.errors()[0]["msg"])

        self.persist_global_config(new_config)
        if not self.config.set(new_config, preserve_conf_file=True):
            raise HTTPException(status_code=400, detail="Invalid config")
        return {"status": "updated"}

    def get_automation_config(self) -> AutomationConfigResponse:
        if not self.config._cache:
            return AutomationConfigResponse()
        current = self.config._cache
        checkiner = current.checkiner if current and current.checkiner else CheckinerConfig()
        registrar = current.registrar if current and current.registrar else RegistrarConfig()
        site = current.site if current and current.site else SiteConfig()
        schedules = []
        preserved_sites = []
        for site_name in site.registrar or []:
            schedule = registrar_schedule_from_config(site_name, registrar.get_site_config(site_name) or {})
            if schedule:
                schedules.append(schedule)
            else:
                preserved_sites.append(site_name)
        return AutomationConfigResponse(
            checkiner_sites=list(site.checkiner or []),
            checkiner_time_range=checkiner.time_range,
            checkiner_interval_days=checkiner.interval_days,
            checkiner_timeout=checkiner.timeout,
            checkiner_retries=checkiner.retries,
            checkiner_concurrency=checkiner.concurrency,
            checkiner_random_start=checkiner.random_start,
            registrar_concurrency=registrar.concurrency,
            registrar_schedules=schedules,
            preserved_registrar_sites=preserved_sites,
        )

    async def refresh_automation_runtime(self):
        try:
            await self.automation_runtime.restart_if_started()
        except Exception as e:
            logger.warning(f"Failed to refresh Telegram automation runtime: {type(e).__name__}")

    async def update_automation_config(self, req: AutomationConfigUpdate) -> AutomationConfigResponse:
        if not self.config._cache:
            raise HTTPException(status_code=503, detail="Config not loaded")

        fields_set = model_fields_set(req)
        new_config = self.config._cache.model_copy(deep=True)
        new_config.checkiner = new_config.checkiner or CheckinerConfig()
        new_config.registrar = new_config.registrar or RegistrarConfig()
        new_config.site = new_config.site or SiteConfig()

        if "checkiner_sites" in fields_set:
            new_config.site.checkiner = normalize_site_list(
                "checkiner_sites", req.checkiner_sites, allow_all=True
            )
        if "checkiner_time_range" in fields_set:
            new_config.checkiner.time_range = normalize_schedule_text(
                "checkiner_time_range", req.checkiner_time_range
            )
        if "checkiner_interval_days" in fields_set:
            new_config.checkiner.interval_days = normalize_schedule_text(
                "checkiner_interval_days", req.checkiner_interval_days
            )
        if "checkiner_timeout" in fields_set:
            new_config.checkiner.timeout = req.checkiner_timeout
        if "checkiner_retries" in fields_set:
            new_config.checkiner.retries = req.checkiner_retries
        if "checkiner_concurrency" in fields_set:
            new_config.checkiner.concurrency = req.checkiner_concurrency
        if "checkiner_random_start" in fields_set:
            new_config.checkiner.random_start = req.checkiner_random_start
        if "registrar_concurrency" in fields_set:
            new_config.registrar.concurrency = req.registrar_concurrency

        validate_schedule_fields(
            new_config.checkiner.interval_days,
            new_config.checkiner.time_range,
            use_defaults=False,
        )

        existing_registrar_data = (
            new_config.registrar.model_dump(exclude_none=True)
            if hasattr(new_config.registrar, "model_dump")
            else new_config.registrar.dict(exclude_none=True)
        )
        generated_registrar_sites = [
            site_name for site_name in (new_config.site.registrar or []) if templ_a_bot(site_name)
        ]
        if "registrar_schedules" in fields_set:
            registrar_schedules = normalize_registrar_schedules(req.registrar_schedules)
            generated_registrar_sites = [site_name for site_name, _site_config in registrar_schedules]
            for key in list(existing_registrar_data.keys()):
                if templ_a_bot(key):
                    existing_registrar_data.pop(key, None)
            for site_name, site_config in registrar_schedules:
                existing_registrar_data[site_name] = site_config
            preserved_sites = [
                site_name for site_name in (new_config.site.registrar or []) if not templ_a_bot(site_name)
            ]
            new_config.site.registrar = preserved_sites + generated_registrar_sites

        existing_registrar_data["concurrency"] = new_config.registrar.concurrency
        try:
            new_config.checkiner = CheckinerConfig.model_validate(
                new_config.checkiner.model_dump(exclude_none=True)
            )
            new_config.registrar = RegistrarConfig.model_validate(existing_registrar_data)
            new_config.site = SiteConfig.model_validate(new_config.site.model_dump(exclude_none=True))
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=e.errors()[0]["msg"])

        self.persist_automation_config(new_config, registrar_sites=generated_registrar_sites)
        if not self.config.set(new_config, preserve_conf_file=True):
            raise HTTPException(status_code=400, detail="Invalid config")
        await self.refresh_automation_runtime()
        return self.get_automation_config()

    def get_notifier_config(self) -> NotifierConfigResponse:
        if not self.config._cache:
            return NotifierConfigResponse()
        notifier = (
            self.config._cache.notifier
            if self.config._cache and self.config._cache.notifier
            else NotifierConfig()
        )
        uri = notifier.apprise_uri
        telegram_chat_id = telegram_chat_id_from_uri(uri)
        if telegram_chat_id:
            target_label = f"Telegram: {telegram_chat_id}"
        elif uri:
            target_label = "Apprise URI configured"
        else:
            target_label = None
        return NotifierConfigResponse(
            enabled=bool(notifier.enabled),
            method="telegram" if telegram_chat_id else (notifier.method or "apprise"),
            configured=bool(uri),
            target_label=target_label,
            telegram_chat_id=telegram_chat_id,
        )

    async def refresh_notifier(self):
        try:
            from embykeeper.notify import start_notifier

            await start_notifier()
        except Exception as e:
            logger.warning(f"Failed to refresh notifier: {type(e).__name__}")

    async def update_notifier_config(self, req: NotifierConfigUpdate) -> NotifierConfigResponse:
        if not self.config._cache:
            raise HTTPException(status_code=503, detail="Config not loaded")

        fields_set = model_fields_set(req)
        new_config = self.config._cache.model_copy(deep=True)
        existing = new_config.notifier or NotifierConfig()
        enabled = existing.enabled if "enabled" not in fields_set else req.enabled
        if enabled is not None and not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled must be a boolean")

        method = normalize_optional_text("method", req.method) if "method" in fields_set else existing.method
        method = (method or "apprise").lower()
        if method not in {"apprise", "telegram"}:
            raise HTTPException(status_code=400, detail="method must be 'apprise' or 'telegram'")

        uri = existing.apprise_uri
        existing_is_telegram = is_telegram_uri(uri)
        if req.clear:
            uri = None
            existing_is_telegram = False

        bot_token = normalize_optional_text("telegram_bot_token", req.telegram_bot_token)
        chat_id = normalize_optional_text("telegram_chat_id", req.telegram_chat_id)
        apprise_uri = normalize_optional_text("apprise_uri", req.apprise_uri)

        if bot_token or chat_id:
            if not bot_token or not chat_id:
                raise HTTPException(
                    status_code=400, detail="telegram_bot_token and telegram_chat_id are required"
                )
            uri = telegram_uri(bot_token, chat_id)
            method = "apprise"
        elif apprise_uri is not None:
            uri = apprise_uri
            method = "apprise"
        elif "method" in fields_set and enabled:
            if method == "telegram" and not existing_is_telegram:
                raise HTTPException(status_code=400, detail="Telegram target is required")
            if method == "apprise" and existing_is_telegram:
                raise HTTPException(status_code=400, detail="Apprise URI is required")

        if enabled and not uri:
            raise HTTPException(status_code=400, detail="Notification target is required when enabled")

        new_config.notifier = NotifierConfig(enabled=bool(enabled), method="apprise", apprise_uri=uri)
        self.persist_global_config(new_config)
        if not self.config.set(new_config, preserve_conf_file=True):
            raise HTTPException(status_code=400, detail="Invalid config")
        await self.refresh_notifier()
        return self.get_notifier_config()

    async def test_notifier(self, req: NotifierConfigUpdate):
        uri = None
        bot_token = normalize_optional_text("telegram_bot_token", req.telegram_bot_token)
        chat_id = normalize_optional_text("telegram_chat_id", req.telegram_chat_id)
        apprise_uri = normalize_optional_text("apprise_uri", req.apprise_uri)
        if bot_token or chat_id:
            if not bot_token or not chat_id:
                raise HTTPException(
                    status_code=400, detail="telegram_bot_token and telegram_chat_id are required"
                )
            uri = telegram_uri(bot_token, chat_id)
        elif apprise_uri:
            uri = apprise_uri
        elif self.config._cache and self.config._cache.notifier:
            uri = self.config._cache.notifier.apprise_uri
        if not uri:
            raise HTTPException(status_code=400, detail="Notification target is required")

        stream = AppriseStream(uri)
        if not getattr(stream, "ready", True):
            raise HTTPException(status_code=400, detail="Notification target is invalid")
        stream.write("INFO#Emby Keeper notification test")
        await stream.join()
        return {"status": "sent"}
