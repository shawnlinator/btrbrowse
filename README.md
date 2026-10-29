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
* **Compare** any two snapshots (or a snapshot and the live subvolume): created, deleted and changed
  files with a diff view, or open the two trees in [meld](https://meldmerge.org/).
  Comparison walks the snapshot directories, so unlike `btrfs subvolume find-new` it also
  sees deleted files.
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
