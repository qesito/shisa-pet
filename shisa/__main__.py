import fcntl
import os
import signal
import sys

from .paths import DATA_DIR

USAGE = """usage: shisa-pet [--toggle]

  (no option)  start Shisa
  --toggle     hide or show the running Shisa (starts it if it isn't running)"""


def run():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(USAGE)
        return
    if args and args[0] != "--toggle":
        sys.exit(USAGE)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(DATA_DIR / "pet.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if args:
            lock.seek(0)
            try:
                os.kill(int(lock.read().strip()), signal.SIGUSR1)
            except (ValueError, ProcessLookupError) as e:
                sys.exit(f"shisa: couldn't reach the running pet ({e})")
            return
        sys.exit("shisa: already running")

    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    from .app import main
    main()


if __name__ == "__main__":
    run()
