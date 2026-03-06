#!/usr/bin/env python3
"""Mac用 画像モザイクアプリ (Cocoa/AppKit版)"""

import objc
from AppKit import (
    NSApplication, NSApp, NSWindow, NSView, NSImageView, NSImage,
    NSBitmapImageRep, NSButton, NSSlider, NSTextField, NSOpenPanel,
    NSSavePanel, NSMenu, NSMenuItem, NSColor, NSFont,
    NSBezierPath, NSCompositingOperationSourceOver,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskMiniaturizable,
    NSBackingStoreBuffered, NSImageScaleProportionallyUpOrDown,
    NSImageAlignCenter, NSViewWidthSizable, NSViewHeightSizable,
    NSViewMinYMargin, NSViewMaxYMargin, NSMakeRect, NSMakeSize,
    NSApplicationActivationPolicyRegular, NSBezelStyleRounded,
    NSOnState, NSOffState, NSRadioButton, NSGraphicsContext,
    NSPNGFileType, NSJPEGFileType, NSCalibratedRGBColorSpace,
    NSTrackingArea, NSTrackingMouseMoved, NSTrackingActiveInActiveApp,
    NSTrackingInVisibleRect, NSTrackingMouseEnteredAndExited,
    NSCursor, NSPointInRect, NSAffineTransform,
)
import Foundation
from Foundation import NSObject, NSMakePoint, NSData, NSURL, NSBundle
from PIL import Image
import io
import os
import sys


def pil_to_nsimage(pil_img):
    """PIL Image → NSImage 変換"""
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    nsimg = NSImage.alloc().initWithData_(data)
    return nsimg


def nsimage_to_pil(nsimage):
    """NSImage → PIL Image 変換"""
    tiff_data = nsimage.TIFFRepresentation()
    pil_img = Image.open(io.BytesIO(tiff_data))
    return pil_img.convert("RGB")


class MosaicCanvasView(NSView):
    """画像を表示してモザイク描画を受け付けるカスタムビュー"""

    def initWithFrame_(self, frame):
        self = objc.super(MosaicCanvasView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._display_image = None   # NSImage
        self._pil_image = None       # PIL Image (フル解像度の編集用)
        self._undo_stack = []
        self._mode = "brush"         # "brush" or "rect"
        self._block_size = 15
        self._brush_size = 30
        self._drag_start = None
        self._drag_current = None
        self._delegate = None
        # ズーム・パン状態
        self._zoom_scale = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_y = 0.0
        self._is_panning = False
        self._pan_start_x = 0.0
        self._pan_start_y = 0.0
        self._pan_offset_start_x = 0.0
        self._pan_offset_start_y = 0.0
        self._setup_tracking()
        return self

    def _setup_tracking(self):
        ta = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            NSTrackingMouseMoved | NSTrackingActiveInActiveApp | NSTrackingInVisibleRect,
            self, None
        )
        self.addTrackingArea_(ta)

    def isFlipped(self):
        return True  # 上から下へ座標

    def acceptsFirstResponder(self):
        return True

    def setImage_(self, pil_img):
        self._pil_image = pil_img.copy()
        self._undo_stack = []
        self._zoom_scale = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_y = 0.0
        self._update_display()

    def _update_display(self):
        if self._pil_image is None:
            return
        self._display_image = pil_to_nsimage(self._pil_image)
        self.setNeedsDisplay_(True)

    def _image_rect(self):
        """画像の描画領域を計算（ズーム・パン対応）"""
        if self._display_image is None:
            return NSMakeRect(0, 0, 0, 0), 1.0
        bounds = self.bounds()
        iw = self._pil_image.width
        ih = self._pil_image.height
        bw = bounds.size.width
        bh = bounds.size.height
        base_scale = min(bw / iw, bh / ih)
        effective_scale = base_scale * self._zoom_scale
        nw = iw * effective_scale
        nh = ih * effective_scale
        x = (bw - nw) / 2.0 + self._pan_offset_x
        y = (bh - nh) / 2.0 + self._pan_offset_y
        return NSMakeRect(x, y, nw, nh), effective_scale

    def drawRect_(self, rect):
        try:
            # 背景
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.17, 0.17, 0.17, 1.0).set()
            NSBezierPath.fillRect_(self.bounds())

            if self._display_image is None:
                # ヒントテキスト（シンプルにNSStringで描画）
                hint = "ここに画像をドラッグ&ドロップ\nまたは「画像を開く」ボタン"
                font = NSFont.systemFontOfSize_(18)
                color = NSColor.grayColor()
                from AppKit import NSFontAttributeName, NSForegroundColorAttributeName
                attrs = {
                    NSFontAttributeName: font,
                    NSForegroundColorAttributeName: color,
                }
                ns_str = Foundation.NSString.stringWithString_(hint)
                size = ns_str.sizeWithAttributes_(attrs)
                bounds = self.bounds()
                x = (bounds.size.width - size.width) / 2
                y = (bounds.size.height - size.height) / 2
                ns_str.drawAtPoint_withAttributes_(NSMakePoint(x, y), attrs)
                return

            img_rect, _ = self._image_rect()
            # isFlippedビューではNSImageが上下反転するため、変換で補正
            NSGraphicsContext.currentContext().saveGraphicsState()
            xf = NSAffineTransform.transform()
            # 画像描画位置に移動し、Y軸を反転
            xf.translateXBy_yBy_(img_rect.origin.x, img_rect.origin.y + img_rect.size.height)
            xf.scaleXBy_yBy_(1.0, -1.0)
            xf.concat()
            self._display_image.drawInRect_fromRect_operation_fraction_(
                NSMakeRect(0, 0, img_rect.size.width, img_rect.size.height),
                NSMakeRect(0, 0, 0, 0),  # ソース全体
                NSCompositingOperationSourceOver,
                1.0
            )
            NSGraphicsContext.currentContext().restoreGraphicsState()

            # 範囲選択中の矩形を描画
            if self._mode == "rect" and self._drag_start and self._drag_current:
                x0, y0 = self._drag_start
                x1, y1 = self._drag_current
                rx = min(x0, x1)
                ry = min(y0, y1)
                rw = abs(x1 - x0)
                rh = abs(y1 - y0)
                NSColor.redColor().set()
                path = NSBezierPath.bezierPathWithRect_(NSMakeRect(rx, ry, rw, rh))
                path.setLineWidth_(2.0)
                pattern = [4.0, 4.0]
                path.setLineDash_count_phase_(pattern, 2, 0)
                path.stroke()
        except Exception as e:
            import sys
            print(f"drawRect_ error: {e}", file=sys.stderr)

    def _view_to_image(self, vx, vy):
        """ビュー座標 → 画像ピクセル座標"""
        if self._pil_image is None:
            return 0, 0
        img_rect, scale = self._image_rect()
        ix = int((vx - img_rect.origin.x) / scale)
        iy = int((vy - img_rect.origin.y) / scale)
        return ix, iy

    def mouseDown_(self, event):
        if self._pil_image is None:
            return
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        vx, vy = loc.x, loc.y

        if self._mode == "rect":
            self._drag_start = (vx, vy)
            self._drag_current = (vx, vy)
        else:
            self._push_undo()
            ix, iy = self._view_to_image(vx, vy)
            self._apply_brush(ix, iy)

    def mouseDragged_(self, event):
        if self._pil_image is None:
            return
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        vx, vy = loc.x, loc.y

        if self._mode == "rect":
            self._drag_current = (vx, vy)
            self.setNeedsDisplay_(True)
        else:
            ix, iy = self._view_to_image(vx, vy)
            self._apply_brush(ix, iy)

    def mouseUp_(self, event):
        if self._pil_image is None:
            return
        if self._mode == "rect" and self._drag_start:
            loc = self.convertPoint_fromView_(event.locationInWindow(), None)
            ix0, iy0 = self._view_to_image(*self._drag_start)
            ix1, iy1 = self._view_to_image(loc.x, loc.y)
            lx, rx = min(ix0, ix1), max(ix0, ix1)
            ly, ry = min(iy0, iy1), max(iy0, iy1)
            if rx - lx > 2 and ry - ly > 2:
                self._push_undo()
                self._apply_mosaic(lx, ly, rx, ry)
            self._drag_start = None
            self._drag_current = None
            self.setNeedsDisplay_(True)

    # --- ズーム（マウスホイール） ---

    def scrollWheel_(self, event):
        """マウスホイールでズーム（カーソル中心）"""
        if self._pil_image is None:
            return
        dy = event.scrollingDeltaY()
        if event.hasPreciseScrollingDeltas():
            zoom_factor = 1.0 + dy * 0.005
        else:
            zoom_factor = 1.0 + dy * 0.05
        zoom_factor = max(0.8, min(1.25, zoom_factor))
        new_zoom = self._zoom_scale * zoom_factor
        new_zoom = max(0.1, min(20.0, new_zoom))
        if abs(new_zoom - self._zoom_scale) < 0.001:
            return
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        mouse_vx = loc.x
        mouse_vy = loc.y
        bounds = self.bounds()
        bw = bounds.size.width
        bh = bounds.size.height
        old_center_x = bw / 2.0 + self._pan_offset_x
        old_center_y = bh / 2.0 + self._pan_offset_y
        dx = mouse_vx - old_center_x
        dy_vec = mouse_vy - old_center_y
        ratio = new_zoom / self._zoom_scale
        new_center_x = mouse_vx - dx * ratio
        new_center_y = mouse_vy - dy_vec * ratio
        self._pan_offset_x = new_center_x - bw / 2.0
        self._pan_offset_y = new_center_y - bh / 2.0
        self._zoom_scale = new_zoom
        self.setNeedsDisplay_(True)
        self._update_status()

    # --- パン（右クリックドラッグ） ---

    def rightMouseDown_(self, event):
        """右クリックでパン開始"""
        if self._pil_image is None:
            return
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        self._is_panning = True
        self._pan_start_x = loc.x
        self._pan_start_y = loc.y
        self._pan_offset_start_x = self._pan_offset_x
        self._pan_offset_start_y = self._pan_offset_y

    def rightMouseDragged_(self, event):
        """右ドラッグでパン実行"""
        if not self._is_panning:
            return
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        dx = loc.x - self._pan_start_x
        dy = loc.y - self._pan_start_y
        self._pan_offset_x = self._pan_offset_start_x + dx
        self._pan_offset_y = self._pan_offset_start_y + dy
        self.setNeedsDisplay_(True)

    def rightMouseUp_(self, event):
        """右クリック解除でパン終了"""
        self._is_panning = False

    # --- フィット・ステータス ---

    def resetZoom(self):
        """ズーム・パンをリセットして画面にフィット"""
        self._zoom_scale = 1.0
        self._pan_offset_x = 0.0
        self._pan_offset_y = 0.0
        self.setNeedsDisplay_(True)
        self._update_status()

    def _update_status(self):
        """ステータスバーにズーム率を通知"""
        if self._delegate and hasattr(self._delegate, '_status_label'):
            if self._delegate._status_label and self._pil_image:
                zoom_pct = int(self._zoom_scale * 100)
                iw, ih = self._pil_image.size
                self._delegate._status_label.setStringValue_(
                    f"{iw} x {ih}  |  ズーム: {zoom_pct}%"
                )

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

    # ドラッグ&ドロップ対応
    def draggingEntered_(self, sender):
        return 1  # NSDragOperationCopy

    def performDragOperation_(self, sender):
        pboard = sender.draggingPasteboard()
        types = pboard.types()
        if "NSFilenamesPboardType" in types:
            files = pboard.propertyListForType_("NSFilenamesPboardType")
            if files and len(files) > 0:
                path = files[0]
                if self._delegate and hasattr(self._delegate, 'loadImageFromPath_'):
                    self._delegate.loadImageFromPath_(path)
                    return True
        if "public.file-url" in types:
            urls = pboard.readObjectsForClasses_options_(
                [NSURL], None
            )
            if urls and len(urls) > 0:
                path = urls[0].path()
                if self._delegate and hasattr(self._delegate, 'loadImageFromPath_'):
                    self._delegate.loadImageFromPath_(path)
                    return True
        return False


class AppDelegate(NSObject):

    def init(self):
        self = objc.super(AppDelegate, self).init()
        self._canvas = None
        self._status_label = None
        self._window = None
        self._mode_brush_btn = None
        self._mode_rect_btn = None
        return self

    def applicationDidFinishLaunching_(self, notification):
        self._setup_menu()
        self._setup_window()

        # コマンドライン引数
        if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
            self.loadImageFromPath_(sys.argv[1])

    def _setup_menu(self):
        menubar = NSMenu.alloc().init()

        # App メニュー
        app_menu_item = NSMenuItem.alloc().init()
        app_menu = NSMenu.alloc().initWithTitle_("モザイクアプリ")
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("終了", "terminate:", "q")
        app_menu.addItem_(quit_item)
        app_menu_item.setSubmenu_(app_menu)
        menubar.addItem_(app_menu_item)

        # ファイルメニュー
        file_menu_item = NSMenuItem.alloc().init()
        file_menu = NSMenu.alloc().initWithTitle_("ファイル")
        open_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("開く...", "openFile:", "o")
        open_item.setTarget_(self)
        file_menu.addItem_(open_item)
        save_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("保存...", "saveFile:", "s")
        save_item.setTarget_(self)
        file_menu.addItem_(save_item)
        file_menu_item.setSubmenu_(file_menu)
        menubar.addItem_(file_menu_item)

        # 編集メニュー
        edit_menu_item = NSMenuItem.alloc().init()
        edit_menu = NSMenu.alloc().initWithTitle_("編集")
        undo_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("元に戻す", "undoAction:", "z")
        undo_item.setTarget_(self)
        edit_menu.addItem_(undo_item)
        edit_menu_item.setSubmenu_(edit_menu)
        menubar.addItem_(edit_menu_item)

        # 表示メニュー
        view_menu_item = NSMenuItem.alloc().init()
        view_menu = NSMenu.alloc().initWithTitle_("表示")
        fit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "画面にフィット", "fitToWindow:", "0"
        )
        fit_item.setTarget_(self)
        view_menu.addItem_(fit_item)
        view_menu_item.setSubmenu_(view_menu)
        menubar.addItem_(view_menu_item)

        NSApp.setMainMenu_(menubar)

    def _setup_window(self):
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                 NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable)
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(100, 100, 1000, 750), style, NSBackingStoreBuffered, False
        )
        self._window.setTitle_("モザイクアプリ")
        self._window.setMinSize_(NSMakeSize(600, 400))

        content = self._window.contentView()
        content_bounds = content.bounds()
        cw = content_bounds.size.width
        ch = content_bounds.size.height

        # --- ツールバー (上部) ---
        toolbar_h = 40
        toolbar_y = ch - toolbar_h
        toolbar = NSView.alloc().initWithFrame_(NSMakeRect(0, toolbar_y, cw, toolbar_h))
        toolbar.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)

        x = 10
        # 画像を開くボタン
        btn_open = NSButton.alloc().initWithFrame_(NSMakeRect(x, 5, 100, 30))
        btn_open.setTitle_("画像を開く")
        btn_open.setBezelStyle_(NSBezelStyleRounded)
        btn_open.setTarget_(self)
        btn_open.setAction_("openFile:")
        toolbar.addSubview_(btn_open)
        x += 108

        btn_save = NSButton.alloc().initWithFrame_(NSMakeRect(x, 5, 60, 30))
        btn_save.setTitle_("保存")
        btn_save.setBezelStyle_(NSBezelStyleRounded)
        btn_save.setTarget_(self)
        btn_save.setAction_("saveFile:")
        toolbar.addSubview_(btn_save)
        x += 68

        btn_undo = NSButton.alloc().initWithFrame_(NSMakeRect(x, 5, 80, 30))
        btn_undo.setTitle_("元に戻す")
        btn_undo.setBezelStyle_(NSBezelStyleRounded)
        btn_undo.setTarget_(self)
        btn_undo.setAction_("undoAction:")
        toolbar.addSubview_(btn_undo)
        x += 100

        # モード: ブラシ
        lbl_mode = NSTextField.labelWithString_("モード:")
        lbl_mode.setFrame_(NSMakeRect(x, 10, 50, 20))
        toolbar.addSubview_(lbl_mode)
        x += 55

        self._mode_brush_btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, 5, 70, 30))
        self._mode_brush_btn.setTitle_("ブラシ")
        self._mode_brush_btn.setButtonType_(NSRadioButton)
        self._mode_brush_btn.setState_(NSOnState)
        self._mode_brush_btn.setTarget_(self)
        self._mode_brush_btn.setAction_("setModeBrush:")
        toolbar.addSubview_(self._mode_brush_btn)
        x += 75

        self._mode_rect_btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, 5, 80, 30))
        self._mode_rect_btn.setTitle_("範囲選択")
        self._mode_rect_btn.setButtonType_(NSRadioButton)
        self._mode_rect_btn.setState_(NSOffState)
        self._mode_rect_btn.setTarget_(self)
        self._mode_rect_btn.setAction_("setModeRect:")
        toolbar.addSubview_(self._mode_rect_btn)
        x += 95

        # モザイク強度スライダー
        lbl_block = NSTextField.labelWithString_("強度:")
        lbl_block.setFrame_(NSMakeRect(x, 10, 40, 20))
        toolbar.addSubview_(lbl_block)
        x += 42

        self._block_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(x, 8, 100, 24))
        self._block_slider.setMinValue_(2)
        self._block_slider.setMaxValue_(50)
        self._block_slider.setIntValue_(15)
        self._block_slider.setTarget_(self)
        self._block_slider.setAction_("blockSizeChanged:")
        toolbar.addSubview_(self._block_slider)
        x += 105

        self._block_label = NSTextField.labelWithString_("15px")
        self._block_label.setFrame_(NSMakeRect(x, 10, 40, 20))
        toolbar.addSubview_(self._block_label)
        x += 48

        # ブラシサイズスライダー
        lbl_brush = NSTextField.labelWithString_("ブラシ:")
        lbl_brush.setFrame_(NSMakeRect(x, 10, 45, 20))
        toolbar.addSubview_(lbl_brush)
        x += 48

        self._brush_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(x, 8, 100, 24))
        self._brush_slider.setMinValue_(10)
        self._brush_slider.setMaxValue_(100)
        self._brush_slider.setIntValue_(30)
        self._brush_slider.setTarget_(self)
        self._brush_slider.setAction_("brushSizeChanged:")
        toolbar.addSubview_(self._brush_slider)
        x += 105

        self._brush_label = NSTextField.labelWithString_("30px")
        self._brush_label.setFrame_(NSMakeRect(x, 10, 40, 20))
        toolbar.addSubview_(self._brush_label)

        content.addSubview_(toolbar)

        # --- ステータスバー (下部) ---
        status_h = 24
        self._status_label = NSTextField.labelWithString_(
            "画像をドラッグ&ドロップ、または「画像を開く」で読み込み")
        self._status_label.setFrame_(NSMakeRect(0, 0, cw, status_h))
        self._status_label.setAutoresizingMask_(NSViewWidthSizable | NSViewMaxYMargin)
        self._status_label.setFont_(NSFont.systemFontOfSize_(12))
        self._status_label.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.93, 0.93, 0.93, 1.0))
        self._status_label.setDrawsBackground_(True)
        content.addSubview_(self._status_label)

        # --- キャンバス (中央) ---
        canvas_y = status_h
        canvas_h = toolbar_y - status_h
        self._canvas = MosaicCanvasView.alloc().initWithFrame_(
            NSMakeRect(0, canvas_y, cw, canvas_h))
        self._canvas.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self._canvas._delegate = self
        # ドラッグ&ドロップ登録
        self._canvas.registerForDraggedTypes_(["NSFilenamesPboardType", "public.file-url"])
        content.addSubview_(self._canvas)

        self._window.makeKeyAndOrderFront_(None)
        self._window.center()
        NSApp.activateIgnoringOtherApps_(True)

    # --- アクション ---

    @objc.IBAction
    def openFile_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setAllowedFileTypes_(["png", "jpg", "jpeg", "bmp", "gif", "tiff", "webp"])
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        if panel.runModal() == 1:
            url = panel.URL()
            if url:
                self.loadImageFromPath_(url.path())

    @objc.IBAction
    def saveFile_(self, sender):
        if self._canvas._pil_image is None:
            return
        panel = NSSavePanel.savePanel()
        panel.setAllowedFileTypes_(["png", "jpg", "bmp"])
        panel.setNameFieldStringValue_("mosaic_image.png")
        if panel.runModal() == 1:
            url = panel.URL()
            if url:
                path = url.path()
                self._canvas._pil_image.save(path)
                self._status_label.setStringValue_(f"保存しました: {os.path.basename(path)}")

    @objc.IBAction
    def undoAction_(self, sender):
        if self._canvas.undo():
            self._status_label.setStringValue_("元に戻しました")
        else:
            self._status_label.setStringValue_("これ以上戻せません")

    @objc.IBAction
    def setModeBrush_(self, sender):
        self._canvas._mode = "brush"
        self._mode_brush_btn.setState_(NSOnState)
        self._mode_rect_btn.setState_(NSOffState)

    @objc.IBAction
    def setModeRect_(self, sender):
        self._canvas._mode = "rect"
        self._mode_brush_btn.setState_(NSOffState)
        self._mode_rect_btn.setState_(NSOnState)

    @objc.IBAction
    def blockSizeChanged_(self, sender):
        val = int(sender.intValue())
        self._canvas._block_size = val
        self._block_label.setStringValue_(f"{val}px")

    @objc.IBAction
    def brushSizeChanged_(self, sender):
        val = int(sender.intValue())
        self._canvas._brush_size = val
        self._brush_label.setStringValue_(f"{val}px")

    @objc.IBAction
    def fitToWindow_(self, sender):
        if self._canvas:
            self._canvas.resetZoom()

    def loadImageFromPath_(self, path):
        path = str(path).strip()
        if not os.path.isfile(path):
            self._status_label.setStringValue_(f"ファイルが見つかりません: {path}")
            return
        try:
            img = Image.open(path)
            img.load()
            img = img.convert("RGB")
        except Exception as e:
            self._status_label.setStringValue_(f"画像を開けません: {e}")
            return
        self._canvas.setImage_(img)
        name = os.path.basename(path)
        self._status_label.setStringValue_(f"{name}  ({img.width} x {img.height})  |  ズーム: 100%")

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()
