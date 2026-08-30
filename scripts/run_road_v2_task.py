"""Run a single full-size V2 task; a model hash change forces a new plan."""
from pathlib import Path
import sys
import argparse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from competition.road_joint_v2.tasks import run_task, assemble, refine_representatives


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',required=True)
    parser.add_argument('--task-id',required=True,help='central-cost, distributed-carbon, hybrid-epsilon-010等；assemble汇总；refine代表解0.1%复核')
    parser.add_argument('--policy-carbon-cap',type=float)
    args=parser.parse_args()
    try:
        if args.task_id=='assemble':
            assemble(args.run_root,policy_carbon_cap=args.policy_carbon_cap)
        elif args.task_id=='refine':
            refine_representatives(args.run_root,policy_carbon_cap=args.policy_carbon_cap)
        else:
            point=run_task(args.run_root,args.task_id)
            print(point)
        return 0
    except (ValueError,OSError) as exc:
        print(f'[停止] {exc}',file=sys.stderr)
        return 2
    except Exception as exc:
        print(f'[未通过] {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
