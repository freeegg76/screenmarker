"""Draw translucent annotations over the Windows desktop."""

# /// script
# requires-python = ">=3.10"
# dependencies = ["PySide6"]
# ///

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import json
import os
import sys

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QEvent,
    QMarginsF,
    QPoint,
    QPointF,
    QRectF,
    QSizeF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QFont,
    QFontMetricsF,
    QPageLayout,
    QPageSize,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPdfWriter,
    QRegion,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QFontComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


@dataclass
class Stroke:
    tool: str
    points: list[QPointF]
    color: QColor
    width: int
    text: str = ""
    font_family: str = ""
    font_size: int = 0


TEXT_EDITABLE_TOOLS = ("text", "rect", "ellipse")
SELECTION_PADDING = 5
HANDLE_SIZE = 8
HANDLE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
}
TEXT_FLAGS = int((Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop).value)
SHAPE_TEXT_FLAGS = int((Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap).value)


class InlineTextEditor(QTextEdit):
    finished = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape or (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.finished.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class QuitHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, app: QApplication, toolbar: "ToolBar") -> None:
        super().__init__()
        self.app = app
        self.toolbar = toolbar

    def nativeEventFilter(self, event_type, message) -> tuple[bool, int]:
        if bytes(event_type) not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        native_message = ctypes.cast(
            int(message), ctypes.POINTER(wintypes.MSG)
        ).contents
        if native_message.message == 0x0312:
            if native_message.wParam == 1:
                self.toolbar.request_quit()
                return True, 0
            if native_message.wParam == 2:
                self.toolbar.toggle_drawing()
                return True, 0
        return False, 0


class DrawingOverlay(QWidget):
    drawing_started = Signal()
    cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setWindowTitle("ScreenMarker")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.tool: str | None = "pen"
        self.color = QColor(255, 230, 0, 115)
        self.width = 24
        self.strokes: list[Stroke] = []
        self.current_stroke: Stroke | None = None
        self.passthrough = False
        self.toolbar: QWidget | None = None

        desktop = QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(desktop)

    def set_passthrough(self, enabled: bool) -> None:
        self.passthrough = enabled
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        self.show()

    def set_tool(self, tool: str | None) -> None:
        self.tool = tool
        self.current_stroke = None
        if self.passthrough != (tool is None):
            self.set_passthrough(tool is None)

    def set_color(self, color: QColor) -> None:
        if self.tool == "pen":
            color.setAlpha(115)
        else:
            color.setAlpha(255)
        self.color = color

    def undo(self) -> None:
        if self.strokes:
            self.strokes.pop()
            self.update()

    def clear(self) -> None:
        self.strokes.clear()
        self.current_stroke = None
        self.update()

    def mousePressEvent(self, event) -> None:
        if (
            event.button() != Qt.MouseButton.LeftButton
            or self.passthrough
            or self.tool is None
        ):
            return
        if self.toolbar and self.toolbar.frameGeometry().contains(
            event.globalPosition().toPoint()
        ):
            return
        # Esc 키를 받을 수 있도록 그리기 시작 시 오버레이에 키보드 포커스를 준다.
        self.activateWindow()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        stroke_color = QColor(self.color)
        if self.tool == "pen":
            stroke_color.setAlpha(115)
        else:
            stroke_color.setAlpha(255)
        points = [point, point] if self.tool in ("rect", "ellipse") else [point]
        self.current_stroke = Stroke(self.tool, points, stroke_color, self.width)
        self.strokes.append(self.current_stroke)
        self.update()
        self.drawing_started.emit()

    def mouseMoveEvent(self, event) -> None:
        if self.current_stroke is None:
            return
        point = event.position()
        if self.current_stroke.tool == "pen":
            self.current_stroke.points.append(point)
        else:
            self.current_stroke.points[1] = point
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.current_stroke = None

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        for stroke in self.strokes:
            if not stroke.points:
                continue
            pen = QPen(stroke.color, stroke.width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            if stroke.tool == "pen":
                path = QPainterPath(stroke.points[0])
                for point in stroke.points[1:]:
                    path.lineTo(point)
                if len(stroke.points) == 1:
                    path.lineTo(stroke.points[0] + QPointF(0.1, 0.1))
                painter.drawPath(path)
            elif len(stroke.points) > 1:
                bounds = QRectF(stroke.points[0], stroke.points[-1]).normalized()
                if stroke.tool == "rect":
                    painter.drawRect(bounds)
                elif stroke.tool == "ellipse":
                    painter.drawEllipse(bounds)

        painter.end()


class WhiteboardCanvas(QWidget):
    changed = Signal()
    selection_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.color = QColor("#20252b")
        self.stroke_width = 5
        self.font_family = QApplication.font().family()
        self.font_size = 24
        self.tool = "pen"
        self.strokes: list[Stroke] = []
        self.current_stroke: Stroke | None = None
        self.selected_stroke: Stroke | None = None
        self.editing_stroke: Stroke | None = None
        self.text_editor: InlineTextEditor | None = None
        self._dragging_stroke: Stroke | None = None
        self._drag_position: QPointF | None = None
        self._selection_moved = False
        self._resize_handle: str | None = None
        self._resize_origin: QPointF | None = None
        self._resize_bounds = QRectF()
        self._resize_points: list[QPointF] = []
        self._resize_font_size = 0
        self.tool_cursor = Qt.CursorShape.CrossCursor
        self.setCursor(self.tool_cursor)
        self.changed.connect(self.fit_contents)

    def fit_contents(self) -> None:
        # 캔버스는 창 크기를 따라가되, 그림이 창 밖으로 나가면 스크롤할 수 있게 최소 크기를 넓힌다.
        bounds = QRectF()
        for stroke in self.strokes:
            bounds = bounds.united(self.stroke_bounds(stroke))
        margin = SELECTION_PADDING * 2
        self.setMinimumSize(
            max(0, int(bounds.right() + margin)), max(0, int(bounds.bottom() + margin))
        )

    def set_tool(self, tool: str) -> None:
        self.finish_text_edit()
        self.tool = tool
        self.tool_cursor = (
            Qt.CursorShape.ArrowCursor if tool == "select" else Qt.CursorShape.CrossCursor
        )
        self.setCursor(self.tool_cursor)

    def undo(self) -> None:
        self.finish_text_edit()
        if self.strokes:
            removed = self.strokes.pop()
            if removed is self.selected_stroke:
                self.selected_stroke = None
            self.update()
            self.changed.emit()

    def clear(self) -> None:
        self.finish_text_edit()
        if self.strokes:
            self.strokes.clear()
            self.selected_stroke = None
            self.update()
            self.changed.emit()

    def set_text_font(self, family: str | None = None, size: int | None = None) -> None:
        if family:
            self.font_family = family
        if size:
            self.font_size = size
        target = self.editing_stroke
        if (
            target is None
            and self.selected_stroke is not None
            and self.selected_stroke.tool in TEXT_EDITABLE_TOOLS
        ):
            target = self.selected_stroke
        if target is not None:
            before = (target.font_family, target.font_size)
            if family:
                target.font_family = family
            if size:
                target.font_size = size
            if before != (target.font_family, target.font_size) and any(
                stroke is target for stroke in self.strokes
            ):
                self.changed.emit()
        if self.text_editor is not None:
            self.style_text_editor()
            self.position_text_editor()
            self.text_editor.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def begin_text_edit(self, stroke: Stroke) -> None:
        self.finish_text_edit()
        if stroke.font_size <= 0:
            if stroke.tool == "text":
                stroke.font_size = self.stroke_font(stroke).pixelSize()
            else:
                stroke.font_size = self.font_size
                stroke.font_family = self.font_family
        self.editing_stroke = stroke
        if any(existing is stroke for existing in self.strokes):
            self.selected_stroke = stroke
        editor = InlineTextEditor(self)
        editor.setAcceptRichText(False)
        editor.setFrameShape(QFrame.Shape.NoFrame)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.document().setDocumentMargin(0)
        editor.setPlainText(stroke.text)
        editor.finished.connect(self.finish_text_edit)
        editor.textChanged.connect(self.position_text_editor)
        self.text_editor = editor
        self.style_text_editor()
        self.position_text_editor()
        editor.show()
        editor.setFocus(Qt.FocusReason.OtherFocusReason)
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        self.update()
        self.selection_changed.emit()

    def finish_text_edit(self) -> None:
        editor = self.text_editor
        stroke = self.editing_stroke
        if editor is None or stroke is None:
            return
        self.text_editor = None
        self.editing_stroke = None
        text = editor.toPlainText()
        editor.hide()
        editor.deleteLater()
        index = next(
            (position for position, existing in enumerate(self.strokes) if existing is stroke),
            None,
        )
        if stroke.tool == "text" and not text.strip():
            if index is not None:
                self.strokes.pop(index)
                self.changed.emit()
            if self.selected_stroke is stroke:
                self.selected_stroke = None
        elif index is None:
            stroke.text = text
            self.strokes.append(stroke)
            self.selected_stroke = stroke
            self.changed.emit()
        elif text != stroke.text:
            stroke.text = text
            self.changed.emit()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def style_text_editor(self) -> None:
        editor = self.text_editor
        stroke = self.editing_stroke
        if editor is None or stroke is None:
            return
        font = self.stroke_font(stroke)
        editor.setFont(font)
        editor.document().setDefaultFont(font)
        option = QTextOption()
        if stroke.tool == "text":
            option.setAlignment(Qt.AlignmentFlag.AlignLeft)
            option.setWrapMode(QTextOption.WrapMode.NoWrap)
            editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        else:
            option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        editor.document().setDefaultTextOption(option)
        color = QColor(stroke.color)
        color.setAlpha(255)
        editor.setStyleSheet(
            "QTextEdit { background: transparent; border: 1px dashed #087e8b; "
            f"color: {color.name()}; }}"
        )

    def position_text_editor(self) -> None:
        editor = self.text_editor
        stroke = self.editing_stroke
        if editor is None or stroke is None:
            return
        metrics = QFontMetricsF(self.stroke_font(stroke))
        text = editor.toPlainText() or " "
        if text.endswith("\n"):
            text += " "
        if stroke.tool == "text":
            size = metrics.boundingRect(QRectF(0, 0, 100000, 100000), TEXT_FLAGS, text).size()
            top_left = stroke.points[0] - QPointF(1, metrics.ascent() + 1)
            rect = QRectF(
                top_left,
                QSizeF(size.width() + metrics.averageCharWidth() * 2 + 2, size.height() + 2),
            )
        else:
            inner = self.shape_text_rect(stroke)
            text_height = metrics.boundingRect(
                QRectF(0, 0, inner.width(), 100000), SHAPE_TEXT_FLAGS, text
            ).height()
            height = min(max(inner.height(), metrics.height() + 2), text_height + 4)
            rect = QRectF(inner.x(), inner.center().y() - height / 2, inner.width(), height)
        editor.setGeometry(rect.toAlignedRect())

    def stroke_font(self, stroke: Stroke) -> QFont:
        font = QFont(stroke.font_family) if stroke.font_family else QFont()
        font.setPixelSize(stroke.font_size if stroke.font_size > 0 else max(16, stroke.width * 4))
        return font

    def text_rect(self, stroke: Stroke) -> QRectF:
        metrics = QFontMetricsF(self.stroke_font(stroke))
        size = metrics.boundingRect(QRectF(0, 0, 100000, 100000), TEXT_FLAGS, stroke.text).size()
        return QRectF(stroke.points[0] - QPointF(0, metrics.ascent()), size)

    @staticmethod
    def shape_text_rect(stroke: Stroke) -> QRectF:
        bounds = QRectF(stroke.points[0], stroke.points[-1]).normalized()
        if stroke.tool == "ellipse":
            inset_x = bounds.width() * 0.146
            inset_y = bounds.height() * 0.146
        else:
            inset_x = inset_y = 6 + stroke.width / 2
        inner = bounds.adjusted(inset_x, inset_y, -inset_x, -inset_y)
        if inner.width() < 20 or inner.height() < 10:
            return QRectF(bounds.center() - QPointF(60, 20), QSizeF(120, 40))
        return inner

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        if self.text_editor is not None:
            self.finish_text_edit()
            if self.tool == "text":
                return
        handle = self.handle_at(point)
        if handle is not None:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.begin_resize(handle, point)
            return
        if self.tool == "select":
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.selected_stroke = self.stroke_at(point)
            self._dragging_stroke = self.selected_stroke
            self._drag_position = point if self.selected_stroke is not None else None
            self._selection_moved = False
            self.update()
            self.selection_changed.emit()
            return
        if self.selected_stroke is not None:
            self.selected_stroke = None
            self.selection_changed.emit()
        if self.tool == "line_eraser":
            self.erase_stroke_at(point)
            return
        if self.tool == "text":
            existing = self.stroke_at(point)
            if existing is not None and existing.tool in TEXT_EDITABLE_TOOLS:
                self.begin_text_edit(existing)
                return
            baseline = point + QPointF(0, QFontMetricsF(self.current_font()).ascent())
            self.begin_text_edit(
                Stroke(
                    "text",
                    [baseline],
                    QColor(self.color),
                    self.stroke_width,
                    "",
                    self.font_family,
                    self.font_size,
                )
            )
            return
        color = QColor("#ffffff") if self.tool == "eraser" else QColor(self.color)
        width = max(24, self.stroke_width * 4) if self.tool == "eraser" else self.stroke_width
        stroke_tool = self.tool if self.tool in ("rect", "ellipse", "line") else "pen"
        points = [point, point] if stroke_tool != "pen" else [point]
        self.current_stroke = Stroke(stroke_tool, points, color, width)
        if stroke_tool in ("rect", "ellipse"):
            self.current_stroke.font_family = self.font_family
            self.current_stroke.font_size = self.font_size
        self.strokes.append(self.current_stroke)
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.tool == "select":
            stroke = self.stroke_at(event.position())
            if stroke is not None and stroke.tool in TEXT_EDITABLE_TOOLS:
                self._dragging_stroke = None
                self._drag_position = None
                self.begin_text_edit(stroke)
                return
        super().mouseDoubleClickEvent(event)

    def current_font(self) -> QFont:
        font = QFont(self.font_family)
        font.setPixelSize(self.font_size)
        return font

    def mouseMoveEvent(self, event) -> None:
        if self._resize_handle is not None:
            self.resize_selected(event.position())
            return
        if (
            self._dragging_stroke is None
            and self.current_stroke is None
            and not event.buttons() & Qt.MouseButton.LeftButton
        ):
            handle = self.handle_at(event.position())
            self.setCursor(HANDLE_CURSORS[handle] if handle else self.tool_cursor)
        if self._dragging_stroke is not None and self._drag_position is not None:
            point = event.position()
            delta = point - self._drag_position
            if delta.x() != 0 or delta.y() != 0:
                self._dragging_stroke.points = [
                    stroke_point + delta for stroke_point in self._dragging_stroke.points
                ]
                self._drag_position = point
                self._selection_moved = True
                self.update()
            return
        if self.current_stroke is not None:
            if self.current_stroke.tool == "pen":
                self.current_stroke.points.append(event.position())
            else:
                self.current_stroke.points[-1] = event.position()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._resize_handle is not None:
                self._resize_handle = None
                self._resize_origin = None
                if self._selection_moved:
                    self._selection_moved = False
                    self.changed.emit()
            elif self._dragging_stroke is not None:
                self._dragging_stroke = None
                self._drag_position = None
                if self._selection_moved:
                    self._selection_moved = False
                    self.changed.emit()
            elif self.current_stroke is not None:
                drawn = self.current_stroke
                self.current_stroke = None
                if self.tool != "eraser":
                    self.selected_stroke = drawn
                    self.setFocus(Qt.FocusReason.MouseFocusReason)
                    self.selection_changed.emit()
                self.update()
                self.changed.emit()

    def stroke_bounds(self, stroke: Stroke) -> QRectF:
        return self.stroke_path(stroke).boundingRect()

    def selection_rect(self) -> QRectF:
        if self.selected_stroke is None:
            return QRectF()
        padding = SELECTION_PADDING
        return self.stroke_bounds(self.selected_stroke).adjusted(
            -padding, -padding, padding, padding
        )

    def handle_points(self) -> dict[str, QPointF]:
        rect = self.selection_rect()
        if rect.isNull():
            return {}
        center = rect.center()
        return {
            "nw": rect.topLeft(),
            "n": QPointF(center.x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), center.y()),
            "se": rect.bottomRight(),
            "s": QPointF(center.x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), center.y()),
        }

    def handle_at(self, point: QPointF) -> str | None:
        if self.selected_stroke is None or self.text_editor is not None:
            return None
        reach = HANDLE_SIZE / 2 + 2
        for name, handle in self.handle_points().items():
            if abs(point.x() - handle.x()) <= reach and abs(point.y() - handle.y()) <= reach:
                return name
        return None

    def begin_resize(self, handle: str, point: QPointF) -> None:
        stroke = self.selected_stroke
        if stroke is None:
            return
        self._resize_handle = handle
        self._resize_origin = point
        self._resize_bounds = self.stroke_bounds(stroke)
        self._resize_points = [QPointF(stroke_point) for stroke_point in stroke.points]
        self._resize_font_size = self.stroke_font(stroke).pixelSize()
        self._selection_moved = False

    def resize_selected(self, point: QPointF) -> None:
        stroke = self.selected_stroke
        handle = self._resize_handle
        if stroke is None or handle is None or self._resize_origin is None:
            return
        old = self._resize_bounds
        delta = point - self._resize_origin
        left, top, right, bottom = old.left(), old.top(), old.right(), old.bottom()
        if "w" in handle:
            left += delta.x()
        if "e" in handle:
            right += delta.x()
        if "n" in handle:
            top += delta.y()
        if "s" in handle:
            bottom += delta.y()

        if stroke.tool == "text":
            if handle in ("e", "w"):
                scale = (right - left) / old.width() if old.width() > 0 else 1
            else:
                scale = (bottom - top) / old.height() if old.height() > 0 else 1
            if not stroke.font_family:
                stroke.font_family = self.stroke_font(stroke).family()
            stroke.font_size = max(4, round(self._resize_font_size * abs(scale)))
            metrics = QFontMetricsF(self.stroke_font(stroke))
            size = metrics.boundingRect(
                QRectF(0, 0, 100000, 100000), TEXT_FLAGS, stroke.text
            ).size()
            new_left = old.right() - size.width() if "w" in handle else old.left()
            new_top = old.bottom() - size.height() if "n" in handle else old.top()
            stroke.points = [QPointF(new_left, new_top + metrics.ascent())]
        else:
            def map_x(x: float) -> float:
                if old.width() <= 0:
                    return x + (left - old.left())
                return left + (x - old.left()) * (right - left) / old.width()

            def map_y(y: float) -> float:
                if old.height() <= 0:
                    return y + (top - old.top())
                return top + (y - old.top()) * (bottom - top) / old.height()

            stroke.points = [
                QPointF(map_x(original.x()), map_y(original.y()))
                for original in self._resize_points
            ]
        self._selection_moved = True
        self.update()

    def keyPressEvent(self, event) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and self.selected_stroke is not None
            and self.selected_stroke.tool in TEXT_EDITABLE_TOOLS
        ):
            self.begin_text_edit(self.selected_stroke)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete and self.selected_stroke is not None:
            selected = self.selected_stroke
            selected_index = next(
                (index for index, stroke in enumerate(self.strokes) if stroke is selected),
                None,
            )
            if selected_index is not None:
                self.strokes.pop(selected_index)
                self.selected_stroke = None
                self.update()
                self.changed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def paint_contents(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for stroke in self.strokes:
            if not stroke.points:
                continue
            pen = QPen(stroke.color, stroke.width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath(stroke.points[0])
            for point in stroke.points[1:]:
                path.lineTo(point)
            if len(stroke.points) == 1:
                path.lineTo(stroke.points[0] + QPointF(0.1, 0.1))
            if stroke.tool == "text":
                if stroke is not self.editing_stroke:
                    painter.setFont(self.stroke_font(stroke))
                    painter.drawText(self.text_rect(stroke), TEXT_FLAGS, stroke.text)
            elif len(stroke.points) > 1 and stroke.tool == "rect":
                painter.drawRect(QRectF(stroke.points[0], stroke.points[-1]).normalized())
            elif len(stroke.points) > 1 and stroke.tool == "ellipse":
                painter.drawEllipse(QRectF(stroke.points[0], stroke.points[-1]).normalized())
            elif len(stroke.points) > 1 and stroke.tool == "line":
                painter.drawLine(stroke.points[0], stroke.points[-1])
            else:
                painter.drawPath(path)
            if (
                stroke.tool in ("rect", "ellipse")
                and stroke.text
                and len(stroke.points) > 1
                and stroke is not self.editing_stroke
            ):
                painter.setPen(QPen(stroke.color))
                painter.setFont(self.stroke_font(stroke))
                painter.drawText(self.shape_text_rect(stroke), SHAPE_TEXT_FLAGS, stroke.text)

    def erase_stroke_at(self, point: QPointF) -> None:
        for index in range(len(self.strokes) - 1, -1, -1):
            stroke = self.strokes[index]
            path = self.stroke_path(stroke)
            if stroke.tool == "text":
                hit = path.contains(point)
            else:
                stroker = QPainterPathStroker()
                stroker.setWidth(max(14, stroke.width + 8))
                hit = stroker.createStroke(path).contains(point)
            if hit:
                self.strokes.pop(index)
                if stroke is self.selected_stroke:
                    self.selected_stroke = None
                self.update()
                self.changed.emit()
                return

    def stroke_path(self, stroke: Stroke) -> QPainterPath:
        path = QPainterPath()
        if not stroke.points:
            return path
        if stroke.tool == "text":
            path.addRect(self.text_rect(stroke))
        elif stroke.tool in ("rect", "ellipse") and len(stroke.points) > 1:
            bounds = QRectF(stroke.points[0], stroke.points[-1]).normalized()
            if stroke.tool == "rect":
                path.addRect(bounds)
            else:
                path.addEllipse(bounds)
        else:
            path.moveTo(stroke.points[0])
            for point in stroke.points[1:]:
                path.lineTo(point)
        return path

    def stroke_at(self, point: QPointF) -> Stroke | None:
        for stroke in reversed(self.strokes):
            path = self.stroke_path(stroke)
            if stroke.tool in ("text", "rect", "ellipse") and path.contains(point):
                return stroke
            stroker = QPainterPathStroker()
            stroker.setWidth(max(14, stroke.width + 8))
            if stroker.createStroke(path).contains(point):
                return stroke
        return None

    def paint_selection(self, painter: QPainter) -> None:
        if self.selected_stroke is None:
            return
        pen = QPen(QColor("#087e8b"), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.selection_rect())
        if self.text_editor is not None:
            return
        painter.setPen(QPen(QColor("#087e8b"), 1))
        painter.setBrush(QColor("#ffffff"))
        half = HANDLE_SIZE / 2
        for handle in self.handle_points().values():
            painter.drawRect(
                QRectF(handle.x() - half, handle.y() - half, HANDLE_SIZE, HANDLE_SIZE)
            )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self.paint_contents(painter)
        self.paint_selection(painter)
        painter.end()


class WhiteboardWindow(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("ScreenMarker 화이트보드")
        screen = parent.screen() or QApplication.primaryScreen()
        self.resize(screen.availableGeometry().size())
        self.canvas = WhiteboardCanvas()
        self.file_path: str | None = None
        self.dirty = False
        self.saved_strokes: list[Stroke] = []
        self.tool_buttons: dict[str, QPushButton] = {}

        layout = QHBoxLayout()
        for tool, label in (
            ("select", "↖"),
            ("pen", "펜"),
            ("eraser", "부분 지우개"),
            ("line_eraser", "라인 지우개"),
            ("rect", "사각형"),
            ("ellipse", "타원"),
            ("line", "선"),
            ("text", "텍스트"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            if tool == "select":
                button.setToolTip("개체 선택 및 이동")
            button.clicked.connect(lambda checked=False, selected=tool: self.select_tool(selected))
            layout.addWidget(button)
            self.tool_buttons[tool] = button
        self.tool_buttons["pen"].setChecked(True)

        color_button = QPushButton("색상")
        color_button.clicked.connect(self.choose_color)
        layout.addWidget(color_button)

        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(1, 24)
        self.width_slider.setValue(self.canvas.stroke_width)
        self.width_slider.setFixedWidth(100)
        self.width_slider.setToolTip("선 굵기")
        self.width_slider.valueChanged.connect(self.set_width)
        layout.addWidget(self.width_slider)

        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.canvas.font_family))
        self.font_combo.setFixedWidth(160)
        self.font_combo.setToolTip("글꼴")
        self.font_combo.currentFontChanged.connect(
            lambda font: self.canvas.set_text_font(family=font.family())
        )
        layout.addWidget(self.font_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 200)
        self.font_size_spin.setValue(self.canvas.font_size)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setToolTip("글자 크기")
        self.font_size_spin.valueChanged.connect(
            lambda size: self.canvas.set_text_font(size=size)
        )
        layout.addWidget(self.font_size_spin)

        undo_button = QPushButton("되돌리기")
        undo_button.clicked.connect(self.canvas.undo)
        layout.addWidget(undo_button)

        clear_button = QPushButton("전체 지우기")
        clear_button.clicked.connect(self.canvas.clear)
        layout.addWidget(clear_button)

        open_button = QPushButton("열기")
        open_button.clicked.connect(self.open_board)
        layout.addWidget(open_button)

        save_button = QPushButton("보드 저장")
        save_button.clicked.connect(self.save_board)
        layout.addWidget(save_button)

        pdf_button = QPushButton("PDF 내보내기")
        pdf_button.clicked.connect(self.export_pdf)
        layout.addWidget(pdf_button)

        scroll_area = QScrollArea()
        scroll_area.setWidget(self.canvas)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        outer_layout = QVBoxLayout(self)
        outer_layout.addLayout(layout)
        outer_layout.addWidget(scroll_area)
        self.canvas.changed.connect(self.mark_dirty)
        self.canvas.selection_changed.connect(self.sync_font_controls)
        self.update_title()

    def select_tool(self, tool: str) -> None:
        self.canvas.set_tool(tool)
        if tool == "select":
            self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        for name, button in self.tool_buttons.items():
            button.setChecked(name == tool)

    def sync_font_controls(self) -> None:
        stroke = self.canvas.editing_stroke or self.canvas.selected_stroke
        if stroke is None or stroke.tool not in TEXT_EDITABLE_TOOLS or stroke.font_size <= 0:
            return
        font = self.canvas.stroke_font(stroke)
        self.canvas.font_family = font.family()
        self.canvas.font_size = font.pixelSize()
        self.font_combo.blockSignals(True)
        self.font_size_spin.blockSignals(True)
        self.font_combo.setCurrentFont(font)
        self.font_size_spin.setValue(font.pixelSize())
        self.font_combo.blockSignals(False)
        self.font_size_spin.blockSignals(False)

    def choose_color(self) -> None:
        selected = QColorDialog.getColor(self.canvas.color, self, "그리기 색상 선택")
        if selected.isValid():
            self.canvas.color = selected
            self.select_tool("pen")

    def set_width(self, width: int) -> None:
        self.canvas.stroke_width = width

    def mark_dirty(self) -> None:
        self.dirty = True
        self.update_title()

    def update_title(self) -> None:
        name = os.path.basename(self.file_path) if self.file_path else "새 보드"
        marker = " *" if self.dirty else ""
        self.setWindowTitle(f"{name}{marker} - ScreenMarker 화이트보드")

    def save_board(self) -> bool:
        self.canvas.finish_text_edit()
        path = self.file_path
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "화이트보드 저장", "화이트보드.sboard", "ScreenMarker 보드 (*.sboard)"
            )
        if not path:
            return False
        if not path.lower().endswith(".sboard"):
            path += ".sboard"
        data = {
            "version": 1,
            "strokes": [
                {
                    "tool": stroke.tool,
                    "color": stroke.color.name(QColor.NameFormat.HexArgb),
                    "width": stroke.width,
                    "points": [[point.x(), point.y()] for point in stroke.points],
                    "text": stroke.text,
                    "font_family": stroke.font_family,
                    "font_size": stroke.font_size,
                }
                for stroke in self.canvas.strokes
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as board_file:
                json.dump(data, board_file, ensure_ascii=False, indent=2)
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "저장 실패", f"화이트보드를 저장할 수 없습니다.\n{error}")
            return False
        self.file_path = path
        self.dirty = False
        self.saved_strokes = self.copy_strokes(self.canvas.strokes)
        self.update_title()
        return True

    def open_board(self) -> None:
        self.canvas.finish_text_edit()
        path, _ = QFileDialog.getOpenFileName(
            self, "화이트보드 열기", "", "ScreenMarker 보드 (*.sboard)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as board_file:
                data = json.load(board_file)
            if (
                not isinstance(data, dict)
                or data.get("version") != 1
                or not isinstance(data.get("strokes"), list)
            ):
                raise ValueError("지원하지 않는 화이트보드 파일입니다.")
            strokes = []
            for item in data["strokes"]:
                color = QColor(item["color"])
                points = [QPointF(float(x), float(y)) for x, y in item["points"]]
                width = int(item["width"])
                tool = item.get("tool", "pen")
                text = item.get("text", "")
                font_family = item.get("font_family", "")
                font_size = int(item.get("font_size", 0))
                if (
                    not color.isValid()
                    or width < 1
                    or not points
                    or tool not in ("pen", "rect", "ellipse", "line", "text")
                    or not isinstance(text, str)
                    or not isinstance(font_family, str)
                    or font_size < 0
                ):
                    raise ValueError("잘못된 획 데이터가 포함되어 있습니다.")
                strokes.append(
                    Stroke(tool, points, color, width, text, font_family, font_size)
                )
        except (OSError, TypeError, ValueError, KeyError, IndexError) as error:
            QMessageBox.warning(self, "열기 실패", f"화이트보드 파일을 열 수 없습니다.\n{error}")
            return
        if not self.confirm_discard_or_save():
            return
        self.canvas.strokes = strokes
        self.canvas.current_stroke = None
        self.canvas.selected_stroke = None
        self.canvas._dragging_stroke = None
        self.canvas._drag_position = None
        self.canvas.fit_contents()
        self.canvas.update()
        self.file_path = path
        self.dirty = False
        self.saved_strokes = self.copy_strokes(strokes)
        self.update_title()

    @staticmethod
    def copy_strokes(strokes: list[Stroke]) -> list[Stroke]:
        return [
            Stroke(
                stroke.tool,
                [QPointF(point) for point in stroke.points],
                QColor(stroke.color),
                stroke.width,
                stroke.text,
                stroke.font_family,
                stroke.font_size,
            )
            for stroke in strokes
        ]

    def confirm_discard_or_save(self) -> bool:
        self.canvas.finish_text_edit()
        if not self.dirty:
            return True
        dialog = QMessageBox(self)
        dialog.setWindowTitle("저장 확인")
        dialog.setText("변경 내용을 화이트보드 파일로 저장하시겠습니까?")
        save_button = dialog.addButton("저장", QMessageBox.ButtonRole.AcceptRole)
        discard_button = dialog.addButton("저장 안 함", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        selected = dialog.clickedButton()
        if selected is save_button:
            return self.save_board()
        if selected is discard_button:
            self.canvas.strokes = self.copy_strokes(self.saved_strokes)
            self.canvas.current_stroke = None
            self.canvas.selected_stroke = None
            self.canvas.fit_contents()
            self.canvas.update()
            self.dirty = False
            self.update_title()
            return True
        return False

    def export_pdf(self) -> bool:
        self.canvas.finish_text_edit()
        path, _ = QFileDialog.getSaveFileName(
            self, "PDF로 내보내기", "화이트보드.pdf", "PDF 파일 (*.pdf)"
        )
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        writer = QPdfWriter(path)
        writer.setResolution(300)
        writer.setPageLayout(
            QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Landscape,
                QMarginsF(8, 8, 8, 8),
            )
        )
        painter = QPainter(writer)
        if not painter.isActive():
            QMessageBox.warning(self, "내보내기 실패", "PDF 파일을 만들 수 없습니다.")
            return False
        page = writer.pageLayout().paintRectPixels(writer.resolution())
        scale = min(page.width() / self.canvas.width(), page.height() / self.canvas.height())
        painter.translate(
            page.x() + (page.width() - self.canvas.width() * scale) / 2,
            page.y() + (page.height() - self.canvas.height() * scale) / 2,
        )
        painter.scale(scale, scale)
        self.canvas.paint_contents(painter)
        painter.end()
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.confirm_discard_or_save():
            event.accept()
        else:
            event.ignore()


class ToolBar(QWidget):
    def __init__(self, overlay: DrawingOverlay) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.overlay = overlay
        self.overlay.toolbar = self
        self.whiteboard: WhiteboardWindow | None = None
        self._drag_start_global: QPoint | None = None
        self._drag_start_position: QPoint | None = None
        self._dragging = False
        self.setWindowTitle("ScreenMarker 도구")
        self.setObjectName("toolbar")
        self.setToolTip("버튼을 누른 채 끌어 도구 상자 이동")
        self.setStyleSheet(
            """
            QWidget#toolbar { background: #20252b; border: 1px solid #424b55; }
            QPushButton {
                color: #f3f5f7; background: #303840; border: 1px solid #4b5661;
                padding: 6px 9px; min-height: 22px;
            }
            QPushButton:hover { background: #414c57; }
            QPushButton:checked { background: #087e8b; border-color: #20b7c2; }
            QLabel { color: #e2e7eb; }
            QSlider::groove:horizontal { height: 4px; background: #59636d; }
            QSlider::handle:horizontal {
                background: #37c2bd; width: 12px; margin: -5px 0; border-radius: 6px;
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(5)

        self.drag_handle = QLabel("도구")
        self.drag_handle.setToolTip("여기를 누른 채 끌어 도구 상자 이동")
        self.drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        layout.addWidget(self.drag_handle)

        self.tool_buttons: dict[str, QPushButton] = {}
        self.last_tool = "pen"
        for tool, label in (("pen", "형광펜"), ("rect", "□ 사각형"), ("ellipse", "○ 원")):
            button = QPushButton(label)
            button.setCheckable(True)
            if tool in ("rect", "ellipse"):
                button.setToolTip("켜고 누른 채 드래그하여 그리기, 다시 누르면 끄고 아래 창 조작")
            else:
                button.setToolTip("켜면 그리기, 다시 누르면 끄고 아래 창 조작")
            button.clicked.connect(lambda checked=False, selected=tool: self.select_tool(selected))
            layout.addWidget(button)
            self.tool_buttons[tool] = button
        self.tool_buttons["pen"].setChecked(True)

        self.color_button = QPushButton("색상")
        self.color_button.clicked.connect(self.choose_color)
        layout.addWidget(self.color_button)

        self.color_swatches: dict[str, QPushButton] = {}
        for name, color in (
            ("노랑", "#ffe600"),
            ("빨강", "#ff3b30"),
            ("초록", "#34c759"),
            ("파랑", "#2684ff"),
            ("분홍", "#ff4fa3"),
            ("흰색", "#ffffff"),
        ):
            button = QPushButton()
            button.setFixedSize(22, 22)
            button.setCheckable(True)
            button.setToolTip(name)
            button.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 2px solid #59636d; padding: 0; }}"
                "QPushButton:checked { border: 3px solid #20b7c2; }"
            )
            button.clicked.connect(
                lambda checked=False, selected=color: self.select_color(selected)
            )
            layout.addWidget(button)
            self.color_swatches[color] = button

        self.width_label = QLabel("굵기")
        layout.addWidget(self.width_label)
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(2, 40)
        self.width_slider.setValue(24)
        self.width_slider.setFixedWidth(90)
        self.width_slider.setToolTip("선 굵기")
        self.width_slider.valueChanged.connect(self.set_width)
        layout.addWidget(self.width_slider)

        undo_button = QPushButton("되돌리기")
        undo_button.clicked.connect(self.overlay.undo)
        layout.addWidget(undo_button)

        clear_button = QPushButton("전부 삭제")
        clear_button.clicked.connect(self.overlay.clear)
        layout.addWidget(clear_button)

        whiteboard_button = QPushButton("화이트보드")
        whiteboard_button.clicked.connect(self.open_whiteboard)
        layout.addWidget(whiteboard_button)

        close_button = QPushButton("닫기")
        close_button.setToolTip("Ctrl+Alt+Q: 종료")
        close_button.clicked.connect(self.request_quit)
        layout.addWidget(close_button)

        self.color_button.setStyleSheet("background: #ffe600; color: #20252b;")
        self.update_color_button()
        self.adjustSize()
        self.position_on_screen()
        self.update_overlay_mask()
        self.drag_handle.installEventFilter(self)
        self.overlay.drawing_started.connect(
            self.keep_on_top, Qt.ConnectionType.QueuedConnection
        )
        self.overlay.cancel_requested.connect(self.cancel_drawing)

    def position_on_screen(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.x() + (screen.width() - self.width()) // 2,
            screen.y() + 18,
        )

    def update_overlay_mask(self) -> None:
        top_left = self.overlay.mapFromGlobal(self.mapToGlobal(self.rect().topLeft()))
        toolbar_region = QRegion(top_left.x(), top_left.y(), self.width(), self.height())
        self.overlay.setMask(QRegion(self.overlay.rect()).subtracted(toolbar_region))

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self.update_overlay_mask()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update_overlay_mask()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_start_global = event.globalPosition().toPoint()
                self._drag_start_position = self.pos()
                self._dragging = False
        elif (
            event.type() == QEvent.Type.MouseMove
            and self._drag_start_global is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            global_position = event.globalPosition().toPoint()
            offset = global_position - self._drag_start_global
            if not self._dragging and offset.manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                if isinstance(watched, QPushButton):
                    watched.setDown(False)
            if self._dragging:
                self.move(self._drag_start_position + offset)
                return True
        elif (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and self._drag_start_global is not None
        ):
            was_dragging = self._dragging
            self._drag_start_global = None
            self._drag_start_position = None
            self._dragging = False
            if was_dragging:
                return True
        return super().eventFilter(watched, event)

    def keep_on_top(self) -> None:
        self.raise_()
        if sys.platform == "win32":
            ctypes.windll.user32.SetWindowPos(
                int(self.winId()), -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010
            )

    def open_whiteboard(self) -> None:
        if self.whiteboard is None:
            self.whiteboard = WhiteboardWindow(self)
        self.whiteboard.showMaximized()
        self.whiteboard.raise_()
        self.whiteboard.activateWindow()

    def request_quit(self) -> None:
        if self.whiteboard is not None and self.whiteboard.isVisible():
            if not self.whiteboard.close():
                return
        QApplication.quit()

    def select_tool(self, tool: str) -> None:
        if self.overlay.tool == tool:
            self.set_active_tool(None)
        else:
            self.set_active_tool(tool)

    def set_active_tool(self, tool: str | None) -> None:
        if tool is not None:
            self.last_tool = tool
        self.overlay.set_tool(tool)
        for name, button in self.tool_buttons.items():
            button.setChecked(name == tool)
        self.update_color_button()
        self.keep_on_top()

    def choose_color(self) -> None:
        selected = QColorDialog.getColor(self.overlay.color, self, "그리기 색상 선택")
        if selected.isValid():
            self.overlay.set_color(selected)
            self.update_color_button()

    def select_color(self, color: str) -> None:
        self.overlay.set_color(QColor(color))
        self.update_color_button()

    def update_color_button(self) -> None:
        color = QColor(self.overlay.color)
        color.setAlpha(255)
        text_color = "#20252b" if color.lightness() > 145 else "#ffffff"
        self.color_button.setStyleSheet(
            f"background: {color.name()}; color: {text_color};"
        )
        for name, button in self.color_swatches.items():
            button.setChecked(name.lower() == color.name().lower())

    def set_width(self, width: int) -> None:
        self.overlay.width = width

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_drawing()
            event.accept()
            return
        super().keyPressEvent(event)

    def cancel_drawing(self) -> None:
        self.overlay.clear()
        self.set_active_tool(None)

    def toggle_drawing(self) -> None:
        self.set_active_tool(self.last_tool if self.overlay.tool is None else None)


def main() -> int:
    if sys.platform == "win32":
        os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication(sys.argv)
    app.setApplicationName("ScreenMarker")
    overlay = DrawingOverlay()
    toolbar = ToolBar(overlay)
    overlay.show()
    toolbar.show()
    toolbar.keep_on_top()
    if sys.platform == "win32":
        hotkey_filter = QuitHotkeyFilter(app, toolbar)
        app.installNativeEventFilter(hotkey_filter)
        user32 = ctypes.windll.user32
        modifiers = 0x0001 | 0x0002 | 0x4000
        registered_hotkeys = [
            (hotkey_id, user32.RegisterHotKey(None, hotkey_id, modifiers, ord(key)))
            for hotkey_id, key in ((1, "Q"), (2, "W"))
        ]
        failed_keys = [
            "Ctrl+Alt+Q" if hotkey_id == 1 else "Ctrl+Alt+W"
            for hotkey_id, registered in registered_hotkeys
            if not registered
        ]
        if failed_keys:
            QMessageBox.warning(
                toolbar,
                "단축키 등록 실패",
                f"다른 프로그램에서 사용 중이거나 등록할 수 없습니다: {', '.join(failed_keys)}",
            )
        try:
            return app.exec()
        finally:
            for hotkey_id, registered in registered_hotkeys:
                if registered:
                    user32.UnregisterHotKey(None, hotkey_id)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
