#!/usr/bin/env python3
"""Windows用 画像モザイクアプリ (tkinter版)"""

import tkinter as tk
from tkinter import ttk, filedialog, colorchooser
from PIL import Image, ImageTk, ImageDraw, ImageChops
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
        self._tolerance = 30  # 透過の色許容範囲
        self._zoom_scale = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_y = 0.0

        # 矩形選択用
        self._drag_start = None
        self._drag_current = None

        # トリミング選択範囲（画像ピクセル座標で保持）
        self._crop_selection = None          # (x0, y0, x1, y1) or None
        self._crop_mode_for_selection = None  # "crop_rect" or "crop_circle"
        # 選択範囲の移動用
        self._is_moving_selection = False
        self._move_start_ix = 0
        self._move_start_iy = 0

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

    @staticmethod
    def _make_checker(w, h, cell=8):
        """透過部分を示すチェッカーボード背景を生成"""
        checker = Image.new("RGB", (w, h), (200, 200, 200))
        draw = ImageDraw.Draw(checker)
        for cy in range(0, h, cell):
            for cx in range(0, w, cell):
                if (cx // cell + cy // cell) % 2 == 0:
                    draw.rectangle([cx, cy, cx + cell - 1, cy + cell - 1],
                                   fill=(255, 255, 255))
        return checker

    def _update_display(self):
        if self._pil_image is None:
            return
        self.delete("all")
        x, y, nw, nh, _ = self._get_image_rect()
        disp_w = max(1, int(nw))
        disp_h = max(1, int(nh))
        display_img = self._pil_image.resize((disp_w, disp_h), Image.NEAREST)
        # RGBA画像はチェッカーボード上に合成して透過を可視化
        if display_img.mode == "RGBA":
            checker = self._make_checker(disp_w, disp_h)
            checker.paste(display_img, mask=display_img.split()[3])
            display_img = checker
        self._photo_image = ImageTk.PhotoImage(display_img)
        self.create_image(x, y, image=self._photo_image, anchor=tk.NW, tags="image")
        # クロップ選択があれば再描画
        if self._crop_selection:
            self._draw_crop_selection()

    def _view_to_image(self, vx, vy):
        """Canvas座標 → 画像ピクセル座標"""
        if self._pil_image is None:
            return 0, 0
        x, y, _, _, scale = self._get_image_rect()
        ix = int((vx - x) / scale)
        iy = int((vy - y) / scale)
        return ix, iy

    # --- マウスイベント ---

    def _is_point_in_crop_selection(self, ix, iy):
        """画像座標がクロップ選択内かどうか"""
        if self._crop_selection is None:
            return False
        sx0, sy0, sx1, sy1 = self._crop_selection
        return sx0 <= ix <= sx1 and sy0 <= iy <= sy1

    def _on_mouse_down(self, event):
        if self._pil_image is None:
            return
        mode = self._mode_var.get() if self._mode_var else "brush"
        ix, iy = self._view_to_image(event.x, event.y)
        if mode in ("rect", "crop_rect", "crop_circle"):
            if mode in ("crop_rect", "crop_circle") and self._crop_selection:
                # 既存の選択範囲内をクリック → 移動モード
                if self._is_point_in_crop_selection(ix, iy):
                    self._is_moving_selection = True
                    self._move_start_ix = ix
                    self._move_start_iy = iy
                    return
            # 新しいドラッグで古いクロップ選択をクリア
            if mode in ("crop_rect", "crop_circle"):
                self._crop_selection = None
                self._app._update_crop_button()
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
        elif mode == "transparent":
            self._push_undo()
            self._apply_transparent(ix, iy)

    def _on_mouse_drag(self, event):
        if self._pil_image is None:
            return
        mode = self._mode_var.get() if self._mode_var else "brush"
        vx, vy = event.x, event.y

        # 選択範囲の移動
        if self._is_moving_selection and self._crop_selection:
            ix, iy = self._view_to_image(vx, vy)
            dx = ix - self._move_start_ix
            dy = iy - self._move_start_iy
            sx0, sy0, sx1, sy1 = self._crop_selection
            iw, ih = self._pil_image.size
            sw, sh = sx1 - sx0, sy1 - sy0
            nx0 = max(0, min(iw - sw, sx0 + dx))
            ny0 = max(0, min(ih - sh, sy0 + dy))
            self._crop_selection = (nx0, ny0, nx0 + sw, ny0 + sh)
            self._move_start_ix = ix
            self._move_start_iy = iy
            mode_name = "□" if self._crop_mode_for_selection == "crop_rect" else "○"
            self._app._status_var.set(
                f"{mode_name} 移動中: {sw} x {sh}  |  「切り取り」ボタンで実行")
            self._draw_crop_selection()
            return

        if mode in ("rect", "crop_rect", "crop_circle"):
            # Ctrl押しで正方形/正円に制約
            if self._drag_start and (event.state & 0x4):  # Ctrl key
                sx, sy = self._drag_start
                dx = vx - sx
                dy = vy - sy
                size = max(abs(dx), abs(dy))
                vx = sx + (size if dx >= 0 else -size)
                vy = sy + (size if dy >= 0 else -size)
            self._drag_current = (vx, vy)
            self._draw_selection_rect()
        elif mode == "brush":
            ix, iy = self._view_to_image(vx, vy)
            self._apply_brush(ix, iy)
        elif mode == "eyedropper":
            ix, iy = self._view_to_image(vx, vy)
            self._pick_color(ix, iy)
        elif mode == "paint":
            ix, iy = self._view_to_image(vx, vy)
            self._apply_paint_brush(ix, iy)

    def _on_mouse_up(self, event):
        if self._pil_image is None:
            return
        # 移動モード終了
        if self._is_moving_selection:
            self._is_moving_selection = False
            if self._crop_selection:
                sx0, sy0, sx1, sy1 = self._crop_selection
                w, h = sx1 - sx0, sy1 - sy0
                mode_name = "□" if self._crop_mode_for_selection == "crop_rect" else "○"
                self._app._status_var.set(
                    f"{mode_name} 選択中: {w} x {h}  |  「切り取り」ボタンで実行")
            return
        mode = self._mode_var.get() if self._mode_var else "brush"
        if mode in ("rect", "crop_rect", "crop_circle") and self._drag_start:
            ix0, iy0 = self._view_to_image(*self._drag_start)
            ix1, iy1 = self._view_to_image(event.x, event.y)
            lx, rx = min(ix0, ix1), max(ix0, ix1)
            ly, ry = min(iy0, iy1), max(iy0, iy1)
            if rx - lx > 2 and ry - ly > 2:
                if mode == "rect":
                    self._push_undo()
                    self._apply_mosaic(lx, ly, rx, ry)
                elif mode in ("crop_rect", "crop_circle"):
                    # トリミングは選択のみ保持（「切り取り」ボタンで実行）
                    self._crop_selection = (lx, ly, rx, ry)
                    self._crop_mode_for_selection = mode
                    w, h = rx - lx, ry - ly
                    mode_name = "□" if mode == "crop_rect" else "○"
                    self._app._status_var.set(
                        f"{mode_name} 選択中: {w} x {h}  |  「切り取り」ボタンで実行")
                    self._app._update_crop_button()
            self._drag_start = None
            self._drag_current = None
            self.delete("selection")
            if mode in ("crop_rect", "crop_circle"):
                self._draw_crop_selection()

    def _draw_selection_rect(self):
        self.delete("selection")
        if self._drag_start and self._drag_current:
            x0, y0 = self._drag_start
            x1, y1 = self._drag_current
            mode = self._mode_var.get() if self._mode_var else "brush"
            if mode == "crop_circle":
                self.create_oval(
                    x0, y0, x1, y1,
                    outline="cyan", width=2, dash=(4, 4), tags="selection"
                )
            elif mode == "crop_rect":
                self.create_rectangle(
                    x0, y0, x1, y1,
                    outline="#00ff4d", width=2, dash=(4, 4), tags="selection"
                )
            else:
                self.create_rectangle(
                    x0, y0, x1, y1,
                    outline="red", width=2, dash=(4, 4), tags="selection"
                )

    def _draw_crop_selection(self):
        """確定済みクロップ選択範囲を描画"""
        self.delete("crop_sel")
        if self._crop_selection is None:
            return
        sx0, sy0, sx1, sy1 = self._crop_selection
        x, y, _, _, scale = self._get_image_rect()
        vx0 = x + sx0 * scale
        vy0 = y + sy0 * scale
        vx1 = x + sx1 * scale
        vy1 = y + sy1 * scale
        if self._crop_mode_for_selection == "crop_circle":
            self.create_oval(
                vx0, vy0, vx1, vy1,
                outline="cyan", width=2, dash=(4, 4), tags="crop_sel"
            )
        else:
            self.create_rectangle(
                vx0, vy0, vx1, vy1,
                outline="#00ff4d", width=2, dash=(4, 4), tags="crop_sel"
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

    def _apply_transparent(self, cx, cy):
        """クリックした色と近い色を透過にする（Pillow演算で高速処理）"""
        if self._pil_image is None:
            return
        iw, ih = self._pil_image.size
        if cx < 0 or cy < 0 or cx >= iw or cy >= ih:
            return
        # Ensure RGBA
        if self._pil_image.mode != "RGBA":
            self._pil_image = self._pil_image.convert("RGBA")
        target = self._pil_image.getpixel((cx, cy))[:3]
        tol = self._tolerance
        r, g, b, a = self._pil_image.split()
        tr, tg, tb = target
        # Difference per channel, then threshold
        diff_r = ImageChops.difference(r, Image.new("L", (iw, ih), tr))
        diff_g = ImageChops.difference(g, Image.new("L", (iw, ih), tg))
        diff_b = ImageChops.difference(b, Image.new("L", (iw, ih), tb))
        # Pixels within tolerance -> 255 (match), else 0
        mask_r = diff_r.point(lambda v: 255 if v <= tol else 0)
        mask_g = diff_g.point(lambda v: 255 if v <= tol else 0)
        mask_b = diff_b.point(lambda v: 255 if v <= tol else 0)
        # Combine: all three must match
        match_mask = ImageChops.multiply(ImageChops.multiply(mask_r, mask_g), mask_b)
        # Invert match_mask: matched pixels become 0 (transparent)
        inv_mask = match_mask.point(lambda v: 0 if v == 255 else 255)
        # Apply: keep existing alpha but set matched pixels to 0
        new_alpha = ImageChops.multiply(a, inv_mask)
        self._pil_image = Image.merge("RGBA", (r, g, b, new_alpha))
        self._update_display()
        self._app._status_var.set(
            f"RGB({tr}, {tg}, {tb}) 許容値±{tol} を透過しました")

    # --- トリミング ---

    def apply_crop(self):
        """保持されたクロップ選択を実行"""
        if self._crop_selection is None:
            return False
        x0, y0, x1, y1 = self._crop_selection
        mode = self._crop_mode_for_selection
        self._push_undo()
        if mode == "crop_rect":
            self._apply_rect_crop(x0, y0, x1, y1)
        elif mode == "crop_circle":
            self._apply_circle_crop(x0, y0, x1, y1)
        self._crop_selection = None
        self._crop_mode_for_selection = None
        self.delete("crop_sel")
        return True

    def clear_crop_selection(self):
        """クロップ選択をクリア"""
        self._crop_selection = None
        self._crop_mode_for_selection = None
        self.delete("crop_sel")

    def _apply_rect_crop(self, x0, y0, x1, y1):
        """四角トリミング"""
        iw, ih = self._pil_image.size
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(iw, x1)
        y1 = min(ih, y1)
        if x1 - x0 > 2 and y1 - y0 > 2:
            self._pil_image = self._pil_image.crop((x0, y0, x1, y1))
            self._zoom_scale = 1.0
            self._pan_offset_x = 0.0
            self._pan_offset_y = 0.0
            self._update_display()
            w, h = self._pil_image.size
            self._app._status_var.set(
                f"トリミング完了  ({w} x {h})  |  ズーム: 100%")

    def _apply_circle_crop(self, x0, y0, x1, y1):
        """円形トリミング"""
        iw, ih = self._pil_image.size
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(iw, x1)
        y1 = min(ih, y1)
        if x1 - x0 > 2 and y1 - y0 > 2:
            region = self._pil_image.crop((x0, y0, x1, y1))
            rw, rh = region.size
            result = region.convert("RGBA")
            mask = Image.new("L", (rw, rh), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, rw - 1, rh - 1), fill=255)
            result.putalpha(mask)
            self._pil_image = result
            self._zoom_scale = 1.0
            self._pan_offset_x = 0.0
            self._pan_offset_y = 0.0
            self._update_display()
            self._app._status_var.set(
                f"円形トリミング完了  ({rw} x {rh})  |  ズーム: 100%")


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
        ttk.Radiobutton(
            toolbar, text="□トリミング", variable=self._mode_var, value="crop_rect"
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            toolbar, text="○トリミング", variable=self._mode_var, value="crop_circle"
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            toolbar, text="透過", variable=self._mode_var, value="transparent"
        ).pack(side=tk.LEFT)

        self._btn_crop = ttk.Button(toolbar, text="切り取り", command=self._do_crop,
                                     state=tk.DISABLED)
        self._btn_crop.pack(side=tk.LEFT, padx=2)

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

        ttk.Label(toolbar, text="許容値:").pack(side=tk.LEFT, padx=(4, 2))
        self._tolerance_var = tk.IntVar(value=30)
        self._tolerance_label = ttk.Label(toolbar, text="30", width=4)
        ttk.Scale(
            toolbar, from_=0, to=128, variable=self._tolerance_var,
            orient=tk.HORIZONTAL, length=100,
            command=self._on_tolerance_changed
        ).pack(side=tk.LEFT)
        self._tolerance_label.pack(side=tk.LEFT, padx=(2, 4))

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
            "crop_rect": "crosshair",
            "crop_circle": "crosshair",
            "transparent": "tcross",
        }
        self._canvas.configure(cursor=cursors.get(mode, "arrow"))
        # トリミング以外のモードに切り替えたらクロップ選択をクリア
        if mode not in ("crop_rect", "crop_circle"):
            self._canvas.clear_crop_selection()
            self._update_crop_button()

    def _do_crop(self):
        """切り取りボタンの実行"""
        if self._canvas.apply_crop():
            self._update_crop_button()

    def _update_crop_button(self):
        """切り取りボタンの有効/無効を更新"""
        if hasattr(self, '_btn_crop'):
            if self._canvas._crop_selection is not None:
                self._btn_crop.config(state=tk.NORMAL)
            else:
                self._btn_crop.config(state=tk.DISABLED)

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

    def _on_tolerance_changed(self, value):
        val = int(float(value))
        self._canvas._tolerance = val
        self._tolerance_label.config(text=f"{val}")

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

    def _estimate_file_size(self, width, height, fmt):
        """フォーマットとサイズから推定ファイルサイズを計算"""
        pixels = width * height
        rates = {
            "png": 2.5, "jpg": 0.4, "gif": 0.8,
            "bmp": 3.0, "tiff": 4.0, "webp": 0.25
        }
        estimated = int(pixels * rates.get(fmt, 2.5))
        if estimated < 1024:
            return f"{estimated} B"
        elif estimated < 1024 * 1024:
            return f"{estimated / 1024:.0f} KB"
        else:
            return f"{estimated / (1024 * 1024):.1f} MB"

    def save_file(self):
        if self._canvas._pil_image is None:
            return
        img = self._canvas._pil_image
        iw, ih = img.size

        # 保存オプションダイアログ
        dlg = tk.Toplevel(self)
        dlg.title("保存オプション")
        dlg.geometry("380x200")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        # フォーマット選択
        ttk.Label(dlg, text="フォーマット:").place(x=20, y=20)
        fmt_var = tk.StringVar(value="PNG")
        fmt_combo = ttk.Combobox(dlg, textvariable=fmt_var, state="readonly",
                                 values=["PNG", "JPEG", "GIF", "BMP", "TIFF", "WebP"],
                                 width=15)
        fmt_combo.place(x=120, y=20)

        # サイズスライダー
        ttk.Label(dlg, text="サイズ:").place(x=20, y=60)
        size_var = tk.IntVar(value=100)
        size_slider = ttk.Scale(dlg, from_=10, to=200, variable=size_var,
                                orient=tk.HORIZONTAL, length=160)
        size_slider.place(x=120, y=58)
        size_label = ttk.Label(dlg, text=f"100% ({iw}x{ih})")
        size_label.place(x=290, y=60)

        # 推定容量
        est = self._estimate_file_size(iw, ih, "png")
        filesize_label = ttk.Label(dlg, text=f"推定容量: 約 {est}", foreground="gray")
        filesize_label.place(x=20, y=100)

        def update_info(*args):
            pct = size_var.get()
            nw = max(1, int(iw * pct / 100))
            nh = max(1, int(ih * pct / 100))
            size_label.config(text=f"{pct}% ({nw}x{nh})")
            fmt_map = {"PNG": "png", "JPEG": "jpg", "GIF": "gif",
                       "BMP": "bmp", "TIFF": "tiff", "WebP": "webp"}
            fmt = fmt_map.get(fmt_var.get(), "png")
            est_str = self._estimate_file_size(nw, nh, fmt)
            filesize_label.config(text=f"推定容量: 約 {est_str}")

        size_var.trace_add("write", update_info)
        fmt_var.trace_add("write", update_info)

        save_result = {"path": None}

        def do_save():
            fmt_map = {
                "PNG": ("png", ".png"), "JPEG": ("jpg", ".jpg"),
                "GIF": ("gif", ".gif"), "BMP": ("bmp", ".bmp"),
                "TIFF": ("tiff", ".tiff"), "WebP": ("webp", ".webp"),
            }
            fmt_key, default_ext = fmt_map.get(fmt_var.get(), ("png", ".png"))
            ft_list = [
                ("PNG", "*.png"), ("JPEG", "*.jpg;*.jpeg"), ("GIF", "*.gif"),
                ("BMP", "*.bmp"), ("TIFF", "*.tiff"), ("WebP", "*.webp"),
                ("すべてのファイル", "*.*"),
            ]
            path = filedialog.asksaveasfilename(
                parent=dlg, title="画像を保存",
                defaultextension=default_ext,
                initialfile=f"mosaic_image{default_ext}",
                filetypes=ft_list
            )
            if path:
                save_result["path"] = path
            dlg.destroy()

        def do_cancel():
            dlg.destroy()

        ttk.Button(dlg, text="保存...", command=do_save).place(x=180, y=150)
        ttk.Button(dlg, text="キャンセル", command=do_cancel).place(x=270, y=150)

        dlg.wait_window()

        path = save_result["path"]
        if not path:
            return

        ext = os.path.splitext(path)[1].lower()
        pct = size_var.get()
        if pct != 100:
            nw = max(1, int(iw * pct / 100))
            nh = max(1, int(ih * pct / 100))
            save_img = img.resize((nw, nh), Image.LANCZOS)
        else:
            save_img = img

        try:
            if ext in (".jpg", ".jpeg"):
                if save_img.mode == "RGBA":
                    bg = Image.new("RGB", save_img.size, (255, 255, 255))
                    bg.paste(save_img, mask=save_img.split()[3])
                    bg.save(path, "JPEG", quality=95)
                else:
                    save_img.convert("RGB").save(path, "JPEG", quality=95)
            elif ext == ".gif":
                if save_img.mode == "RGBA":
                    alpha = save_img.split()[3]
                    rgb = save_img.convert("RGB").quantize(colors=255)
                    rgb.info["transparency"] = 255
                    mask = Image.eval(alpha, lambda a: 255 if a <= 128 else 0)
                    rgb.paste(255, mask=mask)
                    rgb.save(path, "GIF", transparency=255)
                else:
                    save_img.convert("RGB").quantize(colors=256).save(path, "GIF")
            elif ext == ".bmp":
                if save_img.mode == "RGBA":
                    bg = Image.new("RGB", save_img.size, (255, 255, 255))
                    bg.paste(save_img, mask=save_img.split()[3])
                    bg.save(path, "BMP")
                else:
                    save_img.convert("RGB").save(path, "BMP")
            elif ext in (".tif", ".tiff"):
                save_img.save(path, "TIFF")
            elif ext == ".webp":
                save_img.save(path, "WEBP", quality=95)
            else:
                save_img.save(path, "PNG")
            fmt_name = ext.upper().strip('.')
            sw, sh = save_img.size
            self._status_var.set(
                f"保存しました: {os.path.basename(path)} ({fmt_name}, {sw}x{sh})")
        except Exception as e:
            self._status_var.set(f"保存エラー: {e}")

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
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA") if img.mode in ("LA", "PA", "P") else img.convert("RGB")
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
