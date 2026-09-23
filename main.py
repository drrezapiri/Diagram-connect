import sys
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QMainWindow,
    QToolBar,
)


MODEL_WIDTH = 170
MODEL_HEIGHT = 86
PORT_RADIUS = 7


class ConnectionItem(QGraphicsPathItem):
    def __init__(self, source_port, target_port=None):
        super().__init__()
        self.source_port = source_port
        self.target_port = target_port
        self.preview_end = None

        self.setZValue(-1)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

        source_port.connections.append(self)
        if target_port is not None:
            target_port.connections.append(self)

        self.update_path()

    def set_preview_end(self, scene_pos):
        self.preview_end = scene_pos
        self.update_path()

    def attach_target(self, target_port):
        if self.target_port is target_port:
            return
        if self.target_port is not None and self in self.target_port.connections:
            self.target_port.connections.remove(self)
        self.target_port = target_port
        self.preview_end = None
        if self not in target_port.connections:
            target_port.connections.append(self)
        self.update_path()

    def start_point(self):
        return self.source_port.scenePos()

    def end_point(self):
        if self.target_port is not None:
            return self.target_port.scenePos()
        return self.preview_end or self.start_point()

    def update_path(self):
        start = self.start_point()
        end = self.end_point()
        dx = max(70.0, abs(end.x() - start.x()) * 0.5)

        path = QPainterPath(start)
        path.cubicTo(
            QPointF(start.x() + dx, start.y()),
            QPointF(end.x() - dx, end.y()),
            end,
        )
        self.setPath(path)

        if self.isSelected():
            self.setPen(QPen(QColor("#f0b429"), 3.0))
        else:
            self.setPen(QPen(QColor("#6b93ff"), 2.5))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.update_path()
        return super().itemChange(change, value)

    def detach(self):
        if self in self.source_port.connections:
            self.source_port.connections.remove(self)
        if self.target_port is not None and self in self.target_port.connections:
            self.target_port.connections.remove(self)
        scene = self.scene()
        if scene is not None:
            scene.removeItem(self)


class PortItem(QGraphicsEllipseItem):
    def __init__(self, parent_model, kind):
        r = PORT_RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r, parent_model)
        self.parent_model = parent_model
        self.kind = kind
        self.connections = []

        self.setBrush(QBrush(QColor("#50c878") if kind == "input" else QColor("#ff9f43")))
        self.setPen(QPen(QColor("#1d2433"), 1.5))
        self.setZValue(3)
        self.setCursor(Qt.CrossCursor)

    def mousePressEvent(self, event):
        if self.kind == "output" and event.button() == Qt.LeftButton:
            view_scene = self.scene()
            if isinstance(view_scene, PipelineScene):
                view_scene.begin_connection(self, event.scenePos())
                event.accept()
                return
        super().mousePressEvent(event)


class ModelItem(QGraphicsRectItem):
    _counter = 1

    def __init__(self, title=None):
        super().__init__(0, 0, MODEL_WIDTH, MODEL_HEIGHT)
        self.title = title or f"Model {ModelItem._counter}"
        ModelItem._counter += 1

        self.setBrush(QBrush(QColor("#263248")))
        self.setPen(QPen(QColor("#53627a"), 1.5))
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.OpenHandCursor)

        self.header = QGraphicsSimpleTextItem(self.title, self)
        self.header.setBrush(QBrush(QColor("#f5f7fb")))
        self.header.setPos(14, 11)

        self.input_label = QGraphicsSimpleTextItem("IN", self)
        self.input_label.setBrush(QBrush(QColor("#aeb8c9")))
        self.input_label.setPos(14, 54)

        self.output_label = QGraphicsSimpleTextItem("OUT", self)
        self.output_label.setBrush(QBrush(QColor("#aeb8c9")))
        self.output_label.setPos(MODEL_WIDTH - 42, 54)

        self.input_port = PortItem(self, "input")
        self.input_port.setPos(0, MODEL_HEIGHT / 2)

        self.output_port = PortItem(self, "output")
        self.output_port.setPos(MODEL_WIDTH, MODEL_HEIGHT / 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for port in (self.input_port, self.output_port):
                for connection in list(port.connections):
                    connection.update_path()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            if bool(value):
                self.setPen(QPen(QColor("#f0b429"), 2.5))
            else:
                self.setPen(QPen(QColor("#53627a"), 1.5))
        return super().itemChange(change, value)

    def all_connections(self):
        return list(set(self.input_port.connections + self.output_port.connections))


class PipelineScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(QRectF(-2000, -1500, 4000, 3000))
        self.pending_connection = None

    def add_model(self, title=None, pos=None):
        model = ModelItem(title)
        self.addItem(model)
        model.setPos(pos or QPointF(0, 0))
        return model

    def begin_connection(self, source_port, scene_pos):
        self.cancel_pending_connection()
        self.pending_connection = ConnectionItem(source_port)
        self.addItem(self.pending_connection)
        self.pending_connection.set_preview_end(scene_pos)

    def cancel_pending_connection(self):
        if self.pending_connection is not None:
            self.pending_connection.detach()
            self.pending_connection = None

    def mouseMoveEvent(self, event):
        if self.pending_connection is not None:
            self.pending_connection.set_preview_end(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.pending_connection is not None:
            target = self._input_port_at(event.scenePos())
            if target is not None and target.parent_model is not self.pending_connection.source_port.parent_model:
                self.pending_connection.attach_target(target)
                self.pending_connection = None
            else:
                self.cancel_pending_connection()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _input_port_at(self, scene_pos):
        for item in self.items(scene_pos):
            if isinstance(item, PortItem) and item.kind == "input":
                return item
        return None

    def delete_selected(self):
        selected = list(self.selectedItems())

        for item in selected:
            if isinstance(item, ConnectionItem):
                item.detach()

        for item in selected:
            if isinstance(item, ModelItem):
                for connection in item.all_connections():
                    connection.detach()
                self.removeItem(item)


class PipelineView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setBackgroundBrush(QBrush(QColor("#151b26")))

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Diagram Connect — AI Pipeline Mock-up")
        self.resize(1200, 760)

        self.scene = PipelineScene(self)
        self.view = PipelineView(self.scene)
        self.setCentralWidget(self.view)

        self._build_toolbar()
        self._build_demo()

    def _build_toolbar(self):
        toolbar = QToolBar("Pipeline")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        add_model = QAction("Add model", self)
        add_model.triggered.connect(self.add_model_center)
        toolbar.addAction(add_model)

        delete = QAction("Delete selected", self)
        delete.triggered.connect(self.scene.delete_selected)
        toolbar.addAction(delete)

        clear = QAction("Clear", self)
        clear.triggered.connect(self.clear_scene)
        toolbar.addAction(clear)

        toolbar.addSeparator()

        help_action = QAction("How to connect", self)
        help_action.triggered.connect(
            lambda: self.statusBar().showMessage(
                "Drag from an orange OUT port to a green IN port. Drag blocks to move them. Mouse wheel zooms.",
                9000,
            )
        )
        toolbar.addAction(help_action)

        self.statusBar().showMessage(
            "Drag orange OUT → green IN to create a connector. These lines represent future plugins."
        )

    def add_model_center(self):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        offset = QPointF(-MODEL_WIDTH / 2, -MODEL_HEIGHT / 2)
        self.scene.add_model(pos=center + offset)

    def clear_scene(self):
        self.scene.cancel_pending_connection()
        self.scene.clear()

    def connect(self, source_model, target_model):
        connection = ConnectionItem(source_model.output_port, target_model.input_port)
        self.scene.addItem(connection)
        return connection

    def _build_demo(self):
        source = self.scene.add_model("Model 1", QPointF(-360, -60))
        branch_a = self.scene.add_model("Model 2", QPointF(-60, -150))
        branch_b = self.scene.add_model("Model 3", QPointF(-60, 60))
        final = self.scene.add_model("Model 4", QPointF(260, -45))

        self.connect(source, branch_a)
        self.connect(source, branch_b)
        self.connect(branch_a, final)

        self.view.centerOn(QPointF(20, 20))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
