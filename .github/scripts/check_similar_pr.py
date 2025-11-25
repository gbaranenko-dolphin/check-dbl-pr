import os
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin

GITHUB_API = "https://api.github.com"

token = os.environ["GITHUB_TOKEN"]
repo = os.environ["GITHUB_REPOSITORY"]
pr_number = int(os.environ["PR_NUMBER"])
slack_webhook = os.environ.get("SLACK_WEBHOOK_URL")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
})


def gh_get(path, **params):
    url = urljoin(GITHUB_API + "/", path)
    r = session.get(url, params=params)
    r.raise_for_status()
    return r.json()


def get_pr(pr_num: int):
    return gh_get(f"/repos/{repo}/pulls/{pr_num}")


def get_pr_files(pr_num: int):
    files = []
    page = 1
    while True:
        chunk = gh_get(f"/repos/{repo}/pulls/{pr_num}/files", page=page, per_page=100)
        if not chunk:
            break
        files.extend([f["filename"] for f in chunk])
        page += 1
    return files


def tokenize(text: str):
    import re
    text = (text or "").lower()
    tokens = re.findall(r"[a-z0-9_]+", text)
    return set(tokens)


def jaccard(s1, s2):
    if not s1 and not s2:
        return 0.0
    inter = len(s1 & s2)
    union = len(s1 | s2)
    return inter / union if union else 0.0


def compute_similarity(current, candidate):
    cur_files = set(current["files"])
    cand_files = set(candidate["files"])

    if not cur_files:
        file_overlap = 0.0
    else:
        file_overlap = len(cur_files & cand_files) / len(cur_files)

    cur_text = f"{current['title']} {current['body']}"
    cand_text = f"{candidate['title']} {candidate['body']}"

    cur_tokens = tokenize(cur_text)
    cand_tokens = tokenize(cand_text)

    text_sim = jaccard(cur_tokens, cand_tokens)

    score = 0.6 * file_overlap + 0.4 * text_sim
    return score, file_overlap, text_sim


def get_recent_prs(days=7):
    since = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
    prs = []
    page = 1
    while True:
        res = gh_get(
            f"/repos/{repo}/pulls",
            state="all",
            sort="updated",
            direction="desc",
            page=page,
            per_page=50,
        )
        if not res:
            break

        for pr in res:
            # stop if обновлялись слишком давно
            if pr["updated_at"] < since:
                return prs
            prs.append(pr)

        page += 1
    return prs


def notify_slack(message: str):
    if not slack_webhook:
        print("SLACK_WEBHOOK_URL not set, skipping Slack notification")
        return
    resp = requests.post(slack_webhook, json={"text": message})
    print("Slack response:", resp.status_code, resp.text[:200])


def main():
    current_pr = get_pr(pr_number)
    current_files = get_pr_files(pr_number)
    current = {
        "number": current_pr["number"],
        "title": current_pr["title"],
        "body": current_pr.get("body") or "",
        "files": current_files,
        "url": current_pr["html_url"],
        "author": current_pr["user"]["login"],
    }

    recent_prs = get_recent_prs(days=7)

    candidates = []
    for pr in recent_prs:
        if pr["number"] == current["number"]:
            continue
        if pr["state"] not in ("open", "closed"):
            continue

        files = get_pr_files(pr["number"])
        cand = {
            "number": pr["number"],
            "title": pr["title"],
            "body": pr.get("body") or "",
            "files": files,
            "url": pr["html_url"],
            "author": pr["user"]["login"],
        }
        score, file_overlap, text_sim = compute_similarity(current, cand)
        if score >= 0.5 and file_overlap >= 0.4:  # пороги можно крутить
            candidates.append((score, file_overlap, text_sim, cand))

    if not candidates:
        print("No similar PRs found.")
        return

    # отсортируем по score, возьмем топ-3
    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[:3]

    lines = [
        f":mag: *Possible duplicate / overlapping PR detected*",
        f"*Current PR:* <{current['url']}|#{current['number']} - {current['title']}> (author: {current['author']})",
        "",
        f"*Similar PRs within last 7 days:*",
    ]
    for score, file_overlap, text_sim, cand in top:
        lines.append(
            f"- <{cand['url']}|#{cand['number']} - {cand['title']}> "
            f"(author: {cand['author']}, score={score:.2f}, files={file_overlap:.2f}, text={text_sim:.2f})"
        )

    notify_slack("\n".join(lines))


if __name__ == "__main__":
    main()
