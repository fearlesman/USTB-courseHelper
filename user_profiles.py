"""持久化不含凭据的多用户档案与独立课程草稿。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4


PROFILE_VERSION = 1
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class UserProfileStoreError(Exception):
    """表示用户档案或草稿无法校验、读取或写入。"""


@dataclass(frozen=True)
class UserProfile:
    """描述一名不包含登录凭据的本地用户。

    Attributes:
        id: 稳定的用户 UUID。
        alias: 用户可见别名。
        active: 是否出现在默认用户选择器中。
        created_at: 创建时间的 ISO-8601 字符串。
        updated_at: 最后更新时间的 ISO-8601 字符串。
    """

    id: str
    alias: str
    active: bool
    created_at: str
    updated_at: str


class UserProfileStore:
    """管理版本化用户档案、用户目录和当前草稿。"""

    def __init__(self, base_directory: str | os.PathLike[str]) -> None:
        """初始化用户档案存储。

        Args:
            base_directory: 项目数据文件所在目录。

        Returns:
            None: 档案文件和用户数据根目录会保存在实例中。
        """
        self.base_directory = Path(base_directory)
        self.profile_file = self.base_directory / "user_profiles.json"
        self.user_data_directory = self.base_directory / "user_data"

    def list_all(self, include_inactive: bool = False) -> list[UserProfile]:
        """读取全部或仅启用的用户档案。

        Args:
            include_inactive: True 表示包含已软删除用户。

        Returns:
            按创建顺序排列的用户档案列表。

        Raises:
            UserProfileStoreError: 档案文件损坏或字段非法时抛出。
        """
        profiles = self._read_document()
        if include_inactive:
            return profiles
        return [profile for profile in profiles if profile.active]

    def get(self, profile_id: str) -> UserProfile:
        """读取指定 UUID 的用户档案。

        Args:
            profile_id: 目标用户 UUID。

        Returns:
            匹配的用户档案。

        Raises:
            UserProfileStoreError: 用户不存在时抛出。
        """
        for profile in self._read_document():
            if profile.id == profile_id:
                return profile
        raise UserProfileStoreError("指定用户不存在，可能已被移除")

    def create(self, alias: str) -> UserProfile:
        """创建用户档案并导入同名旧版课程文件。

        Args:
            alias: 用户可见别名。

        Returns:
            新建的用户档案。

        Raises:
            UserProfileStoreError: 别名非法、重复或写入失败时抛出。
        """
        normalized_alias = self._validate_alias(alias)
        profiles = self._read_document()
        self._ensure_unique_alias(profiles, normalized_alias)
        timestamp = self._now_iso()
        profile = UserProfile(
            id=str(uuid4()),
            alias=normalized_alias,
            active=True,
            created_at=timestamp,
            updated_at=timestamp,
        )
        profiles.append(profile)
        self._write_document(profiles)
        try:
            self._import_legacy_files(profile)
        except OSError as error:
            raise UserProfileStoreError(f"用户已创建，但旧课程文件导入失败：{error}") from error
        return profile

    def rename(self, profile_id: str, alias: str) -> UserProfile:
        """修改用户别名并保持 UUID 与数据目录不变。

        Args:
            profile_id: 目标用户 UUID。
            alias: 新的用户别名。

        Returns:
            更新后的用户档案。

        Raises:
            UserProfileStoreError: 用户不存在、别名非法或重复时抛出。
        """
        normalized_alias = self._validate_alias(alias)
        profiles = self._read_document()
        self._ensure_unique_alias(profiles, normalized_alias, excluded_id=profile_id)
        return self._replace_profile(profiles, profile_id, alias=normalized_alias)

    def deactivate(self, profile_id: str) -> UserProfile:
        """软删除用户并保留其课程文件。

        Args:
            profile_id: 目标用户 UUID。

        Returns:
            已标记为停用的用户档案。

        Raises:
            UserProfileStoreError: 用户不存在或写入失败时抛出。
        """
        return self._replace_profile(self._read_document(), profile_id, active=False)

    def restore(self, profile_id: str) -> UserProfile:
        """恢复此前软删除的用户。

        Args:
            profile_id: 目标用户 UUID。

        Returns:
            已恢复启用的用户档案。

        Raises:
            UserProfileStoreError: 用户不存在或写入失败时抛出。
        """
        return self._replace_profile(self._read_document(), profile_id, active=True)

    def user_directory(self, profile_id: str) -> Path:
        """返回指定用户的稳定数据目录。

        Args:
            profile_id: 用户 UUID。

        Returns:
            `user_data/<UUID>` 路径。

        Raises:
            UserProfileStoreError: UUID 格式非法时抛出。
        """
        self._validate_profile_id(profile_id)
        return self.user_data_directory / profile_id

    def draft_path(self, profile_id: str) -> Path:
        """返回指定用户当前草稿文件路径。

        Args:
            profile_id: 用户 UUID。

        Returns:
            用户目录中的 `course_list.json` 路径。
        """
        return self.user_directory(profile_id) / "course_list.json"

    def named_lists_path(self, profile_id: str) -> Path:
        """返回指定用户命名列表文件路径。

        Args:
            profile_id: 用户 UUID。

        Returns:
            用户目录中的 `saved_rush_lists.json` 路径。
        """
        return self.user_directory(profile_id) / "saved_rush_lists.json"

    def load_draft(self, profile_id: str) -> list[dict[str, Any]]:
        """读取用户草稿并重建连续界面序号。

        Args:
            profile_id: 用户 UUID。

        Returns:
            带连续 `id` 的独立课程字典列表。

        Raises:
            UserProfileStoreError: 草稿损坏或字段非法时抛出。
        """
        path = self.draft_path(profile_id)
        if not path.exists():
            return []
        try:
            raw_courses = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise UserProfileStoreError(f"用户课程草稿已损坏：{error}") from error
        except OSError as error:
            raise UserProfileStoreError(f"无法读取用户课程草稿：{error}") from error
        if not isinstance(raw_courses, list) or not all(
            isinstance(course, dict) for course in raw_courses
        ):
            raise UserProfileStoreError("用户课程草稿字段非法：根节点必须是课程数组")
        courses = copy.deepcopy(raw_courses)
        for index, course in enumerate(courses, start=1):
            course["id"] = index
        return courses

    def save_draft(
        self, profile_id: str, courses: Sequence[Mapping[str, Any]]
    ) -> None:
        """原子保存指定用户的当前课程草稿。

        Args:
            profile_id: 用户 UUID。
            courses: 当前待抢课程序列。

        Returns:
            None: 草稿会写入用户独立目录。

        Raises:
            UserProfileStoreError: 课程格式非法或写入失败时抛出。
        """
        if not all(isinstance(course, Mapping) for course in courses):
            raise UserProfileStoreError("用户课程草稿包含非法课程记录")
        snapshot: list[dict[str, Any]] = []
        for course in courses:
            copied = copy.deepcopy(dict(course))
            copied.pop("id", None)
            snapshot.append(copied)
        self._atomic_write(self.draft_path(profile_id), snapshot, "用户课程草稿")

    def _replace_profile(
        self,
        profiles: list[UserProfile],
        profile_id: str,
        alias: str | None = None,
        active: bool | None = None,
    ) -> UserProfile:
        """替换指定档案的可变元数据。

        Args:
            profiles: 当前全部用户档案。
            profile_id: 目标用户 UUID。
            alias: 新别名；为 None 时保持不变。
            active: 新启用状态；为 None 时保持不变。

        Returns:
            更新并写入后的用户档案。

        Raises:
            UserProfileStoreError: 用户不存在或写入失败时抛出。
        """
        updated: UserProfile | None = None
        for index, profile in enumerate(profiles):
            if profile.id != profile_id:
                continue
            updated = UserProfile(
                id=profile.id,
                alias=profile.alias if alias is None else alias,
                active=profile.active if active is None else active,
                created_at=profile.created_at,
                updated_at=self._now_iso(),
            )
            profiles[index] = updated
            break
        if updated is None:
            raise UserProfileStoreError("指定用户不存在，无法更新")
        self._write_document(profiles)
        return updated

    def _read_document(self) -> list[UserProfile]:
        """读取并完整校验用户档案文档。

        Args:
            None.

        Returns:
            已校验的用户档案列表。

        Raises:
            UserProfileStoreError: 文件损坏、版本不支持或字段非法时抛出。
        """
        if not self.profile_file.exists():
            return []
        try:
            document = json.loads(self.profile_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise UserProfileStoreError(f"用户档案文件已损坏：{error}") from error
        except OSError as error:
            raise UserProfileStoreError(f"无法读取用户档案文件：{error}") from error
        if not isinstance(document, dict) or document.get("version") != PROFILE_VERSION:
            raise UserProfileStoreError("用户档案文件版本或根节点非法")
        raw_profiles = document.get("users")
        if not isinstance(raw_profiles, list):
            raise UserProfileStoreError("用户档案字段非法：users 必须是数组")
        profiles = [self._parse_profile(item) for item in raw_profiles]
        aliases: set[str] = set()
        ids: set[str] = set()
        for profile in profiles:
            folded = profile.alias.casefold()
            if folded in aliases or profile.id in ids:
                raise UserProfileStoreError("用户档案字段非法：存在重复用户")
            aliases.add(folded)
            ids.add(profile.id)
        return profiles

    def _write_document(self, profiles: Sequence[UserProfile]) -> None:
        """原子写入全部用户档案。

        Args:
            profiles: 需要持久化的用户档案。

        Returns:
            None: 档案文件通过 `os.replace()` 替换。

        Raises:
            UserProfileStoreError: 序列化或写入失败时抛出。
        """
        document = {
            "version": PROFILE_VERSION,
            "users": [
                {
                    "id": profile.id,
                    "alias": profile.alias,
                    "active": profile.active,
                    "created_at": profile.created_at,
                    "updated_at": profile.updated_at,
                }
                for profile in profiles
            ],
        }
        self._atomic_write(self.profile_file, document, "用户档案")

    def _atomic_write(self, path: Path, value: object, label: str) -> None:
        """将 JSON 值写入同目录临时文件后原子替换。

        Args:
            path: 最终文件路径。
            value: 可 JSON 序列化的数据。
            label: 中文错误诊断中的数据名称。

        Returns:
            None: 最终文件被原子替换。

        Raises:
            UserProfileStoreError: 创建目录、序列化或替换失败时抛出。
        """
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{path.stem}_", suffix=".tmp", dir=path.parent, delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
        except (OSError, TypeError, ValueError) as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise UserProfileStoreError(f"写入{label}失败，原文件保持不变：{error}") from error

    def _parse_profile(self, raw_profile: object) -> UserProfile:
        """解析一条用户档案记录。

        Args:
            raw_profile: JSON 中的原始用户记录。

        Returns:
            已校验的用户档案。

        Raises:
            UserProfileStoreError: 必需字段缺失或格式非法时抛出。
        """
        if not isinstance(raw_profile, dict):
            raise UserProfileStoreError("用户档案字段非法：用户记录必须是对象")
        required = {"id", "alias", "active", "created_at", "updated_at"}
        if not required.issubset(raw_profile):
            raise UserProfileStoreError("用户档案字段非法：用户记录缺少必需字段")
        profile_id = raw_profile["id"]
        alias = raw_profile["alias"]
        active = raw_profile["active"]
        created_at = raw_profile["created_at"]
        updated_at = raw_profile["updated_at"]
        if not all(isinstance(value, str) for value in (profile_id, alias, created_at, updated_at)):
            raise UserProfileStoreError("用户档案字段非法：文本字段类型错误")
        if not isinstance(active, bool):
            raise UserProfileStoreError("用户档案字段非法：active 必须是布尔值")
        try:
            self._validate_profile_id(profile_id)
            datetime.fromisoformat(created_at)
            datetime.fromisoformat(updated_at)
        except (ValueError, UserProfileStoreError) as error:
            raise UserProfileStoreError("用户档案字段非法：标识或时间格式错误") from error
        if alias != self._validate_alias(alias):
            raise UserProfileStoreError("用户档案字段非法：别名格式错误")
        return UserProfile(profile_id, alias, active, created_at, updated_at)

    def _validate_alias(self, alias: str) -> str:
        """校验并标准化用户别名。

        Args:
            alias: 用户输入的别名。

        Returns:
            去除首尾空白后的别名。

        Raises:
            UserProfileStoreError: 别名类型或长度非法时抛出。
        """
        if not isinstance(alias, str):
            raise UserProfileStoreError("用户别名必须是文本")
        normalized = alias.strip()
        if not 1 <= len(normalized) <= 40:
            raise UserProfileStoreError("用户别名去除首尾空白后必须为 1-40 个字符")
        return normalized

    def _ensure_unique_alias(
        self,
        profiles: Sequence[UserProfile],
        alias: str,
        excluded_id: str | None = None,
    ) -> None:
        """校验别名在启用和停用档案中均唯一。

        Args:
            profiles: 当前全部用户档案。
            alias: 准备保存的标准化别名。
            excluded_id: 重命名时排除的当前用户 UUID。

        Returns:
            None: 别名可用时不修改数据。

        Raises:
            UserProfileStoreError: 忽略大小写比较后别名重复时抛出。
        """
        folded = alias.casefold()
        if any(profile.id != excluded_id and profile.alias.casefold() == folded for profile in profiles):
            raise UserProfileStoreError(f"已存在名为“{alias}”的用户档案")

    def _validate_profile_id(self, profile_id: str) -> None:
        """校验用户 UUID 格式。

        Args:
            profile_id: 待校验的用户标识。

        Returns:
            None: UUID 合法时不修改数据。

        Raises:
            UserProfileStoreError: UUID 格式非法时抛出。
        """
        try:
            UUID(profile_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise UserProfileStoreError("用户 UUID 格式非法") from error

    def _import_legacy_files(self, profile: UserProfile) -> None:
        """复制同名旧版草稿和命名列表到用户 UUID 目录。

        Args:
            profile: 新创建的用户档案。

        Returns:
            None: 存在的旧文件会复制，源文件保持不变。

        Raises:
            OSError: 目录创建或文件复制失败时抛出。
        """
        safe_alias = profile.alias.replace("/", "_").replace("\\", "_")
        legacy_draft = self.base_directory / f"course_list_{safe_alias}.json"
        legacy_list_name = _INVALID_FILENAME_CHARS.sub("_", profile.alias).rstrip(". ")
        if not legacy_list_name:
            legacy_list_name = "student"
        if legacy_list_name != profile.alias:
            digest = hashlib.sha256(profile.alias.encode("utf-8")).hexdigest()[:10]
            legacy_list_name = f"{legacy_list_name}_{digest}"
        legacy_lists = self.base_directory / f"saved_rush_lists_{legacy_list_name}.json"
        targets = (
            (legacy_draft, self.draft_path(profile.id)),
            (legacy_lists, self.named_lists_path(profile.id)),
        )
        for source, target in targets:
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    def _now_iso(self) -> str:
        """返回当前 UTC 时间的 ISO-8601 文本。

        Args:
            None.

        Returns:
            带时区且精确到微秒的当前时间字符串。
        """
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")
