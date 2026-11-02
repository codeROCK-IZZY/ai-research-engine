"""Synthetic company knowledge corpus with a labelled question set.

A fictional software company, *Northwind Labs*, with four knowledge bases in three
workspaces (all names, numbers and people are invented):

* **Engineering / Engineering Handbook** — one page per service (owner team, uptime SLA,
  latency target, datastore, on-call rotation, runbook), architecture decision records
  (ADRs) and incident postmortems.
* **Engineering / Research Notes** — experiment write-ups with measured results.
* **Operations / Policies** — HR, travel, security and refund policies, including
  deliberate traps: an *outdated* refund policy that contradicts the current one, a
  near-duplicate copy of the travel policy, and an old, stale onboarding guide.
* **Finance / Board Reports** — readable only by the finance team (access-control test).

Formats: Markdown, HTML and plain text, plus PDF for board reports. Every fact is generated
from the seed, so each question has a known answer and known source documents.

Question kinds: ``factual`` (wording close to the source), ``paraphrase`` (different wording:
tests semantic retrieval), ``conflict`` (two sources disagree), ``cross`` (needs two sources)
and ``unanswerable`` (not in the corpus, or only in a knowledge base the asker cannot read).
"""

from __future__ import annotations

import io
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

SEED = 42


@dataclass
class SourceSpec:
    filename: str
    workspace: str
    kb: str
    title: str
    published: str | None  # ISO date
    content: bytes = field(repr=False, default=b"")


@dataclass
class Question:
    id: str
    question: str
    kind: str
    asker: str
    answer: str | None  # a fact string that a correct answer must contain (None = unanswerable)
    gold: list[str]  # filenames of sources that support the answer


SERVICES = [
    ("Payments Service", "payments", "Team Falcon", "PostgreSQL"),
    ("Search Service", "search", "Team Heron", "OpenSearch"),
    ("Notification Service", "notifications", "Team Swift", "Redis"),
    ("Identity Service", "identity", "Team Kestrel", "PostgreSQL"),
    ("Ledger Service", "ledger", "Team Falcon", "PostgreSQL"),
    ("Catalog Service", "catalog", "Team Wren", "DynamoDB"),
    ("Pricing Service", "pricing", "Team Wren", "Redis"),
    ("Recommendations Service", "recommendations", "Team Heron", "Cassandra"),
    ("Media Service", "media", "Team Swift", "Amazon S3"),
    ("Reporting Service", "reporting", "Team Kestrel", "Snowflake"),
]
SLA = ["99.9%", "99.95%", "99.99%", "99.5%"]
ROTATIONS = ["weekly", "fortnightly", "follow-the-sun"]
ROOT_CAUSES = [
    "an expired TLS certificate on the internal load balancer",
    "a database connection pool exhausted by a retry storm",
    "a misconfigured feature flag rolled out to all regions at once",
    "a disk on the primary replica filling up with unrotated logs",
    "a schema migration that locked a hot table during peak traffic",
    "a dependency upgrade that changed default timeouts",
]


def _md(title: str, sections: list[tuple[str, str]], meta: str = "") -> bytes:
    body = f"# {title}\n\n" + (meta + "\n\n" if meta else "")
    body += "\n\n".join(f"## {h}\n\n{t}" for h, t in sections)
    return body.encode()


def _html(title: str, sections: list[tuple[str, str]], published: str | None) -> bytes:
    meta = f'<meta property="article:published_time" content="{published}T09:00:00Z">' if published else ""
    secs = "".join(f"<h2>{h}</h2><p>{t}</p>" for h, t in sections)
    return (
        f"<!DOCTYPE html><html><head><title>{title}</title>{meta}</head><body>"
        f"<nav>Home · Policies · Help</nav><article><h1>{title}</h1>{secs}</article>"
        f"<footer>© Northwind Labs intranet</footer></body></html>"
    ).encode()


def _pdf(title: str, paragraphs: list[str]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    for p in paragraphs:
        story += [Paragraph(p, styles["BodyText"]), Spacer(1, 8)]
    SimpleDocTemplate(buf, pagesize=A4, title=title, invariant=True).build(story)
    return buf.getvalue()


def generate(seed: int = SEED) -> tuple[list[SourceSpec], list[Question]]:
    rng = random.Random(seed)
    sources: list[SourceSpec] = []
    qs: list[Question] = []

    def q(text: str, kind: str, answer: str | None, gold: list[str], asker: str = "erin") -> None:
        qs.append(Question(f"q{len(qs) + 1:03d}", text, kind, asker, answer, gold))

    # ------------------------------------------------------------------ engineering handbook
    for name, slug, team, store in SERVICES:
        sla = rng.choice(SLA)
        p95 = rng.choice([120, 150, 180, 200, 250, 300, 400])
        rotation = rng.choice(ROTATIONS)
        deploys = rng.choice(["several times a day", "daily", "twice a week", "weekly"])
        fn = f"service-{slug}.md"
        sections = [
            ("Ownership", f"The {name} is owned by {team}. Escalations go to the #oncall-{slug} channel."),
            (
                "Service level objectives",
                f"The {name} has an uptime SLA of {sla} measured monthly. "
                f"The p95 latency target is {p95} ms for read requests.",
            ),
            (
                "Architecture",
                f"The {name} stores its data in {store}. It is deployed {deploys} "
                f"through the standard pipeline with automatic canary analysis.",
            ),
            (
                "On-call",
                f"On-call for the {name} rotates {rotation}. The primary responder acknowledges "
                f"pages within 5 minutes during business hours and 15 minutes out of hours.",
            ),
            (
                "Runbook",
                f"If error rates exceed the SLO burn alert, first check recent deploys, then the "
                f"{store} dashboard, then roll back with the one-click rollback in the deploy console.",
            ),
        ]
        sources.append(
            SourceSpec(
                fn,
                "Engineering",
                "Engineering Handbook",
                f"{name} handbook",
                "2026-06-01",
                _md(f"{name} handbook", sections),
            )
        )
        q(f"What is the uptime SLA of the {name}?", "factual", sla, [fn])
        q(f"Which team owns the {name}?", "factual", team, [fn])
        q(f"Where does the {name} keep its data?", "paraphrase", store, [fn])
        q(f"How quickly should the {name} respond for reads at the 95th percentile?", "paraphrase", f"{p95} ms", [fn])

    adr_specs = [
        (
            "ADR-007",
            "Use PostgreSQL for the ledger",
            "Accepted",
            "Ledger entries need multi-row transactions and strict consistency, which PostgreSQL provides; "
            "DynamoDB was rejected because cross-item transactions add latency and cost at our volume.",
        ),
        (
            "ADR-012",
            "Adopt OpenTelemetry for tracing",
            "Accepted",
            "A vendor-neutral tracing standard lets us switch observability vendors without code changes.",
        ),
        (
            "ADR-015",
            "Move image resizing to the edge",
            "Superseded",
            "Superseded by ADR-021 after edge compute costs grew faster than expected.",
        ),
        (
            "ADR-021",
            "Resize images in the Media Service with a cache",
            "Accepted",
            "Central resizing with a CDN cache cut cost by 38% compared with edge resizing.",
        ),
    ]
    for code, title, status, why in adr_specs:
        fn = f"adr-{code[4:]}.md"
        sources.append(
            SourceSpec(
                fn,
                "Engineering",
                "Engineering Handbook",
                f"{code}: {title}",
                "2026-03-15",
                _md(f"{code}: {title}", [("Status", status), ("Decision", title + "."), ("Rationale", why)]),
            )
        )
    q("Why did we choose PostgreSQL over DynamoDB for the ledger?", "factual", "consistency", ["adr-007.md"])
    q("What is the status of ADR-015?", "factual", "Superseded", ["adr-015.md"])
    q("How much cheaper was central image resizing than resizing at the edge?", "paraphrase", "38%", ["adr-021.md"])

    for i in range(8):
        name = SERVICES[rng.randrange(len(SERVICES))][0]
        code = f"INC-{2030 + i * 7}"
        minutes = rng.randint(12, 180)
        cause = ROOT_CAUSES[i % len(ROOT_CAUSES)]
        day = f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}"
        fn = f"postmortem-{code.lower()}.md"
        sources.append(
            SourceSpec(
                fn,
                "Engineering",
                "Engineering Handbook",
                f"Postmortem {code} ({name})",
                day,
                _md(
                    f"Postmortem {code}: {name} degradation",
                    [
                        ("Summary", f"On {day} the {name} was degraded for {minutes} minutes."),
                        ("Root cause", f"The root cause was {cause}."),
                        ("Impact", f"About {rng.randint(2, 40)}% of requests to the {name} failed or timed out."),
                        (
                            "Action items",
                            "Add an alert on the failing signal, add a regression test, and document the "
                            "recovery steps in the service runbook.",
                        ),
                    ],
                ),
            )
        )
        if i < 6:
            q(f"How long did incident {code} last?", "factual", f"{minutes} minutes", [fn])
            q(f"What caused {code}?", "factual", cause.split()[-1], [fn])

    # ------------------------------------------------------------------ research notes
    experiments = [
        ("Search ranking with learned weights", "click-through rate", "+4.2%", "two weeks"),
        ("Cache warming before peak hours", "p95 latency", "-31%", "ten days"),
        ("Smaller thumbnails on mobile", "page weight", "-22%", "one week"),
        ("Batching notification sends", "provider cost", "-17%", "three weeks"),
        ("Pre-computing price quotes", "checkout latency", "-12%", "two weeks"),
    ]
    for i, (title, metric, result, duration) in enumerate(experiments):
        fn = f"research-{i + 1:02d}.txt"
        text = (
            f"Research note: {title}\n\nHypothesis: {title.lower()} improves {metric}.\n\n"
            f"Method: an A/B test ran for {duration} on 10% of traffic.\n\n"
            f"Result: {metric} changed by {result} with no regression in error rates.\n\n"
            f"Decision: roll out to all users and keep monitoring {metric}."
        )
        sources.append(
            SourceSpec(fn, "Engineering", "Research Notes", f"Research: {title}", "2026-07-20", text.encode())
        )
        q(f"What effect did {title.lower()} have on {metric}?", "factual", result, [fn])
    q("Which experiment reduced provider cost and by how much?", "paraphrase", "-17%", ["research-04.txt"])

    # ------------------------------------------------------------------ policies (with traps)
    policies = [
        (
            "policy-remote-work.html",
            "Remote work policy",
            "2026-01-10",
            [
                ("Eligibility", "Employees may work remotely up to 3 days per week with their manager's agreement."),
                (
                    "Equipment",
                    "The company provides a laptop and a monitor; a home-office allowance of $400 per year "
                    "covers furniture.",
                ),
            ],
        ),
        (
            "policy-travel.html",
            "Travel policy",
            "2026-02-01",
            [
                ("Booking", "Book flights through the travel portal at least 14 days before departure."),
                ("Per diem", "The daily meal per diem is $65 for domestic travel and $85 for international travel."),
                ("Class", "Economy class for flights under 6 hours; premium economy for longer flights."),
            ],
        ),
        (
            "policy-security.md",
            "Information security policy",
            "2026-04-05",
            [
                (
                    "Authentication",
                    "Multi-factor authentication is mandatory for all company systems. Passwords "
                    "must be at least 14 characters.",
                ),
                ("Devices", "Laptops must use full-disk encryption and lock after 5 minutes of inactivity."),
                ("Reporting", "Report suspected phishing to security@northwind.example within 1 hour."),
            ],
        ),
        (
            "policy-expenses.md",
            "Expense policy",
            "2026-03-01",
            [
                (
                    "Approval",
                    "Expenses up to $500 are approved by your manager; above $500 also need finance approval.",
                ),
                ("Deadline", "Submit receipts within 30 days of the expense."),
            ],
        ),
        (
            "policy-parental-leave.md",
            "Parental leave policy",
            "2026-05-12",
            [
                (
                    "Entitlement",
                    "Primary caregivers receive 20 weeks of paid leave; secondary caregivers receive "
                    "8 weeks of paid leave.",
                )
            ],
        ),
        (
            "policy-refunds-2026.md",
            "Customer refund policy (current)",
            "2026-08-01",
            [
                ("Window", "Customers can request a refund within 14 days of purchase."),
                ("Exceptions", "Annual plans are refunded pro rata after the window."),
            ],
        ),
    ]
    for fn, title, date, secs in policies:
        content = _html(title, secs, date) if fn.endswith(".html") else _md(title, secs, f"Effective: {date}")
        sources.append(SourceSpec(fn, "Operations", "Policies", title, date, content))
    # Trap 1: an outdated refund policy that contradicts the current one.
    sources.append(
        SourceSpec(
            "policy-refunds-2022.md",
            "Operations",
            "Policies",
            "Customer refund policy (2022)",
            "2022-03-01",
            _md(
                "Customer refund policy",
                [
                    ("Window", "Customers can request a refund within 30 days of purchase."),
                    ("Exceptions", "No refunds for annual plans."),
                ],
                "Effective: 2022-03-01",
            ),
        )
    )
    # Trap 2: a near-duplicate of the travel policy (a forwarded copy with an added note).
    travel = next(p for p in policies if p[0] == "policy-travel.html")
    sources.append(
        SourceSpec(
            "travel-policy-copy.html",
            "Operations",
            "Policies",
            "Travel policy (copy)",
            "2026-02-03",
            _html(travel[1], [*travel[3], ("Note", "Forwarded copy for sales.")], "2026-02-03"),
        )
    )
    # Trap 3: a stale onboarding guide.
    sources.append(
        SourceSpec(
            "onboarding-2019.txt",
            "Operations",
            "Policies",
            "New starter guide (2019)",
            "2019-05-01",
            b"New starter guide\n\nCollect your badge from reception on day one. "
            b"The IT desk is on floor 2 and sets up your laptop. Lunch is provided on Fridays.",
        )
    )

    q("How many days per week can employees work remotely?", "factual", "3 days", ["policy-remote-work.html"])
    q(
        "What is the international per diem for meals?",
        "factual",
        "$85",
        ["policy-travel.html", "travel-policy-copy.html"],
    )
    q("How long must passwords be?", "factual", "14 characters", ["policy-security.md"])
    q("Who has to approve an expense of $800?", "paraphrase", "finance", ["policy-expenses.md"])
    q("How much paid leave do primary caregivers get?", "factual", "20 weeks", ["policy-parental-leave.md"])
    q("How much can I spend on home office furniture each year?", "paraphrase", "$400", ["policy-remote-work.html"])
    q(
        "Within how many days can customers request a refund?",
        "conflict",
        "14 days",
        ["policy-refunds-2026.md", "policy-refunds-2022.md"],
    )
    q(
        "What is the on-call acknowledgement time out of hours?",
        "factual",
        "15 minutes",
        [f"service-{s}.md" for _, s, _, _ in SERVICES],
    )

    # ------------------------------------------------------------------ finance (restricted)
    revenue = rng.randint(40, 90)
    margin = rng.randint(55, 75)
    sources.append(
        SourceSpec(
            "board-report-q2.pdf",
            "Finance",
            "Board Reports",
            "Board report Q2 2026",
            "2026-07-15",
            _pdf(
                "Board report Q2 2026",
                [
                    f"Quarterly revenue was ${revenue} million, up 18% year on year.",
                    f"Gross margin was {margin}%. Headcount ended the quarter at 412.",
                    "The board approved the expansion of the Payments Service into two new regions.",
                ],
            ),
        )
    )
    q(
        "What was the quarterly revenue in Q2 2026?",
        "factual",
        f"${revenue} million",
        ["board-report-q2.pdf"],
        asker="fiona",
    )
    q("What was the gross margin in Q2 2026?", "unanswerable", None, [], asker="erin")  # erin cannot read Finance

    # ------------------------------------------------------------------ unanswerable
    for text in [
        "What is the uptime SLA of the Billing Gateway?",
        "Which dental insurance provider does the company use?",
        "How many parking spaces does the Berlin office have?",
        "What is the p95 latency target of the Translation Service?",
        "Who is the head of the legal department?",
        "What is the maximum hotel rate in Tokyo?",
    ]:
        q(text, "unanswerable", None, [])
    return sources, qs


# Workspace memberships for the demo and the evaluation.
MEMBERS: dict[str, dict[str, str]] = {
    "Engineering": {"erin": "editor", "fiona": "reader"},
    "Operations": {"erin": "reader", "fiona": "editor"},
    "Finance": {"fiona": "editor"},
}


def manifest(sources: list[SourceSpec], questions: list[Question], seed: int = SEED) -> dict[str, Any]:
    return {
        "seed": seed,
        "generated_at": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
        "sources": [{k: v for k, v in asdict(s).items() if k != "content"} for s in sources],
        "questions": [asdict(q) for q in questions],
        "members": MEMBERS,
    }
