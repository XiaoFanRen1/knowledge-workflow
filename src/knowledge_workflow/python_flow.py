"""Bounded control-flow paths, including exceptions and lexical scopes."""
from __future__ import annotations

import ast
import builtins
import copy

from .python_api import MODULES
from .python_model import (AnalysisStop, ContextValue, Environment, FileValue,
    Frame, Function, Generator, PathValue, Symbol, Unknown, finite, known, mapping_key, ITERATIONS)


class Flow:
    def block(self,nodes,states):
        for node in nodes:
            following=[]
            for state in states:
                following.extend(self.statement(node,state) if state.control=="normal" else [state])
            states=self.limit(following,node)
        return states

    def statement(self,node,state):
        self.tick(node)
        if isinstance(node,ast.Expr):
            return self.expr(node.value,state)
        if isinstance(node,(ast.Assign,ast.AnnAssign,ast.NamedExpr)):
            if isinstance(node,ast.AnnAssign) and state.frame==0 and isinstance(node.target,ast.Name):
                state.frames[0].annotations[node.target.id]=self.code(node.annotation)
            if node.value is None:
                return [state]
            results=self.expr(node.value,state)
            for item in results:
                if item.control=="normal":
                    for target in node.targets if isinstance(node,ast.Assign) else [node.target]:
                        self.bind(target,item.value,item)
            return results
        if isinstance(node,ast.AugAssign):
            if not isinstance(node.target,ast.Name):
                raise AnalysisStop("augmented target must be a resolved local name",node)
            results=self.parts([node.target,node.value],state)
            for item in results:
                if item.control=="normal":
                    left,right=item.value
                    if isinstance(left,list) and isinstance(node.op,ast.Add):
                        self.check_mutation(left,item,node)
                        left.extend(finite(right,node))
                        value=left
                    else:
                        value=self.binary(node.op,left,right,node=node,state=item)
                    if item.control=="normal":
                        self.bind(node.target,value,item)
            return results
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            if isinstance(node,ast.ImportFrom) and node.module=="__future__":
                if any(alias.name!="annotations" for alias in node.names):
                    raise AnalysisStop("unsupported future feature",node)
                self.future_annotations=True
                return [state]
            for alias in node.names:
                module=alias.name if isinstance(node,ast.Import) else node.module or ""
                if module.split('.')[0] not in MODULES or alias.name=="*" or getattr(node,"level",0):
                    raise AnalysisStop("unclassified import origin: "+module,node)
                name=alias.asname or (alias.name.split('.')[0] if isinstance(node,ast.Import) else alias.name)
                symbol=module if isinstance(node,ast.Import) and alias.asname else module.split('.')[0] if isinstance(node,ast.Import) else module+"."+alias.name
                self.set_name(name,Symbol(symbol),state)
            return [state]
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            return self.define(node,state)
        if isinstance(node,ast.Return):
            results=self.expr(node.value,state)
            for item in results:
                if item.control=="normal":
                    item.control="return"
            return results
        if isinstance(node,ast.Raise):
            results=self.parts([node.exc,node.cause],state)
            for item in results:
                if item.control=="normal":
                    exception=item.value[0]
                    item.control="raise"
                    item.exception=exception or item.exception or Unknown("reraised exception")
            return results
        if isinstance(node,ast.If):
            results=[]
            for item in self.expr(node.test,state):
                if item.control!="normal":
                    results.append(item)
                    continue
                choices=[True,False] if isinstance(item.value,Unknown) else [bool(item.value)]
                for choice in choices:
                    results.extend(self.block(node.body if choice else node.orelse,
                                              [item.clone() if len(choices)>1 else item]))
            return self.limit(results,node)
        if isinstance(node,ast.For):
            results=[]
            for item in self.expr(node.iter,state):
                if item.control!="normal":
                    results.append(item)
                    continue
                values=finite(item.value,node.iter)
                # Mutation of an active iterable is not silently approximated.
                item.iterating.append(item.value)
                item.stack.append(values)
                active,broken=[item],[]
                for index in range(len(values)):
                    following=[]
                    for current in active:
                        self.bind(node.target,current.stack[-1][index],current)
                        for outcome in self.block(node.body,[current]):
                            if outcome.control=="break":
                                outcome.control="normal"; broken.append(outcome)
                            elif outcome.control in {"normal","continue"}:
                                outcome.control="normal"; following.append(outcome)
                            else:
                                broken.append(outcome)
                    active=self.limit(following,node)
                for current in [*active,*broken]:
                    current.stack.pop(); current.iterating.pop()
                results.extend(broken)
                results.extend(self.block(node.orelse,active))
            return self.limit(results,node)
        if isinstance(node,ast.While):
            active,done,broken=[state],[],[]
            for iteration in range(ITERATIONS+1):
                following=[]
                for current in active:
                    for item in self.expr(node.test,current):
                        if item.control!="normal":
                            broken.append(item); continue
                        choices=[True,False] if isinstance(item.value,Unknown) else [bool(item.value)]
                        for choice in choices:
                            branch=item.clone() if len(choices)>1 else item
                            if not choice:
                                done.append(branch); continue
                            if iteration==ITERATIONS:
                                raise AnalysisStop("loop expansion budget exceeded",node)
                            for outcome in self.block(node.body,[branch]):
                                if outcome.control=="break":
                                    outcome.control="normal"; broken.append(outcome)
                                elif outcome.control in {"normal","continue"}:
                                    outcome.control="normal"; following.append(outcome)
                                else:
                                    broken.append(outcome)
                active=self.limit(following,node)
                if not active:
                    break
            return self.limit([*broken,*self.block(node.orelse,done)],node)
        if isinstance(node,ast.Try):
            return self.try_statement(node,state)
        if isinstance(node,ast.With):
            state.stack.append([])
            active=[state]
            for item in node.items:
                following=[]
                for current in active:
                    if current.control!="normal":
                        following.append(current);continue
                    for outcome in self.expr(item.context_expr,current):
                        if outcome.control=="normal":
                            value=outcome.value
                            if isinstance(value,ContextValue):
                                if value.close:outcome.stack[-1].append(value.value)
                                value=value.value
                            elif isinstance(value,FileValue):
                                outcome.stack[-1].append(value)
                            else:
                                raise AnalysisStop("unclassified context manager",node)
                            if item.optional_vars:
                                self.bind(item.optional_vars,value,outcome)
                        following.append(outcome)
                active=self.limit(following,node)
            results=[]
            for outcome in self.block(node.body,active):
                handles=outcome.stack.pop()
                for handle in reversed(handles):handle.closed=True
                results.append(outcome)
                if handles:
                    error=outcome.clone();error.control="raise";error.exception=Unknown("context close exception")
                    results.append(error)
            return self.limit(results,node)
        if isinstance(node,ast.Assert):
            results=[]
            for item in self.expr(node.test,state):
                if item.control!="normal":
                    results.append(item);continue
                choices=[True,False] if isinstance(item.value,Unknown) else [bool(item.value)]
                for choice in choices:
                    branch=item.clone() if len(choices)>1 else item
                    if choice:
                        results.append(branch)
                    else:
                        for error in self.expr(node.msg,branch):
                            if error.control=="normal":
                                error.control="raise";error.exception=Symbol("builtins.AssertionError")
                            results.append(error)
            return self.limit(results,node)
        if isinstance(node,(ast.Break,ast.Continue)):
            state.control="break" if isinstance(node,ast.Break) else "continue"
        elif isinstance(node,(ast.Pass,ast.Global,ast.Nonlocal)):
            return [state]
        else:
            raise AnalysisStop("unsupported statement: "+type(node).__name__,node)
        return [state]

    def bind(self,target,value,state,*,walrus=False):
        if isinstance(target,ast.Name):
            self.set_name(target.id,value,state,walrus=walrus)
        elif isinstance(target,(ast.Tuple,ast.List)):
            values=finite(value,target)
            stars=[i for i,x in enumerate(target.elts) if isinstance(x,ast.Starred)]
            if len(stars)>1 or (not stars and len(values)!=len(target.elts)):
                raise AnalysisStop("unclassified unpacking shape",target)
            if stars:
                index=stars[0];tail=len(target.elts)-index-1
                if len(values)<len(target.elts)-1:
                    raise AnalysisStop("unclassified unpacking shape",target)
                values=values[:index]+[values[index:len(values)-tail]]+(values[len(values)-tail:] if tail else [])
            for child,item in zip(target.elts,values):
                self.bind(child.value if isinstance(child,ast.Starred) else child,item,state,walrus=walrus)
        elif isinstance(target,ast.Subscript):
            # Assignment target expressions with calls require separate flow
            # paths; rejecting them avoids evaluating a target twice.
            if any(isinstance(x,(ast.Call,ast.NamedExpr,ast.Await)) for x in ast.walk(target)):
                raise AnalysisStop("effectful assignment target is unclassified",target)
            outcomes=self.parts([target.value,target.slice],state)
            if len(outcomes)!=1 or state.control!="normal":
                raise AnalysisStop("assignment target is unresolved",target)
            owner,key=state.value
            if isinstance(owner,Environment):
                self.environment_update(owner,{known(key,target):value},target)
            elif isinstance(owner,(list,dict)):
                self.check_mutation(owner,state,target)
                if isinstance(owner,dict):mapping_key(key,target)
                owner[known(key,target)]=value
            else:
                raise AnalysisStop("assignment target object is unclassified",target)
            state.value=value
        else:
            raise AnalysisStop("unclassified assignment target",target)

    def try_statement(self,node,state):
        results=[]
        for item in self.block(node.body,[state]):
            if item.control=="normal":
                results.extend(self.block(node.orelse,[item]));continue
            if item.control!="raise":
                results.append(item);continue
            remaining=[item]
            for handler in node.handlers:
                following=[]
                for pending in remaining:
                    saved=pending.exception
                    pending.control="normal"
                    types=self.expr(handler.type,pending) if handler.type else [pending]
                    for branch in types:
                        if branch.control!="normal":
                            results.append(branch);continue
                        match=True if handler.type is None else self.exception_match(saved,branch.value)
                        if match is not True:
                            unhandled=branch.clone() if match is None else branch
                            unhandled.control="raise";unhandled.exception=saved
                            following.append(unhandled)
                        if match is not False:
                            branch.exception=saved
                            if handler.name:
                                self.set_name(handler.name,saved,branch)
                            handled=self.block(handler.body,[branch])
                            for outcome in handled:
                                if handler.name:
                                    outcome.frames[outcome.frame].values.pop(handler.name,None)
                            results.extend(handled)
                remaining=self.limit(following,node)
            results.extend(remaining)
        final=[]
        for item in results:
            item.stack.append((item.control,item.value,item.exception))
            item.control="normal"
            for outcome in self.block(node.finalbody,[item]):
                control,value,exception=outcome.stack.pop()
                if outcome.control=="normal":
                    outcome.control,outcome.value,outcome.exception=control,value,exception
                final.append(outcome)
        return self.limit(final,node)

    def exception_match(self,error,kind):
        if isinstance(kind,tuple):
            matches=[self.exception_match(error,k) for k in kind]
            return True if True in matches else None if None in matches else False
        if not isinstance(kind,Symbol):
            raise AnalysisStop("exception handler type is unclassified")
        if kind.name in {"builtins.Exception","builtins.BaseException"}:
            return True
        if isinstance(error,Unknown):
            return None
        if not isinstance(error,Symbol):
            return None
        if error.name==kind.name:
            return True
        left=getattr(builtins,error.name.removeprefix("builtins."),None)
        right=getattr(builtins,kind.name.removeprefix("builtins."),None)
        if isinstance(left,type) and isinstance(right,type):
            return issubclass(left,right)
        return None

    def comprehension(self,node,state,*,initial=None,parent=None):
        if any(generator.is_async for generator in node.generators):
            raise AnalysisStop("async comprehension is unclassified",node)
        initial_states=self.expr(node.generators[0].iter,state) if initial is None else [state]
        if initial is not None:
            state.value=initial
        results=[]
        for item in initial_states:
            if item.control!="normal":
                results.append(item);continue
            if isinstance(node,ast.GeneratorExp) and initial is None:
                item.value=Generator(self.code(node),item.frame,item.value)
                results.append(item);continue
            caller=item.frame
            frame=self.new_frame(item,parent=caller if parent is None else parent,kind="comprehension")
            item.frame=frame
            item.stack.append([])
            outcomes=self.comprehension_level(node,0,item,item.value)
            for outcome in outcomes:
                values=outcome.stack.pop();outcome.frame=caller
                if outcome.control=="normal":
                    outcome.value={mapping_key(k,node):v for k,v in values} if isinstance(node,ast.DictComp) else set(mapping_key(x,node) for x in values) if isinstance(node,ast.SetComp) else values
                results.append(outcome)
        return self.limit(results,node)

    def comprehension_level(self,node,index,state,iterable):
        generator=node.generators[index]
        values=finite(iterable,generator.iter)
        state.iterating.append(iterable)
        state.stack.append(values)
        active=[state]
        for offset in range(len(values)):
            following=[]
            for current in active:
                if current.control!="normal":
                    following.append(current);continue
                self.bind(generator.target,current.stack[-1][offset],current)
                selected,filtered=[current],[]
                for predicate in generator.ifs:
                    choices=[]
                    for pending in selected:
                        for item in self.expr(predicate,pending):
                            if item.control!="normal":
                                filtered.append(item)
                            elif isinstance(item.value,Unknown):
                                filtered.append(item.clone());choices.append(item)
                            elif item.value:
                                choices.append(item)
                            else:
                                filtered.append(item)
                    selected=self.limit(choices,node)
                following.extend(filtered)
                for item in selected:
                    if index+1<len(node.generators):
                        for nested in self.expr(node.generators[index+1].iter,item):
                            following.extend(self.comprehension_level(node,index+1,nested,nested.value) if nested.control=="normal" else [nested])
                    else:
                        nodes=[node.key,node.value] if isinstance(node,ast.DictComp) else [node.elt]
                        for outcome in self.parts(nodes,item):
                            if outcome.control=="normal":
                                # The result accumulator is below all active
                                # iterator stacks in a nested comprehension.
                                accumulator=outcome.stack[-(index+2)]
                                accumulator.append(tuple(outcome.value) if isinstance(node,ast.DictComp) else outcome.value[0])
                            following.append(outcome)
            active=self.limit(following,node)
        for item in active:
            item.stack.pop();item.iterating.pop()
        return active
