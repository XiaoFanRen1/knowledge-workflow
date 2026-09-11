"""Inline programs are policy input, never executed as processes or Python."""
import ast
from pathlib import Path
import tempfile
import unittest

from knowledge_workflow.project_guard import Policy
from knowledge_workflow.python_safety import analyze
def protected_path(path,cwd,root):
    return Policy(root,["sdk"]).protected(path,cwd)

def argv_issue(values,cwd,root):
    return Policy(root,["sdk"]).argv_issue(values,cwd,root)

def shell_issue(command,cwd,root):
    return Policy(root,["sdk"]).shell_issue(command,cwd,root)


class PythonSafety(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="kw-inline-tests-")
        self.root=Path(self.temp.name)
        (self.root/"sdk").mkdir()
        self.sentinel=self.root/"sdk/sentinel.h"
        self.sentinel.write_bytes(b"unchanged")

    def tearDown(self):
        self.assertEqual(self.sentinel.read_bytes(),b"unchanged")
        self.assertEqual(sorted(x.name for x in self.root.iterdir()),["sdk"])
        self.temp.cleanup()

    def check_source(self,source,decision="allow",*,cwd=None):
        result=analyze(source,str(cwd or self.root),self.root,argv_issue,shell_issue,protected_path)
        self.assertEqual(result["decision"],decision,result)
        return result

    def paths(self,result):
        return [[Path(e["path"]).name for e in trace["effects"] if e["operation"]=="write"]
                for trace in result["traces"] if trace["control"]=="normal"]

    def test_safe_homonyms(self):
        for source in ["import platform; print(platform.system())","xs=[1,2]; xs.remove(1)",
                       "import asyncio; asyncio.run(asyncio.sleep(0))",
                       "import asyncio\nloop=asyncio.new_event_loop()\nloop.run_until_complete(asyncio.sleep(0))\nloop.close()"]:
            with self.subTest(source=source):self.check_source(source)

    def test_dangerous_aliases(self):
        for source in ["import subprocess as p; p.run(['git','reset','--hard'])",
                       "from subprocess import run; run(['git','reset','--hard'])",
                       "from subprocess import run as go; go(['git','reset','--hard'])",
                       "import subprocess; go=subprocess.run; go(['git','reset','--hard'])",
                       "from os import remove as erase; erase('sdk/sentinel.h')",
                       "from os import system as command; command('git reset --hard')"]:
            with self.subTest(source=source):self.check_source(source,"deny")

    def test_paths_and_cwd(self):
        for source in ["from pathlib import Path; (Path('sdk')/'sentinel.h').write_text('x')",
                       "from pathlib import Path; Path('docs/../sdk/sentinel.h').write_bytes(b'x')",
                       "import os.path as p; open(p.join('sdk','sentinel.h'),'w')",
                       "from pathlib import Path; p=Path('sdk'); q=p.joinpath('sentinel.h'); q.touch()"]:
            with self.subTest(source=source):self.check_source(source,"deny")
        self.check_source("open('sentinel.h','w')","deny",cwd=self.root/"sdk")

    def test_process_kwargs_and_nested_source(self):
        for source in ["import subprocess; subprocess.run(['git','status'],executable='isd_download.exe')",
                       "import subprocess; subprocess.run(args=['git','reset','--hard'])",
                       "import subprocess; subprocess.run(['python','-c',\"open('sentinel.h','w')\"],cwd='sdk')",
                       "import subprocess; subprocess.run(['git','diff','--output=sdk/sentinel.h'])"]:
            with self.subTest(source=source):self.check_source(source,"deny")
        self.check_source("import subprocess; subprocess.run(['git','status'],env={'PATH':'alternate'})","inconclusive")

    def test_safe_local_process_wrapper(self):
        self.check_source("import subprocess\ndef read(*args):\n r=subprocess.run(['git',*args],capture_output=True,text=True)\n return r.stdout.strip()\nprint(read('status','--short'))")

    def test_finite_dispatch_and_alias_mutation(self):
        self.check_source("from subprocess import run as go\ncommands={'a':['git','status'],'b':['git','diff','--stat']}\nfor command in commands.values():\n go(command)")
        self.check_source("import subprocess\na=['git','status']; b=a\nb[1]='reset'; b.append('--hard')\nsubprocess.run(a)","deny")

    def test_unknown_branches_cannot_hide_danger(self):
        self.check_source("import os\nfrom pathlib import Path\nif os.getenv('FLAG'):\n p=Path('safe')\nelse:\n p=Path('sdk/sentinel.h')\np.write_text('x')","deny")
        result=self.check_source("import os\nfrom pathlib import Path\nif os.getenv('FLAG'):\n p=Path('one')\nelse:\n p=Path('two')\np.write_text('x')")
        self.assertEqual(sorted(self.paths(result)),[["one"],["two"]])

    def test_unknown_third_boolean_operand(self):
        self.check_source("import os\nfrom pathlib import Path\nos.getenv('FLAG') or False or Path('sdk/sentinel.h').write_text('x')","deny")
        self.check_source("import os\nfrom pathlib import Path\nos.getenv('FLAG') and True and Path('sdk/sentinel.h').write_text('x')","deny")
        self.check_source("from pathlib import Path\nTrue or Path('sdk/sentinel.h').write_text('x')")

    def test_nested_call_and_target_once(self):
        source="from pathlib import Path\nseen=[]\ndef argument():\n seen.append(1)\n return 'one'\ndef target():\n seen.append(2)\n return Path\ntarget()(argument()).write_text('x')\nif seen != [2,1]:\n Path('sdk/sentinel.h').write_text('wrong evaluation')"
        result=self.check_source(source)
        self.assertEqual(self.paths(result),[["one"]])

    def test_function_default_and_decorator_order(self):
        source="from pathlib import Path\nseen=[]\ndef mark(name,value):\n seen.append(name)\n return value\ndef decorate(name):\n seen.append('evaluate_'+name)\n def apply(fn):\n  seen.append('apply_'+name)\n  return fn\n return apply\n@decorate('outer')\n@decorate('inner')\ndef f(a=mark('default',[]),*,b=mark('kwdefault',0)):\n a.append(b)\n return len(a)\nx=f(); y=f()\nif seen != ['evaluate_outer','evaluate_inner','default','kwdefault','apply_inner','apply_outer'] or x!=1 or y!=2:\n Path('sdk/sentinel.h').write_text('wrong default semantics')"
        self.check_source(source)
        self.check_source("from pathlib import Path\ndef f(a=Path('sdk/sentinel.h').write_text('x')): return a","deny")

    def test_lambda_default_and_keyword_binding(self):
        self.check_source("from pathlib import Path\nf=lambda x=Path('sdk/sentinel.h').write_text('x'):x","deny")
        self.check_source("from pathlib import Path\ndef f(name,/,*,suffix='.md'):\n return Path(name+suffix)\nf('notes',suffix='.txt').write_text('ok')")

    def test_closure_rebinding_and_nonlocal(self):
        self.check_source("from pathlib import Path\ndef outer():\n target='safe'\n def inner():\n  return target\n target='sdk/sentinel.h'\n return inner()\nPath(outer()).write_text('x')","deny")
        self.check_source("from pathlib import Path\ndef outer():\n target='safe'\n def change():\n  nonlocal target\n  target='sdk/sentinel.h'\n change()\n return target\nPath(outer()).write_text('x')","deny")

    def test_comprehension_walrus_and_scope(self):
        result=self.check_source("from pathlib import Path\nn='outer'\nlast=''\nrows=[(last := str(n)) for n in (1,2)]\nif last!='2' or n!='outer':\n Path('sdk/sentinel.h').write_text('wrong scope')\n[Path(name).write_text('x') for name in ('one','two')]")
        self.assertEqual(self.paths(result),[["one","two"]])
        self.check_source("from pathlib import Path\n[Path(name).write_text('x') for name in ('safe','sdk/sentinel.h')]","deny")

    def test_nested_comprehension(self):
        self.check_source("from pathlib import Path\nrows=[str(a)+str(b) for a in (1,2) for b in (3,4)]\nif rows!=['13','14','23','24']:\n Path('sdk/sentinel.h').write_text('wrong expansion')")

    def test_generator_is_deferred_until_materialized(self):
        self.check_source("from pathlib import Path\ng=(Path(name).write_text('x') for name in ('sdk/sentinel.h',))")
        self.check_source("from pathlib import Path\ng=(Path(name).write_text('x') for name in ('sdk/sentinel.h',))\nlist(g)","deny")

    def test_exception_handler_effects(self):
        self.check_source("from pathlib import Path\ntry:\n open('safe')\nexcept OSError:\n Path('sdk/sentinel.h').write_text('x')","deny")
        self.check_source("from pathlib import Path\ntry:\n raise ValueError('known')\nexcept ValueError:\n Path('one').write_text('x')\nelse:\n Path('sdk/sentinel.h').write_text('unreachable')")

    def test_finally_after_return_and_exception(self):
        for statement in ["return 'done'","raise ValueError('known')"]:
            with self.subTest(statement=statement):
                self.check_source("from pathlib import Path\ndef f():\n try:\n  "+statement+"\n finally:\n  Path('sdk/sentinel.h').write_text('x')\nf()","deny")

    def test_break_continue_and_finite_while(self):
        self.check_source("from pathlib import Path\ncount=0\nwhile count<3:\n count+=1\n if count==2: continue\n if count==3: break\nelse:\n Path('sdk/sentinel.h').write_text('unreachable')\nif count!=3:\n Path('sdk/sentinel.h').write_text('wrong loop')")

    def test_file_contexts_and_print_targets(self):
        self.check_source("with open('safe','w') as out:\n print('x',file=out)")
        self.check_source("with open('sdk/sentinel.h','w') as out:\n out.write('x')","deny")

    def test_format_and_named_expression_effects(self):
        self.check_source("from pathlib import Path\np=Path(f'{\"sdk\"}/{\"sentinel\"}.h')\np.write_text('x')","deny")
        self.check_source("from pathlib import Path\nx=f'{1:{Path(\"sdk/sentinel.h\").write_text(\"x\")}}'","deny")
        self.check_source("from pathlib import Path\n(p:=Path('sdk/sentinel.h')).write_text('x')","deny")

    def test_annotation_is_deferred_and_access_is_checked(self):
        source="from pathlib import Path\ndef f(x:Path('sdk/sentinel.h').write_text('x')): return x"
        self.check_source(source)
        self.check_source(source+"\nf.__annotations__","deny")
        self.check_source("from __future__ import annotations\n"+source+"\nf.__annotations__")

    def test_async_local_function_is_checked_when_awaited(self):
        source="import asyncio\nfrom pathlib import Path\nasync def work():\n await asyncio.sleep(0)\n Path('sdk/sentinel.h').write_text('x')"
        self.check_source(source+"\nwork()")
        self.check_source(source+"\nasyncio.run(work())","deny")

    def test_unknown_apis_and_syntax_are_inconclusive(self):
        for source in ["import foreign_module","mystery()","import os; getattr(os,'remove')('safe')",
                       "class Custom: pass","x=t'{1}'","a=[1]; a.append(a)"]:
            with self.subTest(source=source):self.check_source(source,"inconclusive")

    def test_resource_bounds_before_allocation(self):
        for source in ["x='a'*1000000000","x=2**1000000000","for x in range(1000000000): pass",
                       "def f():return f()\nf()","while True: pass", "x=1\n"*2100]:
            with self.subTest(source=source[:50]):self.check_source(source,"inconclusive")
        self.check_source("#"+"a"*65536,"inconclusive")

    def test_git_output_policy(self):
        self.assertTrue(argv_issue(['git','diff','--output=sdk/sentinel.h'],str(self.root),self.root))
        self.assertTrue(argv_issue(['git','show','--output','sdk/sentinel.h','HEAD'],str(self.root),self.root))
        self.assertEqual(argv_issue(['git','diff','--output=report.txt'],str(self.root),self.root),"")

    def test_unknown_nested_values_cannot_decide_false(self):
        self.check_source("import os\nfrom pathlib import Path\nvalue={'key':os.getenv('FLAG')}\nif value=={'key':'yes'}:\n Path('sdk/sentinel.h').write_text('x')","deny")

    def test_function_identity_is_not_structural_equality(self):
        self.check_source("from pathlib import Path\ndef factory():\n return lambda:0\na=factory(); b=factory()\nif a!=b:\n Path('sdk/sentinel.h').write_text('x')","deny")

    def test_annotation_cache_evaluates_once(self):
        self.check_source("from pathlib import Path\ncount=0\ndef tag():\n global count\n count+=1\n return str\ndef f(x:tag()):return x\nf.__annotations__\nf.__annotations__\nif count!=1:\n Path('sdk/sentinel.h').write_text('wrong annotation cache')")

    def test_name_error_handler_is_not_skipped(self):
        self.check_source("from pathlib import Path\ntry:\n missing\nexcept NameError:\n Path('sdk/sentinel.h').write_text('x')","deny")

    def test_generator_cannot_turn_into_fake_host_typeerror(self):
        for call in ['sum','any','all','min','max','sorted']:
            with self.subTest(call=call):
                self.check_source("from pathlib import Path\n"+call+"(Path(name).write_text('x') for name in ('sdk/sentinel.h',))","inconclusive")

    def test_unknown_json_parse_can_enter_error_handler(self):
        self.check_source("import json\nfrom pathlib import Path\ntry:\n json.loads(Path('safe').read_text())\nexcept ValueError:\n Path('sdk/sentinel.h').write_text('x')","deny")

    def test_process_specific_exception_handler(self):
        self.check_source("import subprocess\nfrom pathlib import Path\ntry:\n subprocess.run(['git','status'],check=True)\nexcept subprocess.CalledProcessError:\n Path('sdk/sentinel.h').write_text('x')","deny")

    def test_shared_container_alias_is_not_cycle(self):
        self.check_source("from pathlib import Path\na=[1]\nb=[a,a]\nb[0].append(2)\nif b[1]!=[1,2]:\n Path('sdk/sentinel.h').write_text('lost alias')")

    def test_active_iterable_mutation_requires_review(self):
        self.check_source("values=['one']\nfor value in values:\n values.append('two')","inconclusive")

    def test_percent_format_allocation_is_bounded(self):
        self.check_source("value='%1000000000s' % 'x'","inconclusive")

    def test_windows_path_comparison(self):
        self.check_source("from pathlib import Path\nif Path('SDK')==Path('sdk'):\n Path('sdk/sentinel.h').write_text('x')","deny")

    def test_git_C_output_uses_effective_directory(self):
        for args in [['git','-C','sdk','diff','--output=sentinel.h'],
                     ['git','-C','sdk','-C','..','-C','sdk','show','--output','sentinel.h']]:
            self.assertIn('read-only',argv_issue(args,str(self.root),self.root))

    def test_shell_directory_transitions(self):
        for command in ["cd sdk; python -c \"open('sentinel.h','w')\"",
                        "Set-Location -LiteralPath sdk; git diff --output=sentinel.h",
                        "Push-Location sdk; echo x > sentinel.h"]:
            self.assertTrue(shell_issue(command,str(self.root),self.root))
        self.assertEqual(shell_issue("Push-Location sdk; Pop-Location; echo x > safe",str(self.root),self.root),"")

    def test_callable_container_aliases(self):
        self.check_source("from subprocess import run\nhandlers=[]\nhandlers.append(run)\nhandlers[0](['git','reset','--hard'])","deny")
        self.check_source("from pathlib import Path\npaths=tuple([Path('one'),Path('two')])\nfor path in paths:\n path.write_text('x')")

    def test_safe_generator_materialization(self):
        self.check_source("from pathlib import Path\nlist(Path(name).write_text('x') for name in ('one','two'))")

    def test_path_mapping_keys_are_not_miscompared(self):
        self.check_source("from pathlib import Path\npaths={Path('SDK'):Path('sdk/sentinel.h')}\npaths.get(Path('sdk'),Path('safe')).write_text('x')","inconclusive")

    def test_handle_name_retains_relative_spelling(self):
        self.check_source("import os\nfrom pathlib import Path\nf=open('sentinel.h','w')\nos.chdir('sdk')\nPath(f.name).write_text('x')","deny")

    def test_closed_handle_exception_branch(self):
        self.check_source("from pathlib import Path\nf=open('safe','w')\nf.close()\ntry:\n f.write('x')\nexcept ValueError:\n Path('sdk/sentinel.h').write_text('x')","deny")
        self.check_source("from pathlib import Path\nf=open('safe','w')\nif not f.closed:\n Path('sdk/sentinel.h').write_text('x')","deny")

    def test_unawaited_async_launch_inside_awaited_function(self):
        source="import asyncio\nasync def work():\n asyncio.create_subprocess_exec('git','reset','--hard')\nasyncio.run(work())"
        self.check_source(source)
        self.check_source(source.replace(" asyncio.create_subprocess_exec"," await asyncio.create_subprocess_exec"),"deny")

    def test_with_closes_handle_on_exit(self):
        self.check_source("from pathlib import Path\nwith open('safe','w') as f:\n pass\nif f.closed:\n Path('sdk/sentinel.h').write_text('x')","deny")
        self.check_source("from pathlib import Path\nentered=False\ntry:\n with open('safe','w') as f:\n  entered=True\nexcept OSError:\n if entered:\n  Path('sdk/sentinel.h').write_text('x')","deny")

    def test_unordered_iteration_is_not_one_chosen_execution(self):
        self.check_source("from pathlib import Path\nfor name in {'safe','sdk/sentinel.h'}:\n Path(name).write_text('x')\n break","inconclusive")
        self.check_source("for name in sorted({'one','two'}): print(name)")
        self.assertTrue(argv_issue(['py','-3.13','-c','print(1)'],str(self.root),self.root))


if __name__=="__main__":
    unittest.main(verbosity=2)
