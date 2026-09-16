# Inertia Content Intelligence — Python Module

## Structure

```
inertia_content/
├── __init__.py     # package exports
├── models.py       # Person, ContentPiece, SendLog dataclasses + all constants
├── tags.py         # auto tag inference rules (pure functions)
├── matcher.py      # matching and ranking engine
├── storage.py      # MySQL storage layer
├── tagger.py       # AI tag suggestion via Anthropic API (content only, no client data)
└── main.py         # CLI demo — run to verify everything works
```

## Setup

```bash
pip install mysql-connector-python anthropic
```

## Database setup (run once)

```python
from inertia_content import Database

db = Database(
    host="localhost",
    name="inertia",
    user="your_user",
    password="your_password",
)
db.create_tables()
# Creates: ic_persons, ic_content, ic_send_log
```

Or use environment variables instead of passing credentials:
```
INERTIA_DB_HOST=localhost
INERTIA_DB_PORT=3306
INERTIA_DB_NAME=inertia
INERTIA_DB_USER=root
INERTIA_DB_PASSWORD=secret
ANTHROPIC_API_KEY=sk-ant-...
```

## Core usage

### Save a person
```python
from inertia_content import Person, Category, AgeBracket, AssetBracket, Database
from datetime import date

person = Person(
    name="Rahul Mehta",
    category=Category.CLIENT,
    age_bracket=AgeBracket.A35_44,
    city="Pune",
    holds_mf=True, holds_stock=True,
    investable_assets=AssetBracket.L50_1CR,
    tag_high_awareness=True,   # adviser sets this
    tag_high_earner=True,
    last_contacted=date.today(),
    max_msgs_per_month=2,
)

db = Database()
person.id = db.save_person(person)
```

### Get matched persons for a content piece
```python
from inertia_content import rank_persons_for_content, format_match_results

persons = db.get_all_persons()
content = db.get_content(content_id=3)

results = rank_persons_for_content(persons, content, min_match=1)
print(format_match_results(results))

# Each result has:
# result.person           — Person object
# result.match_count      — number of matching tags
# result.matched_tags     — list of Tag.* constants
# result.days_since_contact
# result.ok_to_contact    — respects max_msgs_per_month
# result.contact_reason   — explanation string
```

### Find content for a specific person
```python
from inertia_content import find_content_for_person

content_list = db.get_all_content()
matches = find_content_for_person(person, content_list)

for m in matches:
    print(m["content"].title, "—", m["match_count"], "matching tags")
    print("  Tags:", ", ".join(m["matched_tags"]))
```

### AI tag suggestion for a content piece
```python
from inertia_content import suggest_tags, apply_suggestions_to_content, ContentPiece, ContentFormat
from datetime import date

text = """Your content text here..."""

suggestion = suggest_tags(text)  # only content text goes to API, never client data

# Review suggestions
for tag_id, active in suggestion.tags.items():
    print(f"{'YES' if active else ' no'}  {tag_id}: {suggestion.reasons[tag_id]}")

# Apply confirmed tags to a content piece and save
piece = ContentPiece(
    title="Your content title",
    format=ContentFormat.LINKEDIN,
    created_date=date.today(),
    topic_category="Investor Psychology",
)
apply_suggestions_to_content(piece, suggestion)
# Adviser reviews and modifies piece.tag_* fields before saving
piece.id = db.save_content(piece)
```

### Log a send decision
```python
from inertia_content import SendLog, Decision, Engagement
from datetime import date

log = SendLog(
    date_sent=date.today(),
    person_id=person.id,
    person_name=person.name,
    content_id=content.id,
    content_title=content.title,
    decision=Decision.SEND,
    channel="WhatsApp",
    engagement=Engagement.NONE,  # update later
    followup_needed=False,
)
log_id = db.log_send(log)

# Update engagement after 7 days
db.update_engagement(log_id, Engagement.RESPONDED, followup_needed=False, notes="Called back same day")
```

### Check engagement stats
```python
stats = db.get_engagement_stats(person_id=1)
# {'total_sent': 5, 'responded': 2, 'opened': 2, 'no_response': 1}

stats = db.get_content_performance(content_id=3)
# {'total_sent': 12, 'responded': 4, 'opened': 5, 'no_response': 3}
```

## Integration notes

- **All Tag constants** are in `models.Tag.*` — use these, not raw strings
- **Auto tags** (wealth_accum, wealth_preserv, fin_complexity, misaligned_prod) are computed fresh every time from profile fields — do not store them in your UI as fixed values
- **Adviser tags** are stored as booleans on the Person object — adviser sets these once per year via questionnaire
- **Client data never leaves your server** — only content text goes to the Anthropic API via `tagger.py`
- **Storage layer is swappable** — if you want to use your existing ORM/models, just replicate the method signatures in storage.py and inject your implementation

## Run the demo

```bash
cd inertia_content
python main.py
```
