"""Change guide page cleanup and Implementation Plan (Confluence storage format)."""
import html
import logging
import re

logger = logging.getLogger(__name__)


def drop_password_banner(body):
    s = body.find("IMPORTANT! - Please notice")
    e = body.find("from the above list")
    if s == -1 or e == -1:
        return body
    s = body.rfind("<h", 0, s)
    e = body.find("</ul>", e) + len("</ul>")
    tail = re.match(r'\s*</li>\s*</ul>', body[e:])   # outer closers of the nested list
    if tail:
        e += tail.end()
    cut = body[s:e]
    for tag in ("ul", "li", "p", "h1"):
        opens = len(re.findall(rf"<{tag}[\s>]", cut))
        if opens != cut.count(f"</{tag}>"):
            raise ValueError(f"banner cut is unbalanced for <{tag}>")
    return body[:s] + body[e:]


def fill_implementation_plan(body, pr):
    sec_s = body.find("<h1>4. Implementation Plan</h1>")
    sec_e = body.find("<h1>5.", sec_s)
    if sec_s == -1 or sec_e == -1:
        logger.warning("Implementation Plan section not found, leaving it as is")
        return body
    sec = body[sec_s:sec_e]
    if "4.1. Step 1" not in sec:        # already filled
        return body
    n = pr["number"]
    title = html.escape(pr["title"], quote=False)
    url = html.escape(pr["html_url"], quote=True)
    base = html.escape(pr["base"]["ref"], quote=False)
    sec = sec.replace("4.1. Step 1", f"4.1. Merge pull request #{n} into {base}", 1)
    sec = re.sub(r'<h2[^>]*>4\.[234]\. Step[^<]*</h2>\s*', '', sec)
    sec = sec.replace("</h2>",
                      f'</h2>\n<p style="margin-left: 80.0px;">Pull request: <a href="{url}">#{n} {title}</a></p>\n', 1)
    return body[:sec_s] + sec + body[sec_e:]


def fill_rollback_plan(body, pr):
    sec_s = body.find("<h1>6. Rollback Plan</h1>")
    if sec_s == -1:
        logger.warning("Rollback Plan section not found, leaving it as is")
        return body
    sec_e = body.find("<h1", sec_s + 1)
    if sec_e == -1:
        sec_e = len(body)
    sec = body[sec_s:sec_e]
    steps = list(re.finditer(r'<h2[^>]*>.*?</h2>\s*', sec, flags=re.S))
    if not steps or not re.search(r'6\.\d\. Step', steps[-1].group(0)):
        return body                      # already filled or unexpected layout
    n = pr["number"]
    base = html.escape(pr["base"]["ref"], quote=False)
    url = html.escape(pr["html_url"], quote=True)
    title = html.escape(pr["title"], quote=False)
    last = steps[-1]
    heading = re.sub(r'6\.\d\. Step[^<]*', f'6.1. Revert pull request #{n}', last.group(0), count=1)
    heading = heading.rstrip() + (
        f'\n<p style="margin-left: 80.0px;">Revert <a href="{url}">#{n} {title}</a> so that the state of '
        f'<strong>{base}</strong> is the same as before the merge.</p>\n')
    new_sec = sec[:steps[0].start()] + heading + sec[last.end():]
    return body[:sec_s] + new_sec + body[sec_e:]


def prepare_guide(body, pr):
    new = fill_rollback_plan(fill_implementation_plan(drop_password_banner(body), pr), pr)
    if re.search("password", new, flags=re.I):
        raise ValueError("'password' is still on the page (maybe in the PR title), leaving it for a human")
    return new
