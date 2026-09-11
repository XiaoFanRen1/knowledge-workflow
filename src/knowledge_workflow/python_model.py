"""Values and bounded state for non-executing inline Python analysis."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

SOURCE_BYTES = 65536
AST_NODES = 8000
STEPS = 20000
BRANCHES = 32
ITERATIONS = 64
CALL_DEPTH = 8
VALUE_ITEMS = 4096
VALUE_BYTES = 65536


class AnalysisStop(Exception):
    def __init__(self, reason, node=None, *, decision="inconclusive"):
        super().__init__(reason)
        self.reason = reason
        self.node = node
        self.decision = decision


@dataclass(frozen=True)
class Unknown:
    reason: str
    kind: str = "unknown"


@dataclass(frozen=True)
class Symbol:
    name: str


@dataclass(frozen=True)
class PathValue:
    path: Any


@dataclass
class FileValue:
    path: Any
    mode: str
    name: str = ""
    closed: bool = False


@dataclass(frozen=True)
class Method:
    owner: Any
    name: str


@dataclass
class Function:
    code: int
    parent: int
    defaults: list
    kwdefaults: dict
    annotations: dict
    identity: int = 0
    annotation_cache: Any = None


@dataclass
class Coroutine:
    function: Any
    args: list
    kwargs: dict


@dataclass
class Generator:
    code: int
    parent: int
    iterable: Any
    consumed: bool = False


@dataclass
class ContextValue:
    value: Any
    close: bool = False


@dataclass
class Environment:
    updates: dict = field(default_factory=dict)


@dataclass
class Frame:
    kind: str = "module"
    parent: int | None = None
    values: dict = field(default_factory=dict)
    bound: set = field(default_factory=set)
    globals: set = field(default_factory=set)
    nonlocals: set = field(default_factory=set)
    annotations: dict = field(default_factory=dict)


@dataclass
class State:
    frames: dict = field(default_factory=lambda: {0: Frame()})
    frame: int = 0
    value: Any = None
    control: str = "normal"
    exception: Any = None
    effects: list = field(default_factory=list)
    cwd: str = ""
    stack: list = field(default_factory=list)
    iterating: list = field(default_factory=list)
    environment: Environment = field(default_factory=Environment)

    def clone(self):
        # One deepcopy preserves aliasing within a branch, without sharing
        # mutable defaults, containers or closure frames across alternatives.
        return copy.deepcopy(self)


def known(value, node=None):
    if isinstance(value, Unknown):
        raise AnalysisStop("unresolved value: " + value.reason, node)
    return value


def concrete_path(value, node=None):
    value = value.path if isinstance(value, PathValue) else value
    value = known(value, node)
    if not isinstance(value, str) or not value:
        raise AnalysisStop("path must be a known nonempty string", node)
    return value


def bounded_value(value, node=None):
    pending, count = [(value,frozenset())], 0
    while pending:
        item,ancestors = pending.pop()
        count += 1
        if count > VALUE_ITEMS:
            raise AnalysisStop("value item budget exceeded", node)
        if isinstance(item, (str, bytes)) and len(item) > VALUE_BYTES:
            raise AnalysisStop("value size budget exceeded", node)
        if isinstance(item, int) and item.bit_length() > 4096:
            raise AnalysisStop("integer size budget exceeded", node)
        if isinstance(item, (list, tuple, dict, set)):
            if id(item) in ancestors:
                raise AnalysisStop("cyclic abstract container", node)
            if len(ancestors)>64:
                raise AnalysisStop("container depth budget exceeded",node)
            if len(item) > VALUE_ITEMS:
                raise AnalysisStop("container size budget exceeded", node)
            chain=ancestors|{id(item)}
            pending.extend((child,chain) for child in (item.values() if isinstance(item, dict) else item))
    return value


def finite(value, node=None):
    known(value, node)
    if isinstance(value,set):
        raise AnalysisStop("unordered iteration requires explicit deterministic sorting",node)
    if not isinstance(value, (list, tuple, range, str, dict, set)):
        raise AnalysisStop("iteration requires a known finite builtin value", node)
    if len(value) > ITERATIONS:
        raise AnalysisStop("loop expansion budget exceeded", node)
    return list(value)


def exception_name(exc):
    return Symbol("builtins." + type(exc).__name__)


def contains_abstract(value):
    if isinstance(value,(Unknown,Symbol,PathValue,FileValue,Function,Method,Coroutine,Generator,Environment,ContextValue)):
        return True
    if isinstance(value,(list,tuple,set,dict)):
        return any(contains_abstract(x) for x in (value.values() if isinstance(value,dict) else value))
    return False


def mapping_key(value,node=None):
    if isinstance(value,(str,bytes,int,float,bool,type(None))):
        return value
    if isinstance(value,tuple):
        return tuple(mapping_key(x,node) for x in value)
    raise AnalysisStop("mapping/set key needs a concrete primitive value",node)


def contains_unordered(value):
    if isinstance(value,set):return True
    if isinstance(value,(list,tuple,dict)):
        return any(contains_unordered(x) for x in (value.values() if isinstance(value,dict) else value))
    return False
