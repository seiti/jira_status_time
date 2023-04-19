import os
from os import path
import json

import pandas as pd
import matplotlib.pyplot as plt
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
statuses_available = ["to do", "on hold", "in progress", "code review", "broadcast", "done"]


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


def timedelta_to_string(time_delta: timedelta):
    if type(time_delta) is not timedelta:
        return time_delta
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
    time_in_status_header = (
            ["key", "assignee", "created", "resolved"]
            + statuses_available
            + ["description"]
    )
    time_in_status = []
    cfd_header = ['date'] + statuses_available
    cfd_report = defaultdict(lambda: defaultdict(int))

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

    # process each issue, retrieving history data
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
            time_in_status_item = [key, assignee, created, resolved]
            current_status_item = []

            # detailing the issue at hand; step required to obtain status change data
            issue_response = requests.get(issue_url.format(issue['key']), auth=auth)
            changelog = issue_response.json()["values"]
            from_status = "To Do"  # hardcoding
            from_date = datetime.strptime(issue["fields"]["created"], "%Y-%m-%dT%H:%M:%S.%f%z")

            # cycle time per status and per issue
            durations = defaultdict(timedelta)
            current_status = dict()

            # changelog presents a multitude of info, but we're only interested in status changes
            changelog = [change for change in changelog if change["items"][0]["field"] == "status"]

            # calculating things per status
            for i in range(len(changelog)):
                if i > 0:
                    from_status = changelog[i - 1]["items"][0]["toString"]
                    from_date = datetime.strptime(changelog[i - 1]["created"],
                                                  "%Y-%m-%dT%H:%M:%S.%f%z")
                to_status = changelog[i]["items"][0]["toString"]
                to_date = datetime.strptime(changelog[i]["created"], "%Y-%m-%dT%H:%M:%S.%f%z")
                if from_status and to_status:
                    durations[from_status.lower()] += (to_date - from_date)
                if to_status:
                    current_status[to_date.date()] = to_status.lower()  # considering only the last change of day

            for st in statuses_available:
                time_in_status_item.append(durations[st])

            for dt, st in current_status.items():
                cfd_report[dt][st] += 1  # current issue values are consolidated per date

            # leaving the biggest field to be last
            time_in_status_item.append(description)
            time_in_status.append(time_in_status_item)

    # flattening cfd report
    cfd_items = []
    for dt, status_count in cfd_report.items():
        cfd_items.append([dt] + [status_count[st] for st in statuses_available])

    reports = [
        ('Status Duration', [time_in_status_header] + sorted(time_in_status, key=lambda x: x[3])),
        ('CFD', [cfd_header] + sorted(cfd_items, key=lambda x: x[0])),
    ]

    return reports


def to_spreadsheet(reports, filepath):
    wb = openpyxl.Workbook()
    for name, report_content in reports:
        ws = wb.create_sheet(name)
        for item in report_content:
            item = [timedelta_to_string(element) for element in item]
            ws.append(item)
    wb.save(filepath)


def to_json(report_content, filepath):
    data = dict(
        columns=report_content[0],  # header
        values=[item for item in report_content[1:]],  # content
    )
    out = json.dumps(
        data,
        indent=4,
        ensure_ascii=False,  # enabling unicode chars
        default=str,  # mostly for datetime serialization
    )
    with open(filepath, "w") as f:
        f.write(out)


def diagrams(reports):

    def nanos_to_sensible_str(nanos, pos):
        seconds = int(nanos / 10 ** 9)
        # using timedelta object because its string representation is alright
        return str(timedelta(seconds=seconds))

    (duration, cfd) = reports

    report_name, report_content = duration
    data = pd.DataFrame(report_content[1:], columns=report_content[0])
    cdd = pd.DataFrame(columns=['Date'] + statuses_available)

    # set the date range for the chart
    start_date = data['resolved'].min().date()
    end_date = data['resolved'].max().date() + timedelta(days=1)
    date_range = pd.date_range(start_date, end_date)

    # iterate over the date range and aggregate the data for each day
    for date in date_range:
        # only data up until current date, enforces the "cumulative"
        #cumulative_data = data[(date_range[0] <= data['resolved']) & (data['resolved'] <= date)]

        # count the number of issues in each status
        # c = {st: len(cumulative_data[cumulative_data[st] != timedelta()]) for st in statuses_available}
        # c['Date'] = date

        # only data on the current date, not cumulative
        criteria = (date <= data['resolved']) & (data['resolved'] <= date + timedelta(days=1))
        current_data = data[criteria]

        # mean of duration in each status
        # TODO duration is multivariate, given the various statuses. A better alternative is to render a series of boxplot plot per
        # status instaed, or even something like https://www.jstatsoft.org/article/download/v025c01/239
        d = {st: (int(current_data[st].mean())) for st in statuses_available}
        d['Date'] = date
        cdd = pd.concat([cdd, pd.DataFrame([d])], ignore_index=True)

    # date column as the index, enabling sensible defaults when plotting
    cdd.set_index('Date', inplace=True)

    # plotting
    fig, axes = plt.subplots(nrows=2, ncols=1, layout="constrained", figsize=(10, 8))
    # cfd.plot.area(title='Cumulative Flow Diagram', ax=axes[0])
    # cdd_plot = cdd.plot.line(title=report_name, ax=axes[1])
    cdd_plot = cdd.plot.area(title=report_name, ax=axes[1])
    cdd_plot.yaxis.set_major_formatter(nanos_to_sensible_str)
    plt.show()


# if __name__ == '__main__':
#     reports = time_in_status_per_key()
#
#     base_path = path.join(pathlib.Path().resolve(), "output")
#     to_spreadsheet(reports, path.join(base_path, f"{PROJECT}_status_times.xlsx"))
#     # to_json(
#     #     [item[: -1] for item in report_content],  # removing description
#     #     path.join(base_path, f"{PROJECT}_status_times.json"),
#     # )
#     diagrams(reports)
