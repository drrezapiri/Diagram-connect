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

MODEL_WIDTH = 250
PORT_RADIUS = 7
PLUGIN_MIN_DIAMETER = 180

PALETTE = ["#45aaf2", "#a55eea", "#26de81", "#fd9644", "#fc5c65", "#2bcbba"]


def port(port_id, name, data_type, required=True, cardinality="one"):
    return {
        "id": port_id,
        "name": name,
        "type": data_type,
        "required": bool(required),
        "cardinality": cardinality,
    }


DEFAULT_MODELS = [
    {
        "name": "nnU-Net v2",
        "inputs": [port("image", "CT image", "Image")],
        "outputs": [
            port("liver_mask", "Liver mask", "Segmentation"),
            port("tumor_mask", "Tumor mask", "Segmentation"),
        ],
    },
    {
        "name": "MONAI Model",
        "inputs": [port("image", "Input image", "Image")],
        "outputs": [port("prediction", "Prediction mask", "Segmentation")],
    },
    {
        "name": "Measurement Model",
        "inputs": [port("mask", "Target mask", "Segmentation")],
        "outputs": [port("volume", "Measured volume", "Volume")],
    },
]

DEFAULT_PLUGINS = [
    {
        "name": "Spatial locator",
        "inputs": [port("mask", "Reference mask", "Segmentation")],
        "outputs": [port("location", "Spatial location", "Spatial location")],
        "color": "#e056fd",
    },
    {
        "name": "Volume extractor",
        "inputs": [port("mask", "Mask", "Segmentation")],
        "outputs": [port("volume", "Volume", "Volume")],
        "color": "#ff9f43",
    },
    {
        "name": "Crop / ROI",
        "inputs": [
            port("image", "Source image", "Image"),
            port("location", "Crop location", "Spatial location"),
        ],
        "outputs": [port("crop", "Cropped image", "Image")],
        "color": "#20bf6b",
    },
    {
        "name": "Feature extractor",
        "inputs": [
            port("mask", "Mask", "Segmentation"),
            port("image", "Image", "Image", required=False),
        ],
        "outputs": [
            port("scalar", "Scalar feature", "Scalar"),
            port("table", "Feature table", "Table"),
        ],
        "color": "#45aaf2",
    },
]


def normalize_ports(items, kind):
    normalized = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            normalized.append(
                port(
                    f"{kind}_{index + 1}",
                    item,
                    item,
                    required=(kind == "input"),
                    cardinality="one",
                )
            )
        else:
            entry = dict(item)
            entry.setdefault("id", f"{kind}_{index + 1}")
            entry.setdefault("name", entry.get("type", f"{kind.title()} {index + 1}"))
            entry.setdefault("type", "Any")
            entry.setdefault("required", kind == "input")
            entry.setdefault("cardinality", "one")
            normalized.append(entry)
    return normalized


def normalize_definition(definition):
    result = dict(definition)
    result["inputs"] = normalize_ports(result.get("inputs", []), "input")
    result["outputs"] = normalize_ports(result.get("outputs", []), "output")
    return result


def port_label(spec, is_input):
    suffix = ""
    if is_input and not spec.get("required", True):
        suffix += "?"
    if spec.get("cardinality") == "many":
        suffix += "*"
    return f"{spec['name']}{suffix} [{spec['type']}]"


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

        hint = QLabel(
            "Port syntax: name:type. Add ? for optional input and * for many. "
            "Example: CT image:Image, Prior masks:Segmentation?*"
        )
        hint.setWordWrap(True)
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self, key):
        return self.widgets[key].text().strip()


def parse_port_text(text, kind):
    result = []
    used_ids = set()

    for index, raw in enumerate([x.strip() for x in text.split(",") if x.strip()], 1):
        required = True
        cardinality = "one"

        while raw.endswith("?") or raw.endswith("*"):
            if raw.endswith("?"):
                required = False
                raw = raw[:-1].rstrip()
            elif raw.endswith("*"):
                cardinality = "many"
                raw = raw[:-1].rstrip()

        if ":" in raw:
            name, data_type = [x.strip() for x in raw.split(":", 1)]
        else:
            name = raw
            data_type = raw

        name = name or f"{kind.title()} {index}"
        data_type = data_type or "Any"

        base_id = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")
        base_id = base_id or f"{kind}_{index}"
        port_id = base_id
        n = 2
        while port_id in used_ids:
            port_id = f"{base_id}_{n}"
            n += 1
        used_ids.add(port_id)

        result.append(port(port_id, name, data_type, required, cardinality))

    return result


class PortItem(QGraphicsEllipseItem):
    def __init__(self, owner_node, kind, spec):
        r = PORT_RADIUS
        super().__init__(-r, -r, 2 * r, 2 * r, owner_node)

        self.owner_node = owner_node
        self.kind = kind
        self.spec = dict(spec)
        self.port_id = self.spec["id"]
        self.name = self.spec["name"]
        self.data_type = self.spec["type"]
        self.required = bool(self.spec.get("required", kind == "input"))
        self.cardinality = self.spec.get("cardinality", "one")
        self.connections = []

        self.setBrush(QBrush(QColor("#50c878") if kind == "input" else QColor("#ff9f43")))
        pen = QPen(QColor("#111722"), 1.5)
        if kind == "input" and not self.required:
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.setZValue(5)
        self.setCursor(Qt.CrossCursor)
        self.setToolTip(
            f"{kind.title()}: {self.name}\n"
            f"Type: {self.data_type}\n"
            f"{'Required' if self.required else 'Optional'}"
            f" · {'many' if self.cardinality == 'many' else 'one'} connection(s)"
        )

    def mousePressEvent(self, event):
        if self.kind == "output" and event.button() == Qt.LeftButton:
            scene = self.scene()
            if isinstance(scene, PipelineScene):
                scene.begin_transfer(self, event.scenePos())
                event.accept()
                return
        super().mousePressEvent(event)


class TransferItem(QGraphicsPathItem):
    """A direct transfer: it moves data but never transforms it."""

    def __init__(self, source_port, target_port=None):
        super().__init__()
        self.source_port = source_port
        self.target_port = target_port
        self.preview_end = None

        self.arrow = QGraphicsPolygonItem(self)
        self.arrow.setZValue(1)

        self.flow_state = "idle"
        self.flow_progress = 0.0
        self.flow_dot = QGraphicsEllipseItem(-5, -5, 10, 10, self)
        self.flow_dot.setBrush(QBrush(QColor("#dffcff")))
        self.flow_dot.setPen(QPen(QColor("#4dd0e1"), 1.5))
        self.flow_dot.setZValue(3)
        self.flow_dot.hide()

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

    def attach_target(self, target_port):
        self.target_port = target_port
        self.preview_end = None
        if self not in target_port.connections:
            target_port.connections.append(self)
        self.update_path()
        if isinstance(self.scene(), PipelineScene):
            self.scene().refresh_graph_state()

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

        if self.isSelected():
            color = QColor("#f0b429")
            width = 3.2
        elif self.flow_state == "active":
            color = QColor("#4dd0e1")
            width = 3.4
        elif self.flow_state == "done":
            color = QColor("#55c98b")
            width = 2.7
        else:
            color = QColor("#8793a7")
            width = 2.0
        self.setPen(QPen(color, width))

        near_end = path.pointAtPercent(0.97)
        angle = math.atan2(end.y() - near_end.y(), end.x() - near_end.x())
        size = 10
        p1 = QPointF(end.x() - size * math.cos(angle - 0.55), end.y() - size * math.sin(angle - 0.55))
        p2 = QPointF(end.x() - size * math.cos(angle + 0.55), end.y() - size * math.sin(angle + 0.55))
        self.arrow.setPolygon(QPolygonF([end, p1, p2]))
        self.arrow.setBrush(QBrush(color))
        self.arrow.setPen(QPen(color, 1))

        if self.flow_state == "active":
            self.flow_dot.setPos(path.pointAtPercent(max(0.0, min(1.0, self.flow_progress))))
            self.flow_dot.show()
        else:
            self.flow_dot.hide()

    def set_flow_state(self, state, progress=0.0):
        self.flow_state = state
        self.flow_progress = progress
        self.update_path()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.update_path()
        return super().itemChange(change, value)

    def detach(self):
        scene = self.scene()
        if self in self.source_port.connections:
            self.source_port.connections.remove(self)
        if self.target_port and self in self.target_port.connections:
            self.target_port.connections.remove(self)
        if scene:
            scene.removeItem(self)
        if isinstance(scene, PipelineScene):
            scene.refresh_graph_state()


class BaseExecutableNode:
    def setup_node_state(self, definition, node_id):
        self.definition = normalize_definition(definition)
        self.node_id = node_id
        self.inputs = []
        self.outputs = []
        self.port_labels = []
        self.activation_order = None
        self.validation_state = "unknown"
        self.execution_state = "idle"

    def all_connections(self):
        return list({c for p in self.inputs + self.outputs for c in p.connections})

    def refresh_connected_lines(self):
        for p in self.inputs + self.outputs:
            for connection in list(p.connections):
                connection.update_path()

    def set_activation_order(self, order):
        self.activation_order = order
        if hasattr(self, "activation_label"):
            if order is None:
                self.activation_label.setText("Activation: —")
                self.activation_label.setBrush(QBrush(QColor("#8f9bad")))
            else:
                self.activation_label.setText(f"Activation: {order}")
                self.activation_label.setBrush(QBrush(QColor("#80d8a5")))


class ModelItem(QGraphicsRectItem, BaseExecutableNode):
    def __init__(self, definition, node_id):
        self.setup_node_state(definition, node_id)
        self.collapsed = False

        rows = max(len(self.definition["inputs"]), len(self.definition["outputs"]), 1)
        self.expanded_height = max(112, 68 + rows * 30)
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

        self.title = QGraphicsSimpleTextItem("▾  " + self.definition["name"], self)
        self.title.setBrush(QBrush(QColor("#f5f7fb")))
        self.title.setPos(12, 7)

        self.activation_label = QGraphicsSimpleTextItem("Activation: —", self)
        self.activation_label.setBrush(QBrush(QColor("#8f9bad")))
        self.activation_label.setPos(13, 28)

        for idx, spec in enumerate(self.definition["inputs"]):
            y = 67 + idx * 30
            p = PortItem(self, "input", spec)
            p.setPos(0, y)
            self.inputs.append(p)

            label = QGraphicsSimpleTextItem(port_label(spec, True), self)
            label.setBrush(QBrush(QColor("#9ee6b8")))
            label.setPos(12, y - 9)
            self.port_labels.append(label)

        for idx, spec in enumerate(self.definition["outputs"]):
            y = 67 + idx * 30
            p = PortItem(self, "output", spec)
            p.setPos(MODEL_WIDTH, y)
            self.outputs.append(p)

            label = QGraphicsSimpleTextItem(port_label(spec, False), self)
            label.setBrush(QBrush(QColor("#ffc477")))
            label.setPos(MODEL_WIDTH - 12 - label.boundingRect().width(), y - 9)
            self.port_labels.append(label)

        self.setToolTip(f"{self.node_id}\nDouble-click to collapse/expand")

    def mouseDoubleClickEvent(self, event):
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.height = 48
            self.setRect(0, 0, MODEL_WIDTH, self.height)
            self.title.setText("▸  " + self.definition["name"])
            self.activation_label.setPos(13, 27)
            for label in self.port_labels:
                label.hide()
            for p in self.inputs + self.outputs:
                p.hide()
        else:
            self.height = self.expanded_height
            self.setRect(0, 0, MODEL_WIDTH, self.height)
            self.title.setText("▾  " + self.definition["name"])
            for label in self.port_labels:
                label.show()
            for idx, p in enumerate(self.inputs):
                p.setPos(0, 67 + idx * 30)
                p.show()
            for idx, p in enumerate(self.outputs):
                p.setPos(MODEL_WIDTH, 67 + idx * 30)
                p.show()

        self.refresh_connected_lines()
        event.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.refresh_connected_lines()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.setPen(QPen(QColor("#f0b429") if value else QColor("#53627a"), 2.5 if value else 1.5))
        return super().itemChange(change, value)


class FixedEndpointItem(ModelItem):
    def __init__(self, definition, side, node_id):
        super().__init__(definition, node_id)
        self.side = side
        self.setBrush(QBrush(QColor("#183b32") if side == "left" else QColor("#3b2f18")))
        self.setPen(QPen(QColor("#62c7a0") if side == "left" else QColor("#d7ad5c"), 2))
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.activation_label.hide()
        self.title.setText(("DATASET  " if side == "left" else "PIPELINE OUTPUT  ") + self.definition["name"])

        if side == "left":
            for p in self.inputs:
                p.hide()
        else:
            for p in self.outputs:
                p.hide()

    def mouseDoubleClickEvent(self, event):
        event.accept()


class PluginItem(QGraphicsEllipseItem, BaseExecutableNode):
    def __init__(self, definition, node_id):
        self.setup_node_state(definition, node_id)

        rows = max(len(self.definition["inputs"]), len(self.definition["outputs"]), 1)
        self.diameter = max(PLUGIN_MIN_DIAMETER, 104 + rows * 32)
        super().__init__(0, 0, self.diameter, self.diameter)

        color = QColor(self.definition["color"])
        self.setBrush(QBrush(color.darker(250)))
        self.setPen(QPen(color, 2.5))
        for flag in (
            QGraphicsItem.ItemIsMovable,
            QGraphicsItem.ItemIsSelectable,
            QGraphicsItem.ItemSendsGeometryChanges,
        ):
            self.setFlag(flag, True)

        self.title = QGraphicsSimpleTextItem(self.definition["name"], self)
        self.title.setBrush(QBrush(QColor("#f5f7fb")))
        self.title.setPos((self.diameter - self.title.boundingRect().width()) / 2, 17)

        self.activation_label = QGraphicsSimpleTextItem("Activation: —", self)
        self.activation_label.setBrush(QBrush(QColor("#8f9bad")))
        self.activation_label.setPos(
            (self.diameter - self.activation_label.boundingRect().width()) / 2,
            38,
        )

        top = 76
        spacing = 30

        for idx, spec in enumerate(self.definition["inputs"]):
            y = top + idx * spacing
            p = PortItem(self, "input", spec)
            p.setPos(0, y)
            self.inputs.append(p)

            label = QGraphicsSimpleTextItem(port_label(spec, True), self)
            label.setBrush(QBrush(QColor("#9ee6b8")))
            label.setPos(13, y - 9)
            self.port_labels.append(label)

        for idx, spec in enumerate(self.definition["outputs"]):
            y = top + idx * spacing
            p = PortItem(self, "output", spec)
            p.setPos(self.diameter, y)
            self.outputs.append(p)

            label = QGraphicsSimpleTextItem(port_label(spec, False), self)
            label.setBrush(QBrush(QColor("#ffc477")))
            label.setPos(self.diameter - 13 - label.boundingRect().width(), y - 9)
            self.port_labels.append(label)

        self.setToolTip(f"{self.node_id}\nPlugin / processing node")

    def set_activation_order(self, order):
        super().set_activation_order(order)
        if hasattr(self, "activation_label"):
            self.activation_label.setPos(
                (self.diameter - self.activation_label.boundingRect().width()) / 2,
                38,
            )

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.refresh_connected_lines()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            color = QColor("#f0b429") if value else QColor(self.definition["color"])
            self.setPen(QPen(color, 3.0 if value else 2.5))
        return super().itemChange(change, value)


class PipelineScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(QRectF(-2400, -1700, 4800, 3400))
        self.pending_transfer = None
        self.dataset_block = None
        self.output_block = None
        self.model_counter = 0
        self.plugin_counter = 0

    @staticmethod
    def matches(source_type, target_type):
        return source_type == target_type or source_type == "Any" or target_type == "Any"

    def next_id(self, kind):
        if kind == "model":
            self.model_counter += 1
            return f"model_{self.model_counter:03d}"
        self.plugin_counter += 1
        return f"plugin_{self.plugin_counter:03d}"

    def add_model(self, definition, pos):
        model = ModelItem(definition, self.next_id("model"))
        self.addItem(model)
        model.setPos(pos)
        self.refresh_graph_state()
        return model

    def add_plugin(self, definition, pos):
        plugin = PluginItem(definition, self.next_id("plugin"))
        self.addItem(plugin)
        plugin.setPos(pos)
        self.refresh_graph_state()
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
                    item for item in self.items(event.scenePos())
                    if isinstance(item, PortItem) and item.kind == "input"
                ),
                None,
            )

            if target and target.owner_node is not self.pending_transfer.source_port.owner_node:
                source = self.pending_transfer.source_port

                if not self.matches(source.data_type, target.data_type):
                    QMessageBox.warning(
                        None,
                        "Incompatible transfer",
                        f"A direct transfer cannot change {source.data_type} into {target.data_type}.\n\n"
                        "Insert a plugin between them if a transformation is required.",
                    )
                    self.cancel_pending()
                elif target.cardinality == "one" and any(
                    c.target_port is target for c in target.connections if isinstance(c, TransferItem)
                ):
                    QMessageBox.warning(
                        None,
                        "Input already connected",
                        f"{target.name} accepts one connection only.\n"
                        "Use an input with cardinality 'many' if several sources are required.",
                    )
                    self.cancel_pending()
                else:
                    self.pending_transfer.attach_target(target)
                    self.pending_transfer = None
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

        self.refresh_graph_state()

    def completed_transfers(self):
        return [i for i in self.items() if isinstance(i, TransferItem) and i.target_port]

    def all_nodes(self):
        return [
            i for i in self.items()
            if isinstance(i, (ModelItem, PluginItem))
        ]

    def executable_nodes(self):
        return [
            i for i in self.all_nodes()
            if not isinstance(i, FixedEndpointItem)
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

    def incoming_transfers(self, node):
        return [t for t in self.completed_transfers() if t.target_port.owner_node is node]

    def outgoing_transfers(self, node):
        return [t for t in self.completed_transfers() if t.source_port.owner_node is node]

    def dependency_graph(self):
        nodes = self.all_nodes()
        incoming = {node: set() for node in nodes}
        outgoing = {node: set() for node in nodes}

        for transfer in self.completed_transfers():
            src = transfer.source_port.owner_node
            dst = transfer.target_port.owner_node
            if src in outgoing and dst in incoming:
                outgoing[src].add(dst)
                incoming[dst].add(src)

        return nodes, incoming, outgoing

    def execution_orders(self):
        nodes, incoming, outgoing = self.dependency_graph()
        indegree = {node: len(incoming[node]) for node in nodes}
        queue = [node for node in nodes if indegree[node] == 0]
        topo = []

        while queue:
            node = queue.pop(0)
            topo.append(node)
            for nxt in outgoing[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)

        if len(topo) != len(nodes):
            return {}, True

        levels = {}
        if self.dataset_block:
            levels[self.dataset_block] = 0

        for node in topo:
            if node is self.dataset_block:
                continue

            preds = incoming[node]
            if preds and all(p in levels for p in preds):
                levels[node] = max(levels[p] for p in preds) + 1
            elif not preds and isinstance(node, FixedEndpointItem):
                levels[node] = 0

        executable_orders = {
            node: level
            for node, level in levels.items()
            if node in self.executable_nodes()
        }
        return executable_orders, False

    def refresh_graph_state(self):
        orders, _ = self.execution_orders()
        for node in self.executable_nodes():
            node.set_activation_order(orders.get(node))

    def set_node_execution_visual(self, node, state):
        node.execution_state = state

        if isinstance(node, FixedEndpointItem):
            idle_color = QColor("#62c7a0") if node.side == "left" else QColor("#d7ad5c")
        elif isinstance(node, PluginItem):
            idle_color = QColor(node.definition["color"])
        else:
            idle_color = QColor("#53627a")

        if state == "active":
            color = QColor("#f0b429")
            width = 4.0
        elif state == "done":
            color = QColor("#55c98b")
            width = 3.0
        else:
            color = idle_color
            width = 2.5 if isinstance(node, PluginItem) else 1.5

        node.setPen(QPen(color, width))

        if hasattr(node, "activation_label") and not isinstance(node, FixedEndpointItem):
            if state == "active":
                node.activation_label.setText(f"Activation: {node.activation_order} • RUNNING")
                node.activation_label.setBrush(QBrush(QColor("#f0b429")))
            elif state == "done":
                node.activation_label.setText(f"Activation: {node.activation_order} ✓")
                node.activation_label.setBrush(QBrush(QColor("#55c98b")))
            else:
                node.set_activation_order(node.activation_order)

            if isinstance(node, PluginItem):
                node.activation_label.setPos(
                    (node.diameter - node.activation_label.boundingRect().width()) / 2,
                    38,
                )

    def reset_execution_visuals(self):
        for transfer in self.completed_transfers():
            transfer.set_flow_state("idle", 0.0)
        for node in self.all_nodes():
            self.set_node_execution_visual(node, "idle")

    def execution_animation_plan(self):
        orders, has_cycle = self.execution_orders()
        if has_cycle:
            return []

        plan = []
        if self.dataset_block:
            plan.append({
                "kind": "nodes",
                "label": "Dataset ready",
                "nodes": [self.dataset_block],
            })
            dataset_edges = self.outgoing_transfers(self.dataset_block)
            if dataset_edges:
                plan.append({
                    "kind": "transfers",
                    "label": "Transfer dataset inputs",
                    "transfers": dataset_edges,
                })

        grouped = {}
        for node, order in orders.items():
            grouped.setdefault(order, []).append(node)

        for order in sorted(grouped):
            nodes = sorted(grouped[order], key=lambda n: n.node_id)
            plan.append({
                "kind": "nodes",
                "label": f"Activation {order}",
                "nodes": nodes,
            })

            outgoing = []
            seen = set()
            for node in nodes:
                for transfer in self.outgoing_transfers(node):
                    if transfer not in seen:
                        seen.add(transfer)
                        outgoing.append(transfer)

            if outgoing:
                plan.append({
                    "kind": "transfers",
                    "label": f"Transfer outputs from activation {order}",
                    "transfers": outgoing,
                })

        if self.output_block and self.incoming_transfers(self.output_block):
            plan.append({
                "kind": "nodes",
                "label": "Pipeline output reached",
                "nodes": [self.output_block],
            })

        return plan

    def validate_graph(self):
        errors = []
        warnings = []

        orders, has_cycle = self.execution_orders()
        if has_cycle:
            errors.append("Cycle detected: execution order cannot be resolved.")

        for node in self.executable_nodes() + ([self.output_block] if self.output_block else []):
            if node is None:
                continue

            for input_port in node.inputs:
                incoming = [
                    c for c in input_port.connections
                    if isinstance(c, TransferItem) and c.target_port is input_port
                ]

                if input_port.required and not incoming:
                    errors.append(
                        f"{self.node_name(node)} · {input_port.name}: required input is not connected."
                    )

                if input_port.cardinality == "one" and len(incoming) > 1:
                    errors.append(
                        f"{self.node_name(node)} · {input_port.name}: accepts one source but has {len(incoming)}."
                    )

                for transfer in incoming:
                    if not self.matches(transfer.source_port.data_type, input_port.data_type):
                        errors.append(
                            f"Type mismatch: {transfer.source_port.data_type} → {input_port.data_type}."
                        )

        reachable = set()
        if self.dataset_block:
            reachable.add(self.dataset_block)
            changed = True
            while changed:
                changed = False
                for transfer in self.completed_transfers():
                    src = transfer.source_port.owner_node
                    dst = transfer.target_port.owner_node
                    if src in reachable and dst not in reachable:
                        reachable.add(dst)
                        changed = True

        for node in self.executable_nodes():
            if node not in reachable:
                warnings.append(f"{self.node_name(node)} ({node.node_id}) is not reachable from the dataset.")

        if self.output_block and not self.incoming_transfers(self.output_block):
            warnings.append("Pipeline output has no incoming transfer.")

        return errors, warnings, orders

    def validation_report(self):
        errors, warnings, orders = self.validate_graph()
        lines = []

        if not errors:
            lines.append("✓ STRUCTURE VALID")
        else:
            lines.append(f"✕ {len(errors)} ERROR(S)")
            for item in errors:
                lines.append(f"  • {item}")

        if warnings:
            lines.append("")
            lines.append(f"⚠ {len(warnings)} WARNING(S)")
            for item in warnings:
                lines.append(f"  • {item}")

        lines.append("")
        lines.append("ACTIVATION ORDER")
        if not orders:
            lines.append("  No executable activation order available.")
        else:
            grouped = {}
            for node, order in orders.items():
                grouped.setdefault(order, []).append(node)
            for order in sorted(grouped):
                names = ", ".join(
                    f"{self.node_name(node)} ({node.node_id})"
                    for node in sorted(grouped[order], key=lambda n: n.node_id)
                )
                lines.append(f"  {order}: {names}")

        lines.append("")
        lines.append("Nodes with the same activation number are dependency-ready at the same time.")
        return "\n".join(lines)

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
            lines.append(
                f"{n}. {self.node_kind(src).upper()}: {self.node_name(src)} ({src.node_id})\n"
                f"   {transfer.source_port.name} [{transfer.source_port.data_type}]\n"
                f"      ── direct transfer ──►\n"
                f"   {self.node_kind(dst).upper()}: {self.node_name(dst)} ({dst.node_id})\n"
                f"   {transfer.target_port.name} [{transfer.target_port.data_type}]"
            )
        return "\n\n".join(lines)

    def relationship_code(self):
        errors, warnings, orders = self.validate_graph()

        graph = {
            "schema": "diagram-connect.pipeline.v3",
            "semantics": {
                "edges": "direct_transfer_only",
                "plugins": "processing_nodes",
                "execution_order": "derived_from_dependencies",
            },
            "validation": {
                "valid": not errors,
                "errors": errors,
                "warnings": warnings,
            },
            "nodes": [],
            "transfers": [],
        }

        for node in sorted(self.all_nodes(), key=lambda n: n.node_id):
            graph["nodes"].append(
                {
                    "id": node.node_id,
                    "kind": self.node_kind(node),
                    "name": self.node_name(node),
                    "activation_order": orders.get(node),
                    "inputs": [dict(p.spec) for p in node.inputs],
                    "outputs": [dict(p.spec) for p in node.outputs],
                }
            )

        for transfer in reversed(self.completed_transfers()):
            src = transfer.source_port.owner_node
            dst = transfer.target_port.owner_node
            graph["transfers"].append(
                {
                    "from": {
                        "node": src.node_id,
                        "port": transfer.source_port.port_id,
                        "name": transfer.source_port.name,
                        "type": transfer.source_port.data_type,
                    },
                    "to": {
                        "node": dst.node_id,
                        "port": transfer.target_port.port_id,
                        "name": transfer.target_port.name,
                        "type": transfer.target_port.data_type,
                    },
                    "operation": "transfer",
                }
            )

        return json.dumps(graph, indent=2)

    def relationship_qbasic(self):
        _, _, orders = self.validate_graph()
        transfers = self.completed_transfers()
        lines = [
            "' Diagram Connect - QBasic-style pipeline description",
            "' Transfers move data only. Plugin circles perform transformations.",
            "' Activation numbers are derived automatically from graph dependencies.",
            "",
            "CLS",
            'PRINT "AI PIPELINE"',
            "",
        ]

        for node in sorted(self.executable_nodes(), key=lambda n: (orders.get(n, 999999), n.node_id)):
            activation = orders.get(node)
            lines.extend(
                [
                    f"' {node.node_id} / {self.node_kind(node)}",
                    f'NODE_NAME$ = "{self.node_name(node)}"',
                    f'ACTIVATION_ORDER% = {activation if activation is not None else -1}',
                    'PRINT "Activation"; ACTIVATION_ORDER%; ": "; NODE_NAME$',
                    "",
                ]
            )

        for n, transfer in enumerate(reversed(transfers), 1):
            src = transfer.source_port.owner_node
            dst = transfer.target_port.owner_node
            lines.extend(
                [
                    f"' ----- TRANSFER {n} -----",
                    f'SOURCE_NODE$ = "{src.node_id}"',
                    f'SOURCE_PORT$ = "{transfer.source_port.port_id}"',
                    f'SOURCE_NAME$ = "{transfer.source_port.name}"',
                    f'DATA_TYPE$ = "{transfer.source_port.data_type}"',
                    f'RECEIVER_NODE$ = "{dst.node_id}"',
                    f'RECEIVER_PORT$ = "{transfer.target_port.port_id}"',
                    f'RECEIVER_NAME$ = "{transfer.target_port.name}"',
                    "PRINT SOURCE_NODE$; "."; SOURCE_PORT$; " -> "; RECEIVER_NODE$; "."; RECEIVER_PORT$",
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
                "Gray arrows are transfer-only.</small>"
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
    def definition_summary(definition):
        d = normalize_definition(definition)
        ins = ", ".join(port_label(p, True) for p in d["inputs"]) or "—"
        outs = ", ".join(port_label(p, False) for p in d["outputs"]) or "—"
        return f"{ins} → {outs}"

    def add_model_item(self, definition):
        definition = normalize_definition(definition)
        self.model_defs.append(definition)
        item = QListWidgetItem(f"{definition['name']}   [{self.definition_summary(definition)}]")
        item.setData(Qt.UserRole, len(self.model_defs) - 1)
        self.models.addItem(item)

    def add_plugin_item(self, definition):
        definition = normalize_definition(definition)
        self.plugin_defs.append(definition)
        item = QListWidgetItem(f"● {definition['name']}   [{self.definition_summary(definition)}]")
        item.setData(Qt.UserRole, len(self.plugin_defs) - 1)
        item.setForeground(QColor(definition["color"]))
        self.plugins.addItem(item)

    def define_model(self):
        dialog = DefinitionDialog(
            "Define model",
            [
                ("name", "Model name:", "New model"),
                ("inputs", "Inputs:", "CT image:Image"),
                ("outputs", "Outputs:", "Tumor mask:Segmentation"),
            ],
            self,
        )
        if dialog.exec():
            self.add_model_item(
                {
                    "name": dialog.value("name") or "New model",
                    "inputs": parse_port_text(dialog.value("inputs"), "input"),
                    "outputs": parse_port_text(dialog.value("outputs"), "output"),
                }
            )

    def define_plugin(self):
        dialog = DefinitionDialog(
            "Define plugin",
            [
                ("name", "Plugin name:", "New plugin"),
                ("inputs", "Inputs:", "Mask:Segmentation"),
                ("outputs", "Outputs:", "Volume:Volume"),
            ],
            self,
        )
        if dialog.exec():
            self.add_plugin_item(
                {
                    "name": dialog.value("name") or "New plugin",
                    "inputs": parse_port_text(dialog.value("inputs"), "input"),
                    "outputs": parse_port_text(dialog.value("outputs"), "output"),
                    "color": PALETTE[len(self.plugin_defs) % len(PALETTE)],
                }
            )

    def place_model(self, item):
        self.window.add_named_model(self.model_defs[item.data(Qt.UserRole)])

    def place_plugin(self, item):
        self.window.add_named_plugin(self.plugin_defs[item.data(Qt.UserRole)])


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Diagram Connect — AI Graph Execution Mock-up")
        self.resize(1450, 860)

        self.scene = PipelineScene(self)
        self.view = PipelineView(self.scene)
        self.setCentralWidget(self.view)
        self.live_code_dialogs = []

        self.flow_timer = QTimer(self)
        self.flow_timer.setInterval(40)
        self.flow_timer.timeout.connect(self._flow_animation_tick)
        self.flow_plan = []
        self.flow_phase_index = -1
        self.flow_tick = 0

        dock = QDockWidget("Pipeline Library", self)
        self.library = LibraryPanel(self)
        dock.setWidget(self.library)
        dock.setMinimumWidth(430)
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

        validate = QAction("Validate / Execution", self)
        validate.triggered.connect(self.show_validation)
        toolbar.addAction(validate)

        animate = QAction("▶ Animate Flow", self)
        animate.triggered.connect(self.start_flow_animation)
        toolbar.addAction(animate)

        stop_animation = QAction("■ Stop Flow", self)
        stop_animation.triggered.connect(self.stop_flow_animation)
        toolbar.addAction(stop_animation)

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
            "Gray arrows transfer data only. Rectangles are models. Circles are plugins. "
            "? = optional input, * = multiple connections. Activation order is dependency-derived. "
            "Use Animate Flow to preview execution from dataset to output."
        )

    def build_fixed_endpoints(self):
        dataset = {
            "name": "Case data",
            "inputs": [],
            "outputs": [
                port("image", "Source image", "Image", required=False),
                port("segmentation", "Existing segmentation", "Segmentation", required=False),
                port("metadata", "Case metadata", "Metadata", required=False),
            ],
        }
        result = {
            "name": "Results",
            "inputs": [
                port("image", "Final image", "Image", required=False, cardinality="many"),
                port("segmentation", "Final segmentation", "Segmentation", required=False, cardinality="many"),
                port("location", "Spatial location", "Spatial location", required=False, cardinality="many"),
                port("volume", "Volume", "Volume", required=False, cardinality="many"),
                port("scalar", "Scalar", "Scalar", required=False, cardinality="many"),
                port("table", "Table", "Table", required=False, cardinality="many"),
            ],
            "outputs": [],
        }

        self.scene.dataset_block = FixedEndpointItem(dataset, "left", "dataset")
        self.scene.output_block = FixedEndpointItem(result, "right", "pipeline_output")
        self.scene.addItem(self.scene.dataset_block)
        self.scene.addItem(self.scene.output_block)

        self.scene.dataset_block.setPos(-800, -150)
        self.scene.output_block.setPos(610, -205)

    def clear_pipeline(self):
        self.stop_flow_animation()
        self.scene.cancel_pending()
        for item in list(self.scene.items()):
            if isinstance(item, TransferItem):
                item.detach()
            elif isinstance(item, PluginItem):
                self.scene.removeItem(item)
            elif isinstance(item, ModelItem) and not isinstance(item, FixedEndpointItem):
                self.scene.removeItem(item)
        self.scene.refresh_graph_state()

    def start_flow_animation(self):
        errors, warnings, _ = self.scene.validate_graph()
        if errors:
            QMessageBox.warning(
                self,
                "Cannot animate invalid graph",
                "Fix the structural errors before running the flow animation.\n\n"
                + "\n".join(f"• {item}" for item in errors),
            )
            return

        self.stop_flow_animation()
        self.scene.refresh_graph_state()
        self.scene.reset_execution_visuals()

        self.flow_plan = self.scene.execution_animation_plan()
        if not self.flow_plan:
            self.statusBar().showMessage("There is no executable flow to animate.", 5000)
            return

        self.flow_phase_index = 0
        self.flow_tick = 0
        self._begin_flow_phase()
        self.flow_timer.start()

    def stop_flow_animation(self):
        if self.flow_timer.isActive():
            self.flow_timer.stop()
        self.flow_plan = []
        self.flow_phase_index = -1
        self.flow_tick = 0
        if hasattr(self, "scene"):
            self.scene.reset_execution_visuals()
        self.statusBar().showMessage("Flow animation stopped.", 3000)

    def _begin_flow_phase(self):
        if self.flow_phase_index < 0 or self.flow_phase_index >= len(self.flow_plan):
            return

        phase = self.flow_plan[self.flow_phase_index]
        self.flow_tick = 0
        self.statusBar().showMessage(f"Flow: {phase['label']}")

        if phase["kind"] == "nodes":
            for node in phase["nodes"]:
                self.scene.set_node_execution_visual(node, "active")
        else:
            for transfer in phase["transfers"]:
                transfer.set_flow_state("active", 0.0)

    def _finish_flow_phase(self):
        phase = self.flow_plan[self.flow_phase_index]

        if phase["kind"] == "nodes":
            for node in phase["nodes"]:
                self.scene.set_node_execution_visual(node, "done")
        else:
            for transfer in phase["transfers"]:
                transfer.set_flow_state("done", 1.0)

        self.flow_phase_index += 1
        if self.flow_phase_index >= len(self.flow_plan):
            self.flow_timer.stop()
            self.statusBar().showMessage("Flow animation complete — pipeline output reached.", 7000)
            return

        self._begin_flow_phase()

    def _flow_animation_tick(self):
        if self.flow_phase_index < 0 or self.flow_phase_index >= len(self.flow_plan):
            self.flow_timer.stop()
            return

        phase = self.flow_plan[self.flow_phase_index]
        self.flow_tick += 1

        if phase["kind"] == "transfers":
            duration_ticks = 28
            progress = min(1.0, self.flow_tick / duration_ticks)
            for transfer in phase["transfers"]:
                transfer.set_flow_state("active", progress)

            if progress >= 1.0:
                self._finish_flow_phase()
        else:
            duration_ticks = 18
            if self.flow_tick >= duration_ticks:
                self._finish_flow_phase()

    def add_named_model(self, definition):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_model(definition, center - QPointF(MODEL_WIDTH / 2, 80))

    def add_named_plugin(self, definition):
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_plugin(
            definition,
            center - QPointF(PLUGIN_MIN_DIAMETER / 2, PLUGIN_MIN_DIAMETER / 2),
        )

    def connect(self, source_node, source_index, target_node, target_index):
        transfer = TransferItem(source_node.outputs[source_index], target_node.inputs[target_index])
        self.scene.addItem(transfer)
        self.scene.refresh_graph_state()
        return transfer

    def build_demo(self):
        model = self.scene.add_model(DEFAULT_MODELS[0], QPointF(-420, -90))
        plugin = self.scene.add_plugin(DEFAULT_PLUGINS[1], QPointF(-35, -105))

        self.connect(self.scene.dataset_block, 0, model, 0)
        self.connect(model, 0, plugin, 0)
        self.connect(plugin, 0, self.scene.output_block, 3)

        self.scene.refresh_graph_state()
        self.view.centerOn(QPointF(-20, 0))

    def show_validation(self):
        box = QMessageBox(self)
        box.setWindowTitle("Graph Validation / Execution Order")
        box.setIcon(QMessageBox.Information)
        box.setText("Pipeline structural validation and activation order")
        box.setDetailedText(self.scene.validation_report())
        box.setInformativeText(self.scene.validation_report())
        box.setStandardButtons(QMessageBox.Ok)
        box.setMinimumWidth(760)
        box.exec()

    def show_relationships(self):
        box = QMessageBox(self)
        box.setWindowTitle("Pipeline Relationships")
        box.setIcon(QMessageBox.Information)
        box.setText("How the current pipeline is connected")
        box.setInformativeText(self.scene.relationship_summary())
        box.setStandardButtons(QMessageBox.Ok)
        box.setMinimumWidth(760)
        box.exec()

    def make_live_code_dialog(self, title, label_text, generator):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 680)
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
            self.scene.refresh_graph_state()
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
            "Machine-readable JSON representation of semantic ports, nodes, transfers, validation and execution:",
            self.scene.relationship_code,
        )

    def show_qbasic_code(self):
        self.make_live_code_dialog(
            "Pipeline — QBasic",
            "QBasic-style representation of the current graph execution:",
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
