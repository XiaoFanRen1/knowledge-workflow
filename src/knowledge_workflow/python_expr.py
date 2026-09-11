"""Expression evaluation order; only abstract/builtin values are evaluated."""
from __future__ import annotations

import ast
import operator
import re
from pathlib import Path

from .python_model import (AnalysisStop, Coroutine, FileValue, Frame, Function,
    Method, PathValue, Symbol, Unknown, bounded_value, concrete_path, exception_name,
    finite, known, contains_abstract, mapping_key, VALUE_BYTES, VALUE_ITEMS)

BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
          ast.Pow: operator.pow, ast.BitOr: operator.or_, ast.BitAnd: operator.and_,
          ast.BitXor: operator.xor, ast.LShift: operator.lshift, ast.RShift: operator.rshift}
COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
           ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
           ast.In: lambda a,b: a in b, ast.NotIn: lambda a,b: a not in b,
           ast.Is: operator.is_, ast.IsNot: operator.is_not}


class Expressions:
    def parts(self, nodes, state):
        state.stack.append([])
        states = [state]
        for node in nodes:
            following = []
            for item in states:
                if item.control != "normal":
                    following.append(item)
                    continue
                for result in self.expr(node, item):
                    if result.control == "normal":
                        result.stack[-1].append(result.value)
                    following.append(result)
            states = self.limit(following, node)
        for item in states:
            item.value = item.stack.pop()
        return states

    def expr(self, node, state):
        self.tick(node)
        if state.control != "normal":
            return [state]
        if node is None:
            state.value = None
        elif isinstance(node, ast.Constant):
            state.value = bounded_value(node.value, node)
        elif isinstance(node, ast.Name):
            if node.id == "__annotations__":
                raise AnalysisStop("module annotation namespace access is unclassified",node)
            state.value = self.lookup(node.id, state)
            if isinstance(state.value,Unknown) and state.value.kind.startswith("unbound_"):
                state.control="raise"
                state.exception=Symbol("builtins.UnboundLocalError" if state.value.kind=="unbound_local" else "builtins.NameError")
        elif isinstance(node, ast.Attribute):
            results = []
            for item in self.expr(node.value, state):
                if item.control != "normal":
                    results.append(item)
                elif isinstance(item.value, Function) and node.attr == "__annotations__":
                    function=item.value
                    if function.annotation_cache is not None:
                        item.value=function.annotation_cache
                        results.append(item)
                    else:
                        item.stack.append(function)
                        for outcome in self.annotations(function.annotations,function.parent,item,node):
                            owner=outcome.stack.pop()
                            if outcome.control=="normal":owner.annotation_cache=outcome.value
                            results.append(outcome)
                else:
                    item.value = self.attribute(item.value, node.attr, item, node)
                    results.append(item)
            return results
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            results = self.parts([x.value if isinstance(x, ast.Starred) else x for x in node.elts], state)
            for item in results:
                if item.control != "normal":
                    continue
                values = []
                for element, value in zip(node.elts, item.value):
                    values.extend(finite(value, element) if isinstance(element, ast.Starred) else [value])
                item.value = bounded_value(tuple(values) if isinstance(node, ast.Tuple) else
                                           set(mapping_key(x,node) for x in values) if isinstance(node, ast.Set) else values, node)
            return results
        elif isinstance(node, ast.Dict):
            nodes = [part for key,value in zip(node.keys,node.values) for part in ([key,value] if key else [value])]
            results = self.parts(nodes, state)
            for item in results:
                if item.control != "normal":
                    continue
                values, result = iter(item.value), {}
                for key in node.keys:
                    if key is None:
                        mapping = known(next(values), node)
                        if not isinstance(mapping, dict):
                            raise AnalysisStop("mapping expansion requires a known dictionary", node)
                        result.update(mapping)
                    else:
                        key_value = mapping_key(next(values), key)
                        item_value = next(values)
                        result[key_value] = item_value
                item.value = bounded_value(result, node)
            return results
        elif isinstance(node, ast.Call):
            nodes = [node.func, *[x.value if isinstance(x,ast.Starred) else x for x in node.args],
                     *[x.value for x in node.keywords]]
            results = []
            for item in self.parts(nodes, state):
                if item.control != "normal":
                    results.append(item)
                    continue
                function, *values = item.value
                args, kwargs = [], {}
                for argument, value in zip(node.args, values):
                    args.extend(finite(value, argument) if isinstance(argument, ast.Starred) else [value])
                for keyword, value in zip(node.keywords, values[len(node.args):]):
                    mapping = {keyword.arg:value} if keyword.arg else known(value, keyword.value)
                    if not isinstance(mapping, dict) or any(not isinstance(k,str) for k in mapping):
                        raise AnalysisStop("keyword expansion requires known string keys", node)
                    if set(mapping) & set(kwargs):
                        raise AnalysisStop("duplicate call keyword", node)
                    kwargs.update(mapping)
                results.extend(self.call(function,args,kwargs,item,node))
            return self.limit(results,node)
        elif isinstance(node, ast.NamedExpr):
            results = self.expr(node.value, state)
            for item in results:
                if item.control == "normal":
                    self.bind(node.target, item.value, item, walrus=True)
            return results
        elif isinstance(node, ast.IfExp):
            results = []
            for item in self.expr(node.test,state):
                if item.control != "normal":
                    results.append(item)
                    continue
                choices = [True,False] if isinstance(item.value,Unknown) else [bool(item.value)]
                for choice in choices:
                    results.extend(self.expr(node.body if choice else node.orelse,
                                             item.clone() if len(choices)>1 else item))
            return self.limit(results,node)
        elif isinstance(node, ast.BoolOp):
            active, results = [state], []
            for index, value_node in enumerate(node.values):
                following = []
                for current in active:
                    for item in self.expr(value_node,current):
                        if item.control != "normal" or index == len(node.values)-1:
                            results.append(item)
                        elif isinstance(item.value,Unknown):
                            results.append(item.clone())
                            following.append(item)
                        elif bool(item.value) == isinstance(node.op,ast.Or):
                            results.append(item)
                        else:
                            following.append(item)
                active = self.limit(following,node)
                self.limit([*results,*active],node)
            return results
        elif isinstance(node, ast.BinOp):
            results = self.parts([node.left,node.right],state)
            for item in results:
                if item.control == "normal":
                    item.value = self.binary(node.op,*item.value,node=node,state=item)
            return results
        elif isinstance(node, ast.UnaryOp):
            results = self.expr(node.operand,state)
            for item in results:
                if item.control == "normal" and not isinstance(item.value,Unknown):
                    function = {ast.Not:operator.not_,ast.USub:operator.neg,ast.UAdd:operator.pos,ast.Invert:operator.invert}.get(type(node.op))
                    item.value = self.pure(function,[item.value],{},item,node)
            return results
        elif isinstance(node, ast.Compare):
            # The middle operand of a chained comparison is evaluated once.
            state.stack.append(None)
            active, results = self.expr(node.left,state), []
            for op,right in zip(node.ops,node.comparators):
                following = []
                for current in active:
                    if current.control != "normal":
                        results.append(current)
                        continue
                    current.stack[-1] = current.value
                    for item in self.expr(right,current):
                        if item.control != "normal":
                            results.append(item)
                            continue
                        left,right_value = item.stack[-1],item.value
                        outcome = self.compare(op,left,right_value,item,node)
                        if item.control!="normal":
                            results.append(item)
                            continue
                        if isinstance(outcome,Unknown):
                            short = item.clone(); short.value = False; results.append(short)
                            item.value = right_value; following.append(item)
                        elif outcome:
                            item.value = right_value; following.append(item)
                        else:
                            item.value = False; results.append(item)
                active = self.limit(following,node)
            for item in active:
                item.value = True
            results.extend(active)
            for item in results:
                item.stack.pop()
            return self.limit(results,node)
        elif isinstance(node, ast.Subscript):
            results = self.parts([node.value,node.slice],state)
            for item in results:
                if item.control == "normal":
                    owner,key = item.value
                    item.value = self.subscript(owner,key,item,node)
            return results
        elif isinstance(node, ast.Slice):
            results = self.parts([node.lower,node.upper,node.step],state)
            for item in results:
                if item.control == "normal":
                    item.value = slice(*item.value)
            return results
        elif isinstance(node, (ast.JoinedStr,ast.FormattedValue)):
            if isinstance(node,ast.JoinedStr):
                results = self.parts(node.values,state)
                for item in results:
                    if item.control == "normal":
                        item.value = Unknown("formatted string") if any(isinstance(x,Unknown) for x in item.value) else bounded_value("".join(item.value),node)
                return results
            results = self.parts([node.value,node.format_spec],state)
            for item in results:
                if item.control != "normal":
                    continue
                value,spec = item.value
                if isinstance(value,Unknown) or isinstance(spec,Unknown):
                    item.value = Unknown("formatted string")
                    continue
                if not isinstance(value,(str,int,float,bool,type(None))):
                    raise AnalysisStop("custom formatting is unclassified",node)
                if spec and (len(spec)>32 or any(int(x)>VALUE_BYTES for x in re.findall(r'\d+',spec))):
                    raise AnalysisStop("format allocation budget exceeded",node)
                if node.conversion in (97,114,115):
                    value = {97:ascii,114:repr,115:str}[node.conversion](value)
                item.value = self.pure(format,[value,spec or ""],{},item,node)
            return results
        elif isinstance(node,ast.Lambda):
            return self.define(node,state)
        elif isinstance(node,(ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp)):
            return self.comprehension(node,state)
        elif isinstance(node,ast.Await):
            results=[]
            for item in self.expr(node.value,state):
                results.extend(self.await_value(item.value,item,node) if item.control=="normal" else [item])
            return results
        else:
            raise AnalysisStop("unsupported expression: " + type(node).__name__,node)
        return [state]

    def binary(self,op,left,right,*,node,state):
        if isinstance(left,Unknown) or isinstance(right,Unknown):
            return Unknown("computed expression")
        if isinstance(op,ast.Div) and isinstance(left,PathValue):
            return PathValue(str(Path(concrete_path(left,node))/concrete_path(right,node)))
        if isinstance(op,ast.Mod) and isinstance(left,str):
            if any(int(x)>VALUE_BYTES for x in re.findall(r'\d+',left)):
                raise AnalysisStop("format allocation budget exceeded",node)
        if isinstance(op,(ast.Mult,ast.Add)):
            if isinstance(left,(str,bytes,list,tuple)):
                size = len(left)*(right if isinstance(right,int) else 1) if isinstance(op,ast.Mult) else len(left)+(len(right) if isinstance(right,type(left)) else 0)
                if size > (VALUE_BYTES if isinstance(left,(str,bytes)) else VALUE_ITEMS):
                    raise AnalysisStop("allocation budget exceeded",node)
            if isinstance(right,(str,bytes,list,tuple)) and isinstance(op,ast.Mult) and isinstance(left,int):
                return self.binary(op,right,left,node=node,state=state)
        if isinstance(op,(ast.Pow,ast.LShift,ast.RShift)) and (not isinstance(right,int) or abs(right)>4096 or (isinstance(op,ast.Pow) and isinstance(left,int) and left.bit_length()*max(right,1)>4096)):
            raise AnalysisStop("numeric allocation budget exceeded",node)
        if type(op) not in BINOPS:
            raise AnalysisStop("unsupported binary operation",node)
        return self.pure(BINOPS[type(op)],[left,right],{},state,node)

    def pure(self,function,args,kwargs,state,node):
        if function is None:
            raise AnalysisStop("unclassified builtin operation",node)
        if contains_abstract(args) or contains_abstract(kwargs):
            raise AnalysisStop("builtin operation needs a reviewed abstract-value contract",node)
        try:
            return bounded_value(function(*args,**kwargs),node)
        except (ValueError,TypeError,KeyError,IndexError,ZeroDivisionError,OverflowError) as exc:
            state.control, state.exception = "raise",exception_name(exc)
            return None

    def compare(self,op,left,right,state,node):
        if isinstance(left,Unknown) or isinstance(right,Unknown):
            return Unknown("comparison")
        if isinstance(op,(ast.Is,ast.IsNot)):
            if isinstance(left,Symbol) and isinstance(right,Symbol):
                result=left.name==right.name
            elif isinstance(left,Function) and isinstance(right,Function):
                result=left.identity==right.identity
            elif isinstance(left,(list,dict,set)) and isinstance(right,type(left)):
                result=left is right
            elif left is None or right is None or type(left) is bool or type(right) is bool:
                result=left is right
            else:
                return Unknown("implementation-dependent identity")
            return not result if isinstance(op,ast.IsNot) else result
        if isinstance(left,Function) and isinstance(right,Function) and isinstance(op,(ast.Eq,ast.NotEq)):
            return (left.identity==right.identity)==isinstance(op,ast.Eq)
        if isinstance(left,PathValue) and isinstance(right,PathValue):
            left,right=Path(concrete_path(left,node)),Path(concrete_path(right,node))
        if contains_abstract(left) or contains_abstract(right):
            return Unknown("comparison of abstract contents")
        return self.pure(COMPARE[type(op)],[left,right],{},state,node)
