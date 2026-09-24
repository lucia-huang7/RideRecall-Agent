"""Generate and replay compatible synthetic ride-support releases."""

import argparse
import json
from pathlib import Path

from ride_agent.evaluation.simulation.audit import audit
from ride_agent.evaluation.simulation.generate import generate
from ride_agent.evaluation.simulation.replay import replay

parser = argparse.ArgumentParser(description=__doc__)
commands = parser.add_subparsers(dest="command", required=True)
g = commands.add_parser("generate")
g.add_argument("--output", type=Path, required=True)
g.add_argument("--worlds", type=int, default=1000)
g.add_argument("--seed", type=int, default=20261021)
r = commands.add_parser("replay")
r.add_argument("--dataset", type=Path, required=True)
r.add_argument("--output", type=Path, required=True)
r.add_argument("--limit", type=int)
r.add_argument("--split", choices=["development", "calibration", "test"])
r.add_argument("--retain-db", action="store_true")
v = commands.add_parser("audit")
v.add_argument("--dataset", type=Path, required=True)
a = parser.parse_args()
if a.command == "generate":
    if not 8 <= a.worlds <= 100000:
        parser.error("worlds must be between 8 and 100000")
    result = generate(a.output, a.worlds, a.seed)
elif a.command == "audit":
    result = audit(a.dataset)
else:
    result = replay(a.dataset, a.output, a.limit, a.split, a.retain_db)
print(json.dumps(result, indent=2))
