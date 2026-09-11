"""Reviewed standard-library effect contracts, without executing guest calls."""
from __future__ import annotations

import ast
import json
import os
import operator
import re
import sys
from pathlib import Path

from .python_model import (AnalysisStop, ContextValue, Coroutine, Environment,
    FileValue, Function, Generator, Method, PathValue, Symbol, Unknown, bounded_value,
    concrete_path, finite, known, contains_abstract, contains_unordered, mapping_key, exception_name, VALUE_BYTES, VALUE_ITEMS)

MODULES = {"os", "sys", "pathlib", "subprocess", "asyncio", "platform", "json",
           "math", "time", "hashlib", "io", "contextlib", "itertools", "functools",
           "shutil", "typing", "statistics", "re", "tempfile", "collections"}
ENV_CONTROL = {"PATH", "PATHEXT", "COMSPEC", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "BASH_ENV", "ENV"}
DELETES = {"os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename", "os.renames", "os.replace",
           "shutil.rmtree", "shutil.move", "shutil.chown"}
LAUNCHES = {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
            "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}


class APIs:
    def attribute(self,owner,name,state,node):
        if name.startswith("__"):
            raise AnalysisStop("unclassified reflective attribute: " + name,node)
        if isinstance(owner,Symbol):
            full = owner.name + "." + name
            constants = {"sys.executable":self.executable, "sys.argv":self.argv,
                         "sys.platform":sys.platform,"os.name":os.name,
                         "os.sep":os.sep,"os.path.sep":os.sep,"os.path.pardir":"..",
                         "os.environ":state.environment, "subprocess.PIPE":-1,
                         "subprocess.DEVNULL":-3,"subprocess.STDOUT":-2}
            if full in constants:
                return constants[full]
            if owner.name == "subprocess.CompletedProcess":
                return Unknown(full,"str" if name in {"stdout","stderr"} else "int")
            return Symbol(full)
        if isinstance(owner,PathValue):
            path = Path(concrete_path(owner,node))
            if name in {"name","stem","suffix","suffixes","parts","drive","anchor"}:
                return getattr(path,name)
            if name == "parent":
                return PathValue(str(path.parent))
        if isinstance(owner,FileValue):
            if name in {"name","mode","closed"}:return getattr(owner,name)
            if name in {"encoding","errors","newlines"}:return Unknown("file metadata","str")
        if isinstance(owner,(PathValue,FileValue,Environment,ContextValue,list,dict,set,str,bytes,tuple)):
            return Method(owner,name)
        if isinstance(owner,Unknown):
            if owner.kind in {"str","bytes","json"}:
                return Method(owner,name)
            return Unknown(owner.reason + "." + name)
        raise AnalysisStop("unclassified attribute: " + name,node)

    def subscript(self,owner,key,state,node):
        if isinstance(owner,Environment):
            return owner.updates.get(known(key,node),Unknown("environment value"))
        if isinstance(owner,Unknown) or isinstance(key,Unknown):
            return Unknown("subscript")
        if isinstance(owner,dict):mapping_key(key,node)
        if isinstance(owner,(list,tuple,dict,str,bytes)) and not contains_abstract(key):
            return self.data_operation(operator.getitem,[owner,key],{},state,node)
        return self.pure(operator.getitem,[owner,key],{},state,node)

    def api_call(self,function,args,kwargs,state,node,*,force=False):
        if isinstance(function,Method):
            return self.method_call(function.owner,function.name,args,kwargs,state,node)
        if not isinstance(function,Symbol):
            raise AnalysisStop("unclassified callable origin",node)
        name = function.name
        if name in DELETES or name in {"os.system","os.popen","builtins.eval","builtins.exec","builtins.compile","builtins.__import__"} or name.startswith(("os.exec","os.spawn")):
            raise AnalysisStop("destructive or dynamic Python execution requires user-terminal review",node,decision="deny")
        if name in LAUNCHES:
            return self.process_call(name,args,kwargs,state,node,force=force)
        if name in {"pathlib.Path","pathlib.PurePath","pathlib.WindowsPath","pathlib.PureWindowsPath"}:
            parts = [concrete_path(x,node) for x in args] or [state.cwd]
            state.value = PathValue(str(Path(*parts)))
        elif name in {"builtins.open","io.open"}:
            path = args[0] if args else kwargs.get("file",Unknown("file"))
            mode = args[1] if len(args)>1 else kwargs.get("mode","r")
            return self.open_file(path,mode,state,node)
        elif name in {"shutil.copy","shutil.copy2","shutil.copyfile","shutil.copytree"}:
            destination = args[1] if len(args)>1 else kwargs.get("dst",Unknown("copy destination"))
            return self.effect("write",destination,state,node,value=destination)
        elif name in {"os.mkdir","os.makedirs"}:
            return self.effect("write",args[0] if args else kwargs.get("name",Unknown("directory")),state,node)
        elif name == "os.chdir":
            path = self.resolve(args[0],state,node)
            normal = state.clone(); normal.cwd = path; normal.value = None
            state.control, state.exception = "raise",Symbol("builtins.OSError")
            return [normal,state]
        elif name in {"os.getcwd","pathlib.Path.cwd"}:
            state.value = PathValue(state.cwd) if name.startswith("pathlib") else state.cwd
        elif name == "os.getenv":
            if not args or not isinstance(args[0],str):
                raise AnalysisStop("environment key must be a known string",node)
            state.value = state.environment.updates.get(known(args[0],node),Unknown("environment value"))
        elif name in {"os.path.join","os.path.dirname","os.path.basename","os.path.splitext","os.path.normpath","os.path.normcase","os.fspath"}:
            values = [concrete_path(x,node) for x in args]
            operation = os.fspath if name=="os.fspath" else getattr(os.path,name.rsplit('.',1)[1])
            state.value = self.pure(operation,values,kwargs,state,node)
        elif name in {"os.path.abspath","os.path.realpath"}:
            state.value = self.resolve(args[0],state,node)
        elif name in {"os.path.exists","os.path.isfile","os.path.isdir","os.path.islink"}:
            self.resolve(args[0],state,node)
            state.value = Unknown("filesystem predicate")
        elif name == "platform.system":
            state.value = "Windows" if os.name=="nt" else Unknown("platform name")
        elif name in {"platform.platform","platform.python_version","platform.processor","time.time","time.monotonic","time.perf_counter","os.cpu_count"}:
            state.value = Unknown("read-only runtime value")
        elif name in {"asyncio.run","asyncio.loop.run_until_complete"}:
            return self.await_value(args[0],state,node)
        elif name == "asyncio.sleep":
            state.value = Coroutine(Symbol("asyncio.sleep.result"),[],{"result":kwargs.get("result")})
        elif name in {"asyncio.new_event_loop","asyncio.get_event_loop","asyncio.get_running_loop"}:
            state.value = Symbol("asyncio.loop")
        elif name in {"asyncio.loop.close","asyncio.set_event_loop","time.sleep"}:
            state.value = None
        elif name == "asyncio.sleep.result":
            state.value = kwargs.get("result")
        elif name == "contextlib.nullcontext":
            state.value = ContextValue(args[0] if args else kwargs.get("enter_result"))
        elif name == "contextlib.closing":
            if not isinstance(args[0],FileValue):
                raise AnalysisStop("closing requires a known file handle",node)
            state.value = ContextValue(args[0],close=True)
        elif name == "json.loads":
            if contains_abstract(args) or contains_abstract(kwargs):
                return self.uncertain_data(Unknown("JSON value","json"),state)
            state.value=self.pure(json.loads,args,kwargs,state,node)
        elif name == "json.dumps":
            if contains_abstract(args) or contains_abstract(kwargs):
                return self.uncertain_data(Unknown("JSON string","str"),state)
            state.value=self.pure(json.dumps,args,kwargs,state,node)
        elif name.startswith("re.") and name.rsplit('.',1)[1] in {"match","search","findall","split","sub","fullmatch"}:
            # Never execute an untrusted regular expression inside the hook.
            return self.uncertain_data(Unknown("regular expression result"),state)
        elif name.startswith("builtins."):
            return self.builtin(name[9:],args,kwargs,state,node)
        else:
            raise AnalysisStop("unclassified API: " + name,node)
        return [state]

    def resolve(self,value,state,node):
        path = concrete_path(value,node)
        if path.startswith(("\\\\","//")):
            raise AnalysisStop("remote path resolution is unclassified",node)
        candidate = Path(path)
        return str((candidate if candidate.is_absolute() else Path(state.cwd)/candidate).resolve())

    def uncertain_data(self,value,state):
        state.value=value
        error=state.clone();error.control="raise";error.exception=Unknown("data conversion exception")
        return [state,error]

    def data_operation(self,function,args,kwargs,state,node):
        """Only for builtin container operations that never call element code."""
        try:
            return bounded_value(function(*args,**kwargs),node)
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            state.control,state.exception="raise",exception_name(exc)
            return None

    def open_file(self,path,mode,state,node):
        mode = known(mode,node)
        if not isinstance(mode,str) or not mode or any(x not in "rwaxbt+" for x in mode):
            raise AnalysisStop("file mode must be classifiable",node)
        original=concrete_path(path,node)
        path = self.resolve(path,state,node)
        return self.effect("write" if any(x in mode for x in "wax+") else "read",path,state,node,value=FileValue(path,mode,original))

    def process_call(self,name,args,kwargs,state,node,*,force=False):
        if name.startswith("asyncio.") and not force:
            state.value = Coroutine(Symbol(name),args,kwargs)
            return [state]
        if "preexec_fn" in kwargs and kwargs["preexec_fn"] is not None:
            raise AnalysisStop("process preexec callback is unclassified",node)
        argv = args if name=="asyncio.create_subprocess_exec" else args[0] if args else kwargs.get("args",Unknown("process argv"))
        shell = kwargs.get("shell",name=="asyncio.create_subprocess_shell")
        known(shell,node)
        child_cwd = self.resolve(kwargs.get("cwd",state.cwd) or state.cwd,state,node)
        environment = kwargs.get("env")
        if environment is not None:
            environment = environment.updates if isinstance(environment,Environment) else known(environment,node)
            if not isinstance(environment,dict) or any(not isinstance(k,str) for k in environment):
                raise AnalysisStop("process environment is unclassified",node)
            if any(k.upper() in ENV_CONTROL for k in environment):
                raise AnalysisStop("process environment changes executable/interpreter resolution",node)
        for stream in ("stdin","stdout","stderr"):
            handle = kwargs.get(stream)
            if isinstance(handle,FileValue):
                if stream!="stdin" and not any(c in handle.mode for c in "wax+"):
                    raise AnalysisStop("process output handle is not writable",node)
                if stream!="stdin":
                    self.check_write(handle.path,state,node)
            elif handle not in (None,-1,-2,-3) and not (isinstance(handle,Symbol) and handle.name in {"sys.stdin","sys.stdout","sys.stderr"}):
                raise AnalysisStop("process stream handle is unclassified",node)
        executable = kwargs.get("executable")
        if executable is not None:
            executable = concrete_path(executable,node)
        if shell:
            if not isinstance(argv,str):
                raise AnalysisStop("shell process requires exact literal shell text",node)
            if executable and Path(executable).stem.lower() not in {"cmd","powershell","pwsh","bash","sh"}:
                raise AnalysisStop("shell executable is unclassified",node)
            issue = self.shell_check(argv,child_cwd,self.root)
        else:
            if not isinstance(argv,(list,tuple)) or not argv:
                raise AnalysisStop("process requires a resolved argv sequence",node)
            argv = [concrete_path(x,node) for x in argv]
            if executable:
                argv[0] = executable
            issue = self.argv_check(argv,child_cwd,self.root)
        if issue:
            raise AnalysisStop(issue,node,decision="deny")
        state.effects.append({"operation":"process","origin":name,"line":node.lineno,"argv":argv,"cwd":child_cwd})
        state.value = Symbol("subprocess.CompletedProcess") if name=="subprocess.run" else Unknown("process result")
        error = state.clone(); error.control="raise"; error.exception=Unknown("process exception")
        return [state,error]

    def method_call(self,owner,name,args,kwargs,state,node):
        if isinstance(owner,Unknown) and owner.kind in {"str","bytes","json"}:
            if name in {"strip","split","lower","upper","decode","encode","get","startswith","endswith"}:
                return self.uncertain_data(Unknown("data method result",owner.kind),state)
            raise AnalysisStop("unclassified data method: "+name,node)
        if isinstance(owner,PathValue):
            path = concrete_path(owner,node)
            if name in {"unlink","rmdir","rename","replace"}:
                raise AnalysisStop("filesystem removal/replacement requires user-terminal review",node,decision="deny")
            if name in {"write_text","write_bytes","touch","mkdir"}:
                return self.effect("write",owner,state,node,value=Unknown("write result"))
            if name in {"read_text","read_bytes"}:
                return self.effect("read",owner,state,node,value=Unknown("file content","str" if name=="read_text" else "bytes"))
            if name=="open":
                return self.open_file(owner,args[0] if args else kwargs.get("mode","r"),state,node)
            if name in {"resolve","absolute"}:
                state.value=PathValue(self.resolve(owner,state,node))
            elif name=="joinpath":
                state.value=PathValue(str(Path(path).joinpath(*[concrete_path(a,node) for a in args])))
            elif name in {"with_name","with_suffix","with_stem","relative_to"}:
                state.value=PathValue(str(getattr(Path(path),name)(*[concrete_path(a,node) for a in args])))
            elif name in {"as_posix","__str__"}:
                state.value=Path(path).as_posix() if name=="as_posix" else path
            elif name=="is_absolute":
                state.value=Path(path).is_absolute()
            elif name=="is_relative_to":
                state.value=Path(self.resolve(owner,state,node)).is_relative_to(Path(self.resolve(args[0],state,node)))
            elif name in {"exists","is_file","is_dir","stat"}:
                state.value=Unknown("filesystem predicate/value")
            else:
                raise AnalysisStop("unclassified Path method: "+name,node)
            return [state]
        if isinstance(owner,FileValue):
            if name=="close":
                owner.closed=True;state.value=None
                return self.uncertain_data(None,state)
            if owner.closed:
                state.control,state.exception="raise",Symbol("builtins.ValueError")
                return [state]
            if name in {"write","writelines","truncate"}:
                if not any(c in owner.mode for c in "wax+"):
                    state.control,state.exception="raise",Unknown("unsupported file operation")
                    return [state]
                return self.effect("write",owner.path,state,node,value=Unknown("write result"))
            if name in {"read","readline","readlines"}:
                return self.effect("read",owner.path,state,node,value=Unknown("file content","bytes" if "b" in owner.mode else "str"))
            if name in {"flush","seek","tell"}:
                state.value=Unknown("file position") if name=="tell" else None
                return [state]
        if isinstance(owner,Environment):
            if name=="copy":
                state.value=Environment(dict(owner.updates))
            elif name=="get":
                state.value=owner.updates.get(known(args[0],node),Unknown("environment value"))
            elif name=="update":
                mapping=args[0] if args else kwargs
                self.environment_update(owner,known(mapping,node),node)
                state.value=None
            else:
                raise AnalysisStop("unclassified environment operation",node)
            return [state]
        mutating={"append","extend","insert","remove","pop","clear","reverse","sort","update","setdefault","add","discard"}
        if name in mutating:
            self.check_mutation(owner,state,node)
        allowed={list:{"append","extend","insert","remove","pop","clear","reverse","copy","count","index"},
                 dict:{"get","keys","values","items","update","setdefault","pop","copy","clear"},
                 set:{"add","discard","remove","union","intersection","copy","clear"},
                 tuple:{"count","index"},str:{"lower","upper","strip","lstrip","rstrip","startswith","endswith","split","rsplit","splitlines","replace","join","removeprefix","removesuffix","find","rfind","count","isdigit","isalpha"},
                 bytes:{"decode","split","startswith","endswith"}}
        if name not in allowed.get(type(owner),set()):
            raise AnalysisStop("unclassified builtin method: "+name,node)
        if any(isinstance(x,Unknown) for x in args) or any(isinstance(x,Unknown) for x in kwargs.values()):
            if name in mutating:
                raise AnalysisStop("unknown mutation input",node)
            state.value=Unknown("builtin method result")
            return [state]
        if name=="join" and args:
            if not isinstance(args[0],(list,tuple)) or any(not isinstance(x,str) for x in args[0]) or sum(len(x) for x in args[0])+len(owner)*len(args[0])>VALUE_BYTES:
                raise AnalysisStop("join allocation/input budget",node)
        if name=="replace" and isinstance(owner,str) and len(args)>1:
            if not all(isinstance(x,str) for x in args[:2]) or len(owner)+max(0,len(args[1])-len(args[0]))*(len(owner)+1)>VALUE_BYTES:
                raise AnalysisStop("replace allocation/input budget",node)
        passthrough={"append","extend","insert","clear","reverse","copy","keys","values","items","get","pop","update","setdefault"}
        if isinstance(owner,dict) and name in {"get","pop","setdefault"} and args:
            mapping_key(args[0],node)
        if isinstance(owner,dict) and name=="update":
            mapping=args[0] if args else kwargs
            if not isinstance(mapping,dict):raise AnalysisStop("dict.update input must be a known mapping",node)
            for key in mapping:mapping_key(key,node)
        operation=self.data_operation if name in passthrough and isinstance(owner,(list,dict,tuple)) else self.pure
        state.value=operation(getattr(owner,name),args,kwargs,state,node)
        if type(owner) is dict and name in {"keys","values","items"}:
            state.value=list(state.value)
        bounded_value(owner,node)
        return [state]

    def environment_update(self,owner,mapping,node):
        if not isinstance(mapping,dict) or any(not isinstance(k,str) for k in mapping):
            raise AnalysisStop("environment update is unclassified",node)
        if any(k.upper() in ENV_CONTROL for k in mapping):
            raise AnalysisStop("environment changes executable/interpreter resolution",node)
        owner.updates.update(mapping)

    def check_mutation(self,owner,state,node):
        if any(owner is value for value in state.iterating):
            raise AnalysisStop("mutation of active iterable is unclassified",node)

    def builtin(self,name,args,kwargs,state,node):
        if name=="print":
            handle=kwargs.get("file")
            if handle is not None and not (isinstance(handle,Symbol) and handle.name in {"sys.stdout","sys.stderr"}):
                if not isinstance(handle,FileValue):
                    raise AnalysisStop("print file is unclassified",node)
                return self.method_call(handle,"write",[],{},state,node)
            state.value=None
        elif name in {"str","repr","ascii"}:
            value=args[0] if args else ""
            state.value=concrete_path(value,node) if isinstance(value,PathValue) else Unknown("string","str") if contains_abstract(value) or contains_unordered(value) else self.pure({"str":str,"repr":repr,"ascii":ascii}[name],args,kwargs,state,node)
        elif name=="range":
            values=[known(x,node) for x in args]
            try:
                result=range(*values)
                if len(result)>64:
                    raise AnalysisStop("loop expansion budget exceeded",node)
                state.value=result
            except (ValueError,TypeError,OverflowError):
                raise AnalysisStop("range arguments/budget are unclassified",node)
        elif name in {"list","tuple","set","dict"}:
            if args and isinstance(args[0],Generator):
                outcomes=[]
                for item in self.materialize(args[0],state,node):
                    outcomes.extend(self.builtin(name,[item.value,*args[1:]],kwargs,item,node) if item.control=="normal" else [item])
                return self.limit(outcomes,node)
            if name=="dict":
                if len(args)>1:raise AnalysisStop("dict constructor arguments are unclassified",node)
                original=args[0] if args else {}
                pairs=list(original.items()) if isinstance(original,dict) else finite(original,node)
                state.value=bounded_value({**{mapping_key(k,node):v for k,v in pairs},**kwargs},node)
            else:
                values=finite(args[0],node) if args else []
                state.value=bounded_value(tuple(values) if name=="tuple" else set(mapping_key(x,node) for x in values) if name=="set" else values,node)
        elif name in {"enumerate","zip","reversed"}:
            values=[finite(x,node) for x in args] if name=="zip" else [finite(args[0],node),*args[1:]]
            state.value=list({"enumerate":enumerate,"zip":zip,"reversed":reversed}[name](*values,**kwargs))
        elif name in {"len","int","float","bool","abs","min","max","sum","all","any","sorted","round"}:
            if name in {"len","bool"} and args and isinstance(args[0],(list,tuple,dict,set,str,bytes,range)):
                state.value=len(args[0]) if name=="len" else bool(args[0])
                return [state]
            if any(isinstance(x,Generator) for x in args):
                raise AnalysisStop("generator consumption needs explicit list/tuple materialization",node)
            if any(isinstance(x,Unknown) for x in args):
                return self.uncertain_data(Unknown("builtin result"),state)
            else:
                if any(isinstance(x,(Function,Method,Symbol)) for x in kwargs.values()):
                    raise AnalysisStop("builtin callback requires an explicit local call",node)
                functions={"len":len,"int":int,"float":float,"bool":bool,"abs":abs,"min":min,"max":max,"sum":sum,"all":all,"any":any,"sorted":sorted,"round":round}
                state.value=self.pure(functions[name],args,kwargs,state,node)
        elif name.endswith(("Error","Exception")) or name in {"Exception","BaseException"}:
            state.value=Symbol("builtins."+name)
        elif name in {"isinstance","issubclass"}:
            state.value=Unknown("type predicate")
        else:
            raise AnalysisStop("unclassified builtin: "+name,node)
        return [state]
