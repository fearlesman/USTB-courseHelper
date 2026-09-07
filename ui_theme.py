"""界面主题与窗口视觉配置。"""

class ThemeMixin:
    def configure_visual_theme(self: "CourseSelectionApp") -> None:
        """
        配置课程助手统一的颜色、字体和控件视觉层级。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 样式直接注册到 Tkinter ttk 主题系统。
        """
        self.style.theme_use("clam")
        self.style.configure(".", font=("Microsoft YaHei UI", 10))
        self.style.configure("TFrame", background="#ffffff")
        self.style.configure("App.TFrame", background="#f3f6f9")
        self.style.configure("TLabel", background="#ffffff", foreground="#1f2937")
        self.style.configure(
            "Title.TLabel",
            background="#f3f6f9",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        self.style.configure(
            "LoginTitle.TLabel",
            background="#ffffff",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 22, "bold"),
        )
        self.style.configure(
            "Section.TLabel",
            background="#ffffff",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.style.configure(
            "SurfaceMuted.TLabel",
            background="#ffffff",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "ListName.TLabel",
            background="#ffffff",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.style.configure(
            "Dirty.TLabel",
            background="#fff7ed",
            foreground="#c2410c",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(8, 4),
        )
        self.style.configure(
            "Clean.TLabel",
            background="#ecfdf5",
            foreground="#047857",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(8, 4),
        )
        self.style.configure(
            "Header.TLabel",
            background="#f3f6f9",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        self.style.configure(
            "Muted.TLabel",
            background="#f3f6f9",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "Status.TLabel",
            background="#e0f2fe",
            foreground="#075985",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(12, 8),
        )
        self.style.configure(
            "GuideNumber.TLabel",
            background="#ecfdf5",
            foreground="#0f766e",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(8, 5),
        )
        self.style.configure(
            "GuideTitle.TLabel",
            background="#ffffff",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.configure(
            "GuideBody.TLabel",
            background="#ffffff",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "GuideHint.TLabel",
            background="#f0fdfa",
            foreground="#115e59",
            font=("Microsoft YaHei UI", 9),
            padding=(10, 8),
        )
        self.style.configure(
            "TLabelframe",
            background="#ffffff",
            borderwidth=1,
            relief="solid",
            bordercolor="#dbe4ee",
        )
        self.style.configure(
            "TLabelframe.Label",
            background="#ffffff",
            foreground="#0f766e",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.style.configure(
            "TNotebook",
            background="#f3f6f9",
            borderwidth=0,
            tabmargins=(16, 12, 16, 0),
        )
        self.style.configure(
            "TNotebook.Tab",
            background="#e5edf5",
            foreground="#52616f",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(20, 11),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", "#ffffff"), ("active", "#dbeafe")],
            foreground=[("selected", "#0f766e"), ("active", "#0f172a")],
        )
        self.style.configure(
            "TEntry",
            fieldbackground="#ffffff",
            foreground="#1f2937",
            bordercolor="#cbd5e1",
            lightcolor="#cbd5e1",
            darkcolor="#cbd5e1",
            padding=(8, 6),
        )
        self.style.configure(
            "TCombobox",
            fieldbackground="#ffffff",
            foreground="#1f2937",
            bordercolor="#cbd5e1",
            padding=(6, 4),
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#ffffff")],
            foreground=[("readonly", "#1f2937")],
        )
        self.style.configure(
            "Primary.TButton",
            background="#0f766e",
            foreground="#ffffff",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
        )
        self.style.map(
            "Primary.TButton",
            background=[("active", "#115e59"), ("disabled", "#94a3b8")],
            foreground=[("disabled", "#e2e8f0")],
        )
        self.style.configure(
            "Secondary.TButton",
            background="#ffffff",
            foreground="#0f766e",
            borderwidth=1,
            relief="solid",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
        )
        self.style.map(
            "Secondary.TButton",
            background=[("active", "#ccfbf1"), ("disabled", "#f1f5f9")],
            foreground=[("disabled", "#94a3b8")],
        )
        self.style.configure(
            "Danger.TButton",
            background="#dc2626",
            foreground="#ffffff",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
        )
        self.style.map(
            "Danger.TButton",
            background=[("active", "#b91c1c"), ("disabled", "#fca5a5")],
            foreground=[("disabled", "#fef2f2")],
        )
        self.style.configure(
            "Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#243447",
            rowheight=32,
            bordercolor="#dbe4ee",
            relief="solid",
        )
        self.style.map(
            "Treeview",
            background=[("selected", "#ccfbf1")],
            foreground=[("selected", "#134e4a")],
        )
        self.style.configure(
            "Treeview.Heading",
            background="#eaf0f6",
            foreground="#334155",
            font=("Microsoft YaHei UI", 9, "bold"),
            relief="flat",
            padding=(8, 8),
        )
        self.style.configure(
            "Mode.TRadiobutton",
            background="#ffffff",
            foreground="#334155",
            padding=(6, 4),
        )
        self.style.map(
            "Mode.TRadiobutton",
            foreground=[("selected", "#0f766e"), ("disabled", "#94a3b8")],
        )
