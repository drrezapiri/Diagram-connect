import sys
import json
import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QDockWidget, QFormLayout,
    QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsPolygonItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QToolBar, QVBoxLayout, QWidget,
)

MODEL_WIDTH = 220
PORT_RADIUS = 7
PLUGIN_MIN_DIAMETER = 150

DEFAULT_MODELS = [
    {"name": "nnU-Net v2", "inputs": ["Image"], "outputs": ["Segmentation", "Segmentation"]},
    {"name": "MONAI Model", "inputs": ["Image"], "outputs": ["Segmentation"]},
    {"name": "Measurement Model", "inputs": ["Segmentation"], "outputs": ["Volume"]},
]

DEFAULT_PLUGINS = [
    {"name": "Spatial locator", "inputs": ["Segmentation"], "outputs": ["Spatial location"], "color": "#e056fd"},
    {"name": "Volume extractor", "inputs": ["Segmentation"], "outputs": ["Volume"], "color": "#ff9f43"},
    {"name": "Crop / ROI", "inputs": ["Image", "Spatial location"], "outputs": ["Image"], "color": "#20bf6b"},
    {"name": "Feature extractor", "inputs": ["Segmentation", "Image"], "outputs": ["Scalar", "Table"], "color": "#45aaf2"},
]


class DefinitionDialog(QDialog):
    def __init__(self, title, fields, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.widgets = {}
        for key, label, default in fields:
            widget = QLineEdit(default)
            self.widgets[key] = widget
            form.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self, key):
        return self.widgets[key].text().strip()


class PortItem(QGraphicsEllipseItem):
    def __init__(self, owner_node, kind, data_type):
        r = PORT_RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r, owner_node)
        self.owner_node = owner_node
        self.kind = kind
        self.data_type = data_type
        self.connections = []

        self.setBrush(QBrush(QColor("#50c878") if kind == "input" else QColor("#ff9f43")))
        self.setPen(QPen(QColor("#111722"), 1.5))
        self.setZValue(5)
        self.setCursor(Qt.CrossCursor)
        self.setToolTip(f"{kind.title()}: {data_type}")

    def mousePressEvent(self, event):
        if self.kind == "output" and event.button() == Qt.LeftButton:
            scene = self.scene()
            if isinstance(scene, PipelineScene):
                scene.begin_transfer(self, event.scenePos())
                event.accept()
                return
        super().mousePressEvent(event)


class TransferItem(QGraphicsPathItem):
    """A direct data transfer. It performs no processing."""

    def __init__(self, source_port, target_port=None):
        super().__init__()
        self.source_port = source_port
        self.target_port = target_port
        self.preview_end = None

        self.arrow = QGraphicsPolygonItem(self)
        self.arrow.setZValue(1)
        self.setZValue(-2)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

        source_port.connections.append(self)
        if target_port is not None:
            target_port.connections.append(self)

        self.setToolTip("Direct transfer — no processing")
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

        dx = max(60.0, abs(end.x() - start.x()) * 0.45)
        path = QPainterPath(start)
        path.cubicTo(
            QPointF(start.x() + dx, start.y()),
            QPointF(end.x() - dx, end.y()),
            end,
        )
        self.setPath(path)

        color = QColor("#f0b429") if self.isSelected() else QColor("#8793a7")
        self.setPen(QPen(color, 3.2 if self.isSelected() else 2.0))

        near_end = path.pointAtPercent(0.97)
        angle = math.atan2(end.y() - near_end.y(), end.x() - near_end.x())
        size = 10
        p1 = QPointF(
            end.x() - size * math.cos(angle - 0.55),
            end.y() - size * math.sin(angle - 0.55),
        )
        p2 = QPointF(
            end.x() - size * math.cos(angle + 0.55),
            end.y() - size * math.sin(angle + 0.55),
        )
        self.arrow.setPolygon(QPolygonF([end, p1, p2]))
        self.arrow.setBrush(QBrush(color))
        self.arrow.setPen(QPen(color, 1))

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


class ModelItem(QGraphicsRectItem):
    def __init__(self, definition):
        self.definition = definition
        self.inputs = []
        self.outputs = []
        self.port_labels = []
        self.collapsed = False

        rows = max(len(definition["inputs"]), len(definition["outputs"]), 1)
        self.expanded_height = max(100, 54 + rows * 28)
        self.height = self.expanded_height
        super().__init__(0, 0, MODEL_WIDTH, self.height)

        self.setBrush(QBrush(QColor("#263248")))
        self.setPen(QPen(QColor("#53627a"), 1.5))
        for flag in (
            QGraphicsItem.ItemIsMovable,
            QGraphicsItem.ItemIsSelectable,
            QGraphicsItem.ItemSendsGeometryChanges,
        ):
            self.setFlag(flag, True)

        self.title = QGraphicsSimpleTextItem("▾  " + definition["name"], self)
        self.title.setBrush(QBrush(QColor("#f5f7fb")))
        self.title.setPos(12, 8)

        for idx, data_type in enumerate(definition["inputs"]):
            y = 55 + idx * 28
            port = PortItem(self, "input", data_type)
            port.setPos(0, y)
            self.inputs.append(port)

            label = QGraphicsSimpleTextItem(data_type, self)
            label.setBrush(QBrush(QColor("#9ee6b8")))
            label.setPos(12, y - 9)
            self.port_labels.append(label)

        for idx, data_type in enumerate(definition["outputs"]):
            y = 55 + idx * 28
            port = PortItem(self, "output", data_type)
            port.setPos(MODEL_WIDTH, y)
            self.outputs.append(port)

            label = QGraphicsSimpleTextItem(data_type, self)
            label.setBrush(QBrush(QColor("#ffc477")))
            label.setPos(MODEL_WIDTH - 12 - label.boundingRect().width(), y - 9)
            self.port_labels.append(label)

        self.setToolTip(
            "Double-click to collapse/expand\n"
            + "Inputs: " + ", ".join(definition["inputs"])
            + "\nOutputs: " + ", ".join(definition["outputs"])
        )

    def mouseDoubleClickEvent(self, event):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.height = 42
            self.setRect(0, 0, MODEL_WIDTH, self.height)
            self.title.setText("▸  " + self.definition["name"])
            for label in self.port_labels:
                label.hide()
            for port in self.inputs + self.outputs:
                port.hide()
        else:
            self.height = self.expanded_height
            self.setRect(0, 0, MODEL_WIDTH, self.height)
            self.title.setText("▾  " + self.definition["name"])
            for label in self.port_labels:
                label.show()
            for idx, port in enumerate(self.inputs):
                port.setPos(0, 55 + idx * 28)
                port.show()
            for idx, port in enumerate(self.outputs):
                port.setPos(MODEL_WIDTH, 55 + idx * 28)
                port.show()

        for port in self.inputs + self.outputs:
            for connection in list(port.connections):
                connection.update_path()
        event.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for port in self.inputs + self.outputs:
                for connection in list(port.connections):
                    connection.update_path()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.setPen(
                QPen(
                    QColor("#f0b429") if value else QColor("#53627a"),
                    2.5 if value else 1.5,
                )
            )
        return super().itemChange(change, value)

    def all_connections(self):
        return list({c for p in self.inputs + self.outputs for c in p.connections})


class FixedEndpointItem(ModelItem):
    def __init__(self, definition, side):
        super().__init__(definition)
        self.side = side
        self.setBrush(QBrush(QColor("#183b32") if side == "left" else QColor("#3b2f18")))
        self.setPen(QPen(QColor("#62c7a0") if side == "left" else QColor("#d7ad5c"), 2))
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.title.setText(
            ("DATASET  " if side == "left" else "PIPELINE OUTPUT  ") + definition["name"]
        )
        self.setToolTip("Fixed pipeline endpoint. Components are represented by typed ports.")

        if side == "left":
            for port in self.inputs:
                port.hide()
        else:
            for port in self.outputs:
                port.hide()

    def mouseDoubleClickEvent(self, event):
        event.accept()


class PluginItem(QGraphicsEllipseItem):
    """Processing node. Unlike TransferItem, this item changes or derives data."""

    def __init__(self, definition):
        self.definition = definition
        self.inputs = []
        self.outputs = []
        self.port_labels = []

        rows = max(len(definition["inputs"]), len(definition["outputs"]), 1)
        self.diameter = max(PLUGIN_MIN_DIAMETER, 78 + rows * 30)
        super().__init__(0, 0, self.diameter, self.diameter)

        color = QColor(definition["color"])
        self.setBrush(QBrush(color.darker(250)))
        self.setPen(QPen(color, 2.5))
        for flag in (
            QGraphicsItem.ItemIsMovable,
            QGraphicsItem.ItemIsSelectable,
            QGraphicsItem.ItemSendsGeometryChanges,
        ):
            self.setFlag(flag, True)

        title = QGraphicsSimpleTextItem(definition["name"], self)
        title.setBrush(QBrush(QColor("#f5f7fb")))
        title.setPos(
            (self.diameter - title.boundingRect().width()) / 2,
            18,
        )
        self.title = title

        available_top = 62
        spacing = 28
        for idx, data_type in enumerate(definition["inputs"]):
            y = available_top + idx * spacing
            port = PortItem(self, "input", data_type)
            port.setPos(0, y)
            self.inputs.append(port)

            label = QGraphicsSimpleTextItem(data_type, self)
            label.setBrush(QBrush(QColor("#9ee6b8")))
            label.setPos(13, y - 9)
            self.port_labels.append(label)

        for idx, data_type in enumerate(definition["outputs"]):
            y = available_top + idx * spacing
            port = PortItem(self, "output", data_type)
            port.setPos(self.diameter, y)
            self.outputs.append(port)

            label = QGraphicsSimpleTextItem(data_type, self)
            label.setBrush(QBrush(QColor("#ffc477")))
            label.setPos(self.diameter - 13 - label.boundingRect().width(), y - 9)
            self.port_labels.append(label)

        self.setToolTip(
            "Plugin / processing node\n"
            + "Inputs: " + ", ".join(definition["inputs"])
            + "\nOutputs: " + ", ".join(definition["outputs"])
        )

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for port in self.inputs + self.outputs:
                for connection in list(port.connections):
                    connection.update_path()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            color = QColor("#f0b429") if value else QColor(self.definition["color"])
            self.setPen(QPen(color, 3.0 if value else 2.5))
        return super().itemChange(change, value)

    def all_connections(self):
        return list({c for p in self.inputs + self.outputs for c in p.connections})


class PipelineScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(QRectF(-2200, -1600, 4400, 3200))
        self.pending_transfer = None
        self.dataset_block = None
        self.output_block = None

    @staticmethod
    def matches(source_type, target_type):
        return (
            source_type == target_type
            or source_type == "Any"
            or target_type == "Any"
        )

    def add_model(self, definition, pos):
        model = ModelItem(definition)
        self.addItem(model)
        model.setPos(pos)
        return model

    def add_plugin(self, definition, pos):
        plugin = PluginItem(definition)
        self.addItem(plugin)
        plugin.setPos(pos)
        return plugin

    def begin_transfer(self, source_port, pos):
        self.cancel_pending()
        self.pending_transfer = TransferItem(source_port)
        self.addItem(self.pending_transfer)
        self.pending_transfer.set_preview_end(pos)

    def cancel_pending(self):
        if self.pending_transfer:
            self.pending_transfer.detach()
            self.pending_transfer = None

    def mouseMoveEvent(self, event):
        if self.pending_transfer:
            self.pending_transfer.set_preview_end(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.pending_transfer:
            target = next(
                (
                    item
                    for item in self.items(event.scenePos())
                    if isinstance(item, PortItem) and item.kind == "input"
                ),
                None,
            )

            if target and target.owner_node is not self.pending_transfer.source_port.owner_node:
                source_type = self.pending_transfer.source_port.data_type
                if self.matches(source_type, target.data_type):
                    self.pending_transfer.attach_target(target)
                    self.pending_transfer = None
                else:
                    QMessageBox.warning(
                        None,
                        "Incompatible transfer",
                        f"A direct transfer cannot change {source_type} into {target.data_type}.\n\n"
                        "Insert a plugin between them if a transformation is required.",
                    )
                    self.cancel_pending()
            else:
                self.cancel_pending()

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def delete_selected(self):
        selected = list(self.selectedItems())

        for item in selected:
            if isinstance(item, TransferItem):
                item.detach()

        for item in selected:
            if isinstance(item, PluginItem):
                for connection in item.all_connections():
                    connection.detach()
                self.removeItem(item)

        for item in selected:
            if isinstance(item, ModelItem) and not isinstance(item, FixedEndpointItem):
                for connection in item.all_connections():
                    connection.detach()
                self.removeItem(item)

    def completed_transfers(self):
        return [
            item
            for item in self.items()
            if isinstance(item, TransferItem) and item.target_port
        ]

    @staticmethod
    def node_kind(node):
        if isinstance(node, FixedEndpointItem):
            return "dataset" if node.side == "left" else "pipeline_output"
        if isinstance(node, PluginItem):
            return "plugin"
        return "model"

    @staticmethod
    def node_name(node):
        return node.definition["name"]

    def relationship_summary(self):
        transfers = self.completed_transfers()
        if not transfers:
            return (
                "No completed transfers exist yet.\n\n"
                "Lines are direct transfers only. Processing happens inside circular plugin nodes."
            )

        lines = []
        for n, transfer in enumerate(reversed(transfers), 1):
            src = transfer.source_port.owner_node
            dst = transfer.target_port.owner_node
            src_no = src.outputs.index(transfer.source_port) + 1
            dst_no = dst.inputs.index(transfer.target_port) + 1
            lines.append(
                f"{n}. {self.node_kind(src).upper()}: {self.node_name(src)}\n"
                f"   Output {src_no}: {transfer.source_port.data_type}\n"
                f"      ── direct transfer ──►\n"
                f"   {self.node_kind(dst).upper()}: {self.node_name(dst)}\n"
                f"   Input {dst_no}: {transfer.target_port.data_type}"
            )
        return "\n\n".join(lines)

    def relationship_code(self):
        graph = {
            "schema": "diagram-connect.pipeline.v2",
            "semantics": {
                "edges": "direct_transfer_only",
                "plugins": "processing_nodes",
            },
            "nodes": [],
            "transfers": [],
        }

        nodes = []
        for item in self.items():
            if isinstance(item, (ModelItem, PluginItem)):
                if item not in nodes:
                    nodes.append(item)

        node_ids = {}
        for index, node in enumerate(reversed(nodes), 1):
            node_id = f"node_{index}"
            node_ids[node] = node_id
            graph["nodes"].append(
                {
                    "id": node_id,
                    "kind": self.node_kind(node),
                    "name": self.node_name(node),
                    "inputs": list(node.definition["inputs"]),
                    "outputs": list(node.definition["outputs"]),
                }
            )

        for transfer in reversed(self.completed_transfers()):
            src = transfer.source_port.owner_node
            dst = transfer.target_port.owner_node
            graph["transfers"].append(
                {
                    "from": {
                        "node": node_ids[src],
                        "output_index": src.outputs.index(transfer.source_port) + 1,
                        "type": transfer.source_port.data_type,
                    },
                    "to": {
                        "node": node_ids[dst],
                        "input_index": dst.inputs.index(transfer.target_port) + 1,
                        "type": transfer.target_port.data_type,
                    },
                    "operation": "transfer",
                }
            )

        return json.dumps(graph, indent=2)

    def relationship_qbasic(self):
        transfers = self.completed_transfers()
        lines = [
            "' Diagram Connect - QBasic-style pipeline description",
            "' Lines transfer data only; plugin circles perform processing.",
            "",
            "CLS",
            'PRINT "AI PIPELINE"',
            "",
        ]

        if not transfers:
            lines.extend(["' No completed transfers.", "END"])
            return "\n".join(lines)

        for n, transfer in enumerate(reversed(transfers), 1):
            src = transfer.source_port.owner_node
            dst = transfer.target_port.owner_node
            src_no = src.outputs.index(transfer.source_port) + 1
            dst_no = dst.inputs.index(transfer.target_port) + 1
            safe = lambda s: str(s).replace('"', "''")
            lines.extend(
                [
                    f"' ----- TRANSFER {n} -----",
                    f'SOURCE_KIND$ = "{safe(self.node_kind(src))}"',
                    f'SOURCE_NODE$ = "{safe(self.node_name(src))}"',
                    f'SOURCE_OUTPUT% = {src_no}',
                    f'DATA_TYPE$ = "{safe(transfer.source_port.data_type)}"',
                    f'RECEIVER_KIND$ = "{safe(self.node_kind(dst))}"',
                    f'RECEIVER_NODE$ = "{safe(self.node_name(dst))}"',
                    f'RECEIVER_INPUT% = {dst_no}',
                    'PRINT SOURCE_NODE$; " OUT"; SOURCE_OUTPUT%; " -> "; RECEIVER_NODE$; " IN"; RECEIVER_INPUT%',
                    "PRINT",
                    "",
                ]
            )

        lines.append("END")
        return "\n".join(lines)


class PipelineView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setBackgroundBrush(QBrush(QColor("#151b26")))

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class LibraryPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.model_defs = []
        self.plugin_defs = []

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Models</b><br><small>Double-click to place rectangle</small>"))
        self.models = QListWidget()
        layout.addWidget(self.models)
        for definition in DEFAULT_MODELS:
            self.add_model_item(definition)
        self.models.itemDoubleClicked.connect(self.place_model)

        button = QPushButton("+ Define model")
        button.clicked.connect(self.define_model)
        layout.addWidget(button)

        layout.addWidget(
            QLabel(
                "<b>Plugins</b><br>"
                "<small>Double-click to place processing circle. "
                "Lines themselves only transfer data.</small>"
            )
        )
        self.plugins = QListWidget()
        layout.addWidget(self.plugins)
        for definition in DEFAULT_PLUGINS:
            self.add_plugin_item(definition)
        self.plugins.itemDoubleClicked.connect(self.place_plugin)

        button = QPushButton("+ Define plugin")
        button.clicked.connect(self.define_plugin)
        layout.addWidget(button)

        layout.addStretch()

    @staticmethod
    def parse_types(text):
        return [x.strip() for x in text.split(",") if x.strip()] or ["Any"]

    def add_model_item(self, definition):
        self.model_defs.append(definition)
        item = QListWidgetItem(
            f"{definition['name']}   "
            f"[{', '.join(definition['inputs'])} → {', '.join(definition['outputs'])}]"
        )
        item.setData(Qt.UserRole, len(self.model_defs) - 1)
        self.models.addItem(item)

    def add_plugin_item(self, definition):
        self.plugin_defs.append(definition)
        item = QListWidgetItem(
            f"● {definition['name']}   "
            f"[{', '.join(definition['inputs'])} → {', '.join(definition['outputs'])}]"
        )
        item.setData(Qt.UserRole, len(self.plugin_defs) - 1)
        item.setForeground(QColor(definition["color"]))
        self.plugins.addItem(item)

    def define_model(self):
        dialog = DefinitionDialog(
            "Define model",
            [
                ("name", "Model name:", "New model"),
                ("inputs", "Input type(s), comma separated:", "Image"),
                ("outputs", "Output type(s), comma separated:", "Segmentation"),
            ],
            self,
        )
        if dialog.exec():
            self.add_model_item(
                {
                    "name": dialog.value("name") or "New model",
                    "inputs": self.parse_types(dialog.value("inputs")),
                    "outputs": self.parse_types(dialog.value("outputs")),
                }
            )

    def define_plugin(self):
        dialog = DefinitionDialog(
            "Define plugin",
            [
                ("name", "Plugin name:", "New plugin"),
                ("inputs", "Plugin input type(s), comma separated:", "Segmentation"),
                ("outputs", "Plugin output type(s), comma separated:", "Volume"),
            ],
            self,
        )
        if dialog.exec():
            palette = ["#45aaf2", "#a55eea", "#26de81", "#fd9644", "#fc5c65", "#2bcbba"]
            self.add_plugin_item(
                {
                    "name": dialog.value("name") or "New plugin",
                    "inputs": self.parse_types(dialog.value("inputs")),
                    "outputs": self.parse_types(dialog.value("outputs")),
                    "color": palette[len(self.plugin_defs) % len(palette)],
                }
            )

    def place_model(self, item):
        self.window.add_named_model(self.model_defs[item.data(Qt.UserRole)])

    def place_plugin(self, item):
        self.window.add_named_plugin(self.plugin_defs[item.data(Qt.UserRole)])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Diagram Connect — Typed AI Pipeline Mock-up")
        self.resize(1400, 840)

        self.scene = PipelineScene(self)
        self.view = PipelineView(self.scene)
        self.setCentralWidget(self.view)
        self.live_code_dialogs = []

        dock = QDockWidget("Pipeline Library", self)
        self.library = LibraryPanel(self)
        dock.setWidget(self.library)
        dock.setMinimumWidth(390)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

        toolbar = QToolBar("Pipeline")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        delete = QAction("Delete selected", self)
        delete.triggered.connect(self.scene.delete_selected)
        toolbar.addAction(delete)

        clear = QAction("Clear canvas", self)
        clear.triggered.connect(self.clear_pipeline)
        toolbar.addAction(clear)

        toolbar.addSeparator()

        explain = QAction("Explain relationships", self)
        explain.triggered.connect(self.show_relationships)
        toolbar.addAction(explain)

        code = QAction("Show relationship code", self)
        code.triggered.connect(self.show_relationship_code)
        toolbar.addAction(code)

        qbasic = QAction("Show QBasic", self)
        qbasic.triggered.connect(self.show_qbasic_code)
        toolbar.addAction(qbasic)

        self.build_fixed_endpoints()
        self.build_demo()

        self.statusBar().showMessage(
            "Gray arrows = direct transfer only. Colored circles = processing plugins. "
            "Drag from any orange output node to a compatible green input node."
        )

    def build_fixed_endpoints(self):
        dataset = {
            "name": "Case data",
            "inputs": [],
            "outputs": ["Image", "Segmentation", "Metadata"],
        }
        result = {
            "name": "Results",
            "inputs": ["Image", "Segmentation", "Spatial location", "Volume", "Scalar", "Table"],
            "outputs": [],
        }

        self.scene.dataset_block = FixedEndpointItem(dataset, "left")
        self.scene.output_block = FixedEndpointItem(result, "right")
        self.scene.addItem(self.scene.dataset_block)
        self.scene.addItem(self.scene.output_block)

        self.scene.dataset_block.setPos(-760, -140)
        self.scene.output_block.setPos(560, -180)

    def clear_pipeline(self):
        self.scene.cancel_pending()
        for item in list(self.scene.items()):
            if isinstance(item, TransferItem):
                item.detach()
            elif isinstance(item, PluginItem):
                self.scene.removeItem(item)
            elif isinstance(item, ModelItem) and not isinstance(item, FixedEndpointItem):
                self.scene.removeItem(item)

    def add_named_model(self, definition):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_model(definition, center - QPointF(MODEL_WIDTH / 2, 70))

    def add_named_plugin(self, definition):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_plugin(
            definition,
            center - QPointF(PLUGIN_MIN_DIAMETER / 2, PLUGIN_MIN_DIAMETER / 2),
        )

    def connect(self, source_node, source_index, target_node, target_index):
        transfer = TransferItem(source_node.outputs[source_index], target_node.inputs[target_index])
        self.scene.addItem(transfer)
        return transfer

    def build_demo(self):
        model = self.scene.add_model(DEFAULT_MODELS[0], QPointF(-390, -80))
        plugin = self.scene.add_plugin(DEFAULT_PLUGINS[1], QPointF(-40, -90))

        self.connect(self.scene.dataset_block, 0, model, 0)
        self.connect(model, 0, plugin, 0)
        self.connect(plugin, 0, self.scene.output_block, 3)

        self.view.centerOn(QPointF(-40, 0))

    def show_relationships(self):
        box = QMessageBox(self)
        box.setWindowTitle("Pipeline Relationships")
        box.setIcon(QMessageBox.Information)
        box.setText("How the current pipeline is connected")
        box.setInformativeText(self.scene.relationship_summary())
        box.setStandardButtons(QMessageBox.Ok)
        box.setMinimumWidth(700)
        box.exec()

    def make_live_code_dialog(self, title, label_text, generator):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(840, 640)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(label_text + "  (live)"))

        from PySide6.QtWidgets import QPlainTextEdit

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        buttons.clicked.connect(dialog.close)
        layout.addWidget(buttons)

        timer = QTimer(dialog)
        timer.setInterval(200)
        last = {"text": None}

        def refresh():
            text = generator()
            if text != last["text"]:
                cursor = editor.textCursor()
                position = cursor.position()
                editor.setPlainText(text)
                cursor = editor.textCursor()
                cursor.setPosition(min(position, len(text)))
                editor.setTextCursor(cursor)
                last["text"] = text

        timer.timeout.connect(refresh)
        refresh()
        timer.start()

        dialog.setModal(False)
        dialog.show()
        self.live_code_dialogs.append(dialog)
        dialog.finished.connect(
            lambda _=0, d=dialog:
            self.live_code_dialogs.remove(d) if d in self.live_code_dialogs else None
        )

    def show_relationship_code(self):
        self.make_live_code_dialog(
            "Pipeline Relationship Code",
            "Machine-readable JSON representation of nodes and direct transfers:",
            self.scene.relationship_code,
        )

    def show_qbasic_code(self):
        self.make_live_code_dialog(
            "Pipeline — QBasic",
            "QBasic-style representation of the current pipeline:",
            self.scene.relationship_qbasic,
        )


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
