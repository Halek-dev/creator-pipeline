"""Monthly cost summary from the app's job history (Phase 4.3).

  python tools/cost_report.py                 # table, newest month first
  python tools/cost_report.py --csv > costs.csv
  GPU_HOURLY_USD=1.79 python tools/cost_report.py

Render $ is the GPU time the clips actually took. Pod $ (est.) adds the gaps between a day's jobs and the
watchdog's idle allowance, since the pod bills until it stops itself. Seedance jobs are Comfy credits.
Compare with RunPod -> Billing; the estimate is a floor, not an invoice.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import jobs as J  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", action="store_true", help="CSV instead of a table")
    args = ap.parse_args()
    summary = J.cost_summary()
    if not summary:
        print(f"No finished jobs under {J.JOBS_DIR}")
        return
    rows = J.cost_rows(summary)
    if args.csv:
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(J.COST_HEADERS)
        w.writerows(rows)
        return
    widths = [max(len(str(x)) for x in col) for col in zip(J.COST_HEADERS, *rows)]
    for row in [J.COST_HEADERS] + rows:
        print("  ".join(str(x).rjust(w) for x, w in zip(row, widths)))
    total_pod = sum(m["pod_usd"] for m in summary)
    total_sd = sum(m["seedance_usd"] for m in summary)
    print(f"\nAll time: {sum(m['clips'] for m in summary)} clips, about ${total_pod:.2f} of pod time and ${total_sd:.2f} of Seedance credits.")


if __name__ == "__main__":
    main()
