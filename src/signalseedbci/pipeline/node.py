"""Node base class and registry.

A node declares typed ports and parameters as class attributes, which makes every
node introspectable (``signalseedbci nodes --json``) for a graphical pipeline editor.

Port kinds: ``signal``, ``markers``, ``epochs``, ``features``, ``predictions``.
An input may accept several kinds, written ``"epochs|features"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, ClassVar

REGISTRY: dict[str, type["Node"]] = {}
# old type names still accepted in pipeline files -> current name
ALIASES: dict[str, str] = {"LSLSignalOut": "DataVisualizer"}


@dataclass
class Param:
    default: Any = None
    type: str = "float"  # float, int, str, bool, list, dict, choice, path
    help: str = ""
    choices: list | None = None
    required: bool = False

    def schema(self) -> dict:
        d = {"type": self.type, "default": self.default, "help": self.help}
        if self.choices:
            d["choices"] = self.choices
        if self.required:
            d["required"] = True
        return d


@dataclass
class RunContext:
    mode: str = "online"  # "online" | "train" | "offline"
    realtime: bool = True
    store: dict = field(default_factory=dict)  # shared scratch space (e.g. training data)


def _first_paragraph(doc: str | None) -> str:
    para = (doc or "").strip().split("\n\n")[0]
    return " ".join(line.strip() for line in para.splitlines()).replace("``", "")


class Node:
    category: ClassVar[str] = "misc"
    title: ClassVar[str] = ""  # name shown in the editor (default: the type)
    description: ClassVar[str] = ""
    inputs: ClassVar[dict[str, str]] = {}
    outputs: ClassVar[dict[str, str]] = {}
    optional_inputs: ClassVar[set[str]] = set()
    params: ClassVar[dict[str, Param]] = {}
    is_source: ClassVar[bool] = False

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if not cls.__name__.startswith("_"):
            REGISTRY[cls.__name__] = cls

    def __init__(self, node_id: str, params: dict | None = None, context: RunContext | None = None):
        self.id = node_id
        self.context = context or RunContext()
        params = dict(params or {})
        unknown = set(params) - set(self.params)
        if unknown:
            raise ValueError(f"{self.id} ({type(self).__name__}): unknown parameter(s) {sorted(unknown)}; "
                             f"valid: {sorted(self.params)}")
        values = {}
        for name, spec in self.params.items():
            if name not in params and spec.required:
                raise ValueError(f"{self.id} ({type(self).__name__}): parameter '{name}' is required")
            value = params.get(name, spec.default)
            if spec.choices and value not in spec.choices:
                raise ValueError(f"{self.id}: {name}={value!r} not in {spec.choices}")
            values[name] = value
        self.p = SimpleNamespace(**values)

    # -- lifecycle -------------------------------------------------------------
    def setup(self, in_meta: dict[str, Any]) -> dict[str, Any]:
        """Receive metadata of connected inputs, return metadata of outputs."""
        return {}

    def process(self, inputs: dict[str, list]) -> dict[str, list]:
        """Consume lists of items per input port, return lists per output port."""
        return {}

    def close(self) -> None:
        pass

    # sources only
    @property
    def finished(self) -> bool:
        return False

    def log(self, msg: str) -> None:
        print(f"[{self.id}] {msg}")

    @classmethod
    def schema(cls) -> dict:
        return {
            "type": cls.__name__,
            "title": cls.title or cls.__name__,
            "category": cls.category,
            "description": cls.description or _first_paragraph(cls.__doc__),
            "inputs": cls.inputs,
            "optional_inputs": sorted(cls.optional_inputs),
            "outputs": cls.outputs,
            "params": {k: v.schema() for k, v in cls.params.items()},
        }
