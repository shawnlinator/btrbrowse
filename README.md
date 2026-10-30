btrbrowse
=========

btrbrowse is a graphical browser for [btrbk](https://digint.ch/btrbk/) snapshots. It is a fork of
[snapper-gui](https://github.com/ricardomv/snapper-gui) that no longer needs snapper: instead of
talking to `snapperd` over D-Bus it reads the snapshots btrbk creates (plain read-only btrfs
subvolumes named `<name>.<timestamp>[_N]` in a `snapshot_dir`).

What it does
------------

* **Browse** the snapshots of every local btrbk source subvolume, newest first, with the live
  subvolume ("Now") on top and the btrbk backup status of each snapshot.
* **Compare** any two snapshots (or a snapshot and the live subvolume) in a changes window, with a
  per-file Details pane and a diff view, or open the two trees in
  [meld](https://meldmerge.org/).
  The comparison uses `btrfs send --no-data -p <older> <newer> | btrfs receive --dump`: it reads
  btrfs metadata only, so it is fast on large subvolumes, and it reports everything the filesystem
  recorded: created and deleted files, **renames and moves**, rewritten data ranges and truncation,
  chmod/chown, xattrs and ACLs, symlinks, hard links, and timestamps. The stream's replay
  noise (temporary `oNNN-NN-N` names, link+unlink pairs, unlink+create replacements) is folded back
  into what happened to each path. Comparing against the live subvolume takes a short-lived
  read-only snapshot next to your snapshots and deletes it again. If the stream can't be used (for
  example the older side is the live state), it falls back to a directory walk and the status bar
  says so.
* **Restore** a single file or directory from a snapshot (`cp --reflink`) into the live subvolume.
* **Roll back** a subvolume to a snapshot. The current subvolume is renamed to
  `<subvolume>.rollback.<timestamp>`, never deleted. A confirmation dialog shows the exact steps.
* **Create** (`btrbk snapshot`) and **delete** snapshots.
* **Backups** page: backup targets, the backups btrbk reports, and the transfer history parsed from
  btrbk's `transaction_log`. Targets are found in the log as well as in the config, so
  pull-based setups (a backup host that pulls from `ssh://` sources into local targets) show up
  even though they have no local snapshots. Local backups open in the file manager on double click.

Privileges
----------

btrfs and `btrbk list` need root, so the GUI itself runs as your user and calls a small helper,
`btrbrowse-helper`, through `pkexec`. A refresh needs one elevation, and the polkit policy keeps the
authorisation for a few minutes (`auth_admin_keep`).

The helper is a single standard-library script installed in a root-owned location, so it never
loads code you can modify. It also:

* only accepts a btrbk config that is owned by root with root-owned, non-group/world-writable parent
  directories, so an unprivileged user cannot point it at their own config;
* only acts on snapshots that `btrbk list snapshots` reports for that config;
* limits `stream-diff` to two snapshots of the same source (or one plus its live subvolume);
* rejects `..`, absolute and symlink-escaping paths in restore requests;
* supports `--dry-run` for delete, restore and rollback; the GUI always shows the dry-run first.

Not supported: snapshots inside a mounted subvolume cannot be rolled back (a mounted subvolume
cannot be swapped). Use file restore, or mount the top-level volume (`subvolid=5`) and roll back
from there. Reading snapshot contents happens as your user, so files you cannot read are not shown.

Installation
------------

Dependencies: python3, python3-gi, GTK 3, GtkSourceView 4 (`gir1.2-gtksource-4`), btrbk, btrfs-progs,
polkit (`pkexec`), optionally meld.

    $ git clone https://github.com/shawnlinator/btrbrowse
    $ cd btrbrowse
    $ sudo make install-helper     # /usr/libexec/btrbrowse-helper + polkit policy
    $ pip install .                # or: python3 setup.py install

Run `btrbrowse`, or `btrbrowse --config /path/to/btrbk.conf` (default `/etc/btrbk/btrbk.conf`, or
`$BTRBROWSE_CONFIG`). The config must be root-owned, see above.

From a checkout without installing: `BTRBROWSE_HELPER=$PWD/helper/btrbrowse-helper python3 -c "import btrbrowse.application as a; a.start_ui()"`.
`BTRBROWSE_ELEVATE="sudo -n"` replaces `pkexec` (useful for development).

Testing
-------

    $ python3 -m unittest discover -s tests

parses timestamps, btrbk raw output and transaction logs, compares trees and checks the helper's
validation. The end-to-end tests are skipped unless a fixture is configured. Build one with loop
devices:

    truncate -s 600M src.img && mkfs.btrfs src.img && sudo mount -o loop src.img /mnt/bb-src
    sudo btrfs subvolume create /mnt/bb-src/data && sudo mkdir /mnt/bb-src/snaps
    # root-owned config, e.g. /etc/btrbk/bb-test.conf:
    #   timestamp_format long
    #   volume /mnt/bb-src
    #     snapshot_dir snaps
    #     subvolume data
    BTRBROWSE_ELEVATE="sudo -n" BTRBROWSE_IT_CONFIG=/etc/btrbk/bb-test.conf \
      BTRBROWSE_IT_SOURCE=/mnt/bb-src/data python3 -m unittest discover -s tests -v

Remote hosts (VirtioFS and similar)
-----------------------------------

If the disk reaches this machine through VirtioFS or another share, btrfs and btrbk cannot run
here. Install `btrbrowse-helper` on the host that owns the filesystem (a single root-owned file in
`/usr/libexec`), then choose **Connect to remote host...** in the app menu. It asks for the
`user@host`, the ssh key (an ssh-agent key, a key file, or ssh's default), the mapping between this
machine's path and the host's path (`local=remote`) and the btrbk config on the host.

Nothing about the remote is set by flag, environment variable or in the code: you are asked on every
connection. The last session that actually connected is remembered in
`~/.config/btrbrowse/last-session.json` (mode 0600) to pre-fill the next prompt: host, which key you
picked (an agent public key or a key file's path, never private key material), path map and btrbk
config. The dialog's **Forget saved session** button deletes it. One ssh connection is kept open for reuse and closed after 5 idle minutes, after which
the next action asks again. Passphrase and host-key questions appear as dialogs.

Comparison always prefers the btrfs send stream. Without a remote it falls back to a directory
walk and says so in the status bar (the walk is slow, especially on network shares). With a remote
there is no fallback: the error is shown instead.

Benchmark: `python3 -m btrbrowse.bench <snapshot_dir> [--pair A B] [--repeats N] [--walk-budget S]
[--host-walk]` compares the stream with the walk, prompting for the remote on the terminal.

Logging
-------

The app logs to `~/.local/state/btrbrowse/btrbrowse.log` (rotated, 1 MB x 4): start-up, connections
(never keys), every helper call with its duration, uncaught errors with tracebacks (also from worker
threads), GTK/GLib warnings and the open file descriptor count. `btrbrowse.log.fault` receives a
traceback if the process crashes natively. Set `BTRBROWSE_LOG=debug` for more detail.

Credits and license
-------------------

btrbrowse is based on snapper-gui by Ricardo Vieira, Marek Rusinowski and contributors, and is
distributed under the same terms:

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.
This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.
