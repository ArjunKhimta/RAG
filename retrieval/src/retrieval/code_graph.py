"""Resolve each call site to the definition it calls, giving the edges of a call graph.

Python does not declare types, so a call such as `obj.save()` cannot always be traced to one
definition. Resolution works on names alone and prefers leaving a call unresolved over guessing,
because a wrong edge would put unrelated code in front of the answer model. The rules, tried in
order, are:

1. Same class: `self.foo()` or `cls.foo()` inside class `C`, where `C` defines `foo`.
2. Same file: `foo()` or `Foo.bar()`, where this file defines `foo` or `Foo` at the top level.
3. Imported: a name or module bound by an import, followed through re-exports such as a package
   `__init__.py`, to a definition in the repository. A name imported from outside the
   repository is never resolved by the next rule, even when the repository defines one with the
   same name.
4. Unique name: any other attribute call, such as `self.app.handle_exception()`, an inherited
   `self.foo()`, or `super().foo()`, when exactly one definition in the repository has that name.
   Names of methods on Python's built-in containers, such as `get`, `append`, or `join`, are
   skipped, because `config.get()` is far more likely a dict than the repository's one `get`.

A plain `foo()` that is neither defined in the file, imported, nor a built-in must be a local
variable or parameter, so it stays unresolved, unless the file has a star import.

Only source files take part: test files are neither callers nor targets. A definition's symbol is
its file and qualified name, such as `src/flask/app.py::Flask.make_response`, because qualified
names alone repeat across files. Each edge belongs to the chunk that contains the call, so in a
definition split into parts, each part lists only its own calls. A call from a definition to
itself adds no edge.
"""

from __future__ import annotations

import builtins
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from retrieval.call_sites import CallShape, CallSite, FileCalls, ImportBinding, module_name_for
from retrieval.chunker import ChunkKind, CodeChunk, qualify

SYMBOL_SEPARATOR = "::"

MODULE_SEPARATOR = "."

MAX_IMPORT_HOPS = 5

CALLER_KINDS = frozenset({ChunkKind.FUNCTION, ChunkKind.METHOD})

TARGET_KINDS = frozenset({ChunkKind.FUNCTION, ChunkKind.METHOD, ChunkKind.CLASS})

TOP_LEVEL_TARGET_KINDS = frozenset({ChunkKind.FUNCTION, ChunkKind.CLASS})

BUILTIN_NAMES = frozenset(dir(builtins))

BUILTIN_CONTAINER_TYPES = (dict, list, set, frozenset, tuple, str, bytes)

COMMON_METHOD_NAMES = frozenset(
    name
    for container_type in BUILTIN_CONTAINER_TYPES
    for name in dir(container_type)
    if not name.startswith("__")
)


class CallOutcome(StrEnum):
    SAME_CLASS = "same class"
    SAME_FILE = "same file"
    IMPORTED = "imported"
    UNIQUE_NAME = "unique name"
    BUILTIN = "built-in"
    EXTERNAL = "outside the repository"
    LOCAL_OR_UNKNOWN = "local or unknown name"
    COMMON_NAME = "common container method"
    AMBIGUOUS = "ambiguous name"
    NOT_DEFINED = "not defined in the repository"
    UNSUPPORTED = "unsupported call shape"


RESOLVED_OUTCOMES = frozenset(
    {CallOutcome.SAME_CLASS, CallOutcome.SAME_FILE, CallOutcome.IMPORTED, CallOutcome.UNIQUE_NAME}
)


class CallGraphError(RuntimeError):
    """A call site names a definition that has no chunk, which means the walk and chunker differ."""


@dataclass(frozen=True)
class ResolvedCall:
    file_path: str
    call_site: CallSite
    caller_symbol: str
    outcome: CallOutcome
    callee_symbol: str | None


@dataclass(frozen=True)
class CallGraph:
    """Every call site with its outcome, and the callee symbols of each chunk that has any."""

    resolved_calls: list[ResolvedCall]
    callees_by_chunk: dict[CodeChunk, tuple[str, ...]]


def symbol_for(file_path: str, qualified_name: str) -> str:
    return f"{file_path}{SYMBOL_SEPARATOR}{qualified_name}"


def build_call_graph(chunks: list[CodeChunk], file_calls: list[FileCalls]) -> CallGraph:
    """Resolve the calls of every source file. `file_calls` must cover the source files only."""
    source_chunks = [chunk for chunk in chunks if not chunk.is_test_file]
    resolver = _CallResolver(source_chunks, file_calls)
    caller_chunks = _caller_chunks_by_definition(source_chunks)
    resolved_calls: list[ResolvedCall] = []
    callee_sets: dict[CodeChunk, set[str]] = defaultdict(set)
    for one_file in file_calls:
        for call_site in one_file.call_sites:
            caller_chunk = _chunk_containing(caller_chunks, one_file.file_path, call_site)
            caller_symbol = symbol_for(one_file.file_path, call_site.caller_qualified_name)
            outcome, callee_symbol = resolver.resolve(one_file, call_site)
            resolved_calls.append(
                ResolvedCall(
                    file_path=one_file.file_path,
                    call_site=call_site,
                    caller_symbol=caller_symbol,
                    outcome=outcome,
                    callee_symbol=callee_symbol,
                )
            )
            if callee_symbol is not None and callee_symbol != caller_symbol:
                callee_sets[caller_chunk].add(callee_symbol)
    callees_by_chunk = {chunk: tuple(sorted(symbols)) for chunk, symbols in callee_sets.items()}
    return CallGraph(resolved_calls=resolved_calls, callees_by_chunk=callees_by_chunk)


def _caller_chunks_by_definition(chunks: list[CodeChunk]) -> dict[str, list[CodeChunk]]:
    caller_chunks: dict[str, list[CodeChunk]] = defaultdict(list)
    for chunk in chunks:
        if chunk.kind in CALLER_KINDS:
            caller_chunks[symbol_for(chunk.file_path, chunk.qualified_name)].append(chunk)
    return caller_chunks


def _chunk_containing(
    caller_chunks: dict[str, list[CodeChunk]], file_path: str, call_site: CallSite
) -> CodeChunk:
    """Return the chunk of the calling definition whose lines include the call.

    Several chunks share a symbol when a definition is split into parts, or when a name is defined
    more than once, such as a property and its setter, so the line picks the right one.
    """
    caller_symbol = symbol_for(file_path, call_site.caller_qualified_name)
    for chunk in caller_chunks.get(caller_symbol, []):
        if chunk.start_line <= call_site.line <= chunk.end_line:
            return chunk
    raise CallGraphError(f"No chunk of {caller_symbol} contains line {call_site.line}")


@dataclass(frozen=True)
class _SymbolTarget:
    symbol: str


@dataclass(frozen=True)
class _ModuleTarget:
    module: str


@dataclass(frozen=True)
class _ExternalTarget:
    pass


_Target = _SymbolTarget | _ModuleTarget | _ExternalTarget | None

_Resolution = tuple[CallOutcome, str | None]


class _CallResolver:
    def __init__(self, source_chunks: list[CodeChunk], file_calls: list[FileCalls]) -> None:
        self._files_by_module = _files_by_unique_module(file_calls)
        self._internal_roots = {
            module.split(MODULE_SEPARATOR)[0] for module in self._files_by_module
        }
        target_chunks = [chunk for chunk in source_chunks if chunk.kind in TARGET_KINDS]
        self._symbols = {_symbol_of(chunk) for chunk in target_chunks}
        self._class_symbols = {
            _symbol_of(chunk) for chunk in target_chunks if chunk.kind == ChunkKind.CLASS
        }
        self._top_level_by_module: dict[str, dict[str, str]] = defaultdict(dict)
        self._symbols_by_name: dict[str, set[str]] = defaultdict(set)
        self._method_symbols_by_name: dict[str, set[str]] = defaultdict(set)
        for chunk in target_chunks:
            symbol = _symbol_of(chunk)
            self._symbols_by_name[chunk.name].add(symbol)
            if chunk.kind == ChunkKind.METHOD:
                self._method_symbols_by_name[chunk.name].add(symbol)
            if chunk.parent_class is None and chunk.kind in TOP_LEVEL_TARGET_KINDS:
                module = module_name_for(chunk.file_path)
                self._top_level_by_module[module][chunk.name] = symbol

    def resolve(self, file_calls: FileCalls, call_site: CallSite) -> _Resolution:
        called_name = call_site.called_name
        if called_name is None or call_site.shape == CallShape.UNSUPPORTED:
            return CallOutcome.UNSUPPORTED, None
        if call_site.shape == CallShape.NAME:
            return self._resolve_name_call(file_calls, called_name)
        if call_site.shape == CallShape.SELF:
            return self._resolve_self_call(file_calls, call_site, called_name)
        if call_site.shape == CallShape.SUPER:
            own_member = self._own_class_member(file_calls, call_site, called_name)
            excluded = {own_member} if own_member is not None else set()
            return self._resolve_by_unique_name(
                called_name, self._method_symbols_by_name, excluded
            )
        return self._resolve_attribute_call(file_calls, call_site, called_name)

    def _resolve_name_call(self, file_calls: FileCalls, called_name: str) -> _Resolution:
        local_symbol = self._top_level_symbol(file_calls.module, called_name)
        if local_symbol is not None:
            return CallOutcome.SAME_FILE, local_symbol
        binding = file_calls.imports.get(called_name)
        if binding is not None:
            return _imported_resolution(self._resolve_binding(binding, hops=0))
        if called_name in BUILTIN_NAMES:
            return CallOutcome.BUILTIN, None
        if file_calls.has_star_import:
            return self._resolve_by_unique_name(called_name, self._symbols_by_name)
        return CallOutcome.LOCAL_OR_UNKNOWN, None

    def _resolve_self_call(
        self, file_calls: FileCalls, call_site: CallSite, called_name: str
    ) -> _Resolution:
        own_member = self._own_class_member(file_calls, call_site, called_name)
        if own_member is not None and own_member in self._symbols:
            return CallOutcome.SAME_CLASS, own_member
        return self._resolve_by_unique_name(called_name, self._method_symbols_by_name)

    def _own_class_member(
        self, file_calls: FileCalls, call_site: CallSite, called_name: str
    ) -> str | None:
        if call_site.caller_parent_class is None:
            return None
        member_name = qualify(called_name, call_site.caller_parent_class)
        return symbol_for(file_calls.file_path, member_name)

    def _resolve_attribute_call(
        self, file_calls: FileCalls, call_site: CallSite, called_name: str
    ) -> _Resolution:
        receiver_target, is_imported = self._resolve_receiver(
            file_calls, call_site.receiver_parts
        )
        if isinstance(receiver_target, _ExternalTarget):
            return CallOutcome.EXTERNAL, None
        if isinstance(receiver_target, _ModuleTarget):
            member_target = self._member_of_module(receiver_target.module, called_name, hops=0)
            return _imported_resolution(member_target)
        if isinstance(receiver_target, _SymbolTarget):
            member_symbol = f"{receiver_target.symbol}{MODULE_SEPARATOR}{called_name}"
            is_class = receiver_target.symbol in self._class_symbols
            if is_class and member_symbol in self._symbols:
                outcome = CallOutcome.IMPORTED if is_imported else CallOutcome.SAME_FILE
                return outcome, member_symbol
        return self._resolve_by_unique_name(called_name, self._symbols_by_name)

    def _resolve_receiver(
        self, file_calls: FileCalls, receiver_parts: tuple[str, ...]
    ) -> tuple[_Target, bool]:
        """Follow a dotted receiver such as `helpers.json` through modules, as far as it goes.

        Returns the target and whether the first name came from an import.
        """
        if not receiver_parts:
            return None, False
        root_name = receiver_parts[0]
        local_symbol = self._top_level_symbol(file_calls.module, root_name)
        binding = file_calls.imports.get(root_name)
        target: _Target
        if local_symbol is not None:
            target = _SymbolTarget(local_symbol)
            is_imported = False
        elif binding is not None:
            target = self._resolve_binding(binding, hops=0)
            is_imported = True
        else:
            return None, False
        for part in receiver_parts[1:]:
            if isinstance(target, _ExternalTarget):
                break
            if not isinstance(target, _ModuleTarget):
                return None, is_imported
            target = self._member_of_module(target.module, part, hops=0)
        return target, is_imported

    def _resolve_binding(self, binding: ImportBinding, hops: int) -> _Target:
        if hops > MAX_IMPORT_HOPS:
            return None
        root_name = binding.module.split(MODULE_SEPARATOR)[0]
        if root_name not in self._internal_roots:
            return _ExternalTarget()
        if binding.imported_name is None:
            if binding.module in self._files_by_module:
                return _ModuleTarget(binding.module)
            return None
        return self._member_of_module(binding.module, binding.imported_name, hops)

    def _member_of_module(self, module: str, name: str, hops: int) -> _Target:
        """Return what `module.name` refers to: a definition, a submodule, or a re-export."""
        defined_symbol = self._top_level_symbol(module, name)
        if defined_symbol is not None:
            return _SymbolTarget(defined_symbol)
        submodule = f"{module}{MODULE_SEPARATOR}{name}"
        if submodule in self._files_by_module:
            return _ModuleTarget(submodule)
        module_file = self._files_by_module.get(module)
        if module_file is None:
            return None
        re_exported = module_file.imports.get(name)
        if re_exported is None:
            return None
        return self._resolve_binding(re_exported, hops + 1)

    def _top_level_symbol(self, module: str, name: str) -> str | None:
        return self._top_level_by_module.get(module, {}).get(name)

    def _resolve_by_unique_name(
        self,
        called_name: str,
        symbols_by_name: dict[str, set[str]],
        excluded: set[str] | None = None,
    ) -> _Resolution:
        if called_name in COMMON_METHOD_NAMES:
            return CallOutcome.COMMON_NAME, None
        candidates = symbols_by_name.get(called_name, set()) - (excluded or set())
        if len(candidates) == 1:
            return CallOutcome.UNIQUE_NAME, next(iter(candidates))
        if candidates:
            return CallOutcome.AMBIGUOUS, None
        return CallOutcome.NOT_DEFINED, None


def _imported_resolution(target: _Target) -> _Resolution:
    if isinstance(target, _SymbolTarget):
        return CallOutcome.IMPORTED, target.symbol
    if isinstance(target, _ExternalTarget):
        return CallOutcome.EXTERNAL, None
    return CallOutcome.NOT_DEFINED, None


def _files_by_unique_module(file_calls: list[FileCalls]) -> dict[str, FileCalls]:
    """Index files by module name, leaving out any name that two files share.

    Two files can map to one module name, such as `flask/app.py` and `src/flask/app.py`. Neither
    can be told apart by an import, so neither is resolved through one.
    """
    files_by_module: dict[str, FileCalls] = {}
    repeated_modules: set[str] = set()
    for one_file in file_calls:
        if one_file.module in files_by_module:
            repeated_modules.add(one_file.module)
        files_by_module[one_file.module] = one_file
    for module in repeated_modules:
        del files_by_module[module]
    return files_by_module


def _symbol_of(chunk: CodeChunk) -> str:
    return symbol_for(chunk.file_path, chunk.qualified_name)
