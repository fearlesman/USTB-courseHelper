"""按抢课人员隔离的命名课程列表持久化。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID, uuid4


STORE_VERSION = 1
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class RushListStoreError(Exception):
    """表示命名抢课列表无法校验、读取或写入。"""


@dataclass(frozen=True)
class SavedRushList:
    """描述一份带名称和备注的抢课课程快照。

    Attributes:
        id: 列表的 UUID 字符串。
        name: 去除首尾空白后的列表名称。
        note: 列表级备注。
        created_at: 创建时间的 ISO-8601 字符串。
        updated_at: 最后更新时间的 ISO-8601 字符串。
        courses: 已移除界面序号的课程快照。
    """

    id: str
    name: str
    note: str
    created_at: str
    updated_at: str
    courses: list[dict[str, Any]]


@dataclass(frozen=True)
class RushListCopyResult:
    """描述跨用户复制命名列表的部分成功结果。

    Attributes:
        copied_user_ids: 成功创建独立副本的用户标识。
        skipped_user_ids: 因同名或读取错误而跳过的用户标识。
        errors: 按用户标识记录的中文失败原因。
    """

    copied_user_ids: tuple[str, ...]
    skipped_user_ids: tuple[str, ...]
    errors: dict[str, str]


class RushListStore:
    """管理指定目录中的人员命名抢课列表文件。"""

    def __init__(
        self,
        base_directory: str | os.PathLike[str],
        path_resolver: Callable[[str], Path] | None = None,
    ) -> None:
        """初始化命名列表存储。

        Args:
            base_directory: 命名列表 JSON 文件所在目录。
            path_resolver: 可选的用户标识到文件路径解析函数。

        Returns:
            None: 存储目录会保存在实例中。
        """
        self.base_directory = Path(base_directory)
        self.path_resolver = path_resolver

    def path_for(self, student_name: str) -> Path:
        """返回指定人员的命名列表文件路径。

        Args:
            student_name: 当前抢课人员名称。

        Returns:
            该人员独立的 JSON 文件路径。

        Raises:
            RushListStoreError: 人员名称为空时抛出。
        """
        normalized_name = student_name.strip()
        if not normalized_name:
            raise RushListStoreError("抢课人员不能为空，无法访问命名列表")
        if self.path_resolver is not None:
            try:
                return Path(self.path_resolver(normalized_name))
            except Exception as error:
                raise RushListStoreError(f"无法解析用户命名列表路径：{error}") from error
        safe_name = _INVALID_FILENAME_CHARS.sub("_", normalized_name).rstrip(". ")
        if not safe_name:
            safe_name = "student"
        if safe_name != normalized_name:
            digest = hashlib.sha256(normalized_name.encode("utf-8")).hexdigest()[:10]
            safe_name = f"{safe_name}_{digest}"
        return self.base_directory / f"saved_rush_lists_{safe_name}.json"

    def list_all(self, student_name: str) -> list[SavedRushList]:
        """读取指定人员的全部命名列表。

        Args:
            student_name: 当前抢课人员名称。

        Returns:
            按文件顺序返回的命名列表副本。

        Raises:
            RushListStoreError: 文件损坏、版本不支持或字段非法时抛出。
        """
        return [self._clone_saved(item) for item in self._read_document(student_name)]

    def get(self, student_name: str, list_id: str) -> SavedRushList:
        """读取指定人员的一份命名列表。

        Args:
            student_name: 当前抢课人员名称。
            list_id: 目标列表 UUID。

        Returns:
            匹配的命名列表副本。

        Raises:
            RushListStoreError: 目标列表不存在时抛出。
        """
        for saved_list in self._read_document(student_name):
            if saved_list.id == list_id:
                return self._clone_saved(saved_list)
        raise RushListStoreError("指定的命名抢课列表不存在，可能已被删除")

    def create(
        self,
        student_name: str,
        name: str,
        note: str,
        courses: Sequence[Mapping[str, Any]],
    ) -> SavedRushList:
        """为指定人员创建一份新的命名课程快照。

        Args:
            student_name: 当前抢课人员名称。
            name: 新列表名称。
            note: 新列表备注。
            courses: 当前待抢课程序列。

        Returns:
            创建并持久化后的命名列表。

        Raises:
            RushListStoreError: 输入非法、名称重复或写入失败时抛出。
        """
        normalized_name, normalized_note = self._validate_input(name, note, courses)
        saved_lists = self._read_document(student_name)
        self._ensure_unique_name(saved_lists, normalized_name)
        timestamp = self._now_iso()
        saved = SavedRushList(
            id=str(uuid4()),
            name=normalized_name,
            note=normalized_note,
            created_at=timestamp,
            updated_at=timestamp,
            courses=self._snapshot_courses(courses),
        )
        saved_lists.append(saved)
        self._write_document(student_name, saved_lists)
        return self._clone_saved(saved)

    def update(
        self,
        student_name: str,
        list_id: str,
        name: str,
        note: str,
        courses: Sequence[Mapping[str, Any]],
    ) -> SavedRushList:
        """更新指定命名列表的元数据和课程快照。

        Args:
            student_name: 当前抢课人员名称。
            list_id: 需要更新的列表 UUID。
            name: 更新后的列表名称。
            note: 更新后的列表备注。
            courses: 当前待抢课程序列。

        Returns:
            更新并持久化后的命名列表。

        Raises:
            RushListStoreError: 输入非法、列表不存在、名称重复或写入失败时抛出。
        """
        normalized_name, normalized_note = self._validate_input(name, note, courses)
        saved_lists = self._read_document(student_name)
        self._ensure_unique_name(saved_lists, normalized_name, excluded_id=list_id)
        updated: SavedRushList | None = None
        for index, saved_list in enumerate(saved_lists):
            if saved_list.id != list_id:
                continue
            updated = SavedRushList(
                id=saved_list.id,
                name=normalized_name,
                note=normalized_note,
                created_at=saved_list.created_at,
                updated_at=self._now_iso(),
                courses=self._snapshot_courses(courses),
            )
            saved_lists[index] = updated
            break
        if updated is None:
            raise RushListStoreError("指定的命名抢课列表不存在，无法更新")
        self._write_document(student_name, saved_lists)
        return self._clone_saved(updated)

    def delete(self, student_name: str, list_id: str) -> None:
        """删除指定人员的一份命名列表。

        Args:
            student_name: 当前抢课人员名称。
            list_id: 需要删除的列表 UUID。

        Returns:
            None: 列表会从人员文件中移除。

        Raises:
            RushListStoreError: 列表不存在或写入失败时抛出。
        """
        saved_lists = self._read_document(student_name)
        remaining = [item for item in saved_lists if item.id != list_id]
        if len(remaining) == len(saved_lists):
            raise RushListStoreError("指定的命名抢课列表不存在，无法删除")
        self._write_document(student_name, remaining)

    def rename(self, student_name: str, list_id: str, name: str) -> SavedRushList:
        """仅修改命名列表名称并保留备注和课程快照。

        Args:
            student_name: 当前用户标识。
            list_id: 需要重命名的列表 UUID。
            name: 新列表名称。

        Returns:
            重命名并持久化后的列表。

        Raises:
            RushListStoreError: 名称非法、重复、列表不存在或写入失败时抛出。
        """
        normalized_name = name.strip() if isinstance(name, str) else ""
        if not 1 <= len(normalized_name) <= 40:
            raise RushListStoreError("列表名称去除首尾空白后必须为 1-40 个字符")
        saved_lists = self._read_document(student_name)
        self._ensure_unique_name(saved_lists, normalized_name, excluded_id=list_id)
        renamed: SavedRushList | None = None
        for index, saved_list in enumerate(saved_lists):
            if saved_list.id != list_id:
                continue
            renamed = SavedRushList(
                id=saved_list.id,
                name=normalized_name,
                note=saved_list.note,
                created_at=saved_list.created_at,
                updated_at=self._now_iso(),
                courses=copy.deepcopy(saved_list.courses),
            )
            saved_lists[index] = renamed
            break
        if renamed is None:
            raise RushListStoreError("指定的命名抢课列表不存在，无法重命名")
        self._write_document(student_name, saved_lists)
        return self._clone_saved(renamed)

    def copy_to_users(
        self,
        source_student_name: str,
        list_id: str,
        target_student_names: Sequence[str],
    ) -> RushListCopyResult:
        """将命名列表作为独立快照复制给多个目标用户。

        Args:
            source_student_name: 源用户标识。
            list_id: 源列表 UUID。
            target_student_names: 目标用户标识序列。

        Returns:
            成功、跳过用户和错误原因汇总。

        Raises:
            RushListStoreError: 源列表无法读取时抛出。
        """
        source = self.get(source_student_name, list_id)
        copied: list[str] = []
        skipped: list[str] = []
        errors: dict[str, str] = {}
        seen_targets: set[str] = set()
        for target in target_student_names:
            if target in seen_targets or target == source_student_name:
                continue
            seen_targets.add(target)
            try:
                self.create(target, source.name, source.note, source.courses)
                copied.append(target)
            except RushListStoreError as error:
                skipped.append(target)
                errors[target] = str(error)
        return RushListCopyResult(tuple(copied), tuple(skipped), errors)

    def courses_for_loading(self, saved_list: SavedRushList) -> list[dict[str, Any]]:
        """创建可替换当前草稿的课程副本并重建连续序号。

        Args:
            saved_list: 需要加载的命名课程快照。

        Returns:
            带有从 1 开始连续界面序号的课程列表。
        """
        courses = copy.deepcopy(saved_list.courses)
        for index, course in enumerate(courses, start=1):
            course["id"] = index
        return courses

    def _read_document(self, student_name: str) -> list[SavedRushList]:
        """读取并校验人员列表文档。

        Args:
            student_name: 当前抢课人员名称。

        Returns:
            已校验的命名列表对象。

        Raises:
            RushListStoreError: 文件无法读取、JSON 损坏或字段非法时抛出。
        """
        path = self.path_for(student_name)
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as file_handle:
                document = json.load(file_handle)
        except json.JSONDecodeError as error:
            raise RushListStoreError(f"命名列表文件已损坏，无法解析 JSON：{error}") from error
        except OSError as error:
            raise RushListStoreError(f"无法读取命名列表文件：{error}") from error
        if not isinstance(document, dict):
            raise RushListStoreError("命名列表文件字段非法：根节点必须是对象")
        if document.get("version") != STORE_VERSION:
            raise RushListStoreError("命名列表文件版本不受支持")
        raw_lists = document.get("lists")
        if not isinstance(raw_lists, list):
            raise RushListStoreError("命名列表文件字段非法：lists 必须是数组")
        parsed = [self._parse_saved_list(item) for item in raw_lists]
        folded_names: set[str] = set()
        for item in parsed:
            folded = item.name.casefold()
            if folded in folded_names:
                raise RushListStoreError("命名列表文件字段非法：存在重复名称")
            folded_names.add(folded)
        return parsed

    def _write_document(
        self, student_name: str, saved_lists: Sequence[SavedRushList]
    ) -> None:
        """通过同目录临时文件原子写入人员列表文档。

        Args:
            student_name: 当前抢课人员名称。
            saved_lists: 需要写入的全部命名列表。

        Returns:
            None: 最终文件通过 os.replace 原子替换。

        Raises:
            RushListStoreError: 目录创建、序列化或替换失败时抛出。
        """
        path = self.path_for(student_name)
        temporary_path: Path | None = None
        document = {
            "version": STORE_VERSION,
            "lists": [self._saved_to_dict(item) for item in saved_lists],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{path.stem}_",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(serialized)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
        except (OSError, TypeError, ValueError) as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise RushListStoreError(f"写入命名列表失败，原文件保持不变：{error}") from error

    def _parse_saved_list(self, raw_item: object) -> SavedRushList:
        """将文件中的单条记录转换为命名列表对象。

        Args:
            raw_item: 从 JSON 数组读取的原始记录。

        Returns:
            已完整校验的命名列表。

        Raises:
            RushListStoreError: 任一必需字段缺失或类型非法时抛出。
        """
        if not isinstance(raw_item, dict):
            raise RushListStoreError("命名列表文件字段非法：列表记录必须是对象")
        required_fields = {
            "id",
            "name",
            "note",
            "created_at",
            "updated_at",
            "courses",
        }
        if not required_fields.issubset(raw_item):
            raise RushListStoreError("命名列表文件字段非法：列表记录缺少必需字段")
        list_id = raw_item["id"]
        name = raw_item["name"]
        note = raw_item["note"]
        created_at = raw_item["created_at"]
        updated_at = raw_item["updated_at"]
        courses = raw_item["courses"]
        if not all(isinstance(value, str) for value in (list_id, name, note, created_at, updated_at)):
            raise RushListStoreError("命名列表文件字段非法：文本字段类型错误")
        try:
            UUID(list_id)
            datetime.fromisoformat(created_at)
            datetime.fromisoformat(updated_at)
        except (ValueError, TypeError) as error:
            raise RushListStoreError("命名列表文件字段非法：标识或时间格式错误") from error
        if not 1 <= len(name.strip()) <= 40 or name != name.strip():
            raise RushListStoreError("命名列表文件字段非法：名称格式错误")
        if len(note) > 300:
            raise RushListStoreError("命名列表文件字段非法：备注超过 300 个字符")
        if not isinstance(courses, list) or not courses:
            raise RushListStoreError("命名列表文件字段非法：课程快照不能为空")
        if not all(isinstance(course, dict) and "id" not in course for course in courses):
            raise RushListStoreError("命名列表文件字段非法：课程快照格式错误")
        return SavedRushList(
            id=list_id,
            name=name,
            note=note,
            created_at=created_at,
            updated_at=updated_at,
            courses=copy.deepcopy(courses),
        )

    def _validate_input(
        self,
        name: str,
        note: str,
        courses: Sequence[Mapping[str, Any]],
    ) -> tuple[str, str]:
        """校验创建或更新命名列表所需输入。

        Args:
            name: 用户输入的列表名称。
            note: 用户输入的列表备注。
            courses: 当前待抢课程序列。

        Returns:
            标准化后的名称和备注。

        Raises:
            RushListStoreError: 名称、备注或课程不符合规则时抛出。
        """
        if not isinstance(name, str) or not isinstance(note, str):
            raise RushListStoreError("列表名称和备注必须是文本")
        normalized_name = name.strip()
        if not 1 <= len(normalized_name) <= 40:
            raise RushListStoreError("列表名称去除首尾空白后必须为 1-40 个字符")
        if len(note) > 300:
            raise RushListStoreError("列表备注最多允许 300 个字符")
        if not courses:
            raise RushListStoreError("请至少添加一门课程后再保存命名列表")
        if not all(isinstance(course, Mapping) for course in courses):
            raise RushListStoreError("课程快照格式非法，无法保存命名列表")
        return normalized_name, note

    def _ensure_unique_name(
        self,
        saved_lists: Sequence[SavedRushList],
        name: str,
        excluded_id: str | None = None,
    ) -> None:
        """校验同一人员下的名称不重复。

        Args:
            saved_lists: 当前人员已有的命名列表。
            name: 准备保存的标准化名称。
            excluded_id: 更新时允许复用名称的当前列表 UUID。

        Returns:
            None: 名称可用时不修改任何数据。

        Raises:
            RushListStoreError: 忽略大小写比较后名称重复时抛出。
        """
        folded_name = name.casefold()
        for saved_list in saved_lists:
            if saved_list.id != excluded_id and saved_list.name.casefold() == folded_name:
                raise RushListStoreError(f"当前人员下已存在名为“{name}”的列表")

    def _snapshot_courses(
        self, courses: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        """深拷贝课程并移除界面序号。

        Args:
            courses: 当前待抢课程序列。

        Returns:
            不含顶层 id 字段的独立课程快照。
        """
        snapshot: list[dict[str, Any]] = []
        for course in courses:
            copied_course = copy.deepcopy(dict(course))
            copied_course.pop("id", None)
            snapshot.append(copied_course)
        return snapshot

    def _clone_saved(self, saved_list: SavedRushList) -> SavedRushList:
        """返回不共享课程引用的命名列表副本。

        Args:
            saved_list: 需要复制的命名列表。

        Returns:
            课程快照已深拷贝的新对象。
        """
        return SavedRushList(
            id=saved_list.id,
            name=saved_list.name,
            note=saved_list.note,
            created_at=saved_list.created_at,
            updated_at=saved_list.updated_at,
            courses=copy.deepcopy(saved_list.courses),
        )

    def _saved_to_dict(self, saved_list: SavedRushList) -> dict[str, object]:
        """将命名列表转换为可序列化字典。

        Args:
            saved_list: 需要写入 JSON 的命名列表。

        Returns:
            与版本 1 文件结构一致的字典。
        """
        return {
            "id": saved_list.id,
            "name": saved_list.name,
            "note": saved_list.note,
            "created_at": saved_list.created_at,
            "updated_at": saved_list.updated_at,
            "courses": copy.deepcopy(saved_list.courses),
        }

    def _now_iso(self) -> str:
        """返回当前 UTC 时间的 ISO-8601 文本。

        Args:
            None.

        Returns:
            带时区且精确到微秒的当前时间字符串。
        """
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")
