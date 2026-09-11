"""Local command interface sharing the exact service used by MCP."""
import argparse
import json
import sys
from contextlib import ExitStack
from pathlib import Path

from .config import KnowledgeConfig, initialize
from .service import KnowledgeService
from .storage import Store
from .util import read_json, canonical, digest


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kw")
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init", help="Create a new empty managed knowledge root")
    init.add_argument("--root", required=True, type=Path)
    init.add_argument("--model-dir", required=True, type=Path)
    init.add_argument("--source", action="append", default=[], type=Path)
    project = commands.add_parser("project-init")
    project.add_argument("--root", required=True, type=Path)
    project.add_argument("--template", choices=("generic", "python", "embedded"), default="generic")
    project.add_argument("--knowledge-ref", default="project")
    project.add_argument("--dry-run", action="store_true")
    backup = commands.add_parser("backup")
    backup.add_argument("--config", required=True, type=Path)
    backup.add_argument("--destination", required=True, type=Path)
    recovery = commands.add_parser("restore")
    recovery.add_argument("--snapshot", required=True, type=Path)
    recovery.add_argument("--destination", required=True, type=Path)
    recovery.add_argument("--model-dir", required=True, type=Path)
    validation = commands.add_parser("self-test")
    validation.add_argument("--model-dir", required=True, type=Path)
    runners = commands.add_parser("runner-registry")
    runners.add_argument("--registry", required=True, type=Path)
    binding = commands.add_parser("bind")
    binding.add_argument("--registry", required=True, type=Path)
    binding.add_argument("--project", required=True, type=Path)
    binding.add_argument("--knowledge-ref", default="project")
    binding.add_argument("--kb-id", required=True)
    lookup = commands.add_parser("resolve")
    lookup.add_argument("--registry", required=True, type=Path)
    lookup.add_argument("--project", required=True, type=Path)
    lookup.add_argument("--knowledge-ref", default="project")
    activation = commands.add_parser("activate-install")
    for name in ("bundle", "root", "data", "codex", "codex-home", "prepared"):
        activation.add_argument("--" + name, required=True, type=Path)
    activation.add_argument("--no-startup", action="store_true")
    for name in ("recover-install", "deactivate-install"):
        item = commands.add_parser(name)
        item.add_argument("--root", required=True, type=Path)
    for name in ("status", "search", "read", "capture", "feedback", "maintain", "maintenance-status", "cancel", "mcp", "worker", "job-supervise", "job-build", "build", "runner-start", "runner-stop", "runner-serve"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", required=True, type=Path)
        sub.add_argument("--binding-hash")
        if name == "search":
            sub.add_argument("query")
            sub.add_argument("--mode", choices=("auto", "semantic", "lexical"), default="auto")
            sub.add_argument("--workspace")
            sub.add_argument("--include-history", action="store_true")
        if name == "read":
            sub.add_argument("evidence_id")
            sub.add_argument("--cursor")
        if name in {"capture", "feedback"}:
            sub.add_argument("--request", required=True, type=Path)
        if name == "maintain":
            sub.add_argument("--operation-id", required=True)
            sub.add_argument("--timeout-seconds", type=float, default=600)
        if name in {"maintain", "build"}:
            sub.add_argument("--lexical-only", action="store_true")
        if name in {"maintenance-status", "cancel", "job-supervise", "job-build"}:
            sub.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    leases = ExitStack()
    try:
        if args.action == "self-test":
            import asyncio
            from .validation import exercise
            result = asyncio.run(exercise(args.model_dir))
        elif args.action == "runner-registry":
            from .registry import start_runners
            result = start_runners(args.registry)
        elif args.action == "bind":
            from .registry import bind_project
            result = bind_project(args.registry, args.project, args.knowledge_ref, args.kb_id)
        elif args.action == "resolve":
            from .registry import resolve
            result = resolve(args.registry, args.project, args.knowledge_ref)
        elif args.action == "activate-install":
            from .installation import activate
            result = activate(args.bundle, args.root, args.data, args.codex, args.codex_home,
                              read_json(args.prepared), startup=not args.no_startup)
        elif args.action in {"recover-install", "deactivate-install"}:
            from .installation import recover_pending, deactivate
            result = recover_pending(args.root) if args.action == "recover-install" else deactivate(args.root)
        elif args.action == "backup":
            from .recovery import backup
            result = backup(KnowledgeConfig.load(args.config), args.destination)
        elif args.action == "restore":
            from .recovery import restore
            result = restore(args.snapshot, args.destination, args.model_dir)
        elif args.action == "project-init":
            from .project import initialize as project_init
            result = project_init(args.root, template=args.template, knowledge_ref=args.knowledge_ref, dry_run=args.dry_run)
        elif args.action == "init":
            config = initialize(args.root, args.model_dir, args.source)
            Store(config).initialize_empty()
            result = {"ok": True, "kb_id": config.kb_id, "config": str(config.config_path), "index_state": "empty"}
        else:
            config = KnowledgeConfig.load(args.config)
            from .leases import runtime_lease
            leases.enter_context(runtime_lease(config, args.action))
            if args.binding_hash and digest(canonical(config.json())) != args.binding_hash:
                raise ValueError("knowledge_binding_changed")
            if args.action == "mcp":
                from .mcp_server import serve
                serve(config)
                return 0
            if args.action == "worker":
                from .worker import serve
                serve(config)
                return 0
            if args.action.startswith("runner-"):
                from . import runner
                if args.action == "runner-serve":
                    return runner.serve(config)
                result = runner.start(config) if args.action == "runner-start" else runner.stop(config)
            elif args.action in {"maintain", "maintenance-status", "cancel", "job-supervise", "job-build"}:
                from . import jobs
                if args.action == "maintain":
                    result = jobs.submit(config, args.operation_id, timeout_seconds=args.timeout_seconds, lexical_only=args.lexical_only)
                elif args.action == "maintenance-status":
                    result = jobs.status(config, args.job_id)
                elif args.action == "cancel":
                    result = jobs.cancel(config, args.job_id)
                elif args.action == "job-supervise":
                    return jobs.supervise(config, args.job_id)
                else:
                    jobs.execute_build(config, args.job_id)
                    return 0
            elif args.action == "build":
                from .build import build
                result = {"ok": True, **build(config, lexical_only=args.lexical_only)}
            else:
                service = KnowledgeService(config)
                try:
                    if args.action == "status":
                        result = service.status()
                    elif args.action == "search":
                        result = service.search(args.query, mode=args.mode, workspace=args.workspace, include_history=args.include_history)
                    elif args.action == "read":
                        result = service.read_evidence(args.evidence_id, args.cursor)
                    elif args.action == "capture":
                        result = service.capture_knowledge(**read_json(args.request))
                    else:
                        result = service.record_feedback(**read_json(args.request))
                finally:
                    service.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}}), file=sys.stderr)
        return 1
    finally:
        leases.close()


if __name__ == "__main__":
    raise SystemExit(main())
