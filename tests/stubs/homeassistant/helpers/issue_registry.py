issues = []
class IssueSeverity:
    WARNING = "warning"
def async_create_issue(hass, domain, issue_id, **kw): issues.append((domain, issue_id, kw))
