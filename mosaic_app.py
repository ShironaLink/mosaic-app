#!/usr/bin/env python3
"""Windows用 画像モザイクアプリ (tkinter版)"""

import tkinter as tk
from tkinter import ttk, filedialog, colorchooser
from PIL import Image, ImageTk, ImageDraw
import os
import sys

# Windows高DPI対応
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


class MosaicCanvas(tk.Canvas):
    """画像を表示してモザイク描画を受け付けるカスタムCanvas"""

    def __init__(self, master, app, **kwargs):
        super().__init__(master, bg="#2b2b2b", highlightthickness=0, **kwargs)
        self._app = app
        self._pil_image = None
        self._photo_image = None
        self._undo_stack = []
        self._mode_var = None  # MosaicApp側で設定
        self._block_size = 15
        self._brush_size = 30
        self._paint_color = (0, 0, 0)  # ペイント色 (RGB)
        self._zoom_scale = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_y = 0.0

        # 矩形選択用
        self._drag_start = None
        self._drag_current = None

        # パン用
        self._is_panning = False
        self._pan_start_x = 0.0
        self._pan_start_y = 0.0
        self._pan_offset_start_x = 0.0
        self._pan_offset_start_y = 0.0

        # デバウンス用
        self._resize_after_id = None

        # マウスイベントバインド
        self.bind("<ButtonPress-1>", self._on_mouse_down)
        self.bind("<B1-Motion>", self._on_mouse_drag)
        self.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.bind("<ButtonPress-3>", self._on_right_mouse_down)
        self.bind("<B3-Motion>", self._on_right_mouse_drag)
        self.bind("<ButtonRelease-3>", self._on_right_mouse_up)
        self.bind("<MouseWheel>", self._on_mouse_wheel)
        self.bind("<Configure>", self._on_configure)

        # 初期ヒント表示
        self.bind("<Map>", self._on_first_map)

    def _on_first_map(self, event):
        self.unbind("<Map>")
        self.after(50, self._draw_hint)

    def _draw_hint(self):
        if self._pil_image is None:
            self.delete("all")
            cw = self.winfo_width()
            ch = self.winfo_height()
            self.create_text(
                cw / 2, ch / 2,
                text="ここに画像をドラッグ&ドロップ\nまたは「画像を開く」ボタン / Ctrl+O",
                fill="gray", font=("", 16), tags="hint"
            )

    def set_image(self, pil_img):
        self._pil_image = pil_img.copy()
        self._undo_stack = []
        self._zoom_scale = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_y = 0.0
        self._update_display()

    def _get_image_rect(self):
        """画像の描画領域を計算（ズーム・パン対応）"""
        if self._pil_image is None:
            return 0, 0, 0, 0, 1.0
        cw = self.winfo_width()
        ch = self.winfo_height()
        iw, ih = self._pil_image.size
        base_scale = min(cw / iw, ch / ih)
        effective_scale = base_scale * self._zoom_scale
        nw = iw * effective_scale
        nh = ih * effective_scale
        x = (cw - nw) / 2.0 + self._pan_offset_x
        y = (ch - nh) / 2.0 + self._pan_offset_y
        return x, y, nw, nh, effective_scale

    def _update_display(self):
        if self._pil_image is None:
            return
        self.delete("all")
        x, y, nw, nh, _ = self._get_image_rect()
        disp_w = max(1, int(nw))
        disp_h = max(1, int(nh))
        display_img = self._pil_image.resize((disp_w, disp_h), Image.NEAREST)
        self._photo_image = ImageTk.PhotoImage(display_img)
        self.create_image(x, y, image=self._photo_image, anchor=tk.NW, tags="image")

    def _view_to_image(self, vx, vy):
        """Canvas座標 → 画像ピクセル座標"""
        if self._pil_image is None:
            return 0, 0
        x, y, _, _, scale = self._get_image_rect()
        ix = int((vx - x) / scale)
        iy = int((vy - y) / scale)
        return ix, iy

    # --- マウスイベント ---

    def _on_mouse_down(self, event):
        if self._pil_image is None:
            return
        mode = self._mode_var.get() if self._mode_var else "brush"
        ix, iy = self._view_to_image(event.x, event.y)
        if mode == "rect":
            self._drag_start = (event.x, event.y)
            self._drag_current = (event.x, event.y)
        elif mode == "brush":
            self._push_undo()
            self._apply_brush(ix, iy)
        elif mode == "eyedropper":
            self._pick_color(ix, iy)
        elif mode == "paint":
            self._push_undo()
            self._apply_paint_brush(ix, iy)

    def _on_mouse_drag(self, event):
        if self._pil_image is None:
            return
        mode = self._mode_var.get() if self._mode_var else "brush"
        ix, iy = self._view_to_image(event.x, event.y)
        if mode == "rect":
            self._drag_current = (event.x, event.y)
            self._draw_selection_rect()
        elif mode == "brush":
            self._apply_brush(ix, iy)
        elif mode == "eyedropper":
            self._pick_color(ix, iy)
        elif mode == "paint":
            self._apply_paint_brush(ix, iy)

    def _on_mouse_up(self, event):
        if self._pil_image is None:
            return
        mode = self._mode_var.get() if self._mode_var else "brush"
        if mode == "rect" and self._drag_start:
            ix0, iy0 = self._view_to_image(*self._drag_start)
            ix1, iy1 = self._view_to_image(event.x, event.y)
            lx, rx = min(ix0, ix1), max(ix0, ix1)
            ly, ry = min(iy0, iy1), max(iy0, iy1)
            if rx - lx > 2 and ry - ly > 2:
                self._push_undo()
                self._apply_mosaic(lx, ly, rx, ry)
            self._drag_start = None
            self._drag_current = None
            self.delete("selection")

    def _draw_selection_rect(self):
        self.delete("selection")
        if self._drag_start and self._drag_current:
            x0, y0 = self._drag_start
            x1, y1 = self._drag_current
            self.create_rectangle(
                x0, y0, x1, y1,
                outline="red", width=2, dash=(4, 4), tags="selection"
            )

    # --- ズーム ---

    def _on_mouse_wheel(self, event):
        if self._pil_image is None:
            return
        if event.delta > 0:
            zoom_factor = 1.1
        else:
            zoom_factor = 1 / 1.1

        new_zoom = self._zoom_scale * zoom_factor
        new_zoom = max(0.1, min(20.0, new_zoom))

        # カーソル中心ズーム
        mouse_vx = event.x
        mouse_vy = event.y
        cw = self.winfo_width()
        ch = self.winfo_height()

        old_center_x = cw / 2.0 + self._pan_offset_x
        old_center_y = ch / 2.0 + self._pan_offset_y
        dx = mouse_vx - old_center_x
        dy = mouse_vy - old_center_y
        ratio = new_zoom / self._zoom_scale
        new_center_x = mouse_vx - dx * ratio
        new_center_y = mouse_vy - dy * ratio

        self._pan_offset_x = new_center_x - cw / 2.0
        self._pan_offset_y = new_center_y - ch / 2.0
        self._zoom_scale = new_zoom
        self._update_display()
        self._app.update_status()

    # --- パン（右クリックドラッグ） ---

    def _on_right_mouse_down(self, event):
        if self._pil_image is None:
            return
        self._is_panning = True
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self._pan_offset_start_x = self._pan_offset_x
        self._pan_offset_start_y = self._pan_offset_y

    def _on_right_mouse_drag(self, event):
        if not self._is_panning:
            return
        dx = event.x - self._pan_start_x
        dy = event.y - self._pan_start_y
        self._pan_offset_x = self._pan_offset_start_x + dx
        self._pan_offset_y = self._pan_offset_start_y + dy
        self._update_display()

    def _on_right_mouse_up(self, event):
        self._is_panning = False

    # --- リサイズ ---

    def _on_configure(self, event):
        if self._pil_image is not None:
            if self._resize_after_id is not None:
                self.after_cancel(self._resize_after_id)
            self._resize_after_id = self.after(50, self._update_display)
        else:
            self._draw_hint()

    # --- ズームリセット ---

    def reset_zoom(self):
        self._zoom_scale = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_y = 0.0
        self._update_display()
        self._app.update_status()

    # --- Undo ---

    def _push_undo(self):
        self._undo_stack.append(self._pil_image.copy())
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)

    def undo(self):
        if not self._undo_stack:
            return False
        self._pil_image = self._undo_stack.pop()
        self._update_display()
        return True

    # --- モザイクアルゴリズム ---

    def _apply_mosaic(self, x0, y0, x1, y1):
        iw, ih = self._pil_image.size
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(iw, x1)
        y1 = min(ih, y1)
        if x1 <= x0 or y1 <= y0:
            return
        block = max(2, self._block_size)
        region = self._pil_image.crop((x0, y0, x1, y1))
        rw, rh = region.size
        sw = max(1, rw // block)
        sh = max(1, rh // block)
        small = region.resize((sw, sh), Image.BILINEAR)
        mosaic = small.resize((rw, rh), Image.NEAREST)
        self._pil_image.paste(mosaic, (x0, y0))
        self._update_display()

    def _apply_brush(self, cx, cy):
        half = max(5, self._brush_size) // 2
        self._apply_mosaic(cx - half, cy - half, cx + half, cy + half)

    def _pick_color(self, cx, cy):
        """画像座標のピクセル色を取得してペイント色に設定"""
        if self._pil_image is None:
            return
        iw, ih = self._pil_image.size
        if cx < 0 or cy < 0 or cx >= iw or cy >= ih:
            return
        color = self._pil_image.getpixel((cx, cy))
        self._paint_color = color[:3] if isinstance(color, tuple) else (color, color, color)
        self._app.update_color_swatch(self._paint_color)
        self._app.update_status_with_color(self._paint_color)

    def _apply_paint_brush(self, cx, cy):
        """画像座標を中心に円形でペイント色を塗る"""
        half = max(5, self._brush_size) // 2
        draw = ImageDraw.Draw(self._pil_image)
        draw.ellipse(
            [cx - half, cy - half, cx + half, cy + half],
            fill=self._paint_color
        )
        self._update_display()


class MosaicApp(tk.Tk):
    """メインウィンドウ"""

    def __init__(self):
        super().__init__()
        self.title("モザイクアプリ")
        self.geometry("1000x750")
        self.minsize(600, 400)

        # 変数
        self._mode_var = tk.StringVar(value="brush")
        self._block_var = tk.IntVar(value=15)
        self._brush_var = tk.IntVar(value=30)
        self._status_var = tk.StringVar(
            value="画像をドラッグ&ドロップ、または「画像を開く」で読み込み"
        )

        self._setup_menubar()
        self._setup_toolbar()
        self._setup_canvas()
        self._setup_statusbar()
        self._setup_keybindings()
        self._setup_drop()
        self._mode_var.trace_add("write", self._on_mode_changed)

    def _setup_menubar(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="開く...", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(label="保存...", accelerator="Ctrl+S", command=self.save_file)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.quit)
        menubar.add_cascade(label="ファイル", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="元に戻す", accelerator="Ctrl+Z", command=self.undo)
        menubar.add_cascade(label="編集", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(
            label="画面にフィット", accelerator="Ctrl+0", command=self.fit_to_window
        )
        menubar.add_cascade(label="表示", menu=view_menu)

        self.config(menu=menubar)

    def _setup_toolbar(self):
        toolbar = ttk.Frame(self, padding=(5, 2))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="画像を開く", command=self.open_file).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(toolbar, text="保存", command=self.save_file).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(toolbar, text="元に戻す", command=self.undo).pack(
            side=tk.LEFT, padx=2
        )

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=6, pady=2
        )

        ttk.Label(toolbar, text="モード:").pack(side=tk.LEFT, padx=(4, 2))
        ttk.Radiobutton(
            toolbar, text="ブラシ", variable=self._mode_var, value="brush"
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            toolbar, text="範囲選択", variable=self._mode_var, value="rect"
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            toolbar, text="スポイト", variable=self._mode_var, value="eyedropper"
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            toolbar, text="ペイント", variable=self._mode_var, value="paint"
        ).pack(side=tk.LEFT)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=6, pady=2
        )

        ttk.Label(toolbar, text="色:").pack(side=tk.LEFT, padx=(4, 2))
        self._color_swatch = tk.Canvas(
            toolbar, width=24, height=24,
            bg="#000000", relief=tk.SUNKEN, borderwidth=1,
            cursor="hand2"
        )
        self._color_swatch.pack(side=tk.LEFT, padx=2)
        self._color_swatch.bind("<Button-1>", self._on_swatch_click)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=6, pady=2
        )

        ttk.Label(toolbar, text="強度:").pack(side=tk.LEFT, padx=(4, 2))
        self._block_label = ttk.Label(toolbar, text="15px", width=5)
        ttk.Scale(
            toolbar, from_=2, to=50, variable=self._block_var,
            orient=tk.HORIZONTAL, length=100,
            command=self._on_block_changed
        ).pack(side=tk.LEFT)
        self._block_label.pack(side=tk.LEFT, padx=(2, 4))

        ttk.Label(toolbar, text="ブラシ:").pack(side=tk.LEFT, padx=(4, 2))
        self._brush_label = ttk.Label(toolbar, text="30px", width=5)
        ttk.Scale(
            toolbar, from_=10, to=100, variable=self._brush_var,
            orient=tk.HORIZONTAL, length=100,
            command=self._on_brush_changed
        ).pack(side=tk.LEFT)
        self._brush_label.pack(side=tk.LEFT, padx=(2, 4))

    def _setup_canvas(self):
        self._canvas = MosaicCanvas(self, app=self)
        self._canvas._mode_var = self._mode_var
        self._canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _setup_statusbar(self):
        status_bar = ttk.Label(
            self, textvariable=self._status_var,
            relief=tk.SUNKEN, anchor=tk.W, padding=(5, 2)
        )
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _setup_keybindings(self):
        self.bind_all("<Control-o>", lambda e: self.open_file())
        self.bind_all("<Control-s>", lambda e: self.save_file())
        self.bind_all("<Control-z>", lambda e: self.undo())
        self.bind_all("<Control-0>", lambda e: self.fit_to_window())

    def _setup_drop(self):
        """ドラッグ&ドロップ対応 (windnd) - ウィンドウ表示後にフック"""
        try:
            import windnd
            self._windnd = windnd
            self.after(200, self._hook_drop)
        except ImportError:
            pass

    def _hook_drop(self):
        self.update_idletasks()
        self._windnd.hook_dropfiles(self, func=self._on_drop_files)

    def _on_drop_files(self, files):
        if not files:
            return
        path = files[0]
        if isinstance(path, bytes):
            # Windows日本語環境ではShift-JIS (cp932) で返される
            path = path.decode("cp932")
        path = str(path).strip()
        self.load_image(path)

    # --- スライダーコールバック ---

    def _on_mode_changed(self, *args):
        mode = self._mode_var.get()
        cursors = {
            "brush": "circle",
            "rect": "crosshair",
            "eyedropper": "tcross",
            "paint": "circle",
        }
        self._canvas.configure(cursor=cursors.get(mode, "arrow"))

    def _on_swatch_click(self, event=None):
        """カラースウォッチクリック → カラーチューザーで色を手動選択"""
        current = self._canvas._paint_color
        initial_hex = "#{:02x}{:02x}{:02x}".format(*current)
        result = colorchooser.askcolor(color=initial_hex, title="ペイント色を選択")
        if result[0] is not None:
            r, g, b = [int(c) for c in result[0]]
            self._canvas._paint_color = (r, g, b)
            self.update_color_swatch((r, g, b))

    def update_color_swatch(self, rgb):
        """カラースウォッチの表示色を更新"""
        hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
        self._color_swatch.configure(bg=hex_color)

    def update_status_with_color(self, rgb):
        """スポイトで色を取得した際にステータスバーに表示"""
        hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
        self._status_var.set(f"色を取得: RGB({rgb[0]}, {rgb[1]}, {rgb[2]})  {hex_color}")

    def _on_block_changed(self, value):
        val = int(float(value))
        self._canvas._block_size = val
        self._block_label.config(text=f"{val}px")

    def _on_brush_changed(self, value):
        val = int(float(value))
        self._canvas._brush_size = val
        self._brush_label.config(text=f"{val}px")

    # --- アクション ---

    def open_file(self):
        path = filedialog.askopenfilename(
            title="画像を開く",
            filetypes=[
                ("画像ファイル", "*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.tiff;*.webp"),
                ("すべてのファイル", "*.*"),
            ]
        )
        if path:
            self.load_image(path)

    def save_file(self):
        if self._canvas._pil_image is None:
            return
        path = filedialog.asksaveasfilename(
            title="画像を保存",
            defaultextension=".png",
            initialfile="mosaic_image.png",
            filetypes=[
                ("PNG", "*.png"),
                ("JPEG", "*.jpg"),
                ("BMP", "*.bmp"),
            ]
        )
        if path:
            self._canvas._pil_image.save(path)
            self._status_var.set(f"保存しました: {os.path.basename(path)}")

    def undo(self):
        if self._canvas.undo():
            self._status_var.set("元に戻しました")
            self.update_status()
        else:
            self._status_var.set("これ以上戻せません")

    def fit_to_window(self):
        self._canvas.reset_zoom()

    def load_image(self, path):
        path = str(path).strip()
        if not os.path.isfile(path):
            self._status_var.set(f"ファイルが見つかりません: {path}")
            return
        try:
            img = Image.open(path)
            img.load()
            img = img.convert("RGB")
        except Exception as e:
            self._status_var.set(f"画像を開けません: {e}")
            return
        self._canvas.set_image(img)
        name = os.path.basename(path)
        self._status_var.set(f"{name}  ({img.width} x {img.height})  |  ズーム: 100%")

    def update_status(self):
        if self._canvas._pil_image:
            zoom_pct = int(self._canvas._zoom_scale * 100)
            iw, ih = self._canvas._pil_image.size
            self._status_var.set(f"{iw} x {ih}  |  ズーム: {zoom_pct}%")


def main():
    app = MosaicApp()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(100, lambda: app.load_image(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
