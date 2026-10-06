"""URL paths, regexes and tunables for the Arbor guardian portal."""

import os
import re

TIMEOUT = 30
# Politeness: keep at least this many seconds between requests to Arbor, and
# back off (rather than hammer or fail) when the portal throttles us. This
# prevents the script from creating a DoS-like burst, e.g. when fetching many
# message bodies. Tunable via $ARBOR_MIN_INTERVAL.
MIN_INTERVAL = max(0.0, float(os.environ.get("ARBOR_MIN_INTERVAL", "0.5")))
MAX_RETRIES = 4  # on 429/503
MAX_BACKOFF = 30.0  # seconds

LIST_PATH = "/guardians/communication-center-ui/school-messages/?format=javascript"
VIEW_PATH = (
    "/guardians/outbound-in-app-message-ui/view-outbound-in-app-message/id/{id}?format=javascript"
)
WHOAMI_PATH = "/auth/current-user-settings/format/json"
LOGIN_PATH = "/auth/login?lang=en"
SEARCH_BY_EMAIL = "https://login.arbor.sc/applications/search-by-email"
DASHBOARD_PATH = "/guardians/home-ui/dashboard?format=javascript"
KPIS_PATH = "/guardians/student/kpis/id/{id}/"
CALENDAR_PATH = "/guardians/widget-data/get-calendar-data/student-id/{id}/"
MEALS_BALANCE_PATH = "/guardians/customer-account/meals-balance-kpi/student-id/{id}"
OUTSTANDING_PATH = "/guardians/customer-account/payment-total-kpi/student-id/{id}"
ATT_BY_DATE_PATH = "/guardians/student-ui/attendance-by-date/student-id/{id}"
ATT_BY_DATE_YEAR = "/period/academic-year/academic-year-id/{year}"
ASSIGN_PATH = "/guardians/student-ui/assignments/student-id/{id}"
ASSIGN_KPI_PATH = "/guardians/student/assignments-kpi/student-id/{id}/academic-year-id/{year}"
ASSIGN_LIST_PATH = (
    "/guardians/student-ui/assignments-{segment}/student-id/{id}/academic-year-id/{year}"
)
SCHOOLWORK_PATH = (
    "/guardians/student-ui/schoolwork-overview/schoolwork-id/{wid}/student-id/{id}"
    "/list-segment/assignments-{segment}/academic-year-id/{year}"
)
ASSIGN_SEGMENTS = ("due", "overdue", "submitted")
MEAL_CHOICES_PATH = "/guardians/meal-ui/meal-choices/student-id/{id}"
MEAL_GRID_PATH = (
    "/guardians/meal-ui/setup-meal-choices/meal-rotation-menu-id/{menu}/student-id/{id}"
)
MEAL_OPTIONS_PATH = (
    "/guardians/meal-ui/setup-meal-choice/meal-rotation-menu-id/{menu}/student-id/{id}/date/{date}"
)
ACCOUNT_DASHBOARDS = {
    "invoices": "/guardians/customer-account-ui/invoices-dashboard",
    "top-ups": "/guardians/customer-account-ui/top-ups-dashboard",
    "credit-notes": "/guardians/customer-account-ui/credit-notes-dashboard",
}
REPORT_CARDS_PATH = "/guardians/student-ui/report-cards/student-id/{id}"
CLUBS_PATH = "/guardians/club-ui/dashboard/student-id/{id}"
TRIPS_PATH = "/guardians/trip-ui/dashboard/student-id/{id}"
SHOP_PATH = "/guardians/school-shop-ui/dashboard/student-id/{id}"
SHOP_LIST_RE = re.compile(r"^/guardians/school-shop/list/student-id/\d+$")
ACCOUNT_DASH_PATH = "/guardians/customer-account-ui/dashboard/customer-account-id/{acct}"
ACCOUNT_DAY_RE = re.compile(
    r"^/guardians/customer-account-ui/view-payments-on-date/"
    r"date/(\d{4}-\d{2}-\d{2})/customer-account-id/\d+$"
)
BEHAVIOUR_PATH = "/guardians/behaviour-ui/student-behaviour/id/{id}"
EXAMS_PATH = "/guardians/student-ui/examinations/candidate-id/{cid}"
EXAMS_LINK_RE = re.compile(r"^/guardians/student-ui/examinations/candidate-id/(\d+)/?$")
YEAR_SUFFIX = "/academic-year-id/{year}"
ATT_CERT_RE = re.compile(
    r"^/guardians/student/download-attendance-certificate/"
    r"student-id/\d+(?:/academic-year-id/\d+)?$"
)
FILES_LIST_RE = re.compile(r"^/guardians/(club|trip)/list-files/(club|trip)-id/\d+/student-id/\d+$")
# Anything that could spend money or change state. The read helpers refuse to
# follow a portal-supplied URL that matches this, as a belt-and-braces guard.
UNSAFE_URL_RE = re.compile(
    r"(?i)basket|checkout|buy-product|top-up-by|pay-|/pay\b|"
    r"process-|sign-?up|register-|consent|/save|/delete|/create|/update"
)
JS = "?format=javascript"

# Mark colours used by the attendance-by-date grid -> coarse category.
MARK_COLOURS = {
    "#68aa22": "present",
    "#fce015": "late",
    "#f7931e": "authorised_absence",
    "#cb0d0d": "unauthorised",
    "#776e6a": "not_counted",
}

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

RC_DOWNLOAD_RE = re.compile(r"^/guardians/student/download-student-report-card/format/pdf$")
RC_CUSTOM_DOWNLOAD_RE = re.compile(
    r"^/custom-report-card-student/download/"
    r"custom-report-card-student-id/\d+$"
)

_FILE_FIELD_SKIP = re.compile(r"(?i)url|link|href|token|download|src|path|hash|key")
