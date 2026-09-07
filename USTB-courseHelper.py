"""北京科技大学选课助手——程序入口。

本文件只负责组装应用并进入 Tkinter 主循环；所有界面与业务逻辑均位于
独立模块中（登录、查询、抢课、命名列表、用户管理、主题与运行时等）。
导入本文件即会加载 ``app_core`` 完成文件日志的终端重定向配置。
"""

import copy
import os
import orjson
import requests
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, time as clock_time
from io import BytesIO
from tkinter import messagebox, simpledialog, ttk
import tkinter as tk

from PIL import Image, ImageTk
from bs4 import BeautifulSoup

# 主应用类与页面组件：真实功能位于 app_core 及各 ui_* 模块。
from app_core import CourseSelectionApp, UserRushPage

# 抢课接口业务状态分类（结果码映射与纯函数）。
from selection_codes import (
    SELECTION_MESSAGE_STATUSES,
    SELECTION_RESULT_CODE_STATUSES,
    classify_selection_response,
    is_environment_request_error,
)

# 旧版测试兼容全局状态；真实应用路径使用 MultiUserRuntime 用户上下文。
from app_legacy import (
    course_data_list,
    course_id_count,
    current_img_data,
    final_cookies_dict,
    login_success,
    online_thread_running,
    qr_image_url,
    selection_running,
    stop_display,
    stop_selection,
)

# 登录状态与任务状态枚举，供界面和外部测试引用。
from multi_user_runtime import UserLoginState, UserRuntimeContext, UserTaskState


if __name__ == "__main__":
    root = tk.Tk()
    app = CourseSelectionApp(root)
    root.mainloop()