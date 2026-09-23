import sys
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
    QFormLayout, QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QToolBar, QVBoxLayout, QWidget,
)

MODEL_WIDTH, PORT_RADIUS = 220, 7
DATA_TYPES = ["Image", "Segmentation", "Spatial location", "Volume", "Scalar", "Table", "Any"]

DEFAULT_MODELS = [
    {"name": "nnU-Net v2", "inputs": ["Image"], "outputs": ["Segmentation", "Segmentation"]},
    {"name": "MONAI Model", "inputs": ["Image"], "outputs": ["Segmentation"]},
    {"name": "Measurement Model", "inputs": ["Segmentation"], "outputs": ["Volume"]},
]
DEFAULT_PLUGINS = [
    {"name": "Direct image", "from": "Image", "to": "Image", "color": "#6b93ff"},
    {"name": "Direct segmentation", "from": "Segmentation", "to": "Segmentation", "color": "#2bcbba"},
    {"name": "Spatial locator", "from": "Segmentation", "to": "Spatial location", "color": "#e056fd"},
    {"name": "Volume extractor", "from": "Segmentation", "to": "Volume", "color": "#ff9f43"},
]


class DefinitionDialog(QDialog):
    def __init__(self, title, fields, parent=None):
        super().__init__(parent); self.setWindowTitle(title)
        form = QFormLayout(self); self.widgets = {}
        for key, label, default in fields:
            w = QLineEdit(default); self.widgets[key] = w; form.addRow(label, w)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self, key): return self.widgets[key].text().strip()


class PortItem(QGraphicsEllipseItem):
    def __init__(self, model, kind, data_type):
        r = PORT_RADIUS
        super().__init__(-r, -r, 2*r, 2*r, model)
        self.parent_model, self.kind, self.data_type, self.connections = model, kind, data_type, []
        self.setBrush(QBrush(QColor("#50c878") if kind == "input" else QColor("#ff9f43")))
        self.setPen(QPen(QColor("#111722"), 1.5)); self.setZValue(3); self.setCursor(Qt.CrossCursor)
        self.setToolTip(f"{kind.title()}: {data_type}")

    def mousePressEvent(self, event):
        if self.kind == "output" and event.button() == Qt.LeftButton:
            self.scene().begin_connection(self, event.scenePos()); event.accept(); return
        super().mousePressEvent(event)


class ConnectionItem(QGraphicsPathItem):
    def __init__(self, source, target=None, plugin=None):
        super().__init__()
        self.source_port, self.target_port = source, target
        self.plugin = plugin or {"name":"Direct", "from":"Any", "to":"Any", "color":"#6b93ff"}
        self.preview_end = None
        self.setZValue(-1); self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        source.connections.append(self)
        if target: target.connections.append(self)
        self.setToolTip(self.description()); self.update_path()

    def description(self):
        p=self.plugin
        return f"{p['name']}: {p['from']} → {p['to']}"

    def set_preview_end(self, p): self.preview_end=p; self.update_path()

    def attach_target(self, p):
        self.target_port=p; self.preview_end=None; p.connections.append(self)
        self.setToolTip(self.description()); self.update_path()

    def update_path(self):
        a=self.source_port.scenePos()
        b=self.target_port.scenePos() if self.target_port else (self.preview_end or a)
        dx=max(70., abs(b.x()-a.x())*.5)
        path=QPainterPath(a); path.cubicTo(QPointF(a.x()+dx,a.y()), QPointF(b.x()-dx,b.y()), b)
        self.setPath(path)
        self.setPen(QPen(QColor("#f0b429") if self.isSelected() else QColor(self.plugin["color"]),
                         3.5 if self.isSelected() else 2.5))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged: self.update_path()
        return super().itemChange(change,value)

    def detach(self):
        if self in self.source_port.connections: self.source_port.connections.remove(self)
        if self.target_port and self in self.target_port.connections: self.target_port.connections.remove(self)
        if self.scene(): self.scene().removeItem(self)


class ModelItem(QGraphicsRectItem):
    def __init__(self, definition):
        self.definition=definition; self.inputs=[]; self.outputs=[]
        rows=max(len(definition["inputs"]),len(definition["outputs"]),1)
        self.height=max(100, 54+rows*28)
        super().__init__(0,0,MODEL_WIDTH,self.height)
        self.setBrush(QBrush(QColor("#263248"))); self.setPen(QPen(QColor("#53627a"),1.5))
        for f in (QGraphicsItem.ItemIsMovable,QGraphicsItem.ItemIsSelectable,QGraphicsItem.ItemSendsGeometryChanges):
            self.setFlag(f,True)
        title=QGraphicsSimpleTextItem(definition["name"],self); title.setBrush(QBrush(QColor("#f5f7fb"))); title.setPos(12,8)
        for idx,t in enumerate(definition["inputs"]):
            y=55+idx*28; p=PortItem(self,"input",t); p.setPos(0,y); self.inputs.append(p)
            lab=QGraphicsSimpleTextItem(t,self); lab.setBrush(QBrush(QColor("#9ee6b8"))); lab.setPos(12,y-9)
        for idx,t in enumerate(definition["outputs"]):
            y=55+idx*28; p=PortItem(self,"output",t); p.setPos(MODEL_WIDTH,y); self.outputs.append(p)
            lab=QGraphicsSimpleTextItem(t,self); lab.setBrush(QBrush(QColor("#ffc477")))
            lab.setPos(MODEL_WIDTH-12-lab.boundingRect().width(),y-9)
        self.setToolTip("Inputs: "+", ".join(definition["inputs"])+"\nOutputs: "+", ".join(definition["outputs"]))

    def itemChange(self,change,value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for p in self.inputs+self.outputs:
                for c in list(p.connections): c.update_path()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.setPen(QPen(QColor("#f0b429") if value else QColor("#53627a"),2.5 if value else 1.5))
        return super().itemChange(change,value)

    def all_connections(self):
        return list({c for p in self.inputs+self.outputs for c in p.connections})


class PipelineScene(QGraphicsScene):
    def __init__(self,parent=None):
        super().__init__(parent); self.setSceneRect(QRectF(-2000,-1500,4000,3000))
        self.pending=None; self.active_plugin=DEFAULT_PLUGINS[0]

    def add_model(self,d,pos):
        m=ModelItem(d); self.addItem(m); m.setPos(pos); return m

    @staticmethod
    def matches(actual,required): return actual==required or actual=="Any" or required=="Any"

    def choose_port(self, model, kind, required_type):
        ports = model.outputs if kind == "output" else model.inputs
        compatible = [p for p in ports if self.matches(p.data_type, required_type)]
        if not compatible:
            QMessageBox.warning(None, "No compatible port",
                f"{model.definition['name']} has no {kind} compatible with {required_type}.")
            return None
        if len(compatible) == 1:
            return compatible[0]
        labels = []
        for p in compatible:
            number = ports.index(p) + 1
            labels.append(f"{kind.title()} {number}: {p.data_type}")
        choice, ok = QInputDialog.getItem(None, f"Choose {kind}",
            f"{model.definition['name']} has multiple compatible {kind}s. Choose one:",
            labels, 0, False)
        return compatible[labels.index(choice)] if ok else None

    def begin_connection(self,port,pos):
        self.cancel_pending()
        required = self.active_plugin["from"]
        if not self.matches(port.data_type, required):
            chosen = self.choose_port(port.parent_model, "output", required)
            if chosen is None:
                return
            port = chosen
        else:
            same_type = [p for p in port.parent_model.outputs
                         if self.matches(p.data_type, required)]
            if len(same_type) > 1:
                chosen = self.choose_port(port.parent_model, "output", required)
                if chosen is None:
                    return
                port = chosen
        self.pending=ConnectionItem(port,plugin=self.active_plugin.copy())
        self.addItem(self.pending); self.pending.set_preview_end(pos)

    def cancel_pending(self):
        if self.pending: self.pending.detach(); self.pending=None

    def mouseMoveEvent(self,e):
        if self.pending: self.pending.set_preview_end(e.scenePos()); e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self,e):
        if self.pending:
            target=next((i for i in self.items(e.scenePos()) if isinstance(i,PortItem) and i.kind=="input"),None)
            if target and target.parent_model is not self.pending.source_port.parent_model:
                required=self.pending.plugin["to"]
                model=target.parent_model
                compatible=[p for p in model.inputs if self.matches(p.data_type,required)]
                if not compatible:
                    QMessageBox.warning(None,"Incompatible input",
                        f"{self.pending.plugin['name']} produces {required}, "
                        f"but {model.definition['name']} has no compatible input.")
                    self.cancel_pending()
                else:
                    # The drop identifies the model; when several ports accept the same
                    # type, explicitly ask which semantic input slot the plugin feeds.
                    if len(compatible)>1:
                        chosen=self.choose_port(model,"input",required)
                        if chosen is None:
                            self.cancel_pending(); e.accept(); return
                        target=chosen
                    elif not self.matches(target.data_type,required):
                        target=compatible[0]
                    self.pending.attach_target(target); self.pending=None
            else: self.cancel_pending()
            e.accept(); return
        super().mouseReleaseEvent(e)

    def delete_selected(self):
        selected=list(self.selectedItems())
        for i in selected:
            if isinstance(i,ConnectionItem): i.detach()
        for i in selected:
            if isinstance(i,ModelItem):
                for c in i.all_connections(): c.detach()
                self.removeItem(i)


class PipelineView(QGraphicsView):
    def __init__(self,scene):
        super().__init__(scene); self.setRenderHint(QPainter.Antialiasing,True)
        self.setDragMode(QGraphicsView.RubberBandDrag); self.setBackgroundBrush(QBrush(QColor("#151b26")))
    def wheelEvent(self,e):
        f=1.15 if e.angleDelta().y()>0 else 1/1.15; self.scale(f,f)


class LibraryPanel(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window; self.model_defs=[]; self.plugin_defs=[]
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Models</b><br><small>Double-click to place</small>"))
        self.models=QListWidget(); layout.addWidget(self.models)
        for d in DEFAULT_MODELS: self.add_model_item(d)
        self.models.itemDoubleClicked.connect(self.place_model)
        b=QPushButton("+ Define model"); b.clicked.connect(self.define_model); layout.addWidget(b)

        layout.addWidget(QLabel("<b>Plugins / connectors</b><br><small>Select before drawing</small>"))
        self.plugins=QListWidget(); layout.addWidget(self.plugins)
        for d in DEFAULT_PLUGINS: self.add_plugin_item(d)
        self.plugins.currentItemChanged.connect(self.plugin_selected)
        b=QPushButton("+ Define plugin"); b.clicked.connect(self.define_plugin); layout.addWidget(b)
        layout.addStretch(); self.plugins.setCurrentRow(0)

    def add_model_item(self,d):
        self.model_defs.append(d); item=QListWidgetItem(f"{d['name']}   [{', '.join(d['inputs'])} → {', '.join(d['outputs'])}]")
        item.setData(Qt.UserRole,len(self.model_defs)-1); self.models.addItem(item)

    def add_plugin_item(self,d):
        self.plugin_defs.append(d); item=QListWidgetItem(f"{d['name']}   [{d['from']} → {d['to']}]")
        item.setData(Qt.UserRole,len(self.plugin_defs)-1); item.setForeground(QColor(d["color"])); self.plugins.addItem(item)

    @staticmethod
    def parse_types(text):
        return [x.strip() for x in text.split(",") if x.strip()] or ["Any"]

    def define_model(self):
        d=DefinitionDialog("Define model",[
            ("name","Model name:","New model"),("inputs","Input type(s), comma separated:","Image"),
            ("outputs","Output type(s), comma separated:","Segmentation")],self)
        if d.exec():
            self.add_model_item({"name":d.value("name") or "New model",
                "inputs":self.parse_types(d.value("inputs")),"outputs":self.parse_types(d.value("outputs"))})

    def define_plugin(self):
        d=DefinitionDialog("Define plugin",[
            ("name","Plugin name:","New plugin"),("from","Accepts output type:","Segmentation"),
            ("to","Produces / connects to input type:","Image")],self)
        if d.exec():
            palette=["#45aaf2","#a55eea","#26de81","#fd9644","#fc5c65","#2bcbba"]
            self.add_plugin_item({"name":d.value("name") or "New plugin","from":d.value("from") or "Any",
                "to":d.value("to") or "Any","color":palette[len(self.plugin_defs)%len(palette)]})

    def place_model(self,item):
        self.window.add_named_model(self.model_defs[item.data(Qt.UserRole)])

    def plugin_selected(self,current,previous):
        if current:
            d=self.plugin_defs[current.data(Qt.UserRole)]
            self.window.scene.active_plugin=d
            self.window.statusBar().showMessage(f"Active plugin: {d['name']} | {d['from']} → {d['to']}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Diagram Connect — Typed AI Pipeline Mock-up"); self.resize(1350,820)
        self.scene=PipelineScene(self); self.view=PipelineView(self.scene); self.setCentralWidget(self.view)
        dock=QDockWidget("Pipeline Library",self); self.library=LibraryPanel(self); dock.setWidget(self.library)
        dock.setMinimumWidth(360); self.addDockWidget(Qt.LeftDockWidgetArea,dock)
        tb=QToolBar("Pipeline"); tb.setMovable(False); self.addToolBar(tb)
        a=QAction("Delete selected",self); a.triggered.connect(self.scene.delete_selected); tb.addAction(a)
        a=QAction("Clear canvas",self); a.triggered.connect(self.scene.clear); tb.addAction(a)
        self.build_demo()

    def add_named_model(self,d):
        center=self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_model(d,center-QPointF(MODEL_WIDTH/2,70))

    def connect(self,a,a_port,b,b_port,plugin):
        c=ConnectionItem(a.outputs[a_port],b.inputs[b_port],plugin.copy()); self.scene.addItem(c)

    def build_demo(self):
        a=self.scene.add_model(DEFAULT_MODELS[0],QPointF(-350,-60))
        b=self.scene.add_model(DEFAULT_MODELS[2],QPointF(70,-60))
        self.connect(a,0,b,0,DEFAULT_PLUGINS[1])
        self.view.centerOn(QPointF(0,0))


def main():
    app=QApplication(sys.argv); app.setStyle("Fusion")
    w=MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": main()
