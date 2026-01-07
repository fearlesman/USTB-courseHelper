import os
import orjson
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image, ImageTk
from io import BytesIO
import requests
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import queue
import sys
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
from collections import defaultdict

# 全局变量
qr_image_url = None
current_img_data = None
stop_display = False
login_success = False
final_cookies_dict = {}  # 存储提取的 cookies
log_queue = queue.Queue()  # 消息队列
course_data_list = []  # 存储课程数据（含优先级）
course_id_count=0
selection_running = False  # 是否正在抢课
stop_selection = False     # 是否请求停止
online_thread_running = False  # 是否正在运行online线程gio

# 自定义 stdout，将 print 输出重定向到 GUI
class CustomStdout:
    def __init__(self, queue):
        self.queue = queue
        self.terminal = sys.stdout

    def write(self, message):
        self.terminal.write(message)
        if message.strip():
            self.queue.put(message)

    def flush(self):
        self.terminal.flush()

# 替换 stdout
sys.stdout = CustomStdout(log_queue)

# 主应用类
class CourseSelectionApp:
    def __init__(self, root):
        self.target_text = ""
        self.root = root
        self.root.title("北京科技大学选课助手-zby")
        self.root.geometry("1080x720")
        self.root.configure(bg="#f0f0f0")
        
        self.course_cache = {}
        self.cache_file = os.path.join(os.path.dirname(__file__), "course_cache.json")
        # 移除单一课程列表文件，改为按人员保存
        # self.course_list_file = os.path.join(os.path.dirname(__file__), "course_list.json")
        self.switch_timer = None

        # 新增：当前抢课人员名称
        self.current_student_name = ""
        # 新增：人员切换锁，防止频繁切换
        self.student_switch_lock = False

         # 加载课程缓存
        self.load_course_cache()

        # 配置样式
        self.style = ttk.Style()
        self.style.configure("TFrame", background="#f0f0f0")
        self.style.configure("TButton", background="#4CAF50", foreground="black", font=("Arial", 10, "bold"))
        self.style.configure("TLabel", background="#f0f0f0", font=("Arial", 10))
        self.style.configure("Header.TLabel", font=("Arial", 12, "bold"))
        
        # 创建选项卡
        self.tab_control = ttk.Notebook(root)
        
        # 登录选项卡
        self.login_tab = ttk.Frame(self.tab_control, style="TFrame")
        self.tab_control.add(self.login_tab, text="登录")
        self.setup_login_tab()
        
        # 选课选项卡
        self.course_tab = ttk.Frame(self.tab_control, style="TFrame")
        self.tab_control.add(self.course_tab, text="课程选择")
        self.setup_course_tab()
        
        self.tab_control.pack(expand=1, fill="both")
        
        # 初始禁用选课选项卡
        self.tab_control.tab(1, state="disabled")
        
        # 配置浏览器
        self.configure_browser()
        chrome_driver_path = os.path.join(os.path.dirname(__file__), "chromedriver.exe")
        self.service = Service(chrome_driver_path)

         # 设置窗口关闭事件处理
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 启动自动保存线程
        self.auto_save_thread = threading.Thread(target=self.auto_save_course_list, daemon=True)
        self.auto_save_thread.start()

        self.window_minimized = False
        self.window_resizing = False
        self.last_window_state = None
        self.window_state_debounce_id = None
        
        # 绑定窗口状态变化事件
        self.root.bind("<Configure>", self.on_window_configure)
        self.root.bind("<Unmap>", self.on_window_minimize)
        self.root.bind("<Map>", self.on_window_restore)
    
    def on_window_configure(self, event):
        """处理窗口配置变化事件"""
        if self.window_state_debounce_id:
            self.root.after_cancel(self.window_state_debounce_id)
        
        self.window_state_debounce_id = self.root.after(100, self.check_window_state)

    def on_window_minimize(self, event):
        """窗口最小化时调用"""
        self.set_window_minimized(True)

    def on_window_restore(self, event):
        """窗口还原时调用"""
        self.set_window_minimized(False)

    def set_window_minimized(self, is_minimized):
        """设置窗口最小化状态，避免重复处理"""
        if self.window_minimized == is_minimized:
            return
        
        self.window_minimized = is_minimized
        if is_minimized:
            print("窗口已最小化，暂停不必要的UI更新")
        else:
            print("窗口已恢复，恢复UI更新")

    def check_window_state(self):
        """检查并更新窗口状态"""
        try:
            current_state = self.root.state()
            is_minimized = (current_state == 'iconic')
            
            if self.window_minimized != is_minimized:
                self.set_window_minimized(is_minimized)
        except Exception as e:
            print(f"检查窗口状态时出错: {e}")

    def _schedule_treeview_refresh(self):
        """计划Treeview刷新，避免频繁重绘"""
        if hasattr(self, '_treeview_refresh_id'):
            self.root.after_cancel(self._treeview_refresh_id)
        
        self._treeview_refresh_id = self.root.after(50, self._refresh_treeview)

    def _refresh_treeview(self):
        """刷新Treeview显示"""
        try:
            if hasattr(self, 'course_tree'):
                self.course_tree.update_idletasks()
                
                width = self.course_tree.winfo_width()
                if width > 100:
                    col_width = max(50, width // 6 - 10)
                    for col in self.course_tree["columns"]:
                        self.course_tree.column(col, width=col_width)
        except Exception as e:
            print(f"刷新Treeview时出错: {e}")

    def load_course_cache(self):
        """加载课程缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.course_cache = orjson.loads(f.read())
                print(f"✅ 已加载 {len(self.course_cache)} 条课程缓存")
        except Exception as e:
            print(f"⚠️ 加载课程缓存失败: {e}")
            self.course_cache = {}

    def save_course_cache(self):
        """保存课程缓存"""
        try:
            with open(self.cache_file, 'wb') as f:
                f.write(orjson.dumps(self.course_cache, option=orjson.OPT_INDENT_2))
            print(f"💾 已保存 {len(self.course_cache)} 条课程缓存")
        except Exception as e:
            print(f"⚠️ 保存课程缓存失败: {e}")

    def get_cached_course(self, course_id, semester):
        """获取缓存的课程信息"""
        cache_key = f"{semester}_{course_id}"
        return self.course_cache.get(cache_key)

    def cache_course_info(self, course_id, semester, course_info):
        """缓存课程信息"""
        cache_key = f"{semester}_{course_id}"
        self.course_cache[cache_key] = course_info
        self.save_course_cache()

    def get_student_course_file(self, student_name):
        """获取指定学生的课程列表文件路径"""
        if not student_name or not student_name.strip():
            return None
        # 文件名格式：course_list_学生姓名.json
        safe_name = student_name.strip().replace('/', '_').replace('\\', '_')
        return os.path.join(os.path.dirname(__file__), f"course_list_{safe_name}.json")

    def load_saved_course_list(self, student_name=None):
        """加载指定学生的课程列表"""
        global course_data_list, course_id_count
        
        if student_name is None:
            student_name = self.current_student_name
            
        course_file = self.get_student_course_file(student_name)
        if not course_file:
            print("⚠️ 学生姓名为空，无法加载课程列表")
            course_data_list = []
            course_id_count = 0
            self.update_course_list()
            return
        
        try:
            if os.path.exists(course_file):
                with open(course_file, 'r', encoding='utf-8') as f:
                    saved_list = orjson.loads(f.read())
                
                if isinstance(saved_list, list) and len(saved_list) > 0:
                    course_id_count = len(saved_list)
                    
                    valid_courses = []
                    for i, course in enumerate(saved_list):
                        if all(key in course for key in ["priority", "data", "name", "teacher", "course_id", "schedule"]):
                            course["id"] = i + 1
                            valid_courses.append(course)
                    
                    if valid_courses:
                        course_data_list = valid_courses
                        self.update_course_list()
                        print(f"✅ 已加载 {student_name} 的 {len(course_data_list)} 门课程")
                    else:
                        print(f"⚠️ {student_name} 的课程列表数据无效，已清空")
                        course_data_list = []
                        course_id_count = 0
                        self.update_course_list()
                else:
                    print(f"ℹ️ {student_name} 暂无保存的课程")
                    course_data_list = []
                    course_id_count = 0
                    self.update_course_list()
            else:
                print(f"ℹ️ {student_name} 是新用户，暂无课程记录")
                course_data_list = []
                course_id_count = 0
                self.update_course_list()
        except Exception as e:
            print(f"⚠️ 加载 {student_name} 的课程列表失败: {e}")
            course_data_list = []
            course_id_count = 0
            self.update_course_list()

    def save_course_list(self, student_name=None):
        """保存指定学生的课程列表"""
        global course_data_list
        
        if student_name is None:
            student_name = self.current_student_name
            
        course_file = self.get_student_course_file(student_name)
        if not course_file:
            print("⚠️ 学生姓名为空，无法保存课程列表")
            return
        
        try:
            save_list = []
            for course in course_data_list:
                save_course = course.copy()
                if "id" in save_course:
                    del save_course["id"]
                save_list.append(save_course)
            
            with open(course_file, 'wb') as f:
                f.write(orjson.dumps(save_list, option=orjson.OPT_INDENT_2))
            print(f"💾 已保存 {student_name} 的 {len(course_data_list)} 门课程")
        except Exception as e:
            print(f"⚠️ 保存 {student_name} 的课程列表失败: {e}")

    # def on_student_name_change(self, *args):
    #     """学生姓名变化时的回调函数"""
    #     if self.student_switch_lock:
    #         return
            
    #     new_name = self.student_name_var.get().strip()
        
    #     # 如果名字没有实质性变化，不处理
    #     if new_name == self.current_student_name:
    #         return
        
    #     # 如果正在抢课，不允许切换
    #     if selection_running:
    #         messagebox.showwarning("警告", "正在抢课中，无法切换人员！")
    #         self.student_switch_lock = True
    #         self.student_name_var.set(self.current_student_name)
    #         self.student_switch_lock = False
    #         return
        
    #     # 保存当前人员的课程列表（如果有）
    #     if self.current_student_name:
    #         print(f"💾 切换人员：保存 {self.current_student_name} 的课程列表")
    #         self.save_course_list(self.current_student_name)
        
    #     # 更新当前人员
    #     old_name = self.current_student_name
    #     self.current_student_name = new_name
        
    #     # 加载新人员的课程列表
    #     if new_name:
    #         print(f"📂 切换人员：加载 {new_name} 的课程列表")
    #         self.load_saved_course_list(new_name)
    #         self.status_var.set(f"已切换到 {new_name}")
    #     else:
    #         # 如果清空了姓名，也清空课程列表
    #         global course_data_list, course_id_count
    #         course_data_list = []
    #         course_id_count = 0
    #         self.update_course_list()
    #         self.status_var.set("请输入抢课人员姓名")
    def on_student_name_change(self, *args):
        """
        监听输入框变化（防抖处理版本）
        当用户输入停止超过 0.8 秒后，才真正执行切换逻辑
        """
        if self.student_switch_lock:
            return

        # 如果之前有正在等待执行的任务，先取消它
        if self.switch_timer:
            self.root.after_cancel(self.switch_timer)

        # 开启一个新的定时器，800毫秒后执行 process_student_switch
        self.switch_timer = self.root.after(800, self.process_student_switch)

    def process_student_switch(self):
        """
        实际执行人员切换逻辑的函数（由定时器触发）
        """
        # 清空定时器引用
        self.switch_timer = None
        
        new_name = self.student_name_var.get().strip()
        
        # 如果名字没有实质性变化，不处理
        if new_name == self.current_student_name:
            return
        
        # 如果正在抢课，不允许切换
        if selection_running:
            # 注意：因为是延时触发，这里最好不要弹窗打断用户，直接回滚即可
            # 或者仅在日志中提示
            if self.student_switch_lock: 
                return
            self.student_switch_lock = True
            self.student_name_var.set(self.current_student_name)
            self.student_switch_lock = False
            print("⚠️ 正在抢课中，忽略人员切换请求")
            return
        
        # === 以下是原有的切换逻辑 ===
        
        # 保存当前人员的课程列表（如果有）
        if self.current_student_name:
            print(f"💾 切换人员：保存 {self.current_student_name} 的课程列表")
            self.save_course_list(self.current_student_name)
        
        # 更新当前人员
        self.current_student_name = new_name
        
        # 加载新人员的课程列表
        if new_name:
            print(f"📂 切换人员：加载 {new_name} 的课程列表")
            self.load_saved_course_list(new_name)
            self.status_var.set(f"已切换到 {new_name}")
        else:
            # 如果清空了姓名，也清空课程列表
            global course_data_list, course_id_count
            course_data_list = []
            course_id_count = 0
            self.update_course_list()
            self.status_var.set("请输入抢课人员姓名")
    def auto_save_course_list(self):
        """自动保存课程列表（用于异常关闭）"""
        while True:
            time.sleep(30)
            if selection_running or stop_selection:
                continue
            if self.current_student_name:
                self.save_course_list()

    def on_closing(self):
        """处理窗口关闭事件"""
        global stop_display, stop_selection
        stop_display = True
        
        if selection_running:
            stop_selection = True
            print("🛑 正在停止选课进程...")
            time.sleep(1)
        
        self.stop_online_keepalive()
        
        # 保存当前人员的课程列表
        if self.current_student_name:
            self.save_course_list()
        print("👋 程序即将关闭，已保存数据")
        self.root.destroy()

    def configure_browser(self):
        self.chrome_options = Options()
        self.chrome_options.add_argument("--headless=new")
        self.chrome_options.add_argument("--disable-gpu")
        self.chrome_options.add_argument("--no-sandbox")
        self.chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        self.chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        
    def setup_login_tab(self):
        login_frame = ttk.Frame(self.login_tab, style="TFrame")
        login_frame.pack(padx=20, pady=20, fill="both", expand=True)
        
        title_label = ttk.Label(login_frame, text="北京科技大学选课助手登录", style="Header.TLabel")
        title_label.pack(pady=(0, 20))
        
        self.qr_frame = ttk.Frame(login_frame, style="TFrame")
        self.qr_frame.pack(pady=10)
        
        self.qr_label = ttk.Label(self.qr_frame, text="等待二维码生成...")
        self.qr_label.pack(pady=20)
        
        self.status_var = tk.StringVar()
        self.status_var.set("等待操作...")
        status_label = ttk.Label(login_frame, textvariable=self.status_var, foreground="blue")
        status_label.pack(pady=10)
        
        btn_frame = ttk.Frame(login_frame, style="TFrame")
        btn_frame.pack(pady=20)
        
        self.login_btn = ttk.Button(btn_frame, text="开始登录", command=self.start_login)
        self.login_btn.pack(side=tk.LEFT, padx=5)

    def update_console(self):
        """优化后的控制台输出更新，减少CPU占用并处理窗口状态"""
        last_update = time.time()
        MIN_UPDATE_INTERVAL = 0.1
        
        while True:
            try:
                is_minimized = getattr(self, 'window_minimized', False)
                update_interval = 1.0 if is_minimized else MIN_UPDATE_INTERVAL
                
                current_time = time.time()
                if current_time - last_update < update_interval:
                    time.sleep(max(0.01, update_interval - (current_time - last_update)))
                    continue
                    
                messages = []
                for _ in range(20):
                    try:
                        msg = log_queue.get_nowait()
                        messages.append(msg)
                    except queue.Empty:
                        break
                
                if messages and hasattr(self, 'console_output'):
                    self.console_output.config(state=tk.NORMAL)
                    for msg in messages:
                        self.console_output.insert(tk.END, '\n' + msg.strip())
                    
                    self.console_output.see(tk.END)
                    
                    lines = int(self.console_output.index('end-1c').split('.')[0])
                    if lines > 50:
                        self.console_output.delete(1.0, f"{lines-100}.0")
                    
                    self.console_output.config(state=tk.DISABLED)
                    last_update = time.time()
                
                time.sleep(0.1)
                
            except Exception as e:
                print(f"更新控制台输出时出错：{e}")
                time.sleep(0.5)

    def setup_course_tab(self):
        course_frame = ttk.Frame(self.course_tab, style="TFrame")
        course_frame.pack(padx=20, pady=20, fill="both", expand=True)
        
        title_label = ttk.Label(course_frame, text="抢课设置", style="Header.TLabel")
        title_label.pack(pady=(0, 20))

        main_name_frame = ttk.Frame(course_frame, style="TFrame")
        main_name_frame.pack(fill="x", pady=5)
        
        name_frame = ttk.Frame(main_name_frame, style="TFrame")
        name_frame.pack(side=tk.LEFT, fill="y", expand=False)
        
        ttk.Label(name_frame, text="抢课人员：").pack(side=tk.LEFT, padx=(0, 10))
        self.student_name_var = tk.StringVar(value="") 
        # 绑定变量变化事件
        self.student_name_var.trace_add('write', self.on_student_name_change)
        ttk.Entry(name_frame, textvariable=self.student_name_var, width=20).pack(side=tk.LEFT)
        
        console_frame = ttk.LabelFrame(main_name_frame, text="实时控制台输出", style="TFrame")
        console_frame.pack(side=tk.RIGHT, fill="both", expand=True, padx=(20, 0))
        
        self.console_output = scrolledtext.ScrolledText(console_frame, wrap=tk.WORD, height=4)
        self.console_output.pack(fill="both", expand=True, padx=5, pady=5)
        self.console_output.config(state=tk.DISABLED)
        
        self.console_queue = queue.Queue()
        self.update_console_thread = threading.Thread(target=self.update_console, daemon=True)
        self.update_console_thread.start()

        input_frame = ttk.Frame(course_frame, style="TFrame")
        input_frame.pack(fill="x", pady=10)
        
        type_frame = ttk.Frame(input_frame, style="TFrame")
        type_frame.pack(fill="x", pady=5)
        
        stop_on_success_frame = ttk.Frame(input_frame, style="TFrame")
        stop_on_success_frame.pack(fill="x", pady=5)
        self.stop_on_success_var = tk.BooleanVar(value=True)
        stop_on_success_check = ttk.Checkbutton(stop_on_success_frame, 
                                            text="选到一门课后停止选课", 
                                            variable=self.stop_on_success_var)
        stop_on_success_check.pack(side=tk.LEFT)
        ttk.Label(stop_on_success_frame, 
                text="（关闭后，即使选到一门课也会继续尝试其他课程）").pack(side=tk.LEFT, padx=(5, 0))
        
        ttk.Label(type_frame, text="课程类型：").pack(side=tk.LEFT, padx=(0, 10))
        self.course_type_var = tk.StringVar()
        self.course_type_combo = ttk.Combobox(type_frame, textvariable=self.course_type_var, 
                                             values=["素质扩展课", "专业扩展课", "MOOC","必修课"], state="readonly", width=15)
        self.course_type_combo.current(0)
        self.course_type_combo.pack(side=tk.LEFT)
        
        id_frame = ttk.Frame(input_frame, style="TFrame")
        id_frame.pack(fill="x", pady=5)
        
        ttk.Label(id_frame, text="课程ID：").pack(side=tk.LEFT, padx=(0, 10))
        self.course_id_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=self.course_id_var, width=20).pack(side=tk.LEFT)
        
        priority_frame = ttk.Frame(input_frame, style="TFrame")
        priority_frame.pack(fill="x", pady=5)
        
        ttk.Label(priority_frame, text="优先级：").pack(side=tk.LEFT, padx=(0, 10))
        self.priority_var = tk.StringVar(value="1")
        ttk.Spinbox(priority_frame, from_=1, to=99, textvariable=self.priority_var, width=5).pack(side=tk.LEFT)
        ttk.Label(priority_frame, text="（数字越小，优先级越高）").pack(side=tk.LEFT, padx=10)
        
        semester_frame = ttk.Frame(input_frame, style="TFrame")
        semester_frame.pack(fill="x", pady=5)
        
        ttk.Label(semester_frame, text="学期：").pack(side=tk.LEFT, padx=(0, 10))
        self.semester_var = tk.StringVar(value="2025-2026-2")
        ttk.Entry(semester_frame, textvariable=self.semester_var, width=20).pack(side=tk.LEFT)
        
        retry_frame = ttk.Frame(input_frame, style="TFrame")
        retry_frame.pack(fill="x", pady=5)
        
        self.retry_full_var = tk.BooleanVar(value=True)
        retry_check = ttk.Checkbutton(retry_frame, text="课程已满时持续重试", variable=self.retry_full_var)
        retry_check.pack(side=tk.LEFT)
        ttk.Label(retry_frame, text='（关闭后，一旦返回"已满"将不再尝试此课程）').pack(side=tk.LEFT, padx=(5, 0))
        
        btn_frame = ttk.Frame(course_frame, style="TFrame")
        btn_frame.pack(pady=10)
        
        self.add_course_btn = ttk.Button(btn_frame, text="添加课程", command=self.add_course)
        self.add_course_btn.pack(side=tk.LEFT, padx=5)

        self.start_auto_btn = ttk.Button(btn_frame, text="开始自动选课", command=self.start_auto_selection)
        self.start_auto_btn.pack(side=tk.LEFT, padx=5)

        self.stop_auto_btn = ttk.Button(btn_frame, text="停止抢课", command=self.stop_auto_selection, state=tk.DISABLED)
        self.stop_auto_btn.pack(side=tk.LEFT, padx=5)
        
        list_frame = ttk.Frame(course_frame, style="TFrame")
        list_frame.pack(fill="both", expand=True, pady=10)
        
        ttk.Label(list_frame, text="已添加课程：", style="Header.TLabel").pack(anchor="w")
        
        columns = ("id","priority", "name", "teacher", "course_id", "schedule")
        self.course_tree = ttk.Treeview(
            list_frame, 
            columns=columns, 
            show="headings",
            selectmode="extended"
        )
        
        self.course_tree.bind("<Configure>", self.on_treeview_configure)
                
        self.course_tree.heading("id", text="序号")
        self.course_tree.heading("priority", text="优先级")
        self.course_tree.heading("name", text="课程名称")
        self.course_tree.heading("teacher", text="授课教师")
        self.course_tree.heading("course_id", text="课程ID")
        self.course_tree.heading("schedule", text="上课安排")
        
        self.course_tree.column("id", width=50)
        self.course_tree.column("priority", width=50)
        self.course_tree.column("name", width=200)
        self.course_tree.column("teacher", width=100)
        self.course_tree.column("id", width=100)
        self.course_tree.column("schedule", width=200)
        
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.course_tree.yview)
        self.course_tree.configure(yscrollcommand=scrollbar.set)
        
        self.course_tree.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar.pack(side=tk.RIGHT, fill="y")
        
        self.remove_course = ttk.Button(list_frame, text="删除选中课程", command=self.remove_course)
        self.remove_course.pack(pady=5)

        self.update_course_list()

    def on_treeview_configure(self, event):
        """优化表格重绘，减少窗口调整大小时的卡顿"""
        self.course_tree.grid_remove()
        
        if hasattr(self, '_treeview_after_id'):
            self.root.after_cancel(self._treeview_after_id)
        
        self._treeview_after_id = self.root.after(50, self._restore_treeview)

    def _restore_treeview(self):
        """恢复表格重绘"""
        self.course_tree.pack()
        self.course_tree.update_idletasks()

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
    def start_login(self):
        global login_success, stop_display
        
        login_success = False
        stop_display = False
        
        self.login_btn.config(state=tk.DISABLED)
        self.status_var.set("正在启动登录流程...")
        
        login_thread = threading.Thread(target=self.login_process, daemon=True)
        login_thread.start()
        
    def login_process(self):
        global driver, login_success, final_cookies_dict
        try:
            print("🔄 正在检查并下载匹配的 ChromeDriver...")
            self.status_var.set("正在下载/匹配 ChromeDriver...")

            driver_path = ChromeDriverManager().install()
            print(f"✅ 使用 ChromeDriver: {driver_path}")

            service = Service(driver_path)
            driver = webdriver.Chrome(service=service, options=self.chrome_options)

            driver.get("https://byyt.ustb.edu.cn/oauth/login/code")
            print("🌐 已进入登录页面")
            self.status_var.set("等待二维码生成...")
            time.sleep(0.2)

            display_thread = threading.Thread(target=self.display_qr_thread, daemon=True)
            display_thread.start()

            monitor_thread = threading.Thread(target=self.monitor_login_status, daemon=True)
            monitor_thread.start()

            while not login_success and not stop_display:
                time.sleep(0.1)

            driver.quit()

            if login_success:
                self.status_var.set("登录成功！")
                messagebox.showinfo("登录成功", "您已成功登录！")
                self.tab_control.tab(1, state="normal")
                self.tab_control.select(1)
            else:
                self.status_var.set("登录失败或已取消")
                messagebox.showerror("登录失败", "登录过程失败或被取消")

        except Exception as e:
            error_msg = f"登录出错：{str(e)}"
            print(f"❌ {error_msg}")
            self.status_var.set(error_msg)
            messagebox.showerror("错误", error_msg)
        finally:
            self.login_btn.config(state=tk.NORMAL)
            
    def display_qr_thread(self):
        global current_img_data, stop_display
        last_update = time.time()
        MIN_UPDATE_INTERVAL = 0.2
        
        while not stop_display:
            if getattr(self, 'window_minimized', False):
                time.sleep(1.0)
                continue
                
            if current_img_data is not None:
                try:
                    current_time = time.time()
                    if current_time - last_update >= MIN_UPDATE_INTERVAL:
                        tk_image = ImageTk.PhotoImage(current_img_data)
                        self.root.after(100, lambda: self.update_qr_image(tk_image))
                        last_update = current_time
                except Exception as e:
                    print(f"显示二维码时出错：{e}")
            
            time.sleep(0.05)
            
    def update_qr_image(self, image):
        try:
            self.qr_label.config(image=image)
            self.qr_label.image = image
            self.status_var.set("请使用手机扫描二维码")
        except Exception as e:
            print(f"更新二维码时出错：{e}")
            
    def monitor_login_status(self):
        global current_img_data, qr_image_url, stop_display, login_success, final_cookies_dict, driver
        wait = WebDriverWait(driver, 5)
        print("🔄 正在监控二维码与登录状态...")
        while not login_success and not stop_display:
            try:
                try:
                    iframe = driver.find_element(By.TAG_NAME, "iframe")
                    driver.switch_to.frame(iframe)
                    qr_img = driver.find_element(By.ID, "qrimg")
                    new_src = qr_img.get_attribute("src")
                    if new_src and new_src != qr_image_url:
                        qr_image_url = new_src
                        print("🖼️ 二维码已更新")
                        response = requests.get(qr_image_url, headers={"Referer": driver.current_url}, timeout=5)
                        if response.status_code == 200:
                            current_img_data = Image.open(BytesIO(response.content))
                    driver.switch_to.default_content()
                except Exception as e:
                    driver.switch_to.default_content()
                    
                if "https://byyt.ustb.edu.cn/authentication/main" in driver.current_url:
                    print(f"\n🎉 检测到登录成功！")
                    self.status_var.set("登录成功！正在获取 Cookie...")
                    time.sleep(0.1)
                    cookies = driver.get_cookies()
                    final_cookies_dict = {c['name']: c['value'] for c in cookies}
                    print(f"\n🔑 已获取 {len(final_cookies_dict)} 个 Cookie")
                    print(final_cookies_dict)
                    login_success = True
                    self.start_online_keepalive()
                    break
                time.sleep(0.2)
            except Exception as e:
                print("🟡 监控过程中出现异常：", str(e))
                time.sleep(2)
        stop_display = True
        
    def add_course(self):
        global final_cookies_dict, course_data_list
        
        # 检查是否已输入抢课人员
        if not self.current_student_name:
            messagebox.showerror("错误", "请先输入抢课人员姓名")
            return
        
        if not final_cookies_dict:
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
        
    def query_course_info(self, course_id, p_xn, p_xq, p_xnxq, p_dqxn, p_dqxq, p_dqxnxq, p_xkfsdm, priority):
        global final_cookies_dict, course_data_list
        
        semester = f"{p_xn}{p_xq}"
        cache_key = f"{semester}_{course_id}"
        
        cached_course = self.get_cached_course(course_id, semester)
        if cached_course:
            print(f"ℹ️ 从缓存中获取课程 {course_id} 的信息")
            course_name = cached_course["name"]
            teacher = cached_course["teacher"]
            p_id = cached_course["p_id"]
            p_kclb = cached_course["p_kclb"]
            course_schedule = cached_course["schedule"]
            
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
                "id": len(course_data_list) + 1
            }
            course_data_list.append(course_data)
            
            # 添加课程后立即保存
            self.save_course_list()
            
            self.root.after(0, lambda: self.update_course_list())
            self.root.after(0, lambda: messagebox.showinfo("成功", f"已添加课程：{course_name}（来自缓存）"))
            self.root.after(0, lambda: self.status_var.set("课程添加成功"))
            return
        
        try:
            session = requests.Session()
            session.cookies.update(final_cookies_dict)
            session.headers.update({
                "accept": "*/*",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                "cache-control": "no-cache",
                "connection": "keep-alive",
                "content-length": "537",
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
                "x-requested-with": "XMLHttpRequest"
            })
            
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
            global course_id_count
            
            for course in course_info:
                course_id_count+=1
                course_name = course["kcmc"]
                teacher = course["dgjsmc"]
                p_id = course["id"]
                p_kclb = course.get("kclbdm", "2301")
                kcxx_html = course["kcxx"]
                soup = BeautifulSoup(kcxx_html, 'html.parser')
                tag_cyan = soup.find('div', class_='ivu-tag-cyan')
                if tag_cyan:
                    tag_text = tag_cyan.find('span', class_='ivu-tag-text')
                    if tag_text:
                        schedule = tag_text.get_text(strip=True)
                course_schedule=schedule
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
                    "id": course_id_count
                }
                course_data_list.append(course_data)

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
                self.root.after(0, lambda: messagebox.showinfo("成功", f"已添加课程：{course_name}"))
                self.root.after(0, lambda: self.status_var.set("课程添加成功"))

            
        except Exception as e:
            error_msg = f"查询课程 {course_id} 时出错：{e}"
            print(f"❌ {error_msg}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
            
    def update_course_list(self):
        try:
            for item in self.course_tree.get_children():
                self.course_tree.delete(item)
        except Exception as e:
            print(f"清空课程列表时出错：{e}")
        
        sorted_courses = sorted(course_data_list, key=lambda x: x["priority"])
        
        for course in sorted_courses:
            self.course_tree.insert("", "end", values=(
                course["id"],
                course["priority"],
                course["name"],
                course["teacher"],
                course["course_id"],
                course["schedule"]
            ))
            
    def remove_course(self):
        global course_data_list

        selected_items = self.course_tree.selection()
        if not selected_items:
            messagebox.showwarning("警告", "请先选择要删除的课程")
            return

        deleted_names = []
        ids_to_remove = []

        for item in selected_items:
            values = self.course_tree.item(item, "values")
            course_id = int(values[0])  # 第一列是 id
            course_name = values[2]     # 第三列是课程名称
            ids_to_remove.append(course_id)
            deleted_names.append(course_name)

        # 弹出确认框
        if len(deleted_names) > 1:
            confirm = messagebox.askyesno("确认删除", f"确定要删除以下 {len(deleted_names)} 门课程吗？\n\n" + "\n".join(deleted_names))
        else:
            confirm = messagebox.askyesno("确认删除", f"确定要删除课程：{deleted_names[0]} 吗？")

        if not confirm:
            return

        # 从 course_data_list 中移除对应课程
        course_data_list = [c for c in course_data_list if c["id"] not in ids_to_remove]

        # 【核心修改】删除操作后立即保存当前人员的课程列表
        self.save_course_list()

        # 刷新表格
        self.update_course_list()

        # 提示删除成功
        if len(deleted_names) > 1:
            messagebox.showinfo("成功", f"已删除 {len(deleted_names)} 门课程")
        else:
            messagebox.showinfo("成功", f"已删除课程：{deleted_names[0]}")

    def start_auto_selection(self):
        global course_data_list, final_cookies_dict, selection_running, stop_selection

        if not course_data_list:
            messagebox.showerror("错误", "尚未添加任何课程")
            return
            
        if not final_cookies_dict:
            messagebox.showerror("错误", "请先登录")
            return
        
        # 检查是否选择了人员（虽然add时检查过，但防止清空）
        if not self.current_student_name:
            messagebox.showerror("错误", "请确认抢课人员姓名")
            return
            
        course_data_list.sort(key=lambda x: x["priority"])
        
        msg = f"即将开始为【{self.current_student_name}】自动选课，课程如下：\n\n"
        for i, course in enumerate(course_data_list):
            msg += f"{i+1}. [优先级 {course['priority']}] {course['name']} ({course['teacher']})\n"
        msg += "\n是否继续？"
        
        if not messagebox.askyesno("确认", msg):
            return
            
        # === 设置状态 ===
        selection_running = True
        stop_selection = False

        # === 禁用无关按钮 ===
        self.add_course_btn.config(state=tk.DISABLED)
        self.start_auto_btn.config(state=tk.DISABLED)
        self.remove_course.config(state=tk.DISABLED)
        self.student_name_var.set(self.current_student_name) # 锁定输入框显示
        self.stop_auto_btn.config(state=tk.NORMAL)
        
        # 锁定人员切换
        self.student_switch_lock = True

        selection_thread = threading.Thread(target=self.auto_selection_process, daemon=True)
        selection_thread.start()
        self.tab_control.select(2)

    def restore_buttons(self):
        """恢复按钮状态"""
        self.add_course_btn.config(state=tk.NORMAL)
        self.start_auto_btn.config(state=tk.NORMAL)
        self.remove_course.config(state=tk.NORMAL)
        self.stop_auto_btn.config(state=tk.DISABLED)
        self.student_switch_lock = False # 解锁人员切换
        self.status_var.set("抢课结束，按钮已恢复")

    def auto_selection_process(self):
        global course_data_list, final_cookies_dict, selection_running, stop_selection

        print(f"\n🚀 开始为 {self.current_student_name} 自动选课...")
        self.status_var.set("自动选课已启动")

        session = requests.Session()
        session.cookies.update(final_cookies_dict)
        session.headers.update({
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Host": "byyt.ustb.edu.cn",
            "Origin": "https://byyt.ustb.edu.cn",
            "Pragma": "no-cache",
            "Referer": "https://byyt.ustb.edu.cn/Xsxk/query/1",
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36 Edg/139.0.0.0",
            "X-Requested-With": "XMLHttpRequest"
        })

        url = "https://byyt.ustb.edu.cn/Xsxk/addGouwuche"
        count = 0
        success = False

        try:
            # 按优先级分组
            priority_groups = defaultdict(list)
            for course in sorted(course_data_list, key=lambda x: x["priority"]):
                priority_groups[course["priority"]].append(course)

            # 记录已因“冲突”或“已满+不重试”而放弃的课程 ID
            failed_course_ids = set()

            # 按优先级从高到低处理
            for priority in sorted(priority_groups.keys()):
                courses = priority_groups[priority]
                print(f"🎯 开始抢优先级 {priority} 的课程，共 {len(courses)} 门：")
                for c in courses:
                    print(f"   → {c['name']} ({c['teacher']})")

                if stop_selection:
                    break

                while not success and not stop_selection:
                    current_time = datetime.now()
                    minute = current_time.minute
                    if minute == 59:
                        time.sleep(max(58.5-current_time.second,0))
                        continue

                    any_active_in_priority = False  # 当前优先级是否有可抢的课

                    for course in courses:
                        course_id = course["data"]["p_id"]

                        # 如果这门课已经失败过（冲突或已满且不重试），跳过
                        if course_id in failed_course_ids:
                            continue

                        time.sleep(1.5)
                        if stop_selection:
                            break

                        try:
                            response = session.post(url, data=course["data"])
                            count += 1
                            text = response.text.strip()

                            print(f"[{count}] 优先级 {priority} | 课程：{course['name']} | 状态：{response.status_code} | 响应：{text[:160]}...")

                            if "success" in text or "成功" in text:
                                success_msg = f"🎉 选课成功！课程：{course['name']} | 教师：{course['teacher']}"
                                print(success_msg)
                                if self.stop_on_success_var.get():
                                    success = True
                                    self.root.after(0, lambda msg=success_msg: messagebox.showinfo("成功", msg))
                                    self.root.after(0, lambda: self.status_var.set("选课成功！"))
                                    break
                                else:
                                    print("⏩ 选课成功，但将继续尝试其他课程...")
                                    failed_course_ids.add(course_id)
                                    continue
                                break
                            elif "冲突" in text:
                                print(f"⛔ 时间冲突，放弃课程：{course['name']}（不再尝试）")
                                failed_course_ids.add(course_id)
                                continue
                            elif "不符合" in text:
                                print(f"⛔ 不符合要求，放弃课程：{course['name']}（不再尝试）")
                                failed_course_ids.add(course_id)
                                continue
                            elif "full" in text or "已满" in text:
                                retry_enabled = self.retry_full_var.get()
                                if not retry_enabled:
                                    print(f"🚫 课程已满且“不重试”，放弃课程：{course['name']}（不再尝试）")
                                    failed_course_ids.add(course_id)
                                else:
                                    print(f"⏸️ 课程已满：{course['name']}，等待下次重试...")
                                    any_active_in_priority = True
                                continue
                            else:
                                print(f"⚠️ 未知响应（可能可抢）：{text[:100]}...")
                                any_active_in_priority = True

                        except Exception as e:
                            print(f"[{count}] 请求失败（{course['name']}）：{e}")
                            any_active_in_priority = True

                    # 检查是否当前优先级还有可尝试的课程
                    remaining_courses = [c for c in courses if c["data"]["p_id"] not in failed_course_ids]
                    if not remaining_courses:
                        print(f"⏸️ 优先级 {priority} 所有课程均已失败或放弃，进入下一优先级...")
                        break

                    if not any_active_in_priority and not success:
                        print(f"⏸️ 优先级 {priority} 无活跃课程可抢，进入下一优先级...")
                        break

                if success or stop_selection:
                    break

            if not success:
                print("🔚 所有课程均已满或失败，抢课结束。")
                self.root.after(0, lambda: self.status_var.set("所有课程均已满或失败，抢课结束"))

        except Exception as e:
            error_msg = f"自动选课出错：{e}"
            print(f"❌ {error_msg}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
            self.root.after(0, lambda: self.status_var.set("选课失败"))

        finally:
            self.root.after(0, self.restore_buttons)
            selection_running = False
            stop_selection = False

    def stop_auto_selection(self):
        global selection_running, stop_selection
        if not selection_running:
            return

        stop_selection = True
        self.status_var.set("正在停止抢课...")
        print("🛑 用户请求停止抢课")

    def start_online_keepalive(self):
        """启动保持在线的后台线程"""
        global online_thread_running
        online_thread_running = True
        self.online_thread = threading.Thread(target=self.online_keepalive_thread, daemon=True)
        self.online_thread.start()
        print("🔄 已启动会话保持线程，每10分钟发送一次online请求")
        
    def stop_online_keepalive(self):
        """停止保持在线的后台线程"""
        global online_thread_running
        online_thread_running = False
        if hasattr(self, 'online_thread') and self.online_thread and self.online_thread.is_alive():
            print("🛑 等待会话保持线程结束...")
            self.online_thread.join(timeout=2.0)
            if self.online_thread.is_alive():
                print("⚠️ 会话保持线程未能正常结束")
            else:
                print("✅ 会话保持线程已结束")
                
    def online_keepalive_thread(self):
        """保持在线的后台线程"""
        global online_thread_running, login_success
        
        print("⏳ 会话保持线程已启动，等待10分钟后发送首次请求...")
        while online_thread_running:
            # 等待10分钟
            for _ in range(300):  # 600秒 = 10分钟
                if not online_thread_running:
                    break
                time.sleep(1)
            
            if not online_thread_running:
                break
                
            # 检查是否已登录
            if login_success:
                self.send_online_request()
            else:
                print("ℹ️ 未登录，跳过online请求")
                
    def send_online_request(self):
        """发送保持在线的请求"""
        global final_cookies_dict, login_success
        try:
            url = "https://byyt.ustb.edu.cn/component/online"
            headers = {
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Length": "0",
                "Host": "byyt.ustb.edu.cn",
                "Origin": "https://byyt.ustb.edu.cn",
                "Pragma": "no-cache",
                "Referer": "https://byyt.ustb.edu.cn/authentication/main",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36 Edg/139.0.0.0",
                "sec-ch-ua": '"Not;A=Brand";v="99", "Microsoft Edge";v="139", "Chromium";v="139"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
            }
            
            session = requests.Session()
            session.cookies.update(final_cookies_dict)
            session.headers.update(headers)
            
            response = session.post(url)
            if response.status_code == 200:
                print("✅ 成功发送online请求，会话保持活跃")
            else:
                print(f"⚠️ online请求失败，状态码：{response.status_code}")
        except Exception as e:
            print(f"❌ 发送online请求时出错：{str(e)}")
            if "401" in str(e) or "403" in str(e):
                print("⚠️ 可能会话已过期，建议重新登录")

# 启动应用
if __name__ == "__main__":
    root = tk.Tk()
    app = CourseSelectionApp(root)
    root.mainloop()

