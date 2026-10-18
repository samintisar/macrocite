# Paper trading on the server (spec 11)

The directory is named after botbox, where it first ran. Since botbox's SSD failed on
2026-10-17 it runs on ubuntuworker (user `signalbench`) with `data/paper_crypto-v1-2.yaml`;
pass that file with `--file` to every `crypto paper` command.

The paper job runs from its own checkout and database. The live bot (`~/macrocite` on
`live-v2`, database `signalbench`) is not touched.

## Set up (once)

```bash
git clone https://github.com/samintisar/macrocite.git ~/paper/macrocite
git -C ~/paper/macrocite checkout paper-crypto-v1
cd ~/paper/macrocite && ~/.local/bin/uv sync --frozen
docker exec signalbench-postgres-1 psql -U signalbench -d signalbench -c 'CREATE DATABASE signalbench_paper'
mkdir -p ~/paper/config && cp scripts/botbox/paper.env.example ~/paper/config/paper.env
chmod 600 ~/paper/config/paper.env   # then fill in the password, token, and chat id
set -a && . ~/paper/config/paper.env && set +a
case "$DATABASE_URL" in */signalbench_paper) ;; *) echo "DATABASE_URL is not the paper database"; exit 1;; esac
~/.local/bin/uv run --frozen alembic upgrade head
~/.local/bin/uv run --frozen signalbench crypto paper start --file data/paper_crypto-v1-2.yaml
mkdir -p ~/.config/systemd/user
cp scripts/botbox/signalbench-paper*.service scripts/botbox/signalbench-paper*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now signalbench-paper.timer signalbench-paper-backup.timer
```

Use letters and digits only in the password in `paper.env`: systemd and the shell parse the
file differently, so a password with other characters can read differently in each.

## Check

```bash
systemctl --user list-timers 'signalbench-paper*'
journalctl --user -u signalbench-paper.service -n 50
cd ~/paper/macrocite && set -a && . ~/paper/config/paper.env && set +a && ~/.local/bin/uv run --frozen signalbench crypto paper status --file data/paper_crypto-v1-2.yaml
```

After the first nightly run, run `signalbench crypto paper run --file data/paper_crypto-v1-2.yaml` once more by hand: it must
print `ok, 0 days added`. This is the first exact compare against rows saved in Postgres.

## Update the code

Merge on `main`, tag `paper-crypto-v<n>` or move to a later commit, then
`git -C ~/paper/macrocite fetch --tags && git -C ~/paper/macrocite checkout <tag> && uv sync --frozen`.
The next run's replay check proves the saved history still comes out the same.
