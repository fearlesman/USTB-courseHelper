"""旧版测试兼容全局状态。

所有栏目变量只供无运行时上下文时的旧测试兼容路径使用；真实应用路径
一律通过 ``MultiUserRuntime`` 中的用户上下文读写状态，不得在这些全局
变量中保存 Cookie、Token 或 SESSION 等凭据。

功能模块统一通过 ``app_legacy.<名称>`` 访问这些变量，保证测试替身与
功能代码指向同一个绑定。
"""

qr_image_url = None
current_img_data = None
stop_display = False
login_success = False
final_cookies_dict = {}  # 存储提取的 cookies（仅供旧测试兼容路径）
course_data_list = []  # 存储课程数据（含优先级），仅供旧测试兼容路径
course_id_count = 0
selection_running = False  # 是否正在抢课（旧测试兼容路径）
stop_selection = False  # 是否请求停止（旧测试兼容路径）
online_thread_running = False  # 是否正在运行 online 线程（旧测试兼容路径）