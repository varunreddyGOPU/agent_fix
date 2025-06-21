import os
from git import Repo
from github import Github
from dotenv import load_dotenv

# --- Configuration Loading ---
load_dotenv()
# --- Configuration ---
notification_config = {
    "token": os.getenv("GITHUB_TOKEN"),
    "owner": os.getenv("GITHUB_REPO_OWNER"),
    "repo": os.getenv("GITHUB_REPO_NAME"),
}

# Set your branch names
base_branch = "master"  # or "master"
feature_branch = "my-new-feature-branch"  # Change as needed

# --- Local Git Operations ---
repo_path = os.getcwd()  # Assumes script is run from repo root
repo = Repo(repo_path)

# 1. Create and checkout new branch
if feature_branch not in repo.heads:
    new_branch = repo.create_head(feature_branch)
else:
    new_branch = repo.heads[feature_branch]
new_branch.checkout()

# 2. Add all files
repo.git.add(A=True)

# 3. Commit changes
try:
    repo.index.commit("Add all files for new feature")
except Exception as e:
    print("Nothing to commit or error:", e)

# 4. Push branch to GitHub
origin = repo.remote(name='origin')
origin.push(feature_branch)

# --- GitHub Pull Request ---
g = Github(notification_config["token"])
github_repo = g.get_repo(f"{notification_config['owner']}/{notification_config['repo']}")

# 5. Create the pull request
pr = github_repo.create_pull(
    title="Add all files for new feature",
    body="This PR adds all files in the folder.",
    head=feature_branch,
    base=base_branch,
)

print(f"Pull request created: {pr.html_url}")
