"""Local command interface sharing the exact service used by MCP."""
import argparse
import json
import sys
from contextlib import ExitStack
from pathlib import Path

from .config import KnowledgeConfig, initialize
from .storage import Store
from .util import read_json, canonical, digest


def exception_detail(error):
    if isinstance(error, BaseExceptionGroup):
        return "; ".join(exception_detail(item) for item in error.exceptions)[:8000]
    return type(error).__name__ + ": " + str(error)


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
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--root", required=True, type=Path)
    doctor.add_argument("--verify-model", action="store_true")
    for name in ("project-guard", "project-hook-config"):
        guard = commands.add_parser(name)
        guard.add_argument("--root", required=True, type=Path)
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
    register = commands.add_parser("register-library")
    register.add_argument("--root", required=True, type=Path)
    register.add_argument("--config", required=True, type=Path)
    register.add_argument("--knowledge-ref", required=True)
    legacy = commands.add_parser("import-generation")
    for name in ("config", "database", "manifest", "source-map"):
        legacy.add_argument("--" + name, required=True, type=Path)
    legacy.add_argument("--publish", action="store_true")
    legacy_feedback = commands.add_parser("import-feedback")
    legacy_feedback.add_argument("--config", required=True, type=Path)
    legacy_feedback.add_argument("--database", required=True, type=Path)
    activation = commands.add_parser("activate-install")
    for name in ("bundle", "root", "data", "codex", "codex-home", "prepared"):
        activation.add_argument("--" + name, required=True, type=Path)
    activation.add_argument("--no-startup", action="store_true")
    for name in ("recover-install", "deactivate-install", "rollback-install", "prune-install"):
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
        if name == "build":
            sub.add_argument("--force", action="store_true")
        if name in {"maintenance-status", "cancel", "job-supervise", "job-build"}:
            sub.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    leases = ExitStack()
    try:
        if args.action == "doctor":
            from .doctor import inspect
            result = inspect(args.root, verify_model_hashes=args.verify_model)
        elif args.action == "project-guard":
            from .project_guard import hook
            try:
                raw = sys.stdin.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("hook input budget exceeded")
                result = hook(args.root, json.loads(raw))
            except Exception as exc:
                result = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": "Project guard could not classify this request: " + type(exc).__name__}}
        elif args.action == "project-hook-config":
            from .project_guard import configuration
            result = configuration(args.root)
        elif args.action == "import-generation":
            from .legacy import import_generation
            result = import_generation(KnowledgeConfig.load(args.config), args.database, args.manifest,
                                       read_json(args.source_map), publish=args.publish)
        elif args.action == "import-feedback":
            from .legacy import import_feedback
            result = import_feedback(KnowledgeConfig.load(args.config), args.database)
        elif args.action == "self-test":
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
        elif args.action == "register-library":
            from .installation import register_library
            result = register_library(args.root, args.config, args.knowledge_ref)
        elif args.action == "activate-install":
            from .installation import activate
            result = activate(args.bundle, args.root, args.data, args.codex, args.codex_home,
                              read_json(args.prepared), startup=not args.no_startup)
        elif args.action == "prune-install":
            from .cleanup import prune
            result = prune(args.root)
        elif args.action in {"recover-install", "deactivate-install", "rollback-install"}:
            from .installation import recover_pending, deactivate, rollback
            operation = {"recover-install": recover_pending, "deactivate-install": deactivate, "rollback-install": rollback}[args.action]
            result = operation(args.root)
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
            if args.action in {"mcp", "worker", "runner-serve", "job-supervise", "job-build"}:
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
                result = {"ok": True, **build(config, lexical_only=args.lexical_only, force=args.force)}
            else:
                from .service import KnowledgeService
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
        print(json.dumps({"ok": False, "error": {"code": type(exc).__name__, "message": exception_detail(exc)}}), file=sys.stderr)
        return 1
    finally:
        leases.close()


if __name__ == "__main__":
    raise SystemExit(main())
