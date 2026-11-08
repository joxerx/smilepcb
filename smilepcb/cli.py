"""Small command-line entry point for the shared lab service."""
import argparse
import json
from .usb_policy import parse_usb_max_length_mm
from pathlib import Path


def plan_rgb(*args, **kwargs):
    from .service import plan_rgb as function
    return function(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(prog="smilepcb")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("demo", "inspect", "plan-rgb", "plan-encoder", "preview", "apply", "verify", "check", "rollback-encoder"):
        sub = commands.add_parser(command)
        sub.add_argument("project", type=Path)
        if command in ("plan-rgb", "plan-encoder", "check"):
            sub.add_argument("--output", required=True, type=Path)
        if command in ("apply", "preview"):
            sub.add_argument("--plan", required=True, type=Path)
    build = commands.add_parser('build', help='Build a new STM32 keyboard from KLE')
    for name in ('kle','config','tools','output'):
        build.add_argument('--'+name, required=True, type=Path)
    build.add_argument('--controller-source',required=True,type=Path)
    build.add_argument('--component-corridors',action='store_true',help='Experimental controller-local corridors; requires --unified-controller-plan')
    build.add_argument('--unified-controller-plan',action='store_true',help='Experimental joint MCU source batch and required local closure')
    build.add_argument('--coherent-via-banks',action='store_true',help='Experimental source preference; does not assert final coherence')
    build.add_argument('--process-profile',default='fr4-2l-1oz-0p5-0p2',choices=('legacy-0p6-0p3','fr4-2l-1oz-0p5-0p2'))
    build.add_argument('--routing-quality',default='turns',choices=('legacy','turns'))
    build.add_argument('--completion-budget-s',type=int,default=3600)
    build.add_argument('--placement-attempts',type=int,default=8)
    build.add_argument('--placement-budget-s',type=int,default=220,help='Staged placement seconds per attempt (220..1200)')
    build.add_argument('--usb-max-length-mm',type=parse_usb_max_length_mm,default=...,dest='usb_max_length_nm',help='Absolute USB geometry cap in mm (default 20); none removes only this cap, not DRC/skew checks')
    args = parser.parse_args()
    if args.command == 'build':
        try:
            from .stm32.generate import generate_keyboard
            result = generate_keyboard(args.kle,args.config,args.tools,args.output,
                controller_source=args.controller_source,process_profile=args.process_profile,
                usb_max_length_nm=args.usb_max_length_nm,component_corridors=args.component_corridors,
                routing_quality=args.routing_quality,completion_budget_s=args.completion_budget_s,
                placement_attempts=args.placement_attempts,placement_budget_s=args.placement_budget_s,coherent_via_banks=args.coherent_via_banks,unified_controller_plan=args.unified_controller_plan)
        except (ValueError,OSError,RuntimeError) as error:
            parser.exit(1,str(error)+'\n')
        print(json.dumps(result,indent=2))
        return
    from .service import apply_plan, create_fixture, inspect_project, verify_project
    from .encoder import plan_encoder, preview_plan, rollback_encoder
    try:
        if args.command == "demo":
            result = create_fixture(args.project)
            result = {"project": str(args.project.resolve()), "footprints": len(result["footprints"]), "tracks_vias": len(result["tracks"])}
        elif args.command == "inspect":
            result = inspect_project(args.project)
            result = {"rgb_installed": result["rgb_installed"], "encoder_installed": result.get('encoder_installed', False),
                      "footprints": len(result["board"]["footprints"]), "tracks_vias": len(result["board"]["tracks"]), "source_hashes": result["hashes"]}
        elif args.command in ("plan-rgb", "plan-encoder"):
            result = plan_rgb(args.project) if args.command == 'plan-rgb' else plan_encoder(args.project)
            with args.output.open("x", encoding="utf-8") as output:
                output.write(json.dumps(result, indent=2) + "\n")
            result = {"plan": str(args.output.resolve()), "status": result["status"], "block": result["block"]}
        elif args.command == "apply":
            result = apply_plan(args.project, json.loads(args.plan.read_text()))
        elif args.command == 'preview':
            result = preview_plan(args.project, json.loads(args.plan.read_text()))
        elif args.command == 'rollback-encoder':
            result = rollback_encoder(args.project)
        elif args.command == "check":
            from .checks import check_rules
            reports = check_rules(args.project, args.output)
            result = {"reports": str(args.output.resolve()), "erc": reports["erc"]["counts"],
                      "drc": reports["drc"]["counts"], "schematic_parity_issues": len(reports["drc"]["schematic_parity"]),
                      "unconnected_items": len(reports["drc"]["unconnected_items"]),
                      "rule_checks_passed": reports["rule_checks_passed"], "fabrication_ready": reports["fabrication_ready"]}
        else:
            result = verify_project(args.project)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, indent=2))
    if args.command == "check" and not result["rule_checks_passed"]:
        raise SystemExit(5)


if __name__ == "__main__":
    main()
