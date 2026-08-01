# 项目协作说明

## 项目概览

本项目是北京科技大学选课辅助的 Tkinter 桌面程序，入口文件为 `USTB-courseHelper.py`。学年学期计算位于 `academic_term.py`，浏览器启动封装位于 `browser_driver.py`，课程联合查询与字段映射位于 `course_query.py`，三者均可在不启动图形界面的情况下独立测试。

## 课程查询规则

- 联合查询使用 `p_kcdm_cxrw`（课程代码）和 `p_gjz`（课程名称）；至少需要一个非空条件，避免接口返回过大的响应。
- 课程类型默认“所有”，必须分别查询素质扩展课、专业扩展课、MOOC 和必修课，并按任务 ID 去重合并；加入抢课列表时使用结果来源的选课方式代码。
- 接口响应必须先通过 `remove_empty_values()` 递归移除 `null`、空文本、空列表和空对象，且不得误删 `0` 与 `false`。
- 展示字段映射集中维护在 `course_query.py` 的 `DISPLAY_COLUMNS` 与 `CourseSearchResult` 中；课程类别仅显示 `kclbmc`，不作为请求筛选参数。
- 会话 Cookie 仅能来自运行时登录结果，严禁写入源码、测试、README、AGENTS.md 或日志。

## 定时抢课规则

- 抢课时间输入框仅在“定时抢课”模式下显示；轮询模式必须隐藏该输入框。
- 定时模式仅负责等待到目标时刻，随后必须复用 `auto_selection_process()` 的持续轮询逻辑，不得维护另一套抢课请求循环。
- 当日目标时刻已到或已过时应立即开始轮询，不得等待到次日。

## ChromeDriver 规则

- 登录流程必须通过 Selenium 4.35.0 内置的 Selenium Manager 自动匹配 ChromeDriver，调用 `webdriver.Chrome(options=...)` 时不得传入固定驱动路径。
- 不得恢复项目内固定版本的 ChromeDriver 或 `webdriver-manager` 依赖；Chrome 更新后的首次登录依赖 Chrome for Testing 下载源，成功后复用用户目录中的 Selenium 缓存。
- 自动匹配失败时，应保留原始异常并提供中文的网络、代理和 Chrome 安装检查提示。

## 学年学期规则

`get_current_academic_term()` 返回接口使用的 `YYYY-YYYY-N` 格式：

- 1 月 1 日至 5 月 31 日：第二学期（`-2`）。
- 6 月 1 日至 7 月 27 日：第三学期（`-3`）。
- 7 月 28 日至 12 月 31 日：第一学期（`-1`）。

7 月 28 日归入第一学期。修改此规则时，必须同步更新 `tests/test_academic_term.py` 与 README 中的验收场景。

## 开发与验证

- 新增 Python 函数必须使用完整类型注解和 Google 风格多行文档字符串。
- 与学年学期有关的修改至少运行：`python -m pytest tests/test_academic_term.py -q`。
- 与浏览器启动有关的修改至少运行：`python -m pytest tests/test_browser_driver.py -q`。
- 与课程联合查询有关的修改至少运行：`python -m pytest tests/test_course_query.py -q`。
- 与定时抢课有关的修改至少运行：`python -m pytest tests/test_rush_schedule.py -q`。
- 文档内容使用中文，并在功能完成时同步维护本文件和 `README.md`。
