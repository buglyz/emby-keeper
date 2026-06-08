from typing import List, Optional, Union, Dict, Any, ClassVar, Set
from pydantic import BaseModel, Field, StrictInt, model_validator, ValidationError
from pydantic.networks import HttpUrl
from pydantic_core import core_schema

DEFAULT_TIME_RANGE = "<11:00AM,11:00PM>"
DEFAULT_EMBY_INTERVAL_DAYS = "<7,12>"


class ConfigModel(BaseModel):
    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def validate_extra_fields(cls, values):
        if not isinstance(values, dict):
            return values
        if cls.model_config.get("extra") == "allow":
            return values
        allowed_fields = set(cls.model_fields.keys())
        extra_fields = set(values.keys()) - allowed_fields
        if extra_fields:
            raise ValueError(
                f"包含未知设置项：{', '.join(sorted(extra_fields))}, 允许的设置项: {', '.join(sorted(allowed_fields))}"
            )
        return values


class UseStr(str):
    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        return core_schema.no_info_before_validator_function(cls.validate, core_schema.str_schema())

    @classmethod
    def validate(cls, v):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return str(v)
        return v


class UseHttpUrl(HttpUrl):
    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        return core_schema.no_info_before_validator_function(cls.validate, handler.generate_schema(HttpUrl))

    @classmethod
    def validate(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v and not v.startswith(("http://", "https://")):
                v = f"https://{v}"
        return v

    def __str__(self):
        return str(self._url)


class ProxyConfig(ConfigModel):
    hostname: Optional[str] = None
    port: Optional[StrictInt] = Field(None, gt=0)
    scheme: Optional[str] = Field(None, pattern="^(socks5|http)$")
    username: Optional[str] = None
    password: Optional[str] = None


class NotifierConfig(ConfigModel):
    enabled: Optional[bool] = False
    account: Optional[Union[StrictInt, str]] = 1
    immediately: Optional[bool] = False
    once: Optional[bool] = False
    method: Optional[str] = "apprise"
    apprise_uri: Optional[str] = None


class MediaServerBaseConfig(ConfigModel):
    time_range: Optional[UseStr] = DEFAULT_TIME_RANGE
    interval_days: Optional[UseStr] = DEFAULT_EMBY_INTERVAL_DAYS
    concurrency: Optional[StrictInt] = Field(1, gt=0)
    retries: Optional[StrictInt] = Field(5, ge=0)


class EmbyAccount(ConfigModel):
    url: UseHttpUrl
    username: str
    password: Optional[str] = None
    name: Optional[str] = None
    time: Optional[Union[StrictInt, List[StrictInt]]] = Field(default_factory=lambda: [300, 600])
    useragent: Optional[str] = None
    client: Optional[str] = None
    client_version: Optional[str] = None
    device: Optional[str] = None
    device_id: Optional[str] = None
    allow_multiple: Optional[bool] = True
    allow_stream: Optional[bool] = False
    cf_challenge: Optional[bool] = True
    use_proxy: Optional[bool] = True
    play_id: Optional[str] = None
    enabled: Optional[bool] = True

    # 站点单独配置
    interval_days: Optional[UseStr] = None
    time_range: Optional[str] = None

    # 向后兼容字段
    interval: Optional[Union[int, str]] = None
    watchtime: Optional[str] = None
    hide: Optional[bool] = None
    ua: Optional[str] = None
    jellyfin: Optional[bool] = None
    continuous: Optional[bool] = False

    @model_validator(mode="after")
    def validate_time(self):
        if self.time is None:
            return self
        if isinstance(self.time, list):
            if len(self.time) != 2:
                raise ValueError("time must be an integer or a [min, max] pair")
            if self.time[0] <= 0 or self.time[1] <= 0:
                raise ValueError("time values must be positive")
            if self.time[0] > self.time[1]:
                raise ValueError("time[0] (min) must be <= time[1] (max)")
        elif self.time <= 0:
            raise ValueError("time must be positive")
        return self


class EmbyConfig(MediaServerBaseConfig):
    account: Optional[List[EmbyAccount]] = Field(default_factory=list)


class Config(ConfigModel):
    alias_map: ClassVar[Dict[str, str]] = {
        "emby.time_range": "watchtime",
        "emby.concurrency": "watch_concurrent",
        "emby.interval_days": "interval",
    }
    ignored_legacy_fields: ClassVar[Set[str]] = {
        "telegram",
        "checkiner",
        "monitor",
        "messager",
        "registrar",
        "subsonic",
        "site",
        "service",
        "listentime",
        "listen_concurrent",
        "notify_immediately",
        "bot",
        "time",
        "timeout",
        "retries",
        "concurrent",
        "random",
    }

    mongodb: Optional[str] = None
    basedir: Optional[str] = None
    nofail: Optional[bool] = True
    noexit: Optional[bool] = False
    debug_cron: Optional[bool] = False
    proxy: Optional[ProxyConfig] = None
    emby: Optional[EmbyConfig] = Field(default_factory=EmbyConfig)
    notifier: Optional[NotifierConfig] = Field(default_factory=NotifierConfig)

    @model_validator(mode="before")
    @classmethod
    def handle_aliases(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(values, dict):
            return values

        values = values.copy()
        for field in cls.ignored_legacy_fields:
            values.pop(field, None)

        if "emby" in values and isinstance(values["emby"], list):
            accounts = []
            for account in values["emby"]:
                if not isinstance(account, dict):
                    accounts.append(account)
                    continue
                account = account.copy()
                if "ua" in account:
                    account["useragent"] = account.pop("ua")
                accounts.append(account)
            values["emby"] = {"account": accounts}

        if "notifier" in values:
            notifier_value = values["notifier"]
            if isinstance(notifier_value, str):
                values["notifier"] = {
                    "enabled": True,
                    "account": notifier_value,
                }
            elif isinstance(notifier_value, bool):
                values["notifier"] = {
                    "enabled": notifier_value,
                }
            elif isinstance(notifier_value, int):
                values["notifier"] = {
                    "enabled": notifier_value > 0,
                    "account": notifier_value,
                }

        for new_field, old_field in cls.alias_map.items():
            if old_field in values and values[old_field] is not None:
                parts = new_field.split(".")
                existing = values
                for part in parts:
                    if not isinstance(existing, dict) or part not in existing:
                        existing = None
                        break
                    existing = existing[part]
                if existing is not None:
                    values.pop(old_field, None)
                    continue
                target = values
                for part in parts[:-1]:
                    next_target = target.get(part)
                    if isinstance(next_target, dict):
                        next_target = next_target.copy()
                    else:
                        next_target = {}
                    target[part] = next_target
                    target = next_target
                target[parts[-1]] = values[old_field]
                values.pop(old_field, None)

        return values


def format_errors(e: ValidationError) -> str:
    """自定义错误信息格式化"""

    error_translations = {
        "Input should be a valid boolean": "输入应为布尔值 (true/false)",
        "Input should be a valid integer": "输入应为有效的整数",
        "Input should be a valid string": "输入应为有效的字符串, 用英文双引号包裹",
        "Input should be a valid list": "输入应为有效的列表, 用[]符号包裹",
        "Input should be a valid URL": "输入应为有效的URL地址",
        "Field required": "必填字段",
        "Value error": "配置验证错误",
        "Input should match pattern": "输入格式不匹配要求",
        "Value is not a valid dict": "输入应为有效的字典格式",
    }

    reverse_aliases = {}
    for new_field, old_field in Config.alias_map.items():
        if old_field not in reverse_aliases:
            reverse_aliases[old_field] = []
        reverse_aliases[old_field].append(new_field)

    error_groups = {}
    error_messages = ["配置文件错误, 请检查配置文件:"]

    for error in e.errors():
        location = list(error["loc"])
        msg = error["msg"]

        # 翻译错误消息
        for eng, chn in error_translations.items():
            if callable(chn):
                msg = msg.replace(eng, chn(error["loc"]))
            else:
                msg = msg.replace(eng, chn)

        # 如果是根级别的错误, 直接添加错误信息
        if not location:
            error_messages.append(f"  {msg}")
            continue

        loc_str = " -> ".join(str(loc) for loc in location)

        error_key = (() if len(location) <= 1 else tuple(location[1:])) + (msg,)

        # 检查是否有相关的别名字段
        if location[0] in reverse_aliases:
            for new_field in reverse_aliases[location[0]]:
                new_loc = new_field.split(".")
                if len(location) > 1:
                    new_loc.extend(location[1:])
                new_loc_str = " -> ".join(new_loc)
                group_key = f"  {new_loc_str}\n  (旧版本为: {loc_str})"
                error_groups[error_key] = (group_key, msg)
        else:
            error_groups[error_key] = (f"  {loc_str}", msg)

    # 添加分组后的错误消息
    for _, (location, msg) in error_groups.items():
        error_messages.append(f"{location}:")
        error_messages.append(f"    {msg}")

    error_messages.append("详细说明请访问: https://emby-keeper.github.io/guide/配置文件")
    return "\n".join(error_messages)


if __name__ == "__main__":
    import sys
    import tomli

    if len(sys.argv) < 2:
        print("Usage: python schema.py <config.toml>")
        sys.exit(1)

    try:
        with open(sys.argv[1], "rb") as f:
            config_dict = tomli.load(f)
        config = Config(**config_dict)
        print(config.model_dump_json(indent=2))
    except FileNotFoundError:
        print(f"错误: 配置文件 '{sys.argv[1]}' 未找到")
        sys.exit(1)
    except tomli.TOMLDecodeError as e:
        print(f"错误: TOML格式无效 - {e}")
        sys.exit(1)
    except ValidationError as e:
        print(format_errors(e))
        sys.exit(1)
