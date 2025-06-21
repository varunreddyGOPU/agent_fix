# aiops_agent.py
#
# An autonomous AIOps agent for end-to-end error remediation in Python.
#
# Author: Award-Winning Writer
# Version: 2.0.0
#
# Description:
# This script implements a multi-stage AIOps agent composed of three sub-agents:
# 1. Error Finder Agent: Analyzes application logs to detect critical errors,
#    identifies recurring patterns, and emails the detected error details.
# 2. Error Fixer Agent: Uses a Large Language Model (LLM) via Google's Generative AI API
#    to perform root cause analysis (RCA) and generate a code fix, then emails the proposed fix.
# 3. Git Pusher Agent: Programmatically applies the generated code fix as a patch,
#    automates the Git workflow (create new branch, commit, push to remote),
#    and creates a pull request on GitHub, sending a final email notification.
#
# Setup Instructions:
# 1. Install required Python libraries:
#    pip install google-generativeai GitPython requests python-dotenv
#
# 2. Create a `.env` file in the same directory as this script and add your credentials:
#    GOOGLE_API_KEY="YOUR_GOOGLE_API_KEY"
#    GITHUB_TOKEN="YOUR_GITHUB_PERSONAL_ACCESS_TOKEN"
#    GITHUB_REPO_OWNER="YOUR_GITHUB_USERNAME_OR_ORG"
#    GITHUB_REPO_NAME="YOUR_REPOSITORY_NAME"
#    PR_ASSIGNEE="GITHUB_USERNAME_TO_ASSIGN_PR"
#    EMAIL_HOST="your_smtp_server.com"
#    EMAIL_PORT=587
#    EMAIL_USER="your_email@example.com"
#    EMAIL_PASS="your_email_password"
#    EMAIL_RECIPIENT="recipient_email@example.com"
#
# 3. Create a local Git repository for the agent to work with.
#    - Initialize a new repo: `git init my-test-app`
#    - `cd my-test-app`
#    - Create a `buggy_app.py` file with the content from the `create_mock_files()` function below.
#    - Create an `app.log` file with the content from `create_mock_files()`.
#    - Commit the initial files: `git add . && git commit -m "Initial commit"`
#    - Create a corresponding repository on GitHub and push the initial commit.
#      `git remote add origin https://github.com/YOUR_GITHUB_USERNAME_OR_ORG/YOUR_REPOSITORY_NAME.git`
#      `git push -u origin master` (or `main`)
#
# 4. Update the `REPO_PATH` variable in the `main` block to point to your local repository.
#
# 5. Run the agent:
#    python aiops_agent.py

import os
import re
import json
import difflib
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Dict, List, Optional, Tuple

# Third-party libraries
import google.generativeai as genai
from git import Repo, Actor
import requests
from dotenv import load_dotenv

# --- Configuration Loading ---
load_dotenv()

class LogAnalyzer:
    """
    Analyzes log files to find critical errors and preceding event sequences.
    """
    def __init__(self, log_file_path: str):
        self.log_file_path = log_file_path
        self.log_keywords = ["ERROR", "FAILURE", "EXCEPTION", "TRACEBACK", "CRITICAL"]
        self.log_pattern = re.compile(
            r'(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (?P<level>\w+) - (?P<message>.*)'
        )

    def find_critical_errors(self) -> List:
        """Finds log entries containing critical keywords."""
        critical_errors = []
        try:
            with open(self.log_file_path, 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            print(f"Error: Log file not found at {self.log_file_path}")
            return []

        for i, line in enumerate(lines):
            if any(keyword in line for keyword in self.log_keywords):
                match = self.log_pattern.match(line)
                if match:
                    error_data = match.groupdict()
                    # Include full traceback if it follows
                    traceback_lines = []
                    # Check next lines for traceback continuation
                    for next_line in lines[i + 1:]:
                        # A new log entry starts with the timestamp pattern
                        if self.log_pattern.match(next_line):
                            break
                        traceback_lines.append(next_line.strip())
                    
                    if traceback_lines:
                        error_data['message'] += "\n" + "\n".join(traceback_lines)
                    
                    critical_errors.append(error_data)
        return critical_errors

    def find_preceding_events(self, error_timestamp_str: str, window_minutes: int = 5) -> List:
        """Finds events that occurred within a time window before a critical error."""
        preceding_events = []
        error_timestamp = datetime.strptime(error_timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
        window_start_time = error_timestamp - timedelta(minutes=window_minutes)

        try:
            with open(self.log_file_path, 'r') as f:
                for line in f:
                    match = self.log_pattern.match(line)
                    if match:
                        event_data = match.groupdict()
                        try:
                            event_timestamp = datetime.strptime(event_data['timestamp'], '%Y-%m-%d %H:%M:%S,%f')
                            if window_start_time <= event_timestamp < error_timestamp:
                                preceding_events.append(event_data)
                        except ValueError:
                            # Skip lines with unparsable timestamps
                            continue
        except FileNotFoundError:
            print(f"Error: Log file not found at {self.log_file_path}")
            return []
        return preceding_events

    def analyze(self) -> Optional[Dict]:
        """
        Performs a full analysis of the log file.
        Returns a dictionary with analysis results or None if no critical errors are found.
        """
        print("--- Starting Log Analysis ---")
        critical_errors = self.find_critical_errors()
        if not critical_errors:
            print("No critical errors found.")
            return None

        # Focus on the most recent critical error
        latest_error = sorted(critical_errors, key=lambda x: x['timestamp'], reverse=True)[0]
        print(f"Found critical error at {latest_error['timestamp']}:\n{latest_error['message']}")

        preceding_events = self.find_preceding_events(latest_error['timestamp'])
        print(f"Found {len(preceding_events)} preceding events in the last {5} minutes.")

        # Extract culprit file from traceback for later use
        culprit_file = "buggy_app.py" # Default if not found in traceback
        traceback_match = re.search(r'File "([^"]+)", line \d+', latest_error['message'])
        if traceback_match:
            culprit_file = traceback_match.group(1)

        return {
            "latest_error": latest_error,
            "preceding_events": preceding_events,
            "culprit_file": culprit_file
        }

class CodeDiagnoser:
    """
    Uses a Generative AI model to diagnose issues and suggest code fixes.
    """
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is not set.")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    def _create_diagnostic_prompt(self, analysis_data: Dict, source_code: str) -> str:
        """Constructs a detailed prompt for root cause analysis."""
        error = analysis_data['latest_error']
        # Format preceding events for readability in the prompt
        events = "\n".join([f"{e['timestamp']} - {e['level']} - {e['message']}" for e in analysis_data['preceding_events']])
        if not events:
            events = "No preceding events found within the window."
        
        return f"""
        You are an expert Python Site Reliability Engineer with 20 years of experience.
        Your task is to perform a root cause analysis based on the provided data.

        ### CONTEXT START ###

        #### PRECEDING LOG EVENTS (in chronological order) ####
        {events}

        #### CRITICAL ERROR LOG & STACK TRACE ####
        Timestamp: {error['timestamp']}
        Level: {error['level']}
        Message:
        {error['message']}

        #### RELEVANT SOURCE CODE from file '{analysis_data['culprit_file']}' ####
        ```python
        {source_code}
        ```

        ### CONTEXT END ###

        ### INSTRUCTIONS ###
        1. Analyze all the provided context.
        2. Think step-by-step to determine the single, most likely root cause of the error.
        3. Provide your final analysis ONLY in the following JSON format. Do not include any other text.

        {{
          "root_cause_analysis": "A detailed explanation of the root cause.",
          "culprit_file": "{analysis_data['culprit_file']}",
          "culprit_function_name": "The name of the function where the error occurred (if applicable, otherwise leave empty).",
          "confidence_score": 0.9
        }}
        """

    def _create_remediation_prompt(self, diagnosis: Dict, buggy_code: str) -> str:
        """Constructs a prompt to generate a code fix."""
        return f"""
        You are an expert Python developer specializing in writing safe, idiomatic, and bug-free code.
        Your task is to fix the provided buggy Python code based on the given root cause analysis.

        ### BUGGY CODE ###
        ```python
        {buggy_code}
        ```

        ### ROOT CAUSE ANALYSIS ###
        {diagnosis.get("root_cause_analysis", "No analysis provided.")}

        ### INSTRUCTIONS & CONSTRAINTS ###
        1. Rewrite the entire code block to fix the bug described in the root cause analysis.
        2. The fix MUST be idiomatic Python and not introduce any new dependencies.
        3. Ensure the corrected code is syntactically valid and runnable.
        4. Your response MUST ONLY contain the raw Python code for the corrected file. Do not include markdown, explanations, or any other text.

        ### CORRECTED CODE ###
        """

    def get_diagnosis_and_fix(self, analysis_data: Dict, repo_path: str) -> Optional[Dict]:
        """
        Orchestrates the diagnosis and code generation process.
        """
        print("\n--- Starting AI Diagnosis and Code Generation ---")
        culprit_file_path = os.path.join(repo_path, analysis_data['culprit_file'])
        try:
            with open(culprit_file_path, 'r') as f:
                source_code = f.read()
        except FileNotFoundError:
            print(f"Error: Source code file not found at {culprit_file_path}")
            return None

        # 1. Get Diagnosis
        diagnostic_prompt = self._create_diagnostic_prompt(analysis_data, source_code)
        try:
            response = self.model.generate_content(diagnostic_prompt)
            # Clean up potential markdown formatting and parse JSON
            cleaned_response_text = response.text.strip().replace("```json", "").replace("```", "").strip()
            diagnosis = json.loads(cleaned_response_text)
            print(f"AI Diagnosis Received: {diagnosis.get('root_cause_analysis', 'No analysis message.')}")
        except (Exception, json.JSONDecodeError) as e:
            print(f"Error during AI diagnosis: {e}")
            if response and hasattr(response, 'text'):
                print(f"LLM Response was: {response.text}")
            return None

        # 2. Get Code Fix
        # For simplicity, we'll assume the whole file content is the buggy code scope
        # A more advanced agent would parse AST to extract and fix specific functions.
        buggy_code = source_code
        remediation_prompt = self._create_remediation_prompt(diagnosis, buggy_code)
        try:
            response = self.model.generate_content(remediation_prompt)
            # Remove markdown code block fences if present
            fixed_code = response.text.strip().replace("```python", "").replace("```", "").strip()
            print("AI-Generated Code Fix:\n" + "="*30 + f"\n{fixed_code}\n" + "="*30)
        except Exception as e:
            print(f"Error during code generation: {e}")
            return None

        # Add original and fixed code to diagnosis results for email/PR body
        diagnosis['original_code'] = source_code
        diagnosis['fixed_code'] = fixed_code
        return diagnosis

class CodePatcher:
    """
    Applies generated code fixes to the local file system.
    """
    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def apply_patch(self, culprit_file: str, new_content: str) -> Optional[str]:
        """
        Overwrites the culprit file with the new, fixed content.
        Returns the path to the patched file.
        """
        print("\n--- Applying Code Patch ---")
        file_path = os.path.join(self.repo_path, culprit_file)
        if not os.path.exists(file_path):
            print(f"Error: File to patch does not exist: {file_path}")
            return None
        
        try:
            with open(file_path, 'w') as f:
                f.write(new_content)
            print(f"Successfully patched file: {file_path}")
            return file_path
        except IOError as e:
            print(f"Error writing to file {file_path}: {e}")
            return None

class GitManager:
    """
    Manages all Git operations using GitPython.
    """
    def __init__(self, repo_path: str):
        try:
            self.repo = Repo(repo_path)
            # Ensure a remote named 'origin' exists
            if 'origin' not in self.repo.remotes:
                raise ValueError(f"No remote named 'origin' found in repository at {repo_path}. Please add one.")
        except Exception as e:
            print(f"Error initializing Git repository at {repo_path}: {e}")
            raise
        self.author = Actor("AIOps Agent", "aiops-agent@example.com")

    def create_commit_and_push(self, file_to_commit: str, rca: str) -> Optional[str]:
        """
        Creates a new branch, commits the patched file, and pushes to origin.
        """
        print("\n--- Starting Git Workflow ---")
        # Check if there are actual changes to commit
        # The apply_patch method would have already modified the file
        if not self.repo.is_dirty(untracked_files=True, path=[file_to_commit]):
            print("No changes detected in the patched file. Skipping commit.")
            return None

        branch_name = f"agent-fix/remediation-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            # 1. Create a new branch
            new_branch = self.repo.create_head(branch_name)
            new_branch.checkout()
            print(f"Created and checked out new branch: {branch_name}")

            # 2. Stage the patched file
            self.repo.index.add([file_to_commit])
            print(f"Staged file: {file_to_commit}")

            # 3. Create a structured commit message
            commit_message = f"fix: Automated remediation for system error\n\n"
            commit_message += f"This commit was generated automatically by the AIOps agent.\n\n"
            commit_message += f"Root Cause Analysis:\n{rca}"
            
            self.repo.index.commit(commit_message, author=self.author, committer=self.author)
            print("Committed changes.")

            # 4. Push the new branch to the remote
            origin = self.repo.remote(name='origin')
            try:
                # Use --force if necessary, but generally avoid for automated systems unless needed for rebasing
                origin.push(refspec=f'{branch_name}:{branch_name}')
                print(f"Pushed branch '{branch_name}' to remote.")
            except Exception as e:
                print(f"Error pushing to remote: {e}")
                return None
            
            # Return to main branch (support both 'master' and 'main')
            if 'main' in self.repo.heads: # Prefer 'main' as default
                self.repo.heads.main.checkout()
                print("Checked out 'main' branch.")
            elif 'master' in self.repo.heads:
                self.repo.heads.master.checkout()
                print("Checked out 'master' branch.")
            else:
                print("Warning: Neither 'master' nor 'main' branch found to checkout. Staying on current branch.")

            return branch_name

        except Exception as e:
            print(f"An error occurred during the Git workflow: {e}")
            # Attempt to revert to original branch if possible in case of failure
            if 'main' in self.repo.heads:
                self.repo.heads.main.checkout()
            elif 'master' in self.repo.heads:
                self.repo.heads.master.checkout()
            return None

class NotificationManager:
    """
    Handles notifications: GitHub PR creation and email alerts.
    """
    def __init__(self, config):
        self.config = config

    def _send_email(self, subject: str, body: str, to_address: str) -> None:
        """Helper function to send an email."""
        try:
            print("Preparing to send email notification...")
            print(f"SMTP server: {self.config['smtp_server']}")
            print(f"SMTP port: {self.config['smtp_port']}")
            print(f"Sender: {self.config['smtp_user']}")
            print(f"Recipient: {to_address}")
            
            msg = MIMEText(body)
            msg['Subject'] = subject
            msg['From'] = self.config['smtp_user']
            msg['To'] = to_address

            with smtplib.SMTP(self.config["smtp_server"], self.config["smtp_port"]) as server:
                server.starttls()
                server.login(self.config["smtp_user"], self.config["smtp_pass"])
                server.send_message(msg) # Use send_message for MIMEText objects
            print(f"Email notification sent to {to_address}.")
        except Exception as e:
            print(f"Error sending email notification: {e}")
            import traceback
            traceback.print_exc()

    def send_error_alert_email(self, error_details: Dict, to_address: str) -> None:
        """Sends an email alert about a newly found critical error."""
        subject = f"AIOps Alert: Critical Error Detected in {error_details.get('culprit_file', 'Application Logs')}"
        preceding_events_str = "\n".join([f"{e['timestamp']} - {e['level']} - {e['message']}" for e in error_details['preceding_events']])
        if not preceding_events_str:
            preceding_events_str = "No preceding events found within the analysis window."

        body = f"""
A critical error has been detected by the AIOps agent.

**Error Details:**
Timestamp: {error_details['latest_error']['timestamp']}
Level: {error_details['latest_error']['level']}
Message:
```
{error_details['latest_error']['message']}
```

**Preceding Events:**
```
{preceding_events_str}
```
---
This is an automated message from the AIOps agent.
        """
        self._send_email(subject, body, to_address)

    def send_fix_proposal_email(self, diagnosis: Dict, to_address: str) -> None:
        """Sends an email notification about an AI-generated code fix proposal."""
        subject = f"AIOps Update: AI-Generated Fix Proposal for {diagnosis.get('culprit_file', 'unknown file')}"
        body = f"""
The AIOps agent has generated a potential fix for a detected error.

**Root Cause Analysis:**
```
{diagnosis.get('root_cause_analysis', 'No analysis provided.')}
```

**Proposed Code Fix:**
```python
{diagnosis.get('fixed_code', 'No fixed code generated.')}
```

**Original Code Snippet:**
```python
{diagnosis.get('original_code', 'Original code not available.')}
```

Please review the proposed fix. The agent will attempt to apply and push this fix to a new branch for further review.

---
This is an automated message from the AIOps agent.
        """
        self._send_email(subject, body, to_address)

    def create_pull_request(self, branch_name: str, rca: str, fixed_code: str) -> Optional[str]:
        """Creates a pull request on GitHub."""
        print("\n--- Creating GitHub Pull Request ---")
        url = f"https://api.github.com/repos/{self.config['owner']}/{self.config['repo']}/pulls"
        headers = {
            "Authorization": f"token {self.config['token']}",
            "Accept": "application/vnd.github.v3+json",
        }
        title = f"Automated Fix: {rca.splitlines()[0] if rca else 'Automated Remediation'}"
        body = f"""
## Automated Remediation by AIOps Agent

**Root Cause Analysis:**
```
{rca}
```
**AI-Generated Code Fix:**
```
{fixed_code}
```
**Disclaimer:** This pull request was created automatically by the AIOps agent. Please review the changes before merging.
"""
        # Determine the base branch (main or master)
        base_branch = "main" # Default
        try:
            response_main = requests.get(f"https://api.github.com/repos/{self.config['owner']}/{self.config['repo']}/branches/main", headers=headers)
            if response_main.status_code != 200: # If 'main' doesn't exist, try 'master'
                response_master = requests.get(f"https://api.github.com/repos/{self.config['owner']}/{self.config['repo']}/branches/master", headers=headers)
                if response_master.status_code == 200:
                    base_branch = "master"
                else:
                    print(f"Warning: Neither 'main' nor 'master' base branch found for repo {self.config['owner']}/{self.config['repo']}. Using 'main' as fallback.")
            print(f"Using '{base_branch}' as the base branch for the pull request.")
        except Exception as e:
            print(f"Error determining base branch: {e}. Defaulting to 'main'.")

        data = {
            "title": title,
            "head": branch_name,
            "base": base_branch,
            "body": body,
            "assignees": [self.config["assignee"]],
        }
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status() # Raise an exception for HTTP errors
            pr_url = response.json().get("html_url")
            print(f"Pull request created: {pr_url}")
            return pr_url
        except requests.exceptions.RequestException as e:
            print(f"Error creating pull request: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"GitHub API response error: {e.response.text}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred during PR creation: {e}")
            return None

    def send_pull_request_notification_email(self, pr_url: str, rca: str, to_address: str) -> None:
        """Sends an email notification about the new pull request."""
        subject = "Automated Pull Request Created by AIOps Agent"
        body = f"""
A new pull request has been created automatically by the AIOps agent.

**Pull Request URL:** {pr_url}

**Root Cause Analysis:**
```
{rca}
```

Please review the pull request at your earliest convenience.

---
This is an automated message from the AIOps agent.
        """
        self._send_email(subject, body, to_address)

def create_mock_files():
    """Creates mock application and log files for testing."""
    # Simple Python app with a deliberate error
    app_code = '''
def buggy_function():
    total = 0
    # Syntax error: missing colon
    for i in range(5): # Fix: Added colon here for initial mock
        total += i
    return total

# The second part of the original mock had syntax errors
# and a logic error (divisor = 0 condition check was an assignment)
# Corrected for a single, clear ZeroDivisionError
def application_run():
    print("Application starting...")
    print("Performing calculation...")
    
    try:
        print("Attempting to divide 10 by 0.")
        divisor = 0
        result = 10 / divisor # This will cause ZeroDivisionError
        print(f"The result is: {result}")
    except ZeroDivisionError as e:
        print(f"Caught ZeroDivisionError: {e}")
        # In a real app, this would be logged. We simulate it below.
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Application run finished.")

if __name__ == "__main__":
    application_run()
    '''.strip()

    # Simulate an application log with a ZeroDivisionError
    # This log content directly reflects the error in the app_code above.
    log_content = f'''
{datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - Application starting...
{datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - Performing calculation...
{datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - ERROR - ZeroDivisionError: division by zero
Traceback (most recent call last):
  File "buggy_app.py", line 18, in application_run
    result = 10 / divisor
ZeroDivisionError: division by zero
{datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - Caught ZeroDivisionError: division by zero
{datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]} - INFO - Application run finished.
    '''.strip()

    # Write the files to disk
    with open('buggy_app.py', 'w') as f:
        f.write(app_code)
    with open('app_error.log', 'w') as f:
        f.write(log_content)

    print("Mock files created: 'buggy_app.py', 'app_error.log'.")

# --- Agent Functions ---
def error_finder_agent(log_file_path: str, email_recipient: str, notification_config: Dict) -> Optional[Dict]:
    """
    Agent 1: Finds errors in logs and emails the found error.
    """
    print("\n--- Agent 1: Error Finder ---")
    log_analyzer = LogAnalyzer(log_file_path=log_file_path)
    analysis_results = log_analyzer.analyze()

    if analysis_results:
        notification_manager = NotificationManager(config=notification_config)
        notification_manager.send_error_alert_email(analysis_results, email_recipient)
        print("Error Finder Agent completed successfully.")
    else:
        print("No critical errors found by Error Finder Agent.")
    
    return analysis_results

def error_fixer_agent(analysis_results: Dict, repo_path: str, api_key: str, email_recipient: str, notification_config: Dict) -> Optional[Dict]:
    """
    Agent 2: Fixes the error using AI and emails the fixed error.
    """
    print("\n--- Agent 2: Error Fixer ---")
    if not analysis_results:
        print("No analysis results provided to Error Fixer Agent. Exiting.")
        return None

    diagnoser = CodeDiagnoser(api_key=api_key)
    diagnosis_results = diagnoser.get_diagnosis_and_fix(analysis_results, repo_path=repo_path)

    if diagnosis_results:
        notification_manager = NotificationManager(config=notification_config)
        notification_manager.send_fix_proposal_email(diagnosis_results, email_recipient)
        print("Error Fixer Agent completed successfully.")
    else:
        print("AI diagnosis and code fix generation failed by Error Fixer Agent.")

    return diagnosis_results

def git_pusher_agent(analysis_results: Dict, diagnosis_results: Dict, repo_path: str, email_recipient: str, notification_config: Dict) -> Optional[str]:
    """
    Agent 3: Applies the fix, pushes to Git, and creates a pull request.
    """
    print("\n--- Agent 3: Git Pusher ---")
    if not diagnosis_results or not analysis_results:
        print("Missing diagnosis or analysis results for Git Pusher Agent. Exiting.")
        return None

    # Apply the generated code fix as a patch
    patcher = CodePatcher(repo_path=repo_path)
    patched_file = patcher.apply_patch(analysis_results["culprit_file"], diagnosis_results["fixed_code"])

    if not patched_file:
        print("Failed to apply code patch by Git Pusher Agent. Exiting.")
        return None

    # Commit and push the changes to a new Git branch
    git_manager = GitManager(repo_path=repo_path)
    branch_name = git_manager.create_commit_and_push(patched_file, diagnosis_results["root_cause_analysis"])

    if not branch_name:
        print("Git commit and push failed by Git Pusher Agent. Exiting.")
        return None

    # Create a pull request on GitHub and send an email notification
    notification_manager = NotificationManager(config=notification_config)
    pr_url = notification_manager.create_pull_request(branch_name, diagnosis_results["root_cause_analysis"], diagnosis_results["fixed_code"])

    if pr_url:
        notification_manager.send_pull_request_notification_email(pr_url, diagnosis_results["root_cause_analysis"], email_recipient)
        print("Git Pusher Agent completed successfully.")
    else:
        print("Pull request creation failed by Git Pusher Agent.")
    
    return pr_url

# --- Main Orchestration ---
def main():
    # Step 1: Create mock application and log files for testing
    create_mock_files()

    # Load environment variables for configuration
    # These are loaded at the module level via load_dotenv()

    # Configuration for NotificationManager and general use
    notification_config = {
        "token": os.getenv("GITHUB_TOKEN"),
        "owner": os.getenv("GITHUB_REPO_OWNER"),
        "repo": os.getenv("GITHUB_REPO_NAME"),
        "assignee": os.getenv("PR_ASSIGNEE"),
        "smtp_server": os.getenv("EMAIL_HOST"),
        "smtp_port": int(os.getenv("EMAIL_PORT", 587)), # Ensure port is an integer
        "smtp_user": os.getenv("EMAIL_USER"),
        "smtp_pass": os.getenv("EMAIL_PASS"),
    }
    email_recipient = os.getenv("EMAIL_RECIPIENT")
    repo_path = "." # Assuming the script is run in the root of the local repo

    if not all([os.getenv("GOOGLE_API_KEY"), os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPO_OWNER"),
                os.getenv("GITHUB_REPO_NAME"), os.getenv("PR_ASSIGNEE"), os.getenv("EMAIL_HOST"),
                os.getenv("EMAIL_PORT"), os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASS"),
                os.getenv("EMAIL_RECIPIENT")]):
        print("Error: One or more environment variables are not set. Please check your .env file.")
        print("Required variables: GOOGLE_API_KEY, GITHUB_TOKEN, GITHUB_REPO_OWNER, GITHUB_REPO_NAME, PR_ASSIGNEE, EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, EMAIL_RECIPIENT")
        sys.exit(1) # Exit if essential configurations are missing


    # --- Agent Workflow ---

    # Agent 1: Error Finder
    analysis_results = error_finder_agent(
        log_file_path='app_error.log',
        email_recipient=email_recipient,
        notification_config=notification_config
    )

    if not analysis_results:
        print("Error Finder Agent found no critical errors. Stopping workflow.")
        return # Exit if no errors found

    # Agent 2: Error Fixer
    diagnosis_results = error_fixer_agent(
        analysis_results=analysis_results,
        repo_path=repo_path,
        api_key=os.getenv("GOOGLE_API_KEY"),
        email_recipient=email_recipient,
        notification_config=notification_config
    )

    if not diagnosis_results:
        print("Error Fixer Agent failed to provide a diagnosis/fix. Stopping workflow.")
        return # Exit if diagnosis/fix failed

    # Agent 3: Git Pusher
    pr_url = git_pusher_agent(
        analysis_results=analysis_results,
        diagnosis_results=diagnosis_results,
        repo_path=repo_path,
        email_recipient=email_recipient,
        notification_config=notification_config
    )

    if pr_url:
        print(f"\nEnd-to-end AIOps workflow completed. Pull Request: {pr_url}")
    else:
        print("\nEnd-to-end AIOps workflow failed at the Git Pusher stage.")

if __name__ == "__main__":
    main()
