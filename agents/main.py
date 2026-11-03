import json

from agents.graph.workflow import run_supply_chain_workflow


if __name__ == "__main__":
    print(json.dumps(run_supply_chain_workflow(), indent=2))
