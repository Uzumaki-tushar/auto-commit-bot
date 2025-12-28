import os
import time
import subprocess
import json
import logging
from threading import Lock
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ===================== CONFIG =====================
OLLAMA_MODEL = "tinyllama"
DEBOUNCE_SECONDS = 3
STOP_FILE = "stop.txt"
DEFAULT_IGNORED_DIRS = ["node_modules", "target", ".git"]
# =================================================

git_lock = Lock()
ollama_lock = Lock()
last_event_time = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ------------------ UTIL ------------------

def run_cmd(cmd, cwd=None, input_text=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True
    )

def stage_all(repo):
    run_cmd(["git", "add", "."], cwd=repo)

def get_staged_diff(repo):
    r = run_cmd(["git", "diff", "--staged"], cwd=repo)
    return r.stdout.strip()

def generate_commit_message(diff_text):
    if not diff_text:
        return "chore: auto commit"

    prompt = f"""
Write ONE short git commit message.
Rules:
- Conventional Commits
- No markdown
- No explanation
- Max 72 chars

Diff:
{diff_text}
"""

    with ollama_lock:
        r = run_cmd(
            ["ollama", "run", OLLAMA_MODEL],
            input_text=prompt
        )

    if r.returncode != 0 or not r.stdout.strip():
        return "chore: auto commit"

    return r.stdout.strip().splitlines()[0]

def git_commit_and_push(repo, name):
    with git_lock:
        stage_all(repo)
        diff = get_staged_diff(repo)

        if not diff:
            return

        msg = generate_commit_message(diff)
        logging.info(f"[{name}] Commit message: {msg}")

        commit = run_cmd(["git", "commit", "-m", msg], cwd=repo)
        if commit.returncode != 0:
            logging.error(commit.stderr)
            return

        push = run_cmd(["git", "push"], cwd=repo)
        if push.returncode == 0:
            logging.info(f"[{name}] Pushed to GitHub")
        else:
            logging.error(push.stderr)

# ------------------ WATCHDOG ------------------

class RepoHandler(FileSystemEventHandler):
    def __init__(self, repo_path, name, ignored):
        self.repo = repo_path
        self.name = name
        self.ignored = ignored

    def on_any_event(self, event):
        if event.is_directory:
            return

        for d in self.ignored:
            if d in event.src_path:
                return

        now = time.time()
        last = last_event_time.get(self.repo, 0)
        if now - last < DEBOUNCE_SECONDS:
            return

        last_event_time[self.repo] = now
        git_commit_and_push(self.repo, self.name)

# ------------------ MAIN ------------------

def main():
    with open("config.json") as f:
        config = json.load(f)

    extra = input(
        "Default ignored dirs ['node_modules','target','.git'] "
        "Add more or press enter: "
    ).strip()

    ignored = DEFAULT_IGNORED_DIRS[:]
    if extra:
        ignored.extend([x.strip() for x in extra.split(",")])

    observer = Observer()

    for project in config["projects"]:
        path = project["path"]
        name = project["name"]

        if not os.path.exists(os.path.join(path, ".git")):
            logging.error(f"{name} is not a git repo")
            continue

        handler = RepoHandler(path, name, ignored)
        observer.schedule(handler, path, recursive=True)
        logging.info(f"Watching: {name} → {path}")

    observer.start()
    logging.info("Auto Commit Bot started")

    try:
        while True:
            if os.path.exists(STOP_FILE):
                logging.warning("Stopped via stop.txt")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logging.warning("Stopped manually")
    finally:
        observer.stop()
        observer.join()
        logging.info("Bot stopped")

if __name__ == "__main__":
    main()
