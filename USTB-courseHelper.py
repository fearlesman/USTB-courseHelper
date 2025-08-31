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
online_thread_running = False  # 是否正在运行online线程
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
        self.course_list_file = os.path.join(os.path.dirname(__file__), "course_list.json")

         # 加载课程缓存
        self.load_course_cache()
        # 加载保存的课程列表
        self.load_saved_course_list()

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
        self.save_course_cache()  # 立即保存到文件
    def load_saved_course_list(self):
        """加载保存的课程列表"""
        global course_data_list, course_id_count
        
        try:
            if os.path.exists(self.course_list_file):
                with open(self.course_list_file, 'r', encoding='utf-8') as f:
                    saved_list = orjson.loads(f.read())
                
                # 验证数据格式
                if isinstance(saved_list, list) and len(saved_list) > 0:
                    # 重置ID计数
                    course_id_count = len(saved_list)
                    
                    # 重新设置ID并验证数据结构
                    valid_courses = []
                    for i, course in enumerate(saved_list):
                        # 确保必要字段存在
                        if all(key in course for key in ["priority", "data", "name", "teacher", "course_id", "schedule"]):
                            course["id"] = i + 1
                            valid_courses.append(course)
                    
                    if valid_courses:
                        course_data_list = valid_courses
                        self.update_course_list()
                        print(f"✅ 已加载 {len(course_data_list)} 门保存的课程")
                    else:
                        print("⚠️ 保存的课程列表数据无效，已清空")
                else:
                    print("⚠️ 保存的课程列表为空或格式错误")
        except Exception as e:
            print(f"⚠️ 加载课程列表失败: {e}")

    def save_course_list(self):
        """保存课程列表"""
        global course_data_list
        
        try:
            # 创建副本，不保存id字段（因为id会在下次启动时重新生成）
            save_list = []
            for course in course_data_list:
                save_course = course.copy()
                # 移除id，因为下次启动会重新生成
                if "id" in save_course:
                    del save_course["id"]
                save_list.append(save_course)
            
            with open(self.course_list_file, 'wb') as f:
                f.write(orjson.dumps(save_list, option=orjson.OPT_INDENT_2))
            print(f"💾 已保存 {len(course_data_list)} 门课程")
        except Exception as e:
            print(f"⚠️ 保存课程列表失败: {e}")

    def auto_save_course_list(self):
        """自动保存课程列表（用于异常关闭）"""
        while True:
            time.sleep(30)  # 每30秒自动保存一次
            if selection_running or stop_selection:
                continue  # 选课过程中不自动保存
            self.save_course_list()

    def on_closing(self):
        """处理窗口关闭事件"""
        global stop_display, stop_selection
        # 停止显示二维码
        stop_display = True
        # 停止选课
        if selection_running:
            stop_selection = True
            print("🛑 正在停止选课进程...")
            # 等待选课进程停止
            time.sleep(1)
        
        # 停止会话保持线程
        self.stop_online_keepalive()
        
        # 保存课程列表
        self.save_course_list()
        print("👋 程序即将关闭，已保存数据")
        self.root.destroy()

    def configure_browser(self):
        # 浏览器选项
        self.chrome_options = Options()
        self.chrome_options.add_argument("--headless=new")  # 可选：无头模式
        self.chrome_options.add_argument("--disable-gpu")
        self.chrome_options.add_argument("--no-sandbox")
        self.chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        self.chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        
    def setup_login_tab(self):
        login_frame = ttk.Frame(self.login_tab, style="TFrame")
        login_frame.pack(padx=20, pady=20, fill="both", expand=True)
        
        # 标题
        title_label = ttk.Label(login_frame, text="北京科技大学选课助手登录", style="Header.TLabel")
        title_label.pack(pady=(0, 20))
        
        # 二维码框架
        self.qr_frame = ttk.Frame(login_frame, style="TFrame")
        self.qr_frame.pack(pady=10)
        
        self.qr_label = ttk.Label(self.qr_frame, text="等待二维码生成...")
        self.qr_label.pack(pady=20)
        
        # 状态
        self.status_var = tk.StringVar()
        self.status_var.set("等待操作...")
        status_label = ttk.Label(login_frame, textvariable=self.status_var, foreground="blue")
        status_label.pack(pady=10)
        
        # 按钮
        btn_frame = ttk.Frame(login_frame, style="TFrame")
        btn_frame.pack(pady=20)
        
        self.login_btn = ttk.Button(btn_frame, text="开始登录", command=self.start_login)
        self.login_btn.pack(side=tk.LEFT, padx=5)

    def update_console(self):
        while True:
            try:
                messages = []
                for _ in range(10):  # 每次最多处理10条消息
                    if not log_queue.empty():
                        messages.append(log_queue.get_nowait())
                    else:
                        break
                
                if messages and hasattr(self, 'console_output'):
                    self.console_output.config(state=tk.NORMAL)
                    for msg in messages:
                        self.console_output.insert(tk.END, '\n' + msg)
                        self.console_output.see(tk.END)  # 滚动到最新消息
                    
                    # 限制显示行数，防止内存占用过高
                    lines = self.console_output.get(1.0, tk.END).count('\n')
                    if lines > 20:  # 只保留最后20行
                        self.console_output.delete(1.0, f"{lines-100}.0")
                    
                    self.console_output.config(state=tk.DISABLED)
            except Exception as e:
                print(f"更新控制台输出时出错：{e}")
    def setup_course_tab(self):
        course_frame = ttk.Frame(self.course_tab, style="TFrame")
        course_frame.pack(padx=20, pady=20, fill="both", expand=True)
        # 标题
        title_label = ttk.Label(course_frame, text="抢课设置", style="Header.TLabel")
        title_label.pack(pady=(0, 20))

        # 创建一个主框架来容纳"抢课人员"和控制台输出
        main_name_frame = ttk.Frame(course_frame, style="TFrame")
        main_name_frame.pack(fill="x", pady=5)
        
        # 左侧：抢课人员设置
        name_frame = ttk.Frame(main_name_frame, style="TFrame")
        name_frame.pack(side=tk.LEFT, fill="y", expand=False)
        
        ttk.Label(name_frame, text="抢课人员：").pack(side=tk.LEFT, padx=(0, 10))
        self.student_name_var = tk.StringVar(value="") 
        ttk.Entry(name_frame, textvariable=self.student_name_var, width=20).pack(side=tk.LEFT)
        
        # 右侧：控制台输出展示框
        console_frame = ttk.LabelFrame(main_name_frame, text="实时控制台输出", style="TFrame")
        console_frame.pack(side=tk.RIGHT, fill="both", expand=True, padx=(20, 0))
        
        # 创建控制台输出文本区域
        self.console_output = scrolledtext.ScrolledText(console_frame, wrap=tk.WORD, height=4)
        self.console_output.pack(fill="both", expand=True, padx=5, pady=5)
        self.console_output.config(state=tk.DISABLED)
        
        # 队列处理
        self.console_queue = queue.Queue()
        self.update_console_thread = threading.Thread(target=self.update_console, daemon=True)
        self.update_console_thread.start()

        # 课程输入框架
        input_frame = ttk.Frame(course_frame, style="TFrame")
        input_frame.pack(fill="x", pady=10)
        
        # 课程类型
        type_frame = ttk.Frame(input_frame, style="TFrame")
        type_frame.pack(fill="x", pady=5)
        # 是否在选到一门课后停止
        stop_on_success_frame = ttk.Frame(input_frame, style="TFrame")
        stop_on_success_frame.pack(fill="x", pady=5)
        self.stop_on_success_var = tk.BooleanVar(value=True)  # 默认开启（选到一门就停止）
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
        
        # 课程ID
        id_frame = ttk.Frame(input_frame, style="TFrame")
        id_frame.pack(fill="x", pady=5)
        
        ttk.Label(id_frame, text="课程ID：").pack(side=tk.LEFT, padx=(0, 10))
        self.course_id_var = tk.StringVar()
        ttk.Entry(id_frame, textvariable=self.course_id_var, width=20).pack(side=tk.LEFT)
        
        # 优先级
        priority_frame = ttk.Frame(input_frame, style="TFrame")
        priority_frame.pack(fill="x", pady=5)
        
        ttk.Label(priority_frame, text="优先级：").pack(side=tk.LEFT, padx=(0, 10))
        self.priority_var = tk.StringVar(value="1")
        ttk.Spinbox(priority_frame, from_=1, to=99, textvariable=self.priority_var, width=5).pack(side=tk.LEFT)
        ttk.Label(priority_frame, text="（数字越小，优先级越高）").pack(side=tk.LEFT, padx=10)
        
        # 学期
        semester_frame = ttk.Frame(input_frame, style="TFrame")
        semester_frame.pack(fill="x", pady=5)
        
        ttk.Label(semester_frame, text="学期：").pack(side=tk.LEFT, padx=(0, 10))
        self.semester_var = tk.StringVar(value="2025-2026-1")
        ttk.Entry(semester_frame, textvariable=self.semester_var, width=20).pack(side=tk.LEFT)
        
         # 是否持续重试已满课程
        retry_frame = ttk.Frame(input_frame, style="TFrame")
        retry_frame.pack(fill="x", pady=5)
        
        self.retry_full_var = tk.BooleanVar(value=True)  # 默认开启
        retry_check = ttk.Checkbutton(retry_frame, text="课程已满时持续重试", variable=self.retry_full_var)
        retry_check.pack(side=tk.LEFT)
        ttk.Label(retry_frame, text="（关闭后，一旦返回“已满”将不再尝试此课程）").pack(side=tk.LEFT, padx=(5, 0))
        # 按钮
        btn_frame = ttk.Frame(course_frame, style="TFrame")
        btn_frame.pack(pady=10)
        
        self.add_course_btn = ttk.Button(btn_frame, text="添加课程", command=self.add_course)
        self.add_course_btn.pack(side=tk.LEFT, padx=5)

        self.start_auto_btn = ttk.Button(btn_frame, text="开始自动选课", command=self.start_auto_selection)
        self.start_auto_btn.pack(side=tk.LEFT, padx=5)

        self.stop_auto_btn = ttk.Button(btn_frame, text="停止抢课", command=self.stop_auto_selection, state=tk.DISABLED)
        self.stop_auto_btn.pack(side=tk.LEFT, padx=5)
        
        # 课程列表
        list_frame = ttk.Frame(course_frame, style="TFrame")
        list_frame.pack(fill="both", expand=True, pady=10)
        
        ttk.Label(list_frame, text="已添加课程：", style="Header.TLabel").pack(anchor="w")
        
        # 创建表格
        columns = ("id","priority", "name", "teacher", "course_id", "schedule")
        self.course_tree = ttk.Treeview(
            list_frame, 
            columns=columns, 
            show="headings",
            selectmode="extended"  # 支持 Ctrl/Shift 多选
        )
                
        # 定义列名
        self.course_tree.heading("id", text="序号")
        self.course_tree.heading("priority", text="优先级")
        self.course_tree.heading("name", text="课程名称")
        self.course_tree.heading("teacher", text="授课教师")
        self.course_tree.heading("course_id", text="课程ID")
        self.course_tree.heading("schedule", text="上课安排")
        
        
        # 设置列宽
        self.course_tree.column("id", width=50)
        self.course_tree.column("priority", width=50)
        self.course_tree.column("name", width=200)
        self.course_tree.column("teacher", width=100)
        self.course_tree.column("id", width=100)
        self.course_tree.column("schedule", width=200)
        
        # 添加滚动条
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.course_tree.yview)
        self.course_tree.configure(yscrollcommand=scrollbar.set)
        
        self.course_tree.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar.pack(side=tk.RIGHT, fill="y")
        
        # 删除按钮
        self.remove_course = ttk.Button(list_frame, text="删除选中课程", command=self.remove_course)
        self.remove_course.pack(pady=5)

        self.update_course_list()
    
    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
    def start_login(self):
        global login_success, stop_display
        
        # 重置标志
        login_success = False
        stop_display = False
        
        self.login_btn.config(state=tk.DISABLED)
        self.status_var.set("正在启动登录流程...")
        
        # 启动登录线程
        login_thread = threading.Thread(target=self.login_process, daemon=True)
        login_thread.start()
        
    def login_process(self):
        global driver, login_success, final_cookies_dict
        
        try:
            # 初始化浏览器
            driver = webdriver.Chrome(service=self.service, options=self.chrome_options)
            
            # 打开首页
            home_url = "https://byyt.ustb.edu.cn/oauth/login/code"
            driver.get(home_url.strip())
            print("🌐 已进入登录页面")
            self.status_var.set("等待二维码生成...")
            time.sleep(0.2)
            
            # 启动二维码显示线程
            display_thread = threading.Thread(target=self.display_qr_thread, daemon=True)
            display_thread.start()
            
            # 启动登录状态监控线程
            monitor_thread = threading.Thread(target=self.monitor_login_status, daemon=True)
            monitor_thread.start()
            
            # 等待登录结果
            while not login_success and not stop_display:
                time.sleep(0.1)
            # print("🛑 正在关闭浏览器...")

            driver.quit()
            if login_success:
                self.status_var.set("登录成功！")
                messagebox.showinfo("登录成功", "您已成功登录！")
                
                # 启用选课选项卡
                self.tab_control.tab(1, state="normal")
                self.tab_control.select(1)
                
            else:
                self.status_var.set("登录失败或已取消")
                messagebox.showerror("登录失败", "登录过程失败或被取消")
                
            # 关闭浏览器
           
            
        except Exception as e:
            error_msg = f"登录出错：{str(e)}"
            print(f"❌ {error_msg}")
            self.status_var.set(error_msg)
            messagebox.showerror("错误", error_msg)
        finally:
            self.login_btn.config(state=tk.NORMAL)
            
    def display_qr_thread(self):
        global current_img_data, stop_display
        
        while not stop_display:
            if current_img_data is not None:
                try:
                    tk_image = ImageTk.PhotoImage(current_img_data)
                    self.root.after(100, lambda: self.update_qr_image(tk_image))
                except Exception as e:
                    print(f"显示二维码时出错：{e}")
            time.sleep(0.2)
            
    def update_qr_image(self, image):
        try:
            self.qr_label.config(image=image)
            self.qr_label.image = image  # 保持引用
            self.status_var.set("请使用手机扫描二维码")
        except Exception as e:
            print(f"更新二维码时出错：{e}")
            
    def monitor_login_status(self):
        global current_img_data, qr_image_url, stop_display, login_success, final_cookies_dict, driver
        wait = WebDriverWait(driver, 5)
        print("🔄 正在监控二维码与登录状态...")
        while not login_success and not stop_display:
            try:
                # 检查是否在 iframe（二维码阶段）
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
                # 检查是否跳转到目标页面
                if "https://byyt.ustb.edu.cn/authentication/main" in driver.current_url:
                    print(f"\n🎉 检测到登录成功！")
                    self.status_var.set("登录成功！正在获取 Cookie...")
                    time.sleep(0.1)
                    # 提取关键 Cookie
                    cookies = driver.get_cookies()
                    final_cookies_dict = {c['name']: c['value'] for c in cookies}
                    print(f"\n🔐 已获取 {len(final_cookies_dict)} 个 Cookie")
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
        
        # 检查是否已登录
        if not final_cookies_dict:
            messagebox.showerror("错误", "请先登录")
            return
            
        # 获取输入值
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
        
        # 查询课程信息
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
        
        # 构建缓存键 - 使用学期格式：p_xn+p_xq (如: "2023-20241")
        semester = f"{p_xn}{p_xq}"
        cache_key = f"{semester}_{course_id}"
        
        # 检查缓存
        cached_course = self.get_cached_course(course_id, semester)
        if cached_course:
            print(f"ℹ️ 从缓存中获取课程 {course_id} 的信息")
            # 使用缓存数据
            course_name = cached_course["name"]
            teacher = cached_course["teacher"]
            p_id = cached_course["p_id"]
            p_kclb = cached_course["p_kclb"]
            course_schedule = cached_course["schedule"]
            
            # 保存课程数据
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
                p_kclb = course.get("kclbdm", "2301")  # 默认值
                kcxx_html = course["kcxx"]
                soup = BeautifulSoup(kcxx_html, 'html.parser')
                # 查找 class 包含 "ivu-tag-cyan" 的 div（上课信息容器）
                tag_cyan = soup.find('div', class_='ivu-tag-cyan')
                if tag_cyan:
                    # 在容器内查找 class="ivu-tag-text" 的 span
                    tag_text = tag_cyan.find('span', class_='ivu-tag-text')
                    if tag_text:
                        # 提取 span 内部的文本（包含目标上课信息）
                        schedule = tag_text.get_text(strip=True)
                course_schedule=schedule
                print(f"✅ 找到课程：{course_name} | 教师：{teacher} | ID：{p_id} | 课程安排：{course_schedule}")
                # 保存课程数据
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

                self.root.after(0, lambda: self.update_course_list())
                self.root.after(0, lambda: messagebox.showinfo("成功", f"已添加课程：{course_name}"))
                self.root.after(0, lambda: self.status_var.set("课程添加成功"))

            
        except Exception as e:
            error_msg = f"查询课程 {course_id} 时出错：{e}"
            print(f"❌ {error_msg}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
            
    def update_course_list(self):
        # 清空列表
        try:
            for item in self.course_tree.get_children():
                self.course_tree.delete(item)
        except Exception as e:
            print(f"清空课程列表时出错：{e}")
        
        # 按优先级排序
        sorted_courses = sorted(course_data_list, key=lambda x: x["priority"])
        
        # 添加到表格
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

        # 获取要删除的课程名称用于提示
        deleted_names = []
        ids_to_remove = []

        for item in selected_items:
            values = self.course_tree.item(item, "values")
            course_id = int(values[0])  # 第一列是 id
            course_name = values[2]     # 第三列是课程名称
            ids_to_remove.append(course_id)
            deleted_names.append(course_name)

        # 可选：弹出确认框
        if len(deleted_names) > 1:
            confirm = messagebox.askyesno("确认删除", f"确定要删除以下 {len(deleted_names)} 门课程吗？\n\n" + "\n".join(deleted_names))
        else:
            confirm = messagebox.askyesno("确认删除", f"确定要删除课程：{deleted_names[0]} 吗？")

        if not confirm:
            return

        # 从 course_data_list 中移除对应课程
        course_data_list = [c for c in course_data_list if c["id"] not in ids_to_remove]

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
            
        course_data_list.sort(key=lambda x: x["priority"])
        
        msg = "即将开始自动选课，课程如下：\n\n"
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
        self.remove_course.config(state=tk.DISABLED)  # 如果你有这个按钮引用
        self.stop_auto_btn.config(state=tk.NORMAL)

        selection_thread = threading.Thread(target=self.auto_selection_process, daemon=True)
        selection_thread.start()
        self.tab_control.select(2)

    def restore_buttons(self):
        """恢复按钮状态"""
        self.add_course_btn.config(state=tk.NORMAL)
        self.start_auto_btn.config(state=tk.NORMAL)
        self.remove_course.config(state=tk.NORMAL)
        self.stop_auto_btn.config(state=tk.DISABLED)
        self.status_var.set("抢课结束，按钮已恢复")

    def auto_selection_process(self):
            global course_data_list, final_cookies_dict, selection_running, stop_selection

            print("\n🚀 开始自动选课...")
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
                            time.sleep(58.5)
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
                                    # 关键修改：根据用户选择决定是否继续
                                    if self.stop_on_success_var.get():
                                        success = True  # 原有逻辑：设置成功标志
                                        self.root.after(0, lambda msg=success_msg: messagebox.showinfo("成功", msg))
                                        self.root.after(0, lambda: self.status_var.set("选课成功！"))
                                        break  # 跳出当前优先级的课程循环
                                    else:
                                        print("⏩ 选课成功，但将继续尝试其他课程...")
                                        # 不设置success标志，继续尝试其他课程
                                        # 可以将这门课标记为已成功，避免重复尝试
                                        failed_course_ids.add(course_id)
                                        continue
                                    break
                                elif "冲突" in text:
                                    print(f"⛔ 时间冲突，放弃课程：{course['name']}（不再尝试）")
                                    failed_course_ids.add(course_id)  # 标记为失败，不再尝试
                                    continue  # 继续尝试同优先级其他课程
                                elif "不符合" in text:
                                    print(f"⛔ 不符合要求，放弃课程：{course['name']}（不再尝试）")
                                    failed_course_ids.add(course_id)  # 标记为失败，不再尝试
                                    continue  # 继续尝试同优先级其他课程
                                elif "full" in text or "已满" in text:
                                    retry_enabled = self.retry_full_var.get()
                                    if not retry_enabled:
                                        print(f"🚫 课程已满且“不重试”，放弃课程：{course['name']}（不再尝试）")
                                        failed_course_ids.add(course_id)
                                    else:
                                        print(f"⏸️ 课程已满：{course['name']}，等待下次重试...")
                                        any_active_in_priority = True  # 表示这门课还在重试
                                    continue  # 继续下一轮循环
                                else:
                                    print(f"⚠️ 未知响应（可能可抢）：{text[:100]}...")
                                    any_active_in_priority = True  # 可能还能抢，保持活跃

                            except Exception as e:
                                print(f"[{count}] 请求失败（{course['name']}）：{e}")
                                any_active_in_priority = True  # 可能网络波动，保持尝试

                        # 检查是否当前优先级还有可尝试的课程
                        remaining_courses = [c for c in courses if c["data"]["p_id"] not in failed_course_ids]
                        if not remaining_courses:
                            print(f"⏸️ 优先级 {priority} 所有课程均已失败或放弃，进入下一优先级...")
                            break  # 跳出 while，进入下一优先级

                        # 如果没有活跃课程（全部失败/放弃），也跳出
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
            # 如果出现异常，可能是会话已过期
            if "401" in str(e) or "403" in str(e):
                print("⚠️ 可能会话已过期，建议重新登录")
# 启动应用
if __name__ == "__main__":
    root = tk.Tk()
    app = CourseSelectionApp(root)
    root.mainloop()