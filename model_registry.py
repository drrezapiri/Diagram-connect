from __future__ import annotations

import json
import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


SUPPORTED_MODEL_EXTENSIONS = {".pth", ".pt", ".ckpt"}


def _slug(text: str, fallback: str) -> str:
    value = "".join(ch.lower() if ch.isalnum() else "_" for ch in text).strip("_")
    return value or fallback


def parse_ports(text: str, kind: str) -> list[dict]:
    """Parse 'Name:Type, Optional:Type?, Many:Type*' into graph port specs."""
    result = []
    used_ids = set()

    for index, raw_value in enumerate(
        [piece.strip() for piece in text.split(",") if piece.strip()],
        1,
    ):
        raw = raw_value
        required = kind == "input"
        cardinality = "one"

        while raw.endswith("?") or raw.endswith("*"):
            if raw.endswith("?"):
                required = False
                raw = raw[:-1].rstrip()
            elif raw.endswith("*"):
                cardinality = "many"
                raw = raw[:-1].rstrip()

        if ":" in raw:
            name, data_type = [piece.strip() for piece in raw.split(":", 1)]
        else:
            name = raw
            data_type = raw

        name = name or f"{kind.title()} {index}"
        data_type = data_type or "Any"

        base_id = _slug(name, f"{kind}_{index}")
        port_id = base_id
        suffix = 2
        while port_id in used_ids:
            port_id = f"{base_id}_{suffix}"
            suffix += 1
        used_ids.add(port_id)

        result.append(
            {
                "id": port_id,
                "name": name,
                "type": data_type,
                "required": bool(required),
                "cardinality": cardinality,
            }
        )

    return result


class ModelRegistry:
    """Persistent user-local registry for imported model package manifests."""

    SCHEMA = "diagram-connect.model-registry.v1"

    def __init__(self, path: Path | None = None):
        self.path = path or (Path.home() / ".diagram_connect" / "model_registry.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = {"schema": self.SCHEMA, "models": []}
        self.load()

    def load(self):
        if not self.path.exists():
            return

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        if isinstance(payload, dict) and isinstance(payload.get("models"), list):
            self._data = payload
            self._data.setdefault("schema", self.SCHEMA)

    def save(self):
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def models(self) -> list[dict]:
        return list(self._data.get("models", []))

    def register(self, manifest: dict) -> dict:
        item = dict(manifest)
        item.setdefault("schema_version", "1.0")
        item.setdefault("registry_id", f"model_{uuid.uuid4().hex[:12]}")

        artifact = str(Path(item["artifact"]["path"]).expanduser().resolve())
        item["artifact"] = dict(item["artifact"])
        item["artifact"]["path"] = artifact

        existing = [
            model for model in self._data["models"]
            if model.get("registry_id") != item["registry_id"]
        ]
        existing.append(item)
        self._data["models"] = existing
        self.save()
        return item

    @staticmethod
    def to_graph_definition(manifest: dict) -> dict:
        return {
            "name": manifest["name"],
            "inputs": [dict(port) for port in manifest.get("inputs", [])],
            "outputs": [dict(port) for port in manifest.get("outputs", [])],
            "model_package": {
                "registry_id": manifest.get("registry_id"),
                "schema_version": manifest.get("schema_version", "1.0"),
                "version": manifest.get("version", "1.0"),
                "backend": manifest.get("backend", "pytorch"),
                "architecture": manifest.get("architecture", {}),
                "artifact": manifest.get("artifact", {}),
                "runtime": manifest.get("runtime", {}),
            },
        }

    def model_definitions(self) -> list[dict]:
        return [self.to_graph_definition(model) for model in self.models()]


class ModelImportDialog(QDialog):
    """Metadata-first importer for PyTorch-family model artifacts.

    The dialog deliberately does not call torch.load(). It registers a safe
    manifest first; runtime/model validation belongs to the execution layer.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import AI Model")
        self.resize(650, 520)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Register a model artifact and define the contract that Diagram Connect "
            "will use in the graph. This step does not execute or unpickle the model."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        root.addLayout(form)

        artifact_row = QWidget()
        artifact_layout = QHBoxLayout(artifact_row)
        artifact_layout.setContentsMargins(0, 0, 0, 0)
        self.artifact_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        artifact_layout.addWidget(self.artifact_edit)
        artifact_layout.addWidget(browse)
        form.addRow("Model artifact:", artifact_row)

        self.name_edit = QLineEdit()
        form.addRow("Model name:", self.name_edit)

        self.version_edit = QLineEdit("1.0")
        form.addRow("Version:", self.version_edit)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(
            [
                "PyTorch",
                "MONAI / PyTorch",
                "TorchScript",
                "Other PyTorch-compatible",
            ]
        )
        form.addRow("Backend:", self.backend_combo)

        self.checkpoint_combo = QComboBox()
        self.checkpoint_combo.addItems(
            [
                "state_dict",
                "checkpoint dictionary",
                "complete serialized model",
                "unknown",
            ]
        )
        form.addRow("Artifact contents:", self.checkpoint_combo)

        self.architecture_edit = QLineEdit()
        self.architecture_edit.setPlaceholderText(
            "e.g. monai_resnet18, custom.MyNetwork, unknown"
        )
        form.addRow("Architecture:", self.architecture_edit)

        self.inputs_edit = QLineEdit("Image volume:ImageVolume")
        form.addRow("Inputs:", self.inputs_edit)

        self.outputs_edit = QLineEdit("Prediction:CategoricalPrediction")
        form.addRow("Outputs:", self.outputs_edit)

        self.labels_edit = QLineEdit()
        self.labels_edit.setPlaceholderText("Optional, e.g. normal, aneurysm, dissection")
        form.addRow("Class labels:", self.labels_edit)

        self.device_combo = QComboBox()
        self.device_combo.addItems(["Automatic", "CUDA preferred", "CPU"])
        form.addRow("Runtime device:", self.device_combo)

        syntax = QLabel(
            "Port syntax: Name:Type. Add ? for an optional input and * for a "
            "multi-source input. Example: Image:ImageVolume, Mask:Segmentation?"
        )
        syntax.setWordWrap(True)
        syntax.setStyleSheet("color: gray;")
        root.addWidget(syntax)

        warning = QLabel(
            "Security: arbitrary .pth files may contain pickle-based objects. "
            "Diagram Connect only records metadata at this stage; it does not load "
            "the artifact during import."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #c98b42;")
        root.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _browse(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select AI model artifact",
            "",
            "PyTorch models (*.pth *.pt *.ckpt);;All files (*)",
        )
        if not filename:
            return

        self.artifact_edit.setText(filename)
        if not self.name_edit.text().strip():
            self.name_edit.setText(Path(filename).stem)

    def _validate_and_accept(self):
        artifact = Path(self.artifact_edit.text().strip()).expanduser()
        if not artifact.is_file():
            QMessageBox.warning(self, "Missing model", "Choose an existing model artifact.")
            return

        if artifact.suffix.lower() not in SUPPORTED_MODEL_EXTENSIONS:
            QMessageBox.warning(
                self,
                "Unsupported extension",
                "Version 1 of the importer accepts .pth, .pt and .ckpt artifacts.",
            )
            return

        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing name", "Give the model a display name.")
            return

        inputs = parse_ports(self.inputs_edit.text(), "input")
        outputs = parse_ports(self.outputs_edit.text(), "output")
        if not inputs:
            QMessageBox.warning(self, "Missing inputs", "Define at least one model input.")
            return
        if not outputs:
            QMessageBox.warning(self, "Missing outputs", "Define at least one model output.")
            return

        self.accept()

    def manifest(self) -> dict:
        artifact = Path(self.artifact_edit.text().strip()).expanduser().resolve()
        labels = [
            value.strip()
            for value in self.labels_edit.text().split(",")
            if value.strip()
        ]

        backend_label = self.backend_combo.currentText()
        backend_id = {
            "PyTorch": "pytorch",
            "MONAI / PyTorch": "monai_pytorch",
            "TorchScript": "torchscript",
            "Other PyTorch-compatible": "pytorch_custom",
        }[backend_label]

        return {
            "schema_version": "1.0",
            "name": self.name_edit.text().strip(),
            "version": self.version_edit.text().strip() or "1.0",
            "backend": backend_id,
            "artifact": {
                "path": str(artifact),
                "format": artifact.suffix.lower().lstrip("."),
                "contents": self.checkpoint_combo.currentText(),
            },
            "architecture": {
                "name": self.architecture_edit.text().strip() or "unknown",
            },
            "inputs": parse_ports(self.inputs_edit.text(), "input"),
            "outputs": parse_ports(self.outputs_edit.text(), "output"),
            "runtime": {
                "device_preference": self.device_combo.currentText(),
            },
            "class_labels": labels,
        }
