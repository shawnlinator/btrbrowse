"""Compare the two ways btrbrowse can find what changed between two snapshots.

  stream  `btrfs send --no-data -p A B | btrfs receive --dump` on the host that owns the
          btrfs (via the remote helper), then parsed here
  walk    a directory walk over the snapshot trees as this machine sees them
          (over virtiofs when this is a VM)
  host-walk  the same walk run on the host itself, to separate the cost of the algorithm
          from the cost of virtiofs (optional)

Usage: python3 -m btrbrowse.bench /path/to/snapshot_dir

It asks (on the terminal) for the remote host, the ssh key and the path map; none of
that is stored or taken from options or the environment.
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import time

from btrbrowse import compare, lastsession
from btrbrowse.btrbk import Backend, fold_stream, parse_timestamp
from btrbrowse.compare import StatusFlags, WalkAborted, compare_trees
from btrbrowse.prompts import tty_prompt
from btrbrowse.remote import Remote

COMPARABLE = (StatusFlags.CREATED | StatusFlags.DELETED | StatusFlags.TYPE | StatusFlags.CONTENT |
              StatusFlags.PERMISSIONS | StatusFlags.OWNER | StatusFlags.GROUP |
              StatusFlags.XATTRS | StatusFlags.ACL)


def snapshots_in(directory):
    names = [n for n in os.listdir(directory) if parse_timestamp(n)]
    return sorted(names, key=parse_timestamp)


def pick_pairs(names, explicit):
    if explicit:
        return [tuple(names[int(x)] if x.lstrip("-").isdigit() else x for x in pair) for pair in explicit]
    pairs = [(names[-2], names[-1])]
    if len(names) > 6:
        pairs.append((names[-6], names[-1]))
    if len(names) > 3:
        pairs.append((names[0], names[-1]))
    return pairs


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


def measure_stream(backend, begin, end):
    """Returns (seconds total, seconds remote+transfer, seconds parsing, changes)."""
    total_start = time.perf_counter()
    remote_secs, text = timed(lambda: backend._call("stream-diff", begin, end))
    parse_secs, folded = timed(lambda: fold_stream(text, begin, end))
    return time.perf_counter() - total_start, remote_secs, parse_secs, folded[0], len(text)


def measure_walk(begin, end, skip, budget):
    deadline = time.monotonic() + budget
    start = time.perf_counter()
    try:
        changes = compare_trees(begin, end, skip=skip, abort=lambda: time.monotonic() > deadline)
        return time.perf_counter() - start, changes, None
    except WalkAborted as e:
        return time.perf_counter() - start, None, e.visited


def measure_host_walk(remote, begin, end, skip, budget):
    """Run compare.py on the host (read-only) and time it there."""
    source = open(compare.__file__).read()
    driver = ("\nimport json, sys, time\n"
              "t = time.perf_counter()\n"
              "r = compare_trees(%r, %r, skip=%r, abort=lambda: time.perf_counter() - t > %r)\n"
              "print(json.dumps({'seconds': time.perf_counter() - t, 'changes': len(r)}))\n"
              % (remote.to_host(begin), remote.to_host(end), list(skip), budget))
    start = time.perf_counter()
    proc = subprocess.run(remote.shell_argv("python3 -I -"), input=source + driver,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    wall = time.perf_counter() - start
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-1:] or ["failed"]
        return wall, None, "aborted/failed: %s" % tail[0]
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    return wall, data["changes"], None


def ssh_baseline(remote):
    start = time.perf_counter()
    subprocess.run(remote.shell_argv("true"), check=True)
    return time.perf_counter() - start


def agreement(stream_changes, walk_changes):
    s = dict(stream_changes)
    w = dict(walk_changes)
    both = set(s) & set(w)
    mismatched = [p for p in sorted(both) if (s[p] & COMPARABLE) != (w[p] & COMPARABLE)]
    return {"stream": len(s), "walk": len(w), "both": len(both),
            "only_stream": sorted(set(s) - set(w)), "only_walk": sorted(set(w) - set(s)),
            "status_mismatch": mismatched}


def fmt(seconds):
    return "%.2fs" % seconds


def summarize(values):
    return "first %s, median of rest %s" % (fmt(values[0]), fmt(statistics.median(values[1:]))) \
        if len(values) > 1 else "first %s" % fmt(values[0])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", help="snapshot directory as seen from this machine")
    parser.add_argument("--pair", nargs=2, action="append", metavar=("OLDER", "NEWER"),
                        help="snapshot names or indexes (repeatable); default: a few automatic pairs")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--walk-budget", type=float, default=120,
                        help="give up on a directory walk after this many seconds (default 120)")
    parser.add_argument("--host-walk", action="store_true", help="also time the walk on the remote host")
    parser.add_argument("--skip", action="append", default=["/.btrbk-snapshots"],
                        help="path inside a snapshot the walk must not enter (nested snapshot dir)")
    parser.add_argument("--no-walk", action="store_true", help="only time the stream method")
    args = parser.parse_args(argv)

    remote = Remote(prompt=tty_prompt, remember=lastsession.save)
    remote.ensure()  # asks for host, key and path map
    try:
        run_pairs(args, remote)
    finally:
        remote.close()


def run_pairs(args, remote):
    backend = Backend(remote=remote)
    names = snapshots_in(args.directory)
    if len(names) < 2:
        sys.exit("need at least two snapshots in %s" % args.directory)
    pairs = pick_pairs(names, args.pair)

    print("ssh round trip (connection reuse): %s" % fmt(min(ssh_baseline(remote) for _ in range(3))))
    for older, newer in pairs:
        begin, end = os.path.join(args.directory, older), os.path.join(args.directory, newer)
        print("\n== %s -> %s" % (older, newer))
        stream_runs, last = [], None
        for _ in range(args.repeats):
            total, remote_secs, parse_secs, changes, size = measure_stream(backend, begin, end)
            stream_runs.append(total)
            last = (remote_secs, parse_secs, changes, size)
        print("stream: %s  (remote+transfer %s, parse %s, %d entries, %d bytes of stream)"
              % (summarize(stream_runs), fmt(last[0]), fmt(last[1]), len(last[2]), last[3]))
        if args.no_walk:
            continue
        walk_runs, walked, aborted = [], None, None
        for _ in range(args.repeats):
            secs, result, visited = measure_walk(begin, end, args.skip, args.walk_budget)
            if result is None:
                aborted = visited
                walk_runs.append(secs)
                break
            walk_runs.append(secs)
            walked = result
        if aborted is not None:
            print("walk:   did not finish within %.0fs (%d entries visited)" % (args.walk_budget, aborted))
        else:
            print("walk:   %s  (%d entries)" % (summarize(walk_runs), len(walked)))
            diff = agreement(last[2], walked)
            print("        agree on %d paths; only stream %d, only walk %d, status differs %d"
                  % (diff["both"], len(diff["only_stream"]), len(diff["only_walk"]),
                     len(diff["status_mismatch"])))
            for label, key in (("only stream", "only_stream"), ("only walk", "only_walk"),
                               ("status differs", "status_mismatch")):
                for path in diff[key][:3]:
                    print("          %s: %s" % (label, path))
        if args.host_walk:
            wall, count, error = measure_host_walk(remote, begin, end, args.skip, args.walk_budget)
            print("host-walk: %s" % (error if error else "%s on the host (%d entries)" % (fmt(wall), count)))


if __name__ == "__main__":
    main()
