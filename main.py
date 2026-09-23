import sys
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QGraphicsEllipseItem, QGraphicsItem,
    QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
    QToolBar, QVBoxLayout, QWidget,
)

MODEL_WIDTH, MODEL_HEIGHT, PORT_RADIUS = 170, 86, 7

DEFAULT_MODELS = ["nnU-Net v2", "MONAI Model", "Generic Segmentation Model"]
DEFAULT_PLUGINS = [
    ("Direct", "#6b93ff"),
    ("Segmentation → Spatial location", "#e056fd"),
    ("Segmentation → Volume", "#ff9f43"),
    ("Crop / ROI", "#20bf6b"),
]


class ConnectionItem(QGraphicsPathItem):
    def __init__(self, source_port, target_port=None, plugin_name="Direct", color="#6b93ff"):
        super().__init__()
        self.source_port, self.target_port = source_port, target_port
        self.plugin_name, self.color = plugin_name, color
        self.preview_end = None
        self.setZValue(-1)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        source_port.connections.append(self)
        if target_port:
            target_port.connections.append(self)
        self.update_path()

    def set_preview_end(self, pos):
        self.preview_end = pos
        self.update_path()

    def attach_target(self, port):
        self.target_port = port
        self.preview_end = None
        if self not in port.connections:
            port.connections.append(self)
        self.update_path()

    def update_path(self):
        start = self.source_port.scenePos()
        end = self.target_port.scenePos() if self.target_port else (self.preview_end or start)
        dx = max(70.0, abs(end.x() - start.x()) * .5)
        path = QPainterPath(start)
        path.cubicTo(QPointF(start.x()+dx, start.y()), QPointF(end.x()-dx, end.y()), end)
        self.setPath(path)
        self.setPen(QPen(QColor("#f0b429") if self.isSelected() else QColor(self.color),
                         3.5 if self.isSelected() else 2.5))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.update_path()
        return super().itemChange(change, value)

    def detach(self):
        if self in self.source_port.connections:
            self.source_port.connections.remove(self)
        if self.target_port and self in self.target_port.connections:
            self.target_port.connections.remove(self)
        if self.scene():
            self.scene().removeItem(self)


class PortItem(QGraphicsEllipseItem):
    def __init__(self, model, kind):
        r = PORT_RADIUS
        super().__init__(-r, -r, 2*r, 2*r, model)
        self.parent_model, self.kind, self.connections = model, kind, []
        self.setBrush(QBrush(QColor("#50c878") if kind == "input" else QColor("#ff9f43")))
        self.setPen(QPen(QColor("#1d2433"), 1.5))
        self.setZValue(3)
        self.setCursor(Qt.CrossCursor)

    def mousePressEvent(self, event):
        if self.kind == "output" and event.button() == Qt.LeftButton:
            self.scene().begin_connection(self, event.scenePos())
            event.accept()
            return
        super().mousePressEvent(event)


class ModelItem(QGraphicsRectItem):
    def __init__(self, title):
        super().__init__(0, 0, MODEL_WIDTH, MODEL_HEIGHT)
        self.title = title
        self.setBrush(QBrush(QColor("#263248")))
        self.setPen(QPen(QColor("#53627a"), 1.5))
        for flag in (QGraphicsItem.ItemIsMovable, QGraphicsItem.ItemIsSelectable,
                     QGraphicsItem.ItemSendsGeometryChanges):
            self.setFlag(flag, True)
        label = QGraphicsSimpleTextItem(title, self)
        label.setBrush(QBrush(QColor("#f5f7fb"))); label.setPos(14, 11)
        for text, x in (("IN", 14), ("OUT", MODEL_WIDTH-42)):
            lab = QGraphicsSimpleTextItem(text, self)
            lab.setBrush(QBrush(QColor("#aeb8c9"))); lab.setPos(x, 54)
        self.input_port, self.output_port = PortItem(self, "input"), PortItem(self, "output")
        self.input_port.setPos(0, MODEL_HEIGHT/2)
        self.output_port.setPos(MODEL_WIDTH, MODEL_HEIGHT/2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for p in (self.input_port, self.output_port):
                for c in list(p.connections): c.update_path()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.setPen(QPen(QColor("#f0b429") if value else QColor("#53627a"), 2.5 if value else 1.5))
        return super().itemChange(change, value)

    def all_connections(self):
        return list(set(self.input_port.connections + self.output_port.connections))


class PipelineScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(QRectF(-2000, -1500, 4000, 3000))
        self.pending_connection = None
        self.active_plugin = ("Direct", "#6b93ff")

    def add_model(self, title, pos):
        m = ModelItem(title); self.addItem(m); m.setPos(pos); return m

    def begin_connection(self, port, pos):
        self.cancel_pending()
        name, color = self.active_plugin
        self.pending_connection = ConnectionItem(port, plugin_name=name, color=color)
        self.addItem(self.pending_connection)
        self.pending_connection.set_preview_end(pos)

    def cancel_pending(self):
        if self.pending_connection:
            self.pending_connection.detach(); self.pending_connection = None

    def mouseMoveEvent(self, event):
        if self.pending_connection:
            self.pending_connection.set_preview_end(event.scenePos()); event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.pending_connection:
            target = next((i for i in self.items(event.scenePos())
                           if isinstance(i, PortItem) and i.kind == "input"), None)
            if target and target.parent_model is not self.pending_connection.source_port.parent_model:
                self.pending_connection.attach_target(target); self.pending_connection = None
            else:
                self.cancel_pending()
            event.accept(); return
        super().mouseReleaseEvent(event)

    def delete_selected(self):
        selected = list(self.selectedItems())
        for i in selected:
            if isinstance(i, ConnectionItem): i.detach()
        for i in selected:
            if isinstance(i, ModelItem):
                for c in i.all_connections(): c.detach()
                self.removeItem(i)


class PipelineView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setBackgroundBrush(QBrush(QColor("#151b26")))

    def wheelEvent(self, event):
        f = 1.15 if event.angleDelta().y() > 0 else 1/1.15
        self.scale(f, f)


class LibraryPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Models</b>"))
        self.models = QListWidget()
        for name in DEFAULT_MODELS: self.models.addItem(name)
        self.models.itemDoubleClicked.connect(lambda i: window.add_named_model(i.text()))
        layout.addWidget(self.models)
        add_model = QPushButton("+ Add model type")
        add_model.clicked.connect(self.add_model_type); layout.addWidget(add_model)

        layout.addWidget(QLabel("<b>Plugins / connectors</b>"))
        self.plugins = QListWidget()
        for name, color in DEFAULT_PLUGINS: self.add_plugin_item(name, color)
        self.plugins.currentItemChanged.connect(self.plugin_selected)
        layout.addWidget(self.plugins)
        add_plugin = QPushButton("+ Add plugin type")
        add_plugin.clicked.connect(self.add_plugin_type); layout.addWidget(add_plugin)
        layout.addStretch()
        self.plugins.setCurrentRow(0)

    def add_plugin_item(self, name, color):
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole, color)
        item.setForeground(QColor(color))
        self.plugins.addItem(item)

    def add_model_type(self):
        name, ok = QInputDialog.getText(self, "Add model type", "Model name:")
        if ok and name.strip(): self.models.addItem(name.strip())

    def add_plugin_type(self):
        name, ok = QInputDialog.getText(self, "Add plugin type", "Plugin name:")
        if ok and name.strip():
            palette = ["#45aaf2", "#a55eea", "#26de81", "#fd9644", "#fc5c65", "#2bcbba"]
            self.add_plugin_item(name.strip(), palette[self.plugins.count() % len(palette)])

    def plugin_selected(self, current, previous):
        if current:
            self.window.scene.active_plugin = (current.text(), current.data(Qt.UserRole))
            self.window.statusBar().showMessage(
                f"Active connector: {current.text()} — drag OUT → IN to use it."
            )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Diagram Connect — AI Pipeline Mock-up")
        self.resize(1300, 800)
        self.scene = PipelineScene(self)
        self.view = PipelineView(self.scene)
        self.setCentralWidget(self.view)
        self.build_library()
        self.build_toolbar()
        self.build_demo()

    def build_library(self):
        dock = QDockWidget("Pipeline Library", self)
        dock.setWidget(LibraryPanel(self))
        dock.setMinimumWidth(260)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def build_toolbar(self):
        tb = QToolBar("Pipeline"); tb.setMovable(False); self.addToolBar(tb)
        delete = QAction("Delete selected", self); delete.triggered.connect(self.scene.delete_selected); tb.addAction(delete)
        clear = QAction("Clear canvas", self); clear.triggered.connect(self.scene.clear); tb.addAction(clear)

    def add_named_model(self, name):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_model(name, center - QPointF(MODEL_WIDTH/2, MODEL_HEIGHT/2))

    def connect(self, a, b, plugin):
        name, color = plugin
        c = ConnectionItem(a.output_port, b.input_port, name, color)
        self.scene.addItem(c)

    def build_demo(self):
        a = self.scene.add_model("nnU-Net v2", QPointF(-330, -40))
        b = self.scene.add_model("MONAI Model", QPointF(60, -40))
        self.connect(a, b, DEFAULT_PLUGINS[0])
        self.view.centerOn(QPointF(0, 0))


def main():
    app = QApplication(sys.argv); app.setStyle("Fusion")
    w = MainWindow(); w.show(); sys.exit(app.exec())


if __name__ == "__main__":
    main()
