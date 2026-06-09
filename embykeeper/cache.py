import json
import re
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, List

from loguru import logger

from .utils import CachedFuncProxy
from .config import config


class Cache:
    def __init__(self):
        self._mongo_client = None
        if hasattr(config, "mongodb") and config.mongodb:
            try:
                from pymongo import MongoClient

                self._mongo_client = MongoClient(config.mongodb)
                self._db = self._mongo_client.embykeeper
                self._collection = self._db.cache
            except ImportError:
                logger.warning("没有安装 pymongo 包, 将使用 JSON 存储缓存.")
                self._setup_json_cache()
        else:
            self._setup_json_cache()

    def _setup_json_cache(self):
        self._cache_file = config.basedir / "cache.json"
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._data = {}
        if self._cache_file.is_symlink():
            logger.warning("缓存文件不能是符号链接, 将使用全新缓存.")
            return
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                if not isinstance(self._data, dict):
                    logger.warning("缓存文件格式无效, 将使用全新缓存.")
                    self._data = {}
            except json.JSONDecodeError:
                logger.warning("缓存文件损坏, 将使用全新缓存.")
            except OSError as e:
                logger.warning(f"缓存文件读取失败, 将使用全新缓存: {type(e).__name__}.")

    def _write_json_cache(self, data=None):
        tmp_path = None
        payload = self._data if data is None else data
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._cache_file.parent,
                prefix=f".{self._cache_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = Path(f.name)
                json.dump(payload, f, ensure_ascii=False, indent=2)
            try:
                tmp_path.chmod(0o600)
            except OSError:
                pass
            tmp_path.replace(self._cache_file)
        except Exception:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def get(self, key: str, default: Any = None) -> Any:
        if self._mongo_client:
            result = self._collection.find_one({"_id": key})
            if not result or "value" not in result:
                return default
            return result["value"]
        else:
            missing = object()
            value = self._data
            try:
                for part in key.split("."):
                    value = value.get(part, missing)
                    if value is missing:
                        return default
                return deepcopy(value)
            except (AttributeError, TypeError):
                return default

    def set(self, key: str, value: Any) -> None:
        if self._mongo_client:
            self._collection.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)
        else:
            parts = key.split(".")
            next_data = deepcopy(self._data)
            current = next_data
            for part in parts[:-1]:
                next_value = current.get(part)
                if not isinstance(next_value, dict):
                    next_value = {}
                    current[part] = next_value
                current = next_value
            current[parts[-1]] = deepcopy(value)
            self._write_json_cache(next_data)
            self._data = next_data

    def delete(self, key: str) -> None:
        if self._mongo_client:
            self._collection.delete_one({"_id": key})
        else:
            parts = key.split(".")
            next_data = deepcopy(self._data)
            current = next_data
            path = []
            changed = False

            # 遍历路径, 检查每一层
            for part in parts[:-1]:
                if not isinstance(current, dict) or part not in current:
                    return
                path.append((current, part))
                current = current[part]

            # 检查并删除最后一个键
            if isinstance(current, dict) and parts[-1] in current:
                del current[parts[-1]]
                changed = True

                # 清理空字典
                for parent, part in reversed(path):
                    if isinstance(parent, dict) and part in parent and not parent[part]:
                        del parent[part]
                    else:
                        break

            if changed:
                self._write_json_cache(next_data)
                self._data = next_data

    def find_by_prefix(self, prefix: str) -> List[str]:
        if self._mongo_client:
            return [
                doc["_id"]
                for doc in self._collection.find({"_id": {"$regex": f"^{re.escape(prefix)}"}}, {"_id": 1})
            ]
        else:

            def get_keys_with_prefix(d, current_path="", keys=None):
                if keys is None:
                    keys = []
                for k, v in d.items():
                    path = f"{current_path}.{k}" if current_path else k
                    if isinstance(v, dict) and v:
                        get_keys_with_prefix(v, path, keys)
                    else:
                        if path.startswith(prefix):
                            keys.append(path)
                return keys

            return get_keys_with_prefix(self._data)

    def delete_by_prefix(self, prefix: str) -> None:
        keys = self.find_by_prefix(prefix)
        self.delete_many(keys)

    def delete_many(self, keys: List[str]) -> None:
        """批量删除多个键的缓存

        Args:
            keys: 要删除的键列表
        """
        if self._mongo_client:
            self._collection.delete_many({"_id": {"$in": keys}})
        else:
            # 批量删除所有键, 只写入一次文件
            changed = False
            next_data = deepcopy(self._data)
            for key in keys:
                parts = key.split(".")
                current = next_data
                path = []
                parent_found = True

                # 遍历路径, 检查每一层
                for part in parts[:-1]:
                    if not isinstance(current, dict) or part not in current:
                        parent_found = False
                        break
                    path.append((current, part))
                    current = current[part]

                # 检查并删除最后一个键
                if parent_found and isinstance(current, dict) and parts[-1] in current:
                    del current[parts[-1]]
                    changed = True

                    # 清理空字典
                    for parent, part in reversed(path):
                        if isinstance(parent, dict) and part in parent and not parent[part]:
                            del parent[part]
                        else:
                            break

            # 只在有改动时写入一次文件
            if changed:
                self._write_json_cache(next_data)
                self._data = next_data


cache: Cache = CachedFuncProxy(lambda: Cache())
