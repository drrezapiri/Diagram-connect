import sys
import json
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
    QFormLayout, QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
    QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
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
    {"name": "Direct image", "inputs": ["Image"], "outputs": ["Image"], "color": "#6b93ff"},
    {"name": "Direct segmentation", "inputs": ["Segmentation"], "outputs": ["Segmentation"], "color": "#2bcbba"},
    {"name": "Spatial locator", "inputs": ["Segmentation"], "outputs": ["Spatial location"], "color": "#e056fd"},
    {"name": "Volume extractor", "inputs": ["Segmentation"], "outputs": ["Volume"], "color": "#ff9f43"},
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
        self.plugin = plugin or {"name":"Direct", "inputs":["Any"], "outputs":["Any"], "color":"#6b93ff"}
        self.plugin_input = self.plugin.get("selected_input") or self.plugin.get("inputs", ["Any"])[0]
        self.plugin_output = self.plugin.get("selected_output") or self.plugin.get("outputs", ["Any"])[0]
        self.preview_end = None
        self.arrow = QGraphicsPolygonItem(self)
        self.arrow.setZValue(1)
        self.setZValue(-1); self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        source.connections.append(self)
        if target: target.connections.append(self)
        self.setToolTip(self.description()); self.update_path()

    def description(self):
        p=self.plugin
        return f"{p['name']}: {self.plugin_input} → {self.plugin_output}"

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
        color=QColor("#f0b429") if self.isSelected() else QColor(self.plugin["color"])
        self.setPen(QPen(color, 3.5 if self.isSelected() else 2.5))
        # Arrow-shaped plugin: the line terminates in a filled arrow head.
        import math
        angle=math.atan2(b.y()-path.pointAtPercent(.97).y(), b.x()-path.pointAtPercent(.97).x())
        size=12
        p1=QPointF(b.x()-size*math.cos(angle-.55), b.y()-size*math.sin(angle-.55))
        p2=QPointF(b.x()-size*math.cos(angle+.55), b.y()-size*math.sin(angle+.55))
        self.arrow.setPolygon(QPolygonF([b,p1,p2]))
        self.arrow.setBrush(QBrush(color)); self.arrow.setPen(QPen(color,1))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged: self.update_path()
        return super().itemChange(change,value)

    def detach(self):
        if self in self.source_port.connections: self.source_port.connections.remove(self)
        if self.target_port and self in self.target_port.connections: self.target_port.connections.remove(self)
        if self.scene(): self.scene().removeItem(self)


class ModelItem(QGraphicsRectItem):
    def __init__(self, definition):
        self.definition=definition; self.inputs=[]; self.outputs=[]; self.port_labels=[]
        self.collapsed=False
        rows=max(len(definition["inputs"]),len(definition["outputs"]),1)
        self.expanded_height=max(100,54+rows*28)
        self.height=self.expanded_height
        super().__init__(0,0,MODEL_WIDTH,self.height)
        self.setBrush(QBrush(QColor("#263248"))); self.setPen(QPen(QColor("#53627a"),1.5))
        for f in (QGraphicsItem.ItemIsMovable,QGraphicsItem.ItemIsSelectable,QGraphicsItem.ItemSendsGeometryChanges):
            self.setFlag(f,True)
        self.title=QGraphicsSimpleTextItem("▾  "+definition["name"],self)
        self.title.setBrush(QBrush(QColor("#f5f7fb"))); self.title.setPos(12,8)
        for idx,t in enumerate(definition["inputs"]):
            y=55+idx*28; p=PortItem(self,"input",t); p.setPos(0,y); self.inputs.append(p)
            lab=QGraphicsSimpleTextItem(t,self); lab.setBrush(QBrush(QColor("#9ee6b8"))); lab.setPos(12,y-9)
            self.port_labels.append(lab)
        for idx,t in enumerate(definition["outputs"]):
            y=55+idx*28; p=PortItem(self,"output",t); p.setPos(MODEL_WIDTH,y); self.outputs.append(p)
            lab=QGraphicsSimpleTextItem(t,self); lab.setBrush(QBrush(QColor("#ffc477")))
            lab.setPos(MODEL_WIDTH-12-lab.boundingRect().width(),y-9); self.port_labels.append(lab)
        self.setToolTip("Double-click to collapse/expand\nInputs: "+", ".join(definition["inputs"])+"\nOutputs: "+", ".join(definition["outputs"]))

    def mouseDoubleClickEvent(self,event):
        self.collapsed=not self.collapsed
        if self.collapsed:
            self.height=42; self.setRect(0,0,MODEL_WIDTH,self.height)
            self.title.setText("▸  "+self.definition["name"])
            for lab in self.port_labels: lab.hide()
            for p in self.inputs: p.setPos(0,self.height/2); p.hide()
            for p in self.outputs: p.setPos(MODEL_WIDTH,self.height/2); p.hide()
        else:
            self.height=self.expanded_height; self.setRect(0,0,MODEL_WIDTH,self.height)
            self.title.setText("▾  "+self.definition["name"])
            for lab in self.port_labels: lab.show()
            for idx,p in enumerate(self.inputs): p.setPos(0,55+idx*28); p.show()
            for idx,p in enumerate(self.outputs): p.setPos(MODEL_WIDTH,55+idx*28); p.show()
        for p in self.inputs+self.outputs:
            for conn in list(p.connections): conn.update_path()
        event.accept()

    def itemChange(self,change,value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for p in self.inputs+self.outputs:
                for conn in list(p.connections): conn.update_path()
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.setPen(QPen(QColor("#f0b429") if value else QColor("#53627a"),2.5 if value else 1.5))
        return super().itemChange(change,value)

    def all_connections(self):
        return list({conn for p in self.inputs+self.outputs for conn in p.connections})


class FixedEndpointItem(ModelItem):
    def __init__(self, definition, side):
        super().__init__(definition)
        self.side=side
        self.setBrush(QBrush(QColor("#183b32") if side=="left" else QColor("#3b2f18")))
        self.setPen(QPen(QColor("#62c7a0") if side=="left" else QColor("#d7ad5c"),2))
        self.setFlag(QGraphicsItem.ItemIsMovable,False)
        self.setFlag(QGraphicsItem.ItemIsSelectable,False)
        self.title.setText(("DATASET  " if side=="left" else "PIPELINE OUTPUT  ")+definition["name"])
        self.setToolTip("Fixed pipeline endpoint. Components are represented by typed ports.")
        # Dataset is a source: hide its inputs. Final output is a sink: hide its outputs.
        if side=="left":
            for p in self.inputs: p.hide()
        else:
            for p in self.outputs: p.hide()

    def mouseDoubleClickEvent(self,event):
        event.accept()


class PipelineScene(QGraphicsScene):
    def __init__(self,parent=None):
        super().__init__(parent); self.setSceneRect(QRectF(-2000,-1500,4000,3000))
        self.pending=None; self.active_plugin=DEFAULT_PLUGINS[0]
        self.dataset_block=None; self.output_block=None

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
        plugin=self.active_plugin.copy()
        inputs=plugin.get("inputs",["Any"])
        compatible_inputs=[t for t in inputs if self.matches(port.data_type,t)]
        if not compatible_inputs:
            QMessageBox.warning(None,"Incompatible plugin",
                f"{plugin['name']} has no input compatible with model output {port.data_type}.")
            return
        if len(compatible_inputs)>1:
            labels=[f"Input {i+1}: {t}" for i,t in enumerate(compatible_inputs)]
            choice,ok=QInputDialog.getItem(None,"Choose plugin input",
                f"Which input of {plugin['name']} should receive {port.data_type}?",
                labels,0,False)
            if not ok: return
            plugin["selected_input"]=compatible_inputs[labels.index(choice)]
        else:
            plugin["selected_input"]=compatible_inputs[0]
        outputs=plugin.get("outputs",["Any"])
        if len(outputs)>1:
            labels=[f"Output {i+1}: {t}" for i,t in enumerate(outputs)]
            choice,ok=QInputDialog.getItem(None,"Choose plugin output",
                f"Which output of {plugin['name']} should this connection use?",
                labels,0,False)
            if not ok:
                return
            plugin["selected_output"]=outputs[labels.index(choice)]
        else:
            plugin["selected_output"]=outputs[0]
        self.pending=ConnectionItem(port,plugin=plugin)
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
                required=self.pending.plugin_output
                model=target.parent_model
                compatible=[p for p in model.inputs if self.matches(p.data_type,required)]
                if not compatible:
                    QMessageBox.warning(None,"Incompatible input",
                        f"{self.pending.plugin['name']} produces {required}, "
                        f"but {model.definition['name']} has no compatible input.")
                    self.cancel_pending()
                else:
                    # Finishing a plugin on a receiver model always confirms the exact
                    # receiving input slot. This remains explicit even when only one
                    # compatible input currently exists.
                    labels=[]
                    for p in compatible:
                        number=model.inputs.index(p)+1
                        labels.append(f"Input {number}: {p.data_type}")
                    choice,ok=QInputDialog.getItem(
                        None,"Choose receiver input",
                        f"Which input of {model.definition['name']} should receive "
                        f"{self.pending.plugin['name']}?",
                        labels,0,False)
                    if not ok:
                        self.cancel_pending(); e.accept(); return
                    target=compatible[labels.index(choice)]
                    self.pending.attach_target(target); self.pending=None
            else: self.cancel_pending()
            e.accept(); return
        super().mouseReleaseEvent(e)

    def delete_selected(self):
        selected=list(self.selectedItems())
        for i in selected:
            if isinstance(i,ConnectionItem): i.detach()
        for i in selected:
            if isinstance(i,ModelItem) and not isinstance(i,FixedEndpointItem):
                for conn in i.all_connections(): conn.detach()
                self.removeItem(i)

    def relationship_summary(self):
        connections=[i for i in self.items() if isinstance(i,ConnectionItem) and i.target_port]
        if not connections:
            return ("No completed model-plugin-model relationships exist on the canvas yet.\n\n"
                    "A relationship is: source model output → plugin input → plugin output → receiver model input.")
        lines=[]
        for n,conn in enumerate(reversed(connections),1):
            src=conn.source_port.parent_model
            dst=conn.target_port.parent_model
            src_no=src.outputs.index(conn.source_port)+1
            dst_no=dst.inputs.index(conn.target_port)+1
            lines.append(
                f"{n}. {src.definition['name']}\n"
                f"   Output {src_no}: {conn.source_port.data_type}\n"
                f"      ↓\n"
                f"   Plugin: {conn.plugin['name']}\n"
                f"   Plugin input: {conn.plugin_input}\n"
                f"   Plugin output: {conn.plugin_output}\n"
                f"      ↓\n"
                f"   {dst.definition['name']}\n"
                f"   Input {dst_no}: {conn.target_port.data_type}"
            )
        return "\n\n".join(lines)

    def relationship_code(self):
        connections=[i for i in self.items() if isinstance(i,ConnectionItem) and i.target_port]
        graph={"schema":"diagram-connect.pipeline.v1","connections":[]}
        for conn in reversed(connections):
            src=conn.source_port.parent_model
            dst=conn.target_port.parent_model
            graph["connections"].append({
                "source_model":src.definition["name"],
                "source_output":{
                    "index":src.outputs.index(conn.source_port)+1,
                    "type":conn.source_port.data_type,
                },
                "plugin":{
                    "name":conn.plugin["name"],
                    "input":conn.plugin_input,
                    "output":conn.plugin_output,
                },
                "receiver_model":dst.definition["name"],
                "receiver_input":{
                    "index":dst.inputs.index(conn.target_port)+1,
                    "type":conn.target_port.data_type,
                },
            })
        return json.dumps(graph,indent=2)

    def relationship_qbasic(self):
        connections=[i for i in self.items() if isinstance(i,ConnectionItem) and i.target_port]
        lines=[
            "' Diagram Connect - QBasic-style pipeline description",
            "' Generated from the current visual graph",
            "",
            "CLS",
            'PRINT "AI PIPELINE"',
            ""
        ]
        if not connections:
            lines.append("' No completed connections.")
            lines.append("END")
            return "\n".join(lines)
        for n,conn in enumerate(reversed(connections),1):
            src=conn.source_port.parent_model
            dst=conn.target_port.parent_model
            src_no=src.outputs.index(conn.source_port)+1
            dst_no=dst.inputs.index(conn.target_port)+1
            safe=lambda s: str(s).replace('"', "''")
            lines.extend([
                f"' ----- CONNECTION {n} -----",
                f'SOURCE_MODEL$ = "{safe(src.definition["name"])}"',
                f'SOURCE_OUTPUT% = {src_no}',
                f'SOURCE_TYPE$ = "{safe(conn.source_port.data_type)}"',
                f'PLUGIN$ = "{safe(conn.plugin["name"])}"',
                f'PLUGIN_INPUT$ = "{safe(conn.plugin_input)}"',
                f'PLUGIN_OUTPUT$ = "{safe(conn.plugin_output)}"',
                f'RECEIVER_MODEL$ = "{safe(dst.definition["name"])}"',
                f'RECEIVER_INPUT% = {dst_no}',
                f'RECEIVER_TYPE$ = "{safe(conn.target_port.data_type)}"',
                'PRINT SOURCE_MODEL$; " OUT"; SOURCE_OUTPUT%; " ["; SOURCE_TYPE$; "]"',
                'PRINT "  -> "; PLUGIN$; " ("; PLUGIN_INPUT$; " -> "; PLUGIN_OUTPUT$; ")"',
                'PRINT "  -> "; RECEIVER_MODEL$; " IN"; RECEIVER_INPUT%; " ["; RECEIVER_TYPE$; "]"',
                "PRINT",
                ""
            ])
        lines.append("END")
        return "\n".join(lines)


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
        self.plugin_defs.append(d); item=QListWidgetItem(f"{d['name']}   [{', '.join(d['inputs'])} → {', '.join(d['outputs'])}]")
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
            ("name","Plugin name:","New plugin"),
            ("inputs","Plugin input type(s), comma separated:","Segmentation, Image"),
            ("outputs","Plugin output type(s), comma separated:","Image, Spatial location")],self)
        if d.exec():
            palette=["#45aaf2","#a55eea","#26de81","#fd9644","#fc5c65","#2bcbba"]
            self.add_plugin_item({"name":d.value("name") or "New plugin",
                "inputs":self.parse_types(d.value("inputs")),
                "outputs":self.parse_types(d.value("outputs")),"color":palette[len(self.plugin_defs)%len(palette)]})

    def place_model(self,item):
        self.window.add_named_model(self.model_defs[item.data(Qt.UserRole)])

    def plugin_selected(self,current,previous):
        if current:
            d=self.plugin_defs[current.data(Qt.UserRole)]
            self.window.scene.active_plugin=d
            self.window.statusBar().showMessage(f"Active plugin: {d['name']} | {', '.join(d['inputs'])} → {', '.join(d['outputs'])}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Diagram Connect — Typed AI Pipeline Mock-up"); self.resize(1350,820)
        self.scene=PipelineScene(self); self.view=PipelineView(self.scene); self.setCentralWidget(self.view)
        self.live_code_dialogs=[]
        dock=QDockWidget("Pipeline Library",self); self.library=LibraryPanel(self); dock.setWidget(self.library)
        dock.setMinimumWidth(360); self.addDockWidget(Qt.LeftDockWidgetArea,dock)
        tb=QToolBar("Pipeline"); tb.setMovable(False); self.addToolBar(tb)
        a=QAction("Delete selected",self); a.triggered.connect(self.scene.delete_selected); tb.addAction(a)
        a=QAction("Clear canvas",self); a.triggered.connect(self.clear_pipeline); tb.addAction(a)
        tb.addSeparator()
        a=QAction("Explain relationships",self); a.triggered.connect(self.show_relationships); tb.addAction(a)
        a=QAction("Show relationship code",self); a.triggered.connect(self.show_relationship_code); tb.addAction(a)
        a=QAction("Show QBasic",self); a.triggered.connect(self.show_qbasic_code); tb.addAction(a)
        self.build_fixed_endpoints()
        self.build_demo()

    def build_fixed_endpoints(self):
        dataset={"name":"Case data","inputs":[],"outputs":["Image","Segmentation","Metadata"]}
        result={"name":"Results","inputs":["Image","Segmentation","Spatial location","Volume","Scalar","Table"],"outputs":[]}
        self.scene.dataset_block=FixedEndpointItem(dataset,"left")
        self.scene.output_block=FixedEndpointItem(result,"right")
        self.scene.addItem(self.scene.dataset_block); self.scene.addItem(self.scene.output_block)
        self.scene.dataset_block.setPos(-720,-120)
        self.scene.output_block.setPos(500,-160)

    def clear_pipeline(self):
        self.scene.cancel_pending()
        for item in list(self.scene.items()):
            if isinstance(item,ConnectionItem):
                item.detach()
            elif isinstance(item,ModelItem) and not isinstance(item,FixedEndpointItem):
                self.scene.removeItem(item)

    def show_relationships(self):
        box=QMessageBox(self)
        box.setWindowTitle("Model ↔ Plugin Relationships")
        box.setIcon(QMessageBox.Information)
        box.setText("How the current pipeline is connected")
        box.setInformativeText(self.scene.relationship_summary())
        box.setStandardButtons(QMessageBox.Ok)
        box.setMinimumWidth(650)
        box.exec()

    def make_live_code_dialog(self,title,label_text,generator):
        dialog=QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(820,620)
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel(label_text+"  (live)"))
        from PySide6.QtWidgets import QPlainTextEdit
        editor=QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(editor)
        buttons=QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.close)
        buttons.clicked.connect(dialog.close)
        layout.addWidget(buttons)
        timer=QTimer(dialog)
        timer.setInterval(200)
        last={"text":None}
        def refresh():
            text=generator()
            if text != last["text"]:
                cursor=editor.textCursor()
                position=cursor.position()
                editor.setPlainText(text)
                cursor=editor.textCursor()
                cursor.setPosition(min(position,len(text)))
                editor.setTextCursor(cursor)
                last["text"]=text
        timer.timeout.connect(refresh)
        refresh(); timer.start()
        dialog.setModal(False)
        dialog.show()
        self.live_code_dialogs.append(dialog)
        dialog.finished.connect(lambda _=0,d=dialog: self.live_code_dialogs.remove(d) if d in self.live_code_dialogs else None)

    def show_relationship_code(self):
        self.make_live_code_dialog(
            "Pipeline Relationship Code",
            "Machine-readable JSON representation of the current model → plugin → model relationships:",
            self.scene.relationship_code,
        )

    def show_qbasic_code(self):
        self.make_live_code_dialog(
            "Pipeline — QBasic",
            "QBasic-style representation of the current model → plugin → model relationships:",
            self.scene.relationship_qbasic,
        )

    def add_named_model(self,d):
        center=self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_model(d,center-QPointF(MODEL_WIDTH/2,70))

    def connect(self,a,a_port,b,b_port,plugin):
        c=ConnectionItem(a.outputs[a_port],b.inputs[b_port],plugin.copy()); self.scene.addItem(c)

    def build_demo(self):
        a=self.scene.add_model(DEFAULT_MODELS[0],QPointF(-380,-60))
        b=self.scene.add_model(DEFAULT_MODELS[2],QPointF(80,-60))
        self.connect(a,0,b,0,DEFAULT_PLUGINS[1])
        self.view.centerOn(QPointF(0,0))


def main():
    app=QApplication(sys.argv); app.setStyle("Fusion")
    w=MainWindow(); w.show(); sys.exit(app.exec())

if __name__=="__main__": main()
