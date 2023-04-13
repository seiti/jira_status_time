import os
import pathlib
from collections import defaultdict

import openpyxl as openpyxl
import requests
from alive_progress import alive_bar
from datetime import datetime, timedelta


# Configuration
USERNAME = "my.login@loggi.com"
ACCESS_TOKEN = "token"  # see https://id.atlassian.com/manage-profile/security/api-tokens"
JIRA_CLOUD_DOMAIN = "subdomain"  # the subdomain used, like https://subdomain.atlassian.net

# Defaults
project = "FBO"
jql = f"project={project} AND resolved >= startOfYear() AND project = FBO AND status = DONE"
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


def hours_to_time_string(hours: float):
    time_delta = timedelta(hours=hours)
    hours, remainder = divmod(time_delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


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
    header = ["key", "assignee", "created", "resolved"]
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
            created = issue["fields"]["created"]
            resolved = issue["fields"]["resolutiondate"]
            description = adf_to_text(issue["fields"]["description"])

            report_item = [key, assignee, created, resolved]

            # detailing the issue at hand; step required to obtain status change data
            issue_response = requests.get(issue_url.format(issue['key']), auth=auth)
            changelog = issue_response.json()["values"]
            from_status = "To Do"  # hardcoding
            from_date = datetime.strptime(issue["fields"]["created"], "%Y-%m-%dT%H:%M:%S.%f%z")

            # cycle time per status and per issue
            cycle_times = defaultdict(lambda: 0)

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
                    cycle_time = (
                                             to_date - from_date).total_seconds() / 3600.0  # Cycle time in hours
                    cycle_times[from_status.lower()] += cycle_time

            header.extend(stasuses_available)
            for st in stasuses_available:
                report_item.append(hours_to_time_string(cycle_times[st]))

            # leaving the biggest field to be last
            header.append("description")
            report_item.append(description)

            report_content.append(report_item)

    # Create a new Excel workbook and worksheet
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for item in report_content:
        ws.append(item)

    wb.save(os.path.join(pathlib.Path().resolve(), "output", f"{project}_status_times.xlsx"))


if __name__ == '__main__':
    time_in_status_per_key()
