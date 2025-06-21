import os
from github import Github
from github.GithubException import GithubException
from dotenv import load_dotenv

# --- Configuration Loading ---
load_dotenv()

notification_config = {
    "token": os.getenv("GITHUB_TOKEN"),
    "owner": os.getenv("GITHUB_REPO_OWNER"),
    "repo": os.getenv("GITHUB_REPO_NAME"),
}

print("GITHUB_TOKEN:", notification_config["token"])
print("GITHUB_REPO_OWNER:", notification_config["owner"])
print("GITHUB_REPO_NAME:", notification_config["repo"])
repo_path = f"{notification_config['owner']}/{notification_config['repo']}"
print(f"Trying to access repo: {repo_path}")

try:
    g = Github(notification_config["token"])
    repo = g.get_repo(repo_path)
    print(f"Success! You have access to '{repo.full_name}'.")
except GithubException as e:
    print(f"Failed to access repo: {repo_path}")
    print(f"GitHub API error: {e.data.get('message', str(e))}")
    print(f"Status code: {e.status}")
except Exception as e:
    print(f"An unexpected error occurred: {e}")
