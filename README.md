# Diagram Connect

A PySide6 prototype evolving into a typed AI graph editor and execution framework.

## Current architecture

- **Rectangles** are AI model nodes.
- **Circles** are deterministic processing plugins.
- **Gray arrows** are transfer-only edges; they do not transform data.
- A fixed **Dataset** block is on the left.
- A fixed **Pipeline Output** block is on the right.
- Inputs and outputs have semantic names, datatypes, required/optional state, and cardinality.
- Activation order is derived automatically from graph dependencies.
- The graph can be validated and its execution flow can be animated.
- JSON and QBasic views expose machine-readable / alternate representations.

## AI model import — first real runtime milestone

Diagram Connect now supports metadata-first registration of:

- `.pth`
- `.pt`
- `.ckpt`

Use **Import AI Model** in the toolbar or **Import .pth / .pt model** in the model library.

The importer records:

- model name and version
- backend
- artifact/checkpoint type
- architecture name
- typed semantic inputs
- typed semantic outputs
- class labels
- device preference
- artifact path

Imported models are persisted in the user-local registry:

```text
~/.diagram_connect/model_registry.json
```

They immediately appear in the Models library and can be placed on the graph.

### Important

The import step deliberately does **not** execute `torch.load()`.

A `.pth` file may contain a state_dict, checkpoint dictionary, or an arbitrary
pickled Python object. Registering the model contract first keeps the graph
format deterministic and avoids blindly executing untrusted serialized code.

See [MODEL_PACKAGE.md](MODEL_PACKAGE.md) for the v1 manifest specification.

## Port syntax

Custom model/plugin definitions use:

```text
Name:Type
```

Examples:

```text
CT image:ImageVolume
Tumor mask:Segmentation
Prior mask:Segmentation?
Supporting masks:Segmentation*
```

- `?` = optional input
- `*` = multiple incoming transfers allowed

## Run

Create/use a Python environment with PySide6 installed:

```bash
python -m pip install -r requirements.txt
python -m py_compile main.py model_registry.py
python main.py
```

## Next runtime milestone

Add a model-adapter layer instead of loading arbitrary model files directly:

```text
ModelAdapter
├── validate_environment()
├── load()
├── prepare_inputs()
├── run()
├── postprocess()
└── unload()
```

Initial executable support should be narrow and explicit, for example:

- MONAI ResNet state_dict packages
- TorchScript
- selected registered custom PyTorch architectures

The visual editor should remain separate from the execution engine.
