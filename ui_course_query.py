"""课程联合查询、结果渲染与手动参数添加。"""

import orjson
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox
import requests
from bs4 import BeautifulSoup
from academic_term import get_current_academic_term
from course_query import (
    DISPLAY_COLUMNS,
    CourseSearchCriteria,
    CourseSearchResult,
    build_course_query_payload,
    extract_course_search_results,
    format_schedule_text,
)
from manual_course import (
    COURSE_TYPE_LABELS,
    DEFAULT_CATEGORY_CODE,
    build_manual_course,
)
from ui_workspace import open_schedule_popup
from multi_user_runtime import UserLoginState
import app_legacy

# 查询结果表中上课信息列的定位信息（DISPLAY_COLUMNS 动态推导）。
_SCHEDULE_VALUE_INDEX = next(
    index
    for index, (field_name, _) in enumerate(DISPLAY_COLUMNS)
    if field_name == "schedule"
)
_SCHEDULE_COLUMN_ID = f"#{_SCHEDULE_VALUE_INDEX + 1}"

# 课程信息查询请求的浏览器伪装头；长度类请求头由 requests 自动计算。
_QUERY_HEADERS: dict[str, str] = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "cache-control": "no-cache",
    "connection": "keep-alive",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "host": "byyt.ustb.edu.cn",
    "origin": "https://byyt.ustb.edu.cn",
    "pragma": "no-cache",
    "referer": "https://byyt.ustb.edu.cn/Xsxk/query/1",
    "rolecode": "null",
    "sec-ch-ua": '"Not;A=Brand";v="99", "Microsoft Edge";v="139", "Chromium";v="139"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36 Edg/139.0.0.0",
    "x-requested-with": "XMLHttpRequest",
}


class CourseQueryMixin:
    def setup_course_search_tab(self: "CourseSelectionApp") -> None:
        """
        构建课程条件查询与结果选择页面。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 查询控件与结果表直接创建在课程查询页中。
        """
        course_frame = ttk.Frame(
            self.course_search_tab,
            style="App.TFrame",
            padding=(28, 20),
        )
        course_frame.pack(fill="both", expand=True)
        course_frame.columnconfigure(0, weight=1)
        course_frame.rowconfigure(2, weight=1)

        header = ttk.Frame(course_frame, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="课程查询", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            header,
            text="查看抢课任务",
            command=self.show_rush_tab,
            style="Secondary.TButton",
        ).grid(row=0, column=1, sticky="e")

        input_frame = ttk.LabelFrame(course_frame, text="查询条件", padding=12)
        input_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        for column_index in range(5):
            input_frame.columnconfigure(column_index, weight=1)

        self.course_type_var = tk.StringVar(value="所有")
        self.course_id_var = tk.StringVar()
        self.course_name_var = tk.StringVar()
        self.semester_var = tk.StringVar(
            value=get_current_academic_term(datetime.now().date())
        )
        self.priority_var = tk.StringVar(value="1")

        ttk.Label(input_frame, text="课程类型").grid(row=0, column=0, sticky="w")
        self.course_type_combo = ttk.Combobox(
            input_frame,
            textvariable=self.course_type_var,
            values=["所有", "素质扩展课", "专业扩展课", "MOOC", "必修课"],
            state="readonly",
            width=16,
        )
        self.course_type_combo.grid(
            row=1, column=0, sticky="ew", padx=(0, 12), pady=(5, 0)
        )
        ttk.Label(input_frame, text="课程代码").grid(row=0, column=1, sticky="w")
        ttk.Entry(input_frame, textvariable=self.course_id_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 12), pady=(5, 0)
        )
        ttk.Label(input_frame, text="课程名称").grid(row=0, column=2, sticky="w")
        ttk.Entry(input_frame, textvariable=self.course_name_var).grid(
            row=1, column=2, sticky="ew", padx=(0, 12), pady=(5, 0)
        )
        ttk.Label(input_frame, text="学年学期").grid(row=0, column=3, sticky="w")
        ttk.Entry(input_frame, textvariable=self.semester_var).grid(
            row=1, column=3, sticky="ew", padx=(0, 12), pady=(5, 0)
        )
        self.add_course_btn = ttk.Button(
            input_frame,
            text="查询课程",
            command=self.search_courses,
            style="Primary.TButton",
        )
        self.add_course_btn.grid(row=1, column=4, sticky="ew", pady=(5, 0))

        result_frame = ttk.LabelFrame(course_frame, text="课程查询结果", padding=8)
        result_frame.grid(row=2, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(1, weight=1)

        result_toolbar = ttk.Frame(result_frame, style="TFrame")
        result_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        result_toolbar.columnconfigure(0, weight=1)
        self.search_count_var = tk.StringVar(value="尚未查询")
        self.search_type_summary_var = tk.StringVar(value="")
        ttk.Label(
            result_toolbar,
            textvariable=self.search_count_var,
            style="SurfaceMuted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            result_toolbar,
            textvariable=self.search_type_summary_var,
            style="SurfaceMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(result_toolbar, text="优先级").grid(
            row=0, column=1, padx=(12, 6)
        )
        ttk.Spinbox(
            result_toolbar,
            from_=1,
            to=99,
            textvariable=self.priority_var,
            width=5,
        ).grid(row=0, column=2)
        self.advanced_add_btn = ttk.Button(
            result_toolbar,
            text="高级添加",
            command=self.open_manual_course_dialog,
            style="Secondary.TButton",
        )
        self.advanced_add_btn.grid(row=0, column=3, padx=(12, 0))
        self.add_selected_course_btn = ttk.Button(
            result_toolbar,
            text="添加选中课程",
            command=self.add_selected_courses,
            style="Secondary.TButton",
        )
        self.add_selected_course_btn.grid(row=0, column=4, padx=(12, 0))
        self.search_priority_help_label = ttk.Label(
            result_toolbar,
            text="优先级说明：数字越小越先尝试；相同数字按列表顺序执行。",
            style="SurfaceMuted.TLabel",
        )
        self.search_priority_help_label.grid(
            row=1, column=1, columnspan=4, sticky="e", pady=(5, 0)
        )

        result_columns = tuple(field_name for field_name, _ in DISPLAY_COLUMNS)
        self.course_result_tree = ttk.Treeview(
            result_frame,
            columns=result_columns,
            show="headings",
            selectmode="extended",
        )
        column_widths = {
            "display_name": 180,
            "course_code": 110,
            "course_name": 180,
            "course_nature": 90,
            "course_category": 230,
            "teaching_language": 90,
            "scoring_method": 90,
            "credit": 65,
            "class_hours": 65,
            "schedule": 280,
            "capacity_selected": 100,
            "offering_college": 180,
            "campus": 90,
        }
        for field_name, column_title in DISPLAY_COLUMNS:
            self.course_result_tree.heading(field_name, text=column_title)
            self.course_result_tree.column(
                field_name,
                width=column_widths[field_name],
                minwidth=60,
                stretch=False,
            )
        self.search_results_by_task_id: dict[str, CourseSearchResult] = {}
        self.search_result_course_types_by_task_id: dict[str, str] = {}
        result_scrollbar_y = ttk.Scrollbar(
            result_frame, orient="vertical", command=self.course_result_tree.yview
        )
        result_scrollbar_x = ttk.Scrollbar(
            result_frame, orient="horizontal", command=self.course_result_tree.xview
        )
        self.course_result_tree.configure(
            yscrollcommand=result_scrollbar_y.set,
            xscrollcommand=result_scrollbar_x.set,
        )
        self.course_result_tree.grid(row=1, column=0, sticky="nsew")
        result_scrollbar_y.grid(row=1, column=1, sticky="ns")
        result_scrollbar_x.grid(row=2, column=0, sticky="ew")
        self.course_result_tree.bind(
            "<Button-1>", self._on_result_schedule_click
        )

    def _on_result_schedule_click(
        self: "CourseSelectionApp", event: tk.Event
    ) -> None:
        """点击查询结果表上课信息单元格时弹出完整安排窗口。

        Args:
            self: 当前课程助手应用实例。
            event: 表格点击事件。

        Returns:
            None: 点击表头或非上课信息列时静默返回。
        """
        if self.course_result_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.course_result_tree.identify_column(event.x) != _SCHEDULE_COLUMN_ID:
            return
        row_id = self.course_result_tree.identify_row(event.y)
        if not row_id:
            return
        values = self.course_result_tree.item(row_id, "values")
        if len(values) <= _SCHEDULE_VALUE_INDEX:
            return
        open_schedule_popup(self.root, str(values[_SCHEDULE_VALUE_INDEX]))

    def select_user_from_search_combo(
        self: "CourseSelectionApp", event: object | None = None
    ) -> None:
        """从课程查询页选择器切换当前用户。

        Args:
            self: 当前课程助手应用实例。
            event: Tkinter 下拉选择事件；直接调用时为 None。

        Returns:
            None: 两个工作区用户选择器会保持同步。
        """
        alias = self.current_student_display_var.get().strip()
        if alias:
            self.student_name_var.set(alias)
            self.select_user_by_alias(event)

    def search_courses(self: "CourseSelectionApp") -> None:
        """
        读取联合筛选条件并在后台查询可选课程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 查询结果会异步写入课程查询结果表。
        """
        context = self.active_user_context(show_error=True)
        if context is None:
            return
        cookies = dict(context.cookies)
        if context.login_state is not UserLoginState.LOGGED_IN or not cookies:
            messagebox.showerror("错误", "请先登录")
            return

        course_type_codes = {
            "素质扩展课": "sztzk-b-b",
            "专业扩展课": "zytzk-b-b",
            "MOOC": "mooc-b-b",
            "必修课": "bx-b-b",
        }
        selected_course_type = self.course_type_var.get()
        if selected_course_type not in {"所有", *course_type_codes}:
            messagebox.showerror("错误", "课程类型无效")
            return

        criteria = CourseSearchCriteria(
            course_code=self.course_id_var.get().strip(),
            course_name=self.course_name_var.get().strip(),
        )
        try:
            target_course_types = (
                course_type_codes.items()
                if selected_course_type == "所有"
                else ((selected_course_type, course_type_codes[selected_course_type]),)
            )
            payloads = [
                (
                    course_type_code,
                    build_course_query_payload(
                        semester=self.semester_var.get().strip(),
                        course_type_code=course_type_code,
                        criteria=criteria,
                    ),
                )
                for _, course_type_code in target_course_types
            ]
        except ValueError as error:
            messagebox.showerror("错误", str(error))
            return

        self.add_course_btn.config(state=tk.DISABLED)
        self.status_var.set("正在查询课程...")
        threading.Thread(
            target=self.query_course_results,
            args=(context.profile.id, cookies, payloads),
            daemon=True,
        ).start()

    def query_course_results(
        self: "CourseSelectionApp",
        profile_id: str,
        cookies: dict[str, str],
        payloads: list[tuple[str, dict[str, str]]],
    ) -> None:
        """
        使用当前登录会话请求课程结果并安排界面刷新。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 发起查询的用户 UUID。
            cookies: 查询开始时捕获的用户 Cookie 副本。
            payloads: 选课方式代码与其对应的非空查询参数列表。

        Returns:
            None: 结果或错误信息通过 Tkinter 主线程展示。
        """
        try:
            session = requests.Session()
            session.cookies.update(cookies)
            session.headers.update({
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://byyt.ustb.edu.cn",
                "Referer": "https://byyt.ustb.edu.cn/Xsxk/query/1",
                "RoleCode": "null",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
                "X-Requested-With": "XMLHttpRequest",
                "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Microsoft Edge";v="150"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            })
            result_by_task_id: dict[str, CourseSearchResult] = {}
            course_type_by_task_id: dict[str, str] = {}
            failed_type_codes: list[str] = []
            successful_type_count = 0
            course_type_counts = {
                course_type_code: 0 for course_type_code, _ in payloads
            }
            for course_type_code, payload in payloads:
                page_size = max(int(payload.get("pageSize", "100")), 1)
                page_number = 1
                type_task_ids: set[str] = set()
                type_had_success = False
                while page_number <= 100:
                    page_payload = dict(payload)
                    page_payload["pageNum"] = str(page_number)
                    page_payload["pageSize"] = str(page_size)
                    try:
                        response = session.post(
                            "https://byyt.ustb.edu.cn/Xsxk/queryKxrw",
                            data=page_payload,
                            timeout=30,
                        )
                        response.raise_for_status()
                        response_results = extract_course_search_results(
                            orjson.loads(response.content)
                        )
                    except Exception as error:
                        failed_type_codes.append(course_type_code)
                        self.user_log(
                            profile_id,
                            f"课程类型 {course_type_code} 第 {page_number} 页查询失败：{error}",
                        )
                        break
                    type_had_success = True
                    new_task_count = 0
                    for result in response_results:
                        if result.task_id not in type_task_ids:
                            type_task_ids.add(result.task_id)
                            new_task_count += 1
                        result_by_task_id.setdefault(result.task_id, result)
                        course_type_by_task_id.setdefault(
                            result.task_id, course_type_code
                        )
                    if (
                        len(response_results) < page_size
                        or new_task_count == 0
                    ):
                        break
                    page_number += 1
                if type_had_success:
                    successful_type_count += 1
                course_type_counts[course_type_code] = len(type_task_ids)
            if successful_type_count == 0:
                failed_summary = "、".join(failed_type_codes)
                raise RuntimeError(f"所有课程类型查询均失败：{failed_summary}")
            results = list(result_by_task_id.values())
            self.root.after(
                0,
                lambda: self.show_course_search_results(
                    profile_id,
                    results,
                    course_type_by_task_id,
                    len(failed_type_codes),
                    course_type_counts,
                ),
            )
        except Exception as error:
            error_message = f"查询课程时出错：{error}"
            self.user_log(profile_id, f"查询失败：{error}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_message))
        finally:
            self.root.after(0, self.apply_active_user_control_state)

    def show_course_search_results(
        self: "CourseSelectionApp",
        profile_id: str,
        results: list[CourseSearchResult],
        course_type_by_task_id: dict[str, str],
        failed_type_count: int = 0,
        course_type_counts: dict[str, int] | None = None,
    ) -> None:
        """
        清空并填充课程查询结果表。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 发起查询的用户 UUID。
            results: 已完成空值清理和字段映射的查询结果。
            course_type_by_task_id: 每条结果对应的选课方式代码。
            failed_type_count: 本次汇总中请求失败的课程类型数量。
            course_type_counts: 每种选课方式在去重前返回的课程数量。

        Returns:
            None: 结果直接渲染到 Treeview 控件。
        """
        context = self.runtime.require_context(profile_id)
        context.search_results_by_task_id = {
            result.task_id: result for result in results
        }
        context.search_result_course_types_by_task_id = dict(course_type_by_task_id)
        if self.current_profile_id() != profile_id:
            result_message = f"查询完成，共 {len(results)} 门课程"
            if failed_type_count:
                result_message += f"，{failed_type_count} 个课程类型查询失败"
            self.user_log(profile_id, result_message)
            self.refresh_user_views()
            return
        self.search_results_by_task_id = dict(context.search_results_by_task_id)
        self.search_result_course_types_by_task_id = dict(
            context.search_result_course_types_by_task_id
        )
        self.render_course_search_results(
            results, notify_empty=failed_type_count == 0
        )
        if course_type_counts is not None and hasattr(
            self, "search_type_summary_var"
        ):
            type_labels = {
                "sztzk-b-b": "素质扩展课",
                "zytzk-b-b": "专业扩展课",
                "mooc-b-b": "MOOC",
                "bx-b-b": "必修课",
            }
            summary_parts = [
                f"{type_labels.get(code, code)} {count}"
                for code, count in course_type_counts.items()
            ]
            self.search_type_summary_var.set(" / ".join(summary_parts))
        if failed_type_count:
            self.search_count_var.set(
                f"共汇总 {len(results)} 门课程（{failed_type_count} 个类型查询失败）"
            )
            self.status_var.set(
                f"已汇总 {len(results)} 门课程；{failed_type_count} 个课程类型查询失败"
            )

    def render_course_search_results(
        self: "CourseSelectionApp",
        results: list[CourseSearchResult],
        notify_empty: bool = True,
    ) -> None:
        """将当前用户的课程查询结果渲染到表格。

        Args:
            self: 当前课程助手应用实例。
            results: 当前用户需要显示的课程结果。
            notify_empty: True 表示真实查询为空时弹出提示；用户切换时为 False。

        Returns:
            None: 结果表、数量和状态文本会同步更新。
        """
        for item_id in self.course_result_tree.get_children():
            self.course_result_tree.delete(item_id)
        for result in results:
            self.course_result_tree.insert(
                "", "end", iid=result.task_id, values=result.display_values()
            )

        if results:
            self.search_count_var.set(f"共查询到 {len(results)} 门课程")
            self.status_var.set(f"查询到 {len(results)} 门课程，请选择后添加")
        elif notify_empty:
            self.search_count_var.set("未找到符合条件的课程")
            self.status_var.set("未找到符合条件的课程")
            messagebox.showinfo("查询结果", "未找到符合条件的课程")
        else:
            self.search_count_var.set("当前用户暂无查询结果")

    def add_selected_courses(self: "CourseSelectionApp") -> None:
        """
        将查询结果表中选中的课程加入当前抢课列表。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 选中课程会保存到当前人员的课程列表并刷新界面。
        """
        context = self.active_user_context()
        if context is None:
            messagebox.showerror("错误", "请先在抢课任务页设置抢课人员")
            self.show_rush_tab()
            return
        courses = context.courses

        selected_task_ids = self.course_result_tree.selection()
        if not selected_task_ids:
            messagebox.showwarning("警告", "请先选择要添加的课程")
            return

        try:
            priority = int(self.priority_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "优先级必须是整数")
            return

        semester = self.semester_var.get().strip()
        semester_parts = semester.split("-")
        if len(semester_parts) != 3 or not all(semester_parts):
            messagebox.showerror("错误", "学期格式错误，请使用 YYYY-YYYY-N 格式")
            return

        academic_year = "-".join(semester_parts[:2])
        term = semester_parts[2]
        added_count = 0
        for task_id in selected_task_ids:
            result = self.search_results_by_task_id.get(task_id)
            course_type_code = self.search_result_course_types_by_task_id.get(task_id)
            if (
                result is None
                or result.category_code == "—"
                or course_type_code is None
            ):
                continue
            next_id = len(courses) + 1
            courses.append({
                "priority": priority,
                "data": {
                    "p_xktjz": "rwtjzyx",
                    "p_xn": academic_year,
                    "p_xq": term,
                    "p_xkfsdm": course_type_code,
                    "p_kclb": result.category_code,
                    "p_id": result.task_id,
                },
                "name": result.course_name,
                "teacher": result.teacher,
                "course_id": result.course_code,
                "schedule": format_schedule_text(result.schedule),
                "id": next_id,
            })
            self.cache_course_info(
                result.course_code,
                f"{academic_year}{term}",
                {
                    "name": result.course_name,
                    "teacher": result.teacher,
                    "p_id": result.task_id,
                    "p_kclb": result.category_code,
                    "schedule": format_schedule_text(result.schedule),
                },
            )
            added_count += 1

        if not added_count:
            messagebox.showerror("错误", "选中课程缺少课程类别编码，无法添加")
            return
        self.mark_current_list_dirty()
        self.save_course_list()
        self.update_course_list()
        self.status_var.set(f"已添加 {added_count} 门课程")
        messagebox.showinfo("成功", f"已添加 {added_count} 门课程")

    def open_manual_course_dialog(self: "CourseSelectionApp") -> None:
        """打开为特殊课程手动填写请求参数的高级添加窗口。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 校验成功后创建模态输入窗口。
        """
        context = self.active_user_context(show_error=True)
        if context is None:
            self.show_rush_tab()
            return
        if context.login_state is not UserLoginState.LOGGED_IN or not context.cookies:
            messagebox.showerror("错误", "请先登录当前用户")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("高级添加课程")
        dialog.geometry("560x580")
        dialog.minsize(520, 540)
        dialog.transient(self.root)
        dialog.grab_set()
        content = ttk.Frame(dialog, style="TFrame", padding=20)
        content.pack(fill="both", expand=True)
        content.columnconfigure(1, weight=1)

        name_var = tk.StringVar()
        teacher_var = tk.StringVar()
        code_var = tk.StringVar()
        schedule_var = tk.StringVar()
        priority_var = tk.StringVar(value=self.priority_var.get())
        type_var = tk.StringVar(value="素质扩展课")
        category_var = tk.StringVar(value=DEFAULT_CATEGORY_CODE)
        task_id_var = tk.StringVar()

        ttk.Label(
            content,
            text="高级添加课程",
            style="Section.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(
            content,
            text="适用于无法通过查询自动获取参数的课程，请手动填写抢课请求参数。",
            style="SurfaceMuted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 14))

        def add_row(row_index: int, label_text: str, widget: tk.Widget) -> None:
            """在对话框中按行放置标签与输入控件。

            Args:
                row_index: 目标网格行号。
                label_text: 字段标签文本。
                widget: 已创建的输入控件。

            Returns:
                None: 标签与控件直接放入网格。
            """
            ttk.Label(content, text=label_text).grid(
                row=row_index, column=0, sticky="w", padx=(0, 10), pady=(6, 0)
            )
            widget.grid(row=row_index, column=1, sticky="ew", pady=(6, 0))

        add_row(2, "课程名称 *", ttk.Entry(content, textvariable=name_var))
        add_row(3, "授课教师", ttk.Entry(content, textvariable=teacher_var))
        add_row(4, "课程代码", ttk.Entry(content, textvariable=code_var))
        add_row(5, "上课安排", ttk.Entry(content, textvariable=schedule_var))
        add_row(
            6,
            "优先级",
            ttk.Spinbox(content, from_=1, to=99, textvariable=priority_var, width=8),
        )
        add_row(
            7,
            "选课方式",
            ttk.Combobox(
                content,
                textvariable=type_var,
                values=[label for label, _ in COURSE_TYPE_LABELS],
                state="readonly",
            ),
        )
        add_row(
            8,
            "课程类别代码",
            ttk.Entry(content, textvariable=category_var),
        )
        add_row(9, "课程任务 ID *", ttk.Entry(content, textvariable=task_id_var))
        ttk.Label(
            content,
            text="课程任务 ID（p_id）与课程类别代码可在页面源码或接口返回值中查找。",
            style="SurfaceMuted.TLabel",
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=(10, 0))

        actions = ttk.Frame(content, style="TFrame")
        actions.grid(row=11, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(
            actions,
            text="取消",
            command=dialog.destroy,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            actions,
            text="添加课程",
            command=lambda: self.submit_manual_course(
                dialog,
                name_var,
                teacher_var,
                code_var,
                schedule_var,
                priority_var,
                type_var,
                category_var,
                task_id_var,
            ),
            style="Primary.TButton",
        ).pack(side=tk.LEFT)
        name_entry = content.grid_slaves(row=2, column=1)[0]
        name_entry.focus_set()

    def submit_manual_course(
        self: "CourseSelectionApp",
        dialog: tk.Toplevel,
        name_var: tk.StringVar,
        teacher_var: tk.StringVar,
        code_var: tk.StringVar,
        schedule_var: tk.StringVar,
        priority_var: tk.StringVar,
        type_var: tk.StringVar,
        category_var: tk.StringVar,
        task_id_var: tk.StringVar,
    ) -> None:
        """校验高级参数并加入当前用户待抢列表。

        Args:
            self: 当前课程助手应用实例。
            dialog: 当前高级添加对话框。
            name_var: 课程名称变量。
            teacher_var: 授课教师变量。
            code_var: 课程代码变量。
            schedule_var: 上课安排变量。
            priority_var: 抢课优先级变量。
            type_var: 选课方式变量。
            category_var: 课程类别代码变量。
            task_id_var: 课程任务 ID 变量。

        Returns:
            None: 校验失败时保持列表不变，成功后停留在课程查询页。
        """
        context = self.active_user_context()
        if context is None:
            messagebox.showerror("错误", "请先在抢课任务页设置抢课人员")
            self.show_rush_tab()
            return
        try:
            course = build_manual_course(
                semester=self.semester_var.get().strip(),
                course_type_label=type_var.get(),
                category_code=category_var.get(),
                task_id=task_id_var.get(),
                name=name_var.get(),
                priority=priority_var.get(),
                teacher=teacher_var.get(),
                course_code=code_var.get(),
                schedule=schedule_var.get(),
            )
        except ValueError as error:
            messagebox.showerror("添加失败", str(error))
            return
        next_id = len(context.courses) + 1
        course["id"] = next_id
        context.courses.append(course)
        self.mark_current_list_dirty()
        self.save_course_list()
        self.update_course_list()
        dialog.destroy()
        self.status_var.set(f"已添加高级课程：{course['name']}")
        messagebox.showinfo("成功", f"已添加课程：{course['name']}")

    def add_course(self: "CourseSelectionApp") -> None:
        """将当前查询目标课程加入旧版全局待抢列表并保存草稿。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 缺少抢课人员、会话或课程代码时提示并返回。
        """
        # 检查是否已输入抢课人员
        if not self.current_student_name:
            messagebox.showerror("错误", "请先输入抢课人员姓名")
            return

        if not app_legacy.final_cookies_dict:
            messagebox.showerror("错误", "请先登录")
            return

        course_type_text = self.course_type_var.get()
        if course_type_text == "素质扩展课":
            p_xkfsdm = "sztzk-b-b"
        elif course_type_text == "专业扩展课":
            p_xkfsdm = "zytzk-b-b"
        elif course_type_text == "MOOC":
            p_xkfsdm = "mooc-b-b"
        elif course_type_text == "必修课":
            p_xkfsdm = "bx-b-b"
        else:
            messagebox.showerror("错误", "课程类型无效")
            return

        course_id = self.course_id_var.get().strip()
        if not course_id:
            messagebox.showerror("错误", "请输入课程ID")
            return

        try:
            priority = int(self.priority_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "优先级必须是整数")
            return

        p_xn_xq = self.semester_var.get().strip()
        if not p_xn_xq or p_xn_xq.count("-") != 2:
            messagebox.showerror("错误", "学期格式错误，请使用 YYYY-YYYY-N 格式")
            return

        course_time = p_xn_xq.split("-")
        p_xn = f"{course_time[0]}-{course_time[1]}"
        p_xq = course_time[2]
        p_xnxq = p_xn + p_xq
        p_dqxn = p_xn
        p_dqxq = p_xq
        p_dqxnxq = p_xnxq

        print(f"🔍 正在查询课程 {course_id} 的信息...")
        self.status_var.set(f"正在查询课程 {course_id}...")

        query_thread = threading.Thread(
            target=self.query_course_info,
            args=(course_id, p_xn, p_xq, p_xnxq, p_dqxn, p_dqxq, p_dqxnxq, p_xkfsdm, priority),
            daemon=True
        )
        query_thread.start()

    def query_course_info(
        self: "CourseSelectionApp",
        course_id: str,
        p_xn: str,
        p_xq: str,
        p_xnxq: str,
        p_dqxn: str,
        p_dqxq: str,
        p_dqxnxq: str,
        p_xkfsdm: str,
        priority: int,
    ) -> None:
        """按参数查询课程信息并加入旧版全局待抢列表。

        Args:
            self: 当前课程助手应用实例。
            course_id: 待查询课程代码。
            p_xn: 学年。
            p_xq: 学期。
            p_xnxq: 学年学期组合值。
            p_dqxn: 当前学年。
            p_dqxq: 当前学期。
            p_dqxnxq: 当前学年学期组合值。
            p_xkfsdm: 选课方式代码。
            priority: 加入列表的抢课优先级。

        Returns:
            None: 查询失败或未找到课程时提示并返回。
        """
        semester = f"{p_xn}{p_xq}"
        cache_key = f"{semester}_{course_id}"

        cached_courses = self.get_cached_course(course_id, semester)
        if cached_courses:
            print(f"ℹ️ 从缓存中获取课程 {course_id} 的信息（共 {len(cached_courses)} 门课）")

            # 获取全局变量

            added_count = 0
            for cached_course in cached_courses:
                course_name = cached_course["name"]
                teacher = cached_course["teacher"]
                p_id = cached_course["p_id"]
                p_kclb = cached_course["p_kclb"]
                course_schedule = format_schedule_text(cached_course["schedule"])

                # 使用 app_legacy.course_id_count 生成新的ID
                new_id = app_legacy.course_id_count + 1
                app_legacy.course_id_count = new_id

                course_data = {
                    "priority": priority,
                    "data": {
                        "p_xktjz": "rwtjzyx",
                        "p_xn": p_xn,
                        "p_xq": p_xq,
                        "p_xkfsdm": p_xkfsdm,
                        "p_kclb": p_kclb,
                        "p_id": p_id
                    },
                    "name": course_name,
                    "teacher": teacher,
                    "course_id": course_id,
                    "schedule": course_schedule,
                    "id": new_id
                }
                app_legacy.course_data_list.append(course_data)
                added_count += 1
                print(f"✅ 已添加：{course_name} | 教师：{teacher} | 时间：{course_schedule}")

            # 添加课程后立即保存
            self.save_course_list()
            self.root.after(0, lambda: self.update_course_list())
            self.root.after(0, self.mark_current_list_dirty)
            self.root.after(0, lambda ac=added_count: messagebox.showinfo("成功", f"已从缓存添加 {ac} 门课程"))
            self.root.after(0, lambda: self.status_var.set("课程添加成功"))
            return

        try:
            session = requests.Session()
            session.cookies.update(app_legacy.final_cookies_dict)
            session.headers.update(_QUERY_HEADERS)

            qurl = "https://byyt.ustb.edu.cn/Xsxk/queryKxrw"
            qdata = {
                'cxsfmt': "1",
                'p_pylx': "1",
                'mxpylx': "1",
                'p_xn': p_xn,
                'p_xq': p_xq,
                'p_xnxq': p_xnxq,
                'p_dqxn': p_dqxn,
                'p_dqxq': p_dqxq,
                'p_dqxnxq': p_dqxnxq,
                'p_xkfsdm': p_xkfsdm,
                'p_kcdm_cxrw': course_id,
                'p_kcdm_cxrw_zckc': course_id,
                "p_sfxsgwckb": "1",
                "p_sfgldjr":"0",
                "p_sfredis":"0",
                "p_sfsyxkgwc":"0",
                "p_sfhlctkc":"0",
                "p_sfhllrlkc":"0",
                'pageNum': "1",
                'pageSize': "100"
            }
            print(f"🔍 正在查询课程 {course_id} 的信息...")
            response = session.post(qurl, data=qdata)
            if response.status_code != 200:
                error_msg = f"查询失败，状态码：{response.status_code}"
                print(f"⚠️ {error_msg}")
                self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
                return

            coursedata = orjson.loads(response.content)
            if not coursedata['kxrwList']['list']:
                error_msg = f"未找到课程 {course_id}"
                print(f"⚠️ {error_msg}")
                self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
                return

            course_total = coursedata['kxrwList']['total']
            course_info = coursedata['kxrwList']['list']

            added_count = 0

            for course in course_info:
                app_legacy.course_id_count += 1
                course_name = course["kcmc"]
                teacher = course["dgjsmc"]
                p_id = course["id"]
                p_kclb = course.get("kclbdm", "2301")
                kcxx_html = course["kcxx"]
                soup = BeautifulSoup(kcxx_html, 'html.parser')
                tag_cyan = soup.find('div', class_='ivu-tag-cyan')
                schedule = "未知时间"
                if tag_cyan:
                    tag_text = tag_cyan.find('span', class_='ivu-tag-text')
                    if tag_text:
                        schedule = format_schedule_text(
                            tag_text.get_text(strip=True)
                        )
                course_schedule = schedule
                print(f"✅ 找到课程：{course_name} | 教师：{teacher} | ID：{p_id} | 课程安排：{course_schedule}")

                course_data = {
                    "priority": priority,
                    "data": {
                        "p_xktjz": "rwtjzyx",
                        "p_xn": p_xn,
                        "p_xq": p_xq,
                        "p_xkfsdm": p_xkfsdm,
                        "p_kclb": p_kclb,
                        "p_id": p_id
                    },
                    "name": course_name,
                    "teacher": teacher,
                    "course_id": course_id,
                    "schedule": course_schedule,
                    "id": app_legacy.course_id_count
                }
                app_legacy.course_data_list.append(course_data)
                added_count += 1

                self.cache_course_info(
                    course_id,
                    semester,
                    {
                        "name": course_name,
                        "teacher": teacher,
                        "p_id": p_id,
                        "p_kclb": p_kclb,
                        "schedule": course_schedule
                    }
                )

            # 添加课程后立即保存
            self.save_course_list()

            self.root.after(0, lambda: self.update_course_list())
            self.root.after(0, self.mark_current_list_dirty)
            self.root.after(0, lambda ac=added_count: messagebox.showinfo("成功", f"已添加 {ac} 门课程"))
            self.root.after(0, lambda: self.status_var.set("课程添加成功"))


        except Exception as e:
            error_msg = f"查询课程 {course_id} 时出错：{e}"
            print(f"❌ {error_msg}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
