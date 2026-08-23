import json
import sys
import os
import pandas as pd
import importlib.util


def main():
    workdir = sys.argv[1]
    bars_path = os.path.join(workdir, "bars.csv")
    strategy_path = os.path.join(workdir, "strategy.py")
    signals_path = os.path.join(workdir, "signals.json")

    bars = pd.read_csv(bars_path, index_col=0, parse_dates=True)

    spec = importlib.util.spec_from_file_location("strategy", strategy_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["strategy"] = mod
    spec.loader.exec_module(mod)

    signals = mod.strategy(bars)

    with open(signals_path, "w") as f:
        json.dump(signals, f)

    print("OK")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
