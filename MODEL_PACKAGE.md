# Diagram Connect Model Package — v1

Diagram Connect does not treat a `.pth` file as a complete model definition.

A runnable AI model needs an explicit **model package manifest** describing the
artifact, backend, architecture, input/output contract and runtime assumptions.

## Version 1 scope

The first importer registers:

- `.pth`
- `.pt`
- `.ckpt`

The import step is **metadata-only**. It deliberately does not call
`torch.load()` or execute model code.

Imported manifests are stored in the user's local registry:

```text
~/.diagram_connect/model_registry.json
```

The binary model remains at its original path.

## Manifest example

```json
{
  "schema_version": "1.0",
  "registry_id": "model_ab12cd34ef56",
  "name": "Aorta Classifier",
  "version": "1.0",
  "backend": "monai_pytorch",
  "artifact": {
    "path": "D:/Models/aorta_classifier.pth",
    "format": "pth",
    "contents": "state_dict"
  },
  "architecture": {
    "name": "monai_resnet18"
  },
  "inputs": [
    {
      "id": "image",
      "name": "Image volume",
      "type": "ImageVolume",
      "required": true,
      "cardinality": "one"
    },
    {
      "id": "mask",
      "name": "Segmentation",
      "type": "Segmentation",
      "required": true,
      "cardinality": "one"
    }
  ],
  "outputs": [
    {
      "id": "prediction",
      "name": "Diagnosis",
      "type": "CategoricalPrediction",
      "required": false,
      "cardinality": "one"
    }
  ],
  "runtime": {
    "device_preference": "CUDA preferred"
  },
  "class_labels": ["normal", "aneurysm", "dissection"]
}
```

## Why metadata comes first

A PyTorch `.pth` file can represent very different things:

- a `state_dict`
- a checkpoint dictionary
- a complete pickled Python model
- a framework-specific checkpoint

A `state_dict` cannot run without reconstructing the network architecture.
A pickled complete model can also be unsafe to load when it comes from an
untrusted source.

For that reason Diagram Connect first establishes a deterministic contract.
A later runtime-validation step will resolve an adapter and perform a safe
test forward pass where possible.

## Next runtime milestone

The next implementation should introduce:

```text
ModelAdapter
├── validate_environment()
├── load()
├── prepare_inputs()
├── run()
├── postprocess()
└── unload()
```

Initial adapters should be narrow and explicit, for example:

- MONAI ResNet state_dict
- TorchScript
- selected custom registered PyTorch architectures

Do not implement a generic "load any .pth" executor.
