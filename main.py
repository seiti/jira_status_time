import json
import os
from os import path
import pathlib
from collections import defaultdict

import openpyxl as openpyxl
import requests
from alive_progress import alive_bar
from datetime import datetime, timedelta


# Configuration
USERNAME = os.getenv("USERNAME")  # my.login@loggi.com
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")  # see https://id.atlassian.com/manage-profile/security/api-tokens"
JIRA_CLOUD_DOMAIN = os.getenv("JIRA_CLOUD_DOMAIN")  # the subdomain part of url, like https://subdomain.atlassian.net
PROJECT = os.getenv("PROJECT")  # the project code, as FBO, when issues are like FBO-123

# Defaults
jql = f"project={PROJECT} AND resolved >= startOfYear() AND project = FBO AND status = DONE"
fields = "key,assignee,status,created,resolutiondate,description"  # Fields to retrieve
max_results = 100  # set to maximum value available for search endpoint
# lower cased list of status used in the workflow
stasuses_available = ["to do", "on hold", "in progress", "code review", "broadcast", "done"]


# JIRA API endpoints
base_url = f"https://{JIRA_CLOUD_DOMAIN}.atlassian.net/rest/api/3/"
search_url = base_url + "search"
issue_url = f"https://{JIRA_CLOUD_DOMAIN}.atlassian.net/rest/api/latest/issue/{{}}/changelog"

params = {
    "jql": jql,
    "fields": fields,
    "maxResults": max_results,
}

# JIRA API authentication credentials
auth = (USERNAME, ACCESS_TOKEN)  # Replace with your JIRA username and API key


def hours_to_time_string(time_delta: timedelta):
    hours, remainder = divmod(int(time_delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def jira_date_to_naive(date_str):
    date_format = "%Y-%m-%dT%H:%M:%S.%f%z"
    return datetime.strptime(date_str, date_format).replace(tzinfo=None)


def adf_to_text(adf: dict):
    """ADF stands for Atlassian Document Format, enforced in API 3.0, replacing markdown."""
    text = ""

    if adf is None:
        return text

    def extract_text(node):
        nonlocal text
        if node.get("type") == "text":
            text += node.get("text", "")
        elif node.get("content"):
            for child_node in node["content"]:
                extract_text(child_node)

    for content_node in adf.get("content", []):
        extract_text(content_node)

    return text.strip()


def time_in_status_per_key():
    """Produce a "report", meaning a simple spreadsheet like matrix."""
    report_header = (
            ["key", "assignee", "created", "resolved"]
            + stasuses_available
            + ["description"]
    )
    report_content = []

    # retrieve list of issues, through the paginated API
    response = requests.get(search_url, params=params, auth=auth)
    response.raise_for_status()

    issues_list = []
    while len(response.json()["issues"]) >= max_results:
        issues_list.extend(response.json()["issues"])
        params["startAt"] = len(issues_list)
        response = requests.get(search_url, params=params, auth=auth)
    else:
        issues_list.extend(response.json()["issues"])

    with alive_bar(len(issues_list), force_tty=True) as t:
        for issue in issues_list:
            t()

            # info useful to mention in report
            key = issue["key"]
            assignee = issue["fields"]["assignee"]["emailAddress"] if issue["fields"][
                "assignee"] else ""
            created = jira_date_to_naive(issue["fields"]["created"])
            resolved = jira_date_to_naive(issue["fields"]["resolutiondate"])
            description = adf_to_text(issue["fields"]["description"])

            report_item = [key, assignee, created, resolved]

            # detailing the issue at hand; step required to obtain status change data
            issue_response = requests.get(issue_url.format(issue['key']), auth=auth)
            changelog = issue_response.json()["values"]
            from_status = "To Do"  # hardcoding
            from_date = datetime.strptime(issue["fields"]["created"], "%Y-%m-%dT%H:%M:%S.%f%z")

            # cycle time per status and per issue
            cycle_times = defaultdict(timedelta)

            # changelog presents a multitude of info, but we're only interested in status changes
            changelog = [change for change in changelog if change["items"][0]["field"] == "status"]
            for i in range(len(changelog)):
                if i > 0:
                    from_status = changelog[i - 1]["items"][0]["toString"]
                    from_date = datetime.strptime(changelog[i - 1]["created"],
                                                  "%Y-%m-%dT%H:%M:%S.%f%z")
                to_status = changelog[i]["items"][0]["toString"]
                to_date = datetime.strptime(changelog[i]["created"], "%Y-%m-%dT%H:%M:%S.%f%z")
                if from_status and to_status:
                    cycle_time = to_date - from_date
                    cycle_times[from_status.lower()] += cycle_time

            for st in stasuses_available:
                report_item.append(hours_to_time_string(cycle_times[st]))

            # leaving the biggest field to be last
            report_item.append(description)
            report_content.append(report_item)

    return [report_header] + sorted(report_content, key=lambda x: x[3])


def to_spreadsheet(report_content, filepath):
    wb = openpyxl.Workbook()
    ws = wb.active
    for item in report_content:
        ws.append(item)
    wb.save(filepath)


def to_json(report_content, filepath):
    data = dict(
        columns=report_content[0],  # header
        values=report_content[1:],  # content
    )
    out = json.dumps(data, indent=4, default=str)
    with open(filepath, "w") as f:
        f.write(out)


if __name__ == '__main__':
    report_content = time_in_status_per_key()
    base_path = path.join(pathlib.Path().resolve(), "output")
    to_spreadsheet(report_content, path.join(base_path, f"{PROJECT}_status_times.xlsx"))
    to_json(report_content, path.join(base_path, f"{PROJECT}_status_times.json"))
