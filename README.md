# Diagram Connect

A small PySide6 prototype for visually composing AI model pipelines.

## Current prototype

- Add draggable **model** blocks to a canvas.
- Each model has an **input port** and an **output port**.
- Create a visual connection (a future **plugin/connector**) by dragging from an output port to an input port.
- Move connected models and the line follows automatically.
- Select and delete models/connections.
- Includes a small example pipeline to demonstrate branching.

This repository is intentionally a UI/interaction prototype. It does **not** run real AI models yet.

## Run

```bash
python -m pip install -r requirements.txt
python main.py
```

## Planned direction

The prototype is designed so it can later grow into:

- typed inputs/outputs such as Image, Segmentation, Coordinates, Scalar, etc.
- explicit plugin nodes that transform data between models
- multiple inputs and outputs per model
- branching and merging pipelines
- right-click configuration for model/plugin settings and resources
- pipeline validation and execution history
- saving/loading pipeline definitions
