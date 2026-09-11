"""Bounded inline Python effect analysis. Guest code is never executed."""
from __future__ import annotations

import ast
import builtins
import inspect
from contextvars import ContextVar
import sys
import warnings
from pathlib import Path

from .python_api import APIs
from .python_expr import Expressions
from .python_flow import Flow
from .python_model import (AnalysisStop, Coroutine, Frame, Function, Generator,
    State, Symbol, Unknown, bounded_value, AST_NODES, BRANCHES, CALL_DEPTH, SOURCE_BYTES, STEPS)

SHARED_BUDGET=ContextVar("kw_inline_analysis_budget",default=None)


class Analyzer(Flow,Expressions,APIs):
    def __init__(self,root,argv_check,shell_check,protected,*,argv=None,depth=0):
        self.root=Path(root)
        self.argv_check=argv_check
        self.shell_check=shell_check
        self.protected=protected
        self.argv=argv or ["-c"]
        self.executable=sys.executable
        self.steps=0
        self.depth=depth
        self.awaiting=False
        self.future_annotations=False
        self.nodes={}
        self.frame_number=0
        self.function_number=0
        self.budget=SHARED_BUDGET.get() or {"steps":0}
        self.last_state=None

    def code(self,node):
        self.nodes[id(node)]=node
        return id(node)

    def tick(self,node):
        self.steps+=1
        self.budget["steps"]+=1
        if self.budget["steps"]>STEPS:
            raise AnalysisStop("analysis step budget exceeded",node)

    def limit(self,states,node):
        if len(states)>BRANCHES:
            raise AnalysisStop("analysis branch budget exceeded",node)
        return states

    def new_frame(self,state,*,parent,kind="function"):
        self.frame_number+=1
        state.frames[self.frame_number]=Frame(kind=kind,parent=parent)
        return self.frame_number

    def target_frame(self,name,state,*,walrus=False):
        number=state.frame
        if walrus:
            while state.frames[number].kind=="comprehension":
                number=state.frames[number].parent
        frame=state.frames[number]
        if name in frame.globals:
            return 0
        if name in frame.nonlocals:
            number=frame.parent
            while number is not None and number!=0:
                if name in state.frames[number].bound:
                    return number
                number=state.frames[number].parent
            raise AnalysisStop("nonlocal binding is unresolved")
        return number

    def set_name(self,name,value,state,*,walrus=False):
        number=self.target_frame(name,state,walrus=walrus)
        frame=state.frames[number]
        frame.values[name]=bounded_value(value)
        frame.bound.add(name)

    def lookup(self,name,state):
        number=self.target_frame(name,state)
        while number is not None:
            frame=state.frames[number]
            if name in frame.values:
                return frame.values[name]
            if name in frame.bound and frame.kind=="function":
                return Unknown("unbound local "+name,"unbound_local")
            number=frame.parent
        if hasattr(builtins,name):
            return Symbol("builtins."+name)
        return Unknown("unbound name "+name,"unbound_name")

    def check_write(self,path,state,node):
        resolved=self.resolve(path,state,node)
        if self.protected(resolved,state.cwd,self.root):
            raise AnalysisStop("Project source paths are read-only",node,decision="deny")
        return resolved

    def effect(self,operation,path,state,node,*,value=None):
        self.last_state=state
        resolved=self.check_write(path,state,node) if operation=="write" else self.resolve(path,state,node)
        state.effects.append({"operation":operation,"path":resolved,"line":node.lineno})
        state.value=value
        error=state.clone()
        error.control="raise"
        # File/encoding/process failures can choose different exception paths.
        # Unknown preserves all possible handlers instead of silently skipping.
        error.exception=Unknown("I/O exception")
        return [state,error]

    def define(self,node,state):
        if getattr(node,"type_params",[]):
            raise AnalysisStop("generic type parameter scope is unclassified",node)
        decorators=getattr(node,"decorator_list",[])
        defaults=node.args.defaults
        keywords=[(arg.arg,value) for arg,value in zip(node.args.kwonlyargs,node.args.kw_defaults) if value is not None]
        results=[]
        for item in self.parts([*decorators,*defaults,*[value for _,value in keywords]],state):
            if item.control!="normal":
                results.append(item);continue
            values=item.value
            annotations={arg.arg:self.code(arg.annotation) for arg in [*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs,
                         *([node.args.vararg] if node.args.vararg else []),*([node.args.kwarg] if node.args.kwarg else [])] if arg.annotation}
            if getattr(node,"returns",None):
                annotations["return"]=self.code(node.returns)
            function=Function(self.code(node),item.frame,values[len(decorators):len(decorators)+len(defaults)],
                              dict(zip([key for key,_ in keywords],values[len(decorators)+len(defaults):])),annotations)
            self.function_number+=1
            function.identity=self.function_number
            item.stack.append([function,*values[:len(decorators)]])
            active=[item]
            for _ in decorators:
                following=[]
                for pending in active:
                    if pending.control!="normal":
                        following.append(pending);continue
                    decorator=pending.stack[-1].pop()
                    for outcome in self.call(decorator,[pending.stack[-1][0]],{},pending,node):
                        if outcome.control=="normal":
                            outcome.stack[-1][0]=outcome.value
                        following.append(outcome)
                active=self.limit(following,node)
            for outcome in active:
                value=outcome.stack.pop()[0]
                if outcome.control=="normal":
                    if not isinstance(node,ast.Lambda):
                        self.set_name(node.name,value,outcome)
                    outcome.value=value
                results.append(outcome)
        return self.limit(results,node)

    def local_names(self,node):
        bound,global_names,nonlocal_names=set(),set(),set()
        def visit(item,*,root=False):
            if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and not root:
                bound.add(item.name)
                for child in getattr(item,"decorator_list",[]):visit(child)
                for child in getattr(getattr(item,"args",None),"defaults",[]):visit(child)
                return
            if isinstance(item,ast.Lambda) and not root:
                for child in item.args.defaults:visit(child)
                return
            if isinstance(item,(ast.ListComp,ast.DictComp,ast.SetComp,ast.GeneratorExp)):
                for child in ast.walk(item):
                    if isinstance(child,ast.NamedExpr) and isinstance(child.target,ast.Name):bound.add(child.target.id)
                visit(item.generators[0].iter)
                return
            if isinstance(item,ast.Name) and isinstance(item.ctx,ast.Store):bound.add(item.id)
            if isinstance(item,(ast.Import,ast.ImportFrom)):
                bound.update(alias.asname or (alias.name.split('.')[0] if isinstance(item,ast.Import) else alias.name) for alias in item.names)
            if isinstance(item,ast.ExceptHandler) and item.name:bound.add(item.name)
            if isinstance(item,ast.Global):global_names.update(item.names)
            if isinstance(item,ast.Nonlocal):nonlocal_names.update(item.names)
            for child in ast.iter_child_nodes(item):visit(child)
        visit(node,root=True)
        return bound-global_names-nonlocal_names,global_names,nonlocal_names

    def call(self,function,args,kwargs,state,node,*,force=False):
        self.tick(node)
        if not isinstance(function,Function):
            return self.api_call(function,args,kwargs,state,node,force=force)
        definition=self.nodes[function.code]
        if isinstance(definition,ast.AsyncFunctionDef) and not force:
            state.value=Coroutine(function,args,kwargs)
            return [state]
        if self.depth>=CALL_DEPTH:
            raise AnalysisStop("local call depth budget exceeded",node)
        parameters=[]
        positional=[*definition.args.posonlyargs,*definition.args.args]
        offset=len(positional)-len(function.defaults)
        for index,arg in enumerate(positional):
            parameters.append(inspect.Parameter(arg.arg,inspect.Parameter.POSITIONAL_ONLY if index<len(definition.args.posonlyargs)
                              else inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              default=function.defaults[index-offset] if index>=offset else inspect.Parameter.empty))
        if definition.args.vararg:parameters.append(inspect.Parameter(definition.args.vararg.arg,inspect.Parameter.VAR_POSITIONAL))
        parameters.extend(inspect.Parameter(arg.arg,inspect.Parameter.KEYWORD_ONLY,default=function.kwdefaults.get(arg.arg,inspect.Parameter.empty))
                          for arg in definition.args.kwonlyargs)
        if definition.args.kwarg:parameters.append(inspect.Parameter(definition.args.kwarg.arg,inspect.Parameter.VAR_KEYWORD))
        try:
            bound=inspect.Signature(parameters).bind(*args,**kwargs)
            bound.apply_defaults()
        except TypeError:
            state.control,state.exception="raise",Symbol("builtins.TypeError")
            return [state]
        caller=state.frame
        number=self.new_frame(state,parent=function.parent)
        frame=state.frames[number]
        frame.bound,frame.globals,frame.nonlocals=self.local_names(definition)
        frame.values=dict(bound.arguments)
        frame.bound.update(frame.values)
        state.frame=number
        self.depth+=1
        try:
            if isinstance(definition,ast.Lambda):
                results=self.expr(definition.body,state)
            else:
                results=self.block(definition.body,[state])
                for item in results:
                    if item.control=="normal":item.value=None
                    elif item.control=="return":item.control="normal"
            for item in results:item.frame=caller
            return self.limit(results,node)
        finally:
            self.depth-=1

    def await_value(self,value,state,node):
        if not isinstance(value,Coroutine):
            raise AnalysisStop("awaitable origin is unclassified",node)
        previous=self.awaiting
        self.awaiting=True
        try:
            return self.call(value.function,value.args,value.kwargs,state,node,force=True)
        finally:
            self.awaiting=previous

    def materialize(self,value,state,node):
        if not isinstance(value,Generator):
            state.value=value
            return [state]
        if value.consumed:
            state.value=[]
            return [state]
        value.consumed=True
        return self.comprehension(self.nodes[value.code],state,initial=value.iterable,parent=value.parent)

    def annotations(self,annotations,parent,state,node):
        if self.future_annotations:
            state.value={name:ast.unparse(self.nodes[code]) for name,code in annotations.items()}
            return [state]
        caller=state.frame
        state.frame=parent
        results=self.parts([self.nodes[code] for code in annotations.values()],state)
        for item in results:
            item.frame=caller
            if item.control=="normal":item.value=dict(zip(annotations,item.value))
        return results

    def analyze(self,source,cwd):
        states=[]
        try:
            if len(source.encode("utf-8"))>SOURCE_BYTES:
                raise AnalysisStop("source byte budget exceeded")
            tree=ast.parse(source)
            if sum(1 for _ in ast.walk(tree))>AST_NODES:
                raise AnalysisStop("AST node budget exceeded",tree)
            # Compilation validates scopes and control syntax, but no generated
            # code object is ever executed by this analyzer.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore",SyntaxWarning)
                compile(tree,"<inline-safety>","exec",dont_inherit=True)
            states=self.block(tree.body,[State(cwd=str(Path(cwd).resolve()))])
            if any(state.control=="raise" and isinstance(state.exception,Symbol) and
                   state.exception.name in {"builtins.NameError","builtins.UnboundLocalError"} for state in states):
                raise AnalysisStop("unresolved name/callable binding")
            result={"decision":"allow","complete":True,"reason":"","line":None,"column":None}
        except AnalysisStop as exc:
            result={"decision":exc.decision,"complete":False,"reason":exc.reason,
                    "line":getattr(exc.node,"lineno",None),"column":getattr(exc.node,"col_offset",None)}
        except (SyntaxError,RecursionError,ValueError,TypeError,KeyError,IndexError,OverflowError) as exc:
            result={"decision":"inconclusive","complete":False,"reason":"analysis unavailable: "+type(exc).__name__,
                    "line":getattr(exc,"lineno",None),"column":None}
        result["steps"]=self.steps
        result["traces"]=[{"control":state.control,"effects":state.effects} for state in states]
        return result


def analyze(source,cwd,root,argv_check,shell_check,protected,*,argv=None):
    token=SHARED_BUDGET.set({"steps":0}) if SHARED_BUDGET.get() is None else None
    try:
        return Analyzer(root,argv_check,shell_check,protected,argv=argv).analyze(source,cwd)
    finally:
        if token is not None:SHARED_BUDGET.reset(token)
